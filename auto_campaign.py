"""Continuous, user-authorized applications with durable deduplication and evidence."""
import asyncio
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from keyword_filter import split_keywords, match_filter_keyword, mark_blacklist_match
from app_paths import DATA_DIR
from settings import current_settings
from job_filters import check_job, mark_filter, filter_signature

ROOT = DATA_DIR
CN = timezone(timedelta(hours=8))

def now():
    return datetime.now(CN).isoformat()


def saved_resume_after(path=None):
    """Keep a site-imposed wait across process restarts."""
    try:
        data = json.loads((path or ROOT / 'campaign_status.json').read_text(encoding='utf-8'))
        resume = datetime.fromisoformat(data.get('resume_after', ''))
        if resume.tzinfo is not None and resume > datetime.now(CN):
            return resume.isoformat()
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None

def key_for(job):
    match = re.search(r'/jobdetail/([A-Za-z0-9_-]+)\.htm', job.get('url') or '')
    return match.group(1) if match else hashlib.sha256((job.get('url') or '').encode()).hexdigest()

def normalized(text):
    return re.sub(r'\W+', '', text or '').lower()

def platform_limited(text):
    return bool(re.search(r'(?:今日|今天|当日).{0,12}(?:投递|申请).{0,15}(?:上限|超过|用完)|已超过今日投递|投递次数已达上限', text))

class Ledger:
    def __init__(self, path=None):
        self.path = path or ROOT / 'campaign.db'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS submissions (job_key TEXT PRIMARY KEY, batch_id INTEGER, title TEXT, company TEXT, url TEXT, status TEXT, evidence TEXT, created_at TEXT, updated_at TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS batches (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT)')

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=20)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def new_batch(self):
        with self.connect() as db:
            return db.execute('INSERT INTO batches(created_at) VALUES (?)', (now(),)).lastrowid

    def seen(self, key):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM submissions WHERE job_key=?', (key,)).fetchone() is not None

    def reserve(self, job, batch, limit=30, daily_limit=0, total_limit=0):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM submissions WHERE batch_id=?', (batch,)).fetchone()[0] >= limit:
                return False
            if daily_limit and db.execute("SELECT COUNT(*) FROM submissions WHERE substr(created_at,1,10)=?", (datetime.now(CN).date().isoformat(),)).fetchone()[0] >= daily_limit:
                return False
            if total_limit and db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0] >= total_limit:
                return False
            try:
                db.execute('INSERT INTO submissions VALUES (?,?,?,?,?,?,?,?,?)',
                           (key_for(job), batch, job['title'], job['company'], job['url'], 'reserved', '', now(), now()))
            except sqlite3.IntegrityError:
                return False
            return True

    def result(self, job, status, evidence):
        with self.connect() as db:
            db.execute('UPDATE submissions SET status=?,evidence=?,updated_at=? WHERE job_key=?',
                       (status, evidence[:1200], now(), key_for(job)))

    def counts(self):
        with self.connect() as db:
            return dict(db.execute('SELECT status,COUNT(*) FROM submissions GROUP BY status').fetchall())

    def today_count(self):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM submissions WHERE substr(created_at,1,10)=?", (datetime.now(CN).date().isoformat(),)).fetchone()[0]

    def total_count(self):
        with self.connect() as db:
            return db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0]

    def all_keys(self):
        with self.connect() as db:
            return {row[0] for row in db.execute('SELECT job_key FROM submissions')}

    def batch_count(self, batch):
        with self.connect() as db:
            return db.execute('SELECT COUNT(*) FROM submissions WHERE batch_id=?', (batch,)).fetchone()[0]

def pending_jobs(rows, ledger, no_filter=False, keywords=None, settings=None):
    seen = ledger.all_keys()
    queue = []
    for job in rows:
        if settings is not None:
            if job.get('review_status')=='excluded' and job.get('filter_signature')==filter_signature(settings):continue
            eligible,reason=check_job(job,settings,final=False)
            if not eligible:
                mark_filter(job,reason,filter_signature(settings))
                continue
        elif mark_blacklist_match(job, keywords):
            continue
        if not job.get('url') or job.get('already_applied_on_site'):
            continue
        if no_filter and urlparse(job['url']).hostname != 'www.zhaopin.com':
            continue
        if not no_filter and job.get('review_status') == 'excluded':
            continue
        key = key_for(job)
        if key in seen:
            continue
        seen.add(key)
        queue.append(job)
    return queue

class Campaign:
    def __init__(self, worker):
        self.worker = worker
        self.ledger = Ledger()
        self.status = {'mode': 'auto', 'batch_size': 30, 'daily_limit': None,
                       'started_at': now(), 'paused': False}
        resume = saved_resume_after()
        if resume:
            self.status['resume_after'] = resume

    def report(self, stage, message):
        self.status.update(stage=stage, message=message, updated_at=now(),
                           counts=self.ledger.counts(), nickname=self.worker.nickname,
                           no_filter=self.worker.config.get('campaign_no_filter', False),
                           blacklist_keywords=split_keywords(self.worker.config.get('filter_keywords', '')),
                           apply_interval_seconds=self.worker.config.get('campaign_apply_interval_seconds', 10),
                           batch_interval_seconds=self.worker.config.get('campaign_batch_interval_seconds', 60),
                           pid=__import__('os').getpid())
        from preview_jobs import write_json
        write_json(ROOT / 'campaign_status.json', self.status)
        write_json(ROOT / 'preview_status.json', dict(self.status, count=len(self.worker.rows),
                    login_verified=bool(self.worker.nickname), campaign_mode=True))
        print(message, flush=True)

    async def submit(self, job, batch):
        if self.ledger.seen(key_for(job)) or self.worker.db.is_job_applied(job):
            return
        await self.worker.wait_control()
        settings=current_settings(self.worker.config)
        if settings.processing_mode!='apply':return
        eligible,reason=check_job(job,settings,final=False)
        if not eligible:
            mark_filter(job,reason,filter_signature(settings));self.worker.save_rows();return
        page=await self.worker.context.new_page()
        keep_open=False
        try:
            await page.goto(job['url'],wait_until='domcontentloaded',timeout=45000)
            await page.wait_for_selector('.summary-planes__action button, .job-detail-summary button',timeout=12000)
            body_before=await page.locator('body').inner_text()
            actions=page.locator('.summary-planes__action, .job-detail-summary').first
            if platform_limited(body_before):raise PlatformLimit()
            if urlparse(page.url).hostname=='passport.zhaopin.com' or '请先登录' in body_before:
                keep_open=True
                raise NeedsUser('登录失效，请在程序浏览器中重新登录。',page)
            for field,selector in (
                ('title','.summary-planes__title, .job-detail-summary__title-text'),
                ('company','.summary-planes__company-name, .job-detail-summary__company-name'),
                ('salary','.summary-planes__salary, .job-detail-summary__salary')):
                element=page.locator(selector).first
                if await element.count():
                    value=(await element.inner_text()).strip()
                    if value:job[field]=value
            description=page.locator('.describtion-card, .job-description__content').first
            try:
                await description.wait_for(state='visible',timeout=10000)
                job['description']=(await description.inner_text()).strip()
            except Exception:job['description']=''
            job['is_favorite']=None
            for label,state in (('取消收藏',True),('已收藏',True),('收藏',False)):
                locator=page.get_by_role('button',name=label,exact=True).first
                if await locator.is_visible():job['is_favorite']=state;break
            await self.worker.wait_control()
            settings=current_settings(self.worker.config)
            if settings.processing_mode!='apply':return
            eligible,reason=check_job(job,settings,final=True)
            if not eligible:
                mark_filter(job,reason,filter_signature(settings))
                self.report('applying',f"过滤跳过：{job['title']}（{reason}）")
                return
            for name in ('已投递', '已申请', '继续沟通'):
                button = actions.get_by_role('button', name=name, exact=True).first
                if await button.is_visible():
                    job['application_result'] = '站点显示已投递/已沟通，跳过'
                    self.worker.db.add_record(job, 'already_applied', job['application_result'])
                    return
            apply = actions.get_by_role('button', name='立即投递', exact=True).first
            if not await apply.is_visible() or not await apply.is_enabled():
                job['application_result'] = '未找到可用的立即投递按钮，跳过'
                return
            if not self.ledger.reserve(job, batch, settings.batch_size, settings.daily_limit, settings.total_limit):
                return
            job['application_result'] = '已发起投递，等待站点结果'
            self.worker.save_rows()
            await apply.click()
            confirmed = False
            last_text = ''
            for _ in range(16):
                await asyncio.sleep(1)
                last_text = await page.locator('body').inner_text()
                if platform_limited(last_text):
                    self.ledger.result(job, 'platform_limit', '站点提示今日投递上限')
                    job['application_result'] = '平台今日上限，等待下一自然日'
                    raise PlatformLimit()
                for phrase in ('投递成功', '已向对方发送简历', '简历已发送', '简历投递成功'):
                    if phrase in last_text and phrase not in body_before:
                        confirmed = True
                for name in ('已投递', '已申请', '继续沟通'):
                    button = actions.get_by_role('button', name=name, exact=True).first
                    if await button.is_visible():
                        confirmed = True
                if confirmed:
                    break
                if any(word in last_text for word in ('请完成验证', '安全验证', '拖动滑块')):
                    self.ledger.result(job, 'unknown', '需要本人完成站点验证；本职位不自动重试')
                    job['application_result'] = '验证中，投递结果未确认；不重复提交'
                    keep_open = True
                    await page.bring_to_front()
                    raise NeedsUser('智联要求本人验证，当前投递结果未确认。请在程序窗口处理。', page)
                if urlparse(page.url).hostname == 'passport.zhaopin.com' or '请先登录' in last_text:
                    self.ledger.result(job, 'unknown', '需要重新登录，投递结果未确认')
                    keep_open = True
                    await page.bring_to_front()
                    raise NeedsUser('程序会话需要重新登录，已暂停提交。', page)
                # Only resume-related confirmation dialogs; never click generic page buttons.
                dialogs = page.locator('[role="dialog"], .el-dialog, .ant-modal, .zp-dialog, .resume-multiple')
                for dialog in await dialogs.all():
                    if not await dialog.is_visible():
                        continue
                    text = await dialog.inner_text()
                    if not any(word in text for word in ('简历', '投递')):
                        continue
                    if any(word in text for word in ('用户协议', '服务协议', '支付', '购买')):
                        keep_open = True
                        self.ledger.result(job, 'unknown', '出现需要本人处理的协议/付费提示，未继续操作')
                        raise NeedsUser('投递弹窗需要本人处理协议或付费提示，已暂停提交。', page)
                    for name in ('确认投递', '投递', '确定'):
                        button = dialog.get_by_role('button', name=name, exact=True).first
                        if await button.is_visible() and await button.is_enabled():
                            await button.click()
                            break
            if confirmed:
                self.ledger.result(job, 'success', '站点显示投递成功或按钮变为已投递/继续沟通')
                job['application_result'] = '投递成功（站点确认）'
                self.worker.db.add_record(job, 'success', '自动循环投递；站点已确认')
                self.report('applying', f"投递成功：{job['company']} / {job['title']}")
            else:
                # Unknown outcomes consume a reservation and are never retried automatically.
                self.ledger.result(job, 'unknown', '未观察到可靠成功提示；不自动重试')
                job['application_result'] = '投递结果未确认，不自动重试'
                self.report('applying', f"结果待核验：{job['company']} / {job['title']}，不重复提交。")
        except (PlatformLimit, NeedsUser):
            raise
        except Exception as error:
            if self.ledger.seen(key_for(job)):
                self.ledger.result(job, 'unknown', str(error).splitlines()[0])
                job['application_result'] = '投递结果未确认，不自动重试'
            else:
                job['application_result'] = '尚未提交：' + str(error).splitlines()[0][:160]
        finally:
            self.worker.save_rows()
            await self.worker.context.storage_state(path=str(ROOT / self.worker.state_file))
            if not keep_open:
                await page.close()

    async def run(self, resume_batch=None):
        self.report('running','循环已启动，按网页中的当前配置处理。')
        while self.worker.browser.is_connected():
            await self.worker.wait_control()
            settings=current_settings(self.worker.config)
            resume=self.status.get('resume_after')
            if resume and datetime.fromisoformat(resume)>datetime.now(CN):
                self.report('platform_limit','平台当日额度等待中，到期后自动继续。')
                await self.worker.wait_control(15);continue
            self.status.pop('resume_after',None)
            if settings.processing_mode=='apply' and settings.total_limit and self.ledger.total_count()>=settings.total_limit:
                self.report('total_limit','达到累计提交上限，调整上限后可继续。')
                await self.worker.wait_control(10);continue
            if settings.processing_mode=='apply' and settings.daily_limit and self.ledger.today_count()>=settings.daily_limit:
                self.report('daily_limit','达到用户设定的每日上限，等待下一自然日或调整上限。')
                await self.worker.wait_control(15);continue
            batch=self.ledger.new_batch();self.status['batch_id']=batch
            try:
                candidates=pending_jobs(self.worker.rows,self.ledger,True,settings=settings)
                if settings.processing_mode=='preview' or len(candidates)<settings.batch_size:
                    self.worker.initial_count=len(self.worker.rows)
                    self.report('collecting','正在采集新岗位，并执行当前过滤规则。')
                    await self.worker.scan()
                    settings=current_settings(self.worker.config)
                    candidates=pending_jobs(self.worker.rows,self.ledger,True,settings=settings)
                if settings.processing_mode=='apply':
                    for job in candidates:
                        await self.worker.wait_control()
                        settings=current_settings(self.worker.config)
                        if settings.processing_mode!='apply' or self.ledger.batch_count(batch)>=settings.batch_size:break
                        if self.ledger.seen(key_for(job)) or self.worker.db.is_job_applied(job):continue
                        if settings.daily_limit and self.ledger.today_count()>=settings.daily_limit:break
                        if settings.total_limit and self.ledger.total_count()>=settings.total_limit:break
                        self.report('applying',f"处理：{job['company']} / {job['title']}")
                        await self.submit(job,batch)
                        await self.worker.wait_control(settings.apply_interval_seconds)
                self.worker.save_rows()
                if not settings.loop_enabled:
                    self.report('completed','单轮任务完成。关闭浏览器前可在网页查看与导出记录。')
                    return
                self.report('waiting_next_batch',f'本批完成，等待{settings.batch_interval_seconds:g}秒。')
                await self.worker.wait_control(settings.batch_interval_seconds)
            except NeedsUser as blocked:
                self.report('needs_user',str(blocked))
                while self.worker.browser.is_connected() and not blocked.page.is_closed():
                    await self.worker.wait_control(3)
                    text=await blocked.page.locator('body').inner_text()
                    if not any(word in text for word in ('请完成验证','安全验证','拖动滑块','请先登录','用户协议','服务协议','支付','购买')) and urlparse(blocked.page.url).hostname!='passport.zhaopin.com':break
            except PlatformLimit:
                resume=datetime.now(CN).replace(hour=0,minute=10,second=0,microsecond=0)+timedelta(days=1)
                self.status['resume_after']=resume.isoformat()
                self.report('platform_limit','智联提示当日上限，下一自然日00:10后继续。')
            except Exception as error:
                self.report('retry_wait',f'暂缓：{str(error).splitlines()[0][:180]}；30秒后重试。')
                await self.worker.wait_control(30)

class PlatformLimit(Exception):
    pass

class NeedsUser(Exception):
    def __init__(self, message, page):
        super().__init__(message)
        self.page = page
