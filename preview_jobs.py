"""Collect a review list using the project's browser scraper. Never clicks Apply."""
import asyncio
import csv
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse, urljoin

from playwright.async_api import async_playwright
from candidate_rules import CITY_CODES, SEARCH_TERMS, assess
from zhilian import ZhaopinAutomation
from keyword_filter import split_keywords, match_filter_keyword, mark_blacklist_match
from app_paths import DATA_DIR
from settings import current_settings, CITY_CODES
from job_filters import check_job, mark_filter, filter_signature

ROOT = DATA_DIR
STATE = ROOT / 'preview_status.json'
ROWS = ROOT / 'candidate_jobs.json'
CSV = ROOT / '候选岗位清单.csv'

def collection_decision(job, direct=False, keywords=None):
    hit = match_filter_keyword(keywords, job)
    if hit:
        return dict(review_status='excluded', score=0, reasons=[f'命中黑名单关键词：{hit}'],
                    uncertainties=[], blacklist_keyword=hit)
    if direct:
        return dict(review_status='direct_pending', score=0, reasons=['按用户广投授权，采集后直接投递'], uncertainties=[])
    return assess(job)


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


class Preview(ZhaopinAutomation):
    def __init__(self):
        super().__init__(headless=False)
        self.rows = json.loads(ROWS.read_text(encoding='utf-8')) if ROWS.exists() else []
        for row in self.rows:
            row.update(collection_decision(row, self.config.get('campaign_no_filter', False),
                                           split_keywords(self.config.get('filter_keywords', ''))))
        self.seen = {f"{row.get('company')}|{row.get('title')}|{row.get('location')}" for row in self.rows}
        self.initial_count = len(self.rows)
        self.nickname = ''

    def report(self, stage, message):
        automatic = self.config.get('campaign_authorized', False)
        if automatic and stage == 'awaiting_confirmation':
            stage = 'auto_monitoring'
            message = '本轮采集结束，继续无筛选广投。' if self.config.get('campaign_no_filter') else '本轮采集结束，自动循环将继续处理符合条件的新岗位。'
        print(message, flush=True)
        status = dict(stage=stage, message=message, nickname=self.nickname,
                              count=len(self.rows), updated_at=datetime.now().isoformat(),
                              login_verified=bool(self.nickname), page_url=self.page.url if self.page else '',
                              campaign_mode=automatic)
        if automatic and (ROOT / 'campaign_status.json').exists():
            status['campaign'] = json.loads((ROOT / 'campaign_status.json').read_text(encoding='utf-8'))
        write_json(STATE, status)

    async def wait_control(self,delay=0):
        end=time.monotonic()+delay
        reported=False
        while True:
            self.config.load_from_db()
            if self.config.get('campaign_paused',False):
                if not reported:self.report('paused','已暂停，浏览器和登录保留。');reported=True
                await asyncio.sleep(1);continue
            if reported:self.report('running','已恢复任务。')
            remaining=end-time.monotonic()
            if remaining<=0:return
            await asyncio.sleep(min(1,remaining))

    async def init_browser(self):
        self.playwright=await async_playwright().start()
        channel=current_settings(self.config).browser_channel
        choices=['chrome','msedge','chromium'] if channel=='auto' else [channel]
        errors=[]
        for choice in choices:
            options=dict(headless=False,args=['--start-maximized'])
            if choice!='chromium':options['channel']=choice
            try:
                self.context=await self.playwright.chromium.launch_persistent_context(
                    str(ROOT/'.browser-profile'),viewport={'width':1440,'height':960},**options)
                break
            except Exception as error:errors.append(str(error).splitlines()[0])
        if not self.context:
            raise RuntimeError('找不到可用浏览器。请安装Chrome/Edge，或运行 uv run playwright install chromium。'+ ' / '.join(errors))
        self.browser=self.context.browser
        self.page=self.context.pages[0] if self.context.pages else await self.context.new_page()

    async def read_nickname(self):
        selectors = ('.home-header__c-login .c-login__top__name',
                     '.page-header__user__logged-in > span')
        for page in self.context.pages:
            if page.is_closed() or urlparse(page.url).hostname not in ('www.zhaopin.com', 'xiaoyuan.zhaopin.com'):
                continue
            for selector in selectors:
                name = page.locator(selector).first
                if await name.is_visible():
                    nickname = (await name.inner_text()).strip()
                    if nickname:
                        changed = nickname != self.nickname
                        self.nickname = nickname
                        if changed:
                            self.config.update({'login_success_keyword': nickname})
                            await self.context.storage_state(path=str(ROOT / self.state_file))
                        return True
        return False

    async def login(self):
        self.report('checking_login','正在检查程序浏览器登录状态。')
        await self.page.goto('https://www.zhaopin.com/',wait_until='domcontentloaded',timeout=60000)
        reported=False
        while self.browser.is_connected():
            await self.wait_control()
            if await self.read_nickname():
                self.report('logged_in','登录已确认，按网页配置开始处理。')
                return True
            if not reported:
                self.report('needs_login','请在程序浏览器中扫码或短信登录，完成后会自动继续。');reported=True
            await asyncio.sleep(2)
        return False

    async def legacy_info(self, card):
        async def text(selector):
            element = await card.query_selector(selector)
            return ((await element.inner_text()) or '').strip() if element else ''
        link = await card.query_selector('a.jobinfo__name')
        href = urljoin('https://www.zhaopin.com', await link.get_attribute('href') or '') if link else ''
        if urlparse(href).hostname == 'www.zhaopin.com':
            href = href.replace('http://', 'https://', 1)
        else:
            href = ''
        tags = await card.query_selector_all('.jobinfo__other-info-item')
        tags = [((await element.inner_text()) or '').strip() for element in tags]
        return dict(title=await text('.jobinfo__name'), company=await text('.companyinfo__name'),
                    salary=await text('.jobinfo__salary'), location=tags[0] if tags else '',
                    experience=tags[1] if len(tags) > 1 else '', education=tags[2] if len(tags) > 2 else '',
                    url=href, description='', requirements='')

    async def legacy_detail(self, info):
        if not info.get('url'):
            return info
        detail = await self.context.new_page()
        try:
            await detail.goto(info['url'], wait_until='domcontentloaded', timeout=45000)
            await detail.wait_for_selector('.describtion-card', timeout=10000)
            description = detail.locator('.describtion-card').first
            info['description'] = await description.inner_text()
            info['is_favorite']=None
            for label,state in (('取消收藏',True),('已收藏',True),('收藏',False)):
                if await detail.get_by_role('button',name=label,exact=True).first.is_visible():
                    info['is_favorite']=state;break
            if await detail.get_by_role('button', name='继续沟通', exact=True).count():
                info['already_applied_on_site'] = True
        except Exception as error:
            print(f"详情未完整读取，列入待核验：{info.get('title')}：{error}", flush=True)
        finally:
            await detail.close()
        return info

    def save_rows(self):
        settings=current_settings(self.config)
        signature=filter_signature(settings)
        for row in self.rows:
            eligible,reason=check_job(row,settings,final=False)
            if not eligible:mark_filter(row,reason,signature)
            elif row.get('filter_signature')!=signature:
                row.update(review_status='direct_pending',score=0,reasons=['进入待处理队列'],uncertainties=[])
                if str(row.get('application_result','')).startswith('未投递：'):row['application_result']='待按新配置处理'
        self.rows.sort(key=lambda row:row.get('captured_at',''))
        write_json(ROWS,self.rows)
        with CSV.open('w',encoding='utf-8-sig',newline='') as handle:
            writer=csv.writer(handle);writer.writerow(['公司','职位','城市','薪资','链接','过滤原因','结果'])
            for row in self.rows:
                values=[row.get(key,'') for key in ('company','title','location','salary','url')]
                values+=['；'.join(row.get('reasons',[])),row.get('application_result','待处理')]
                writer.writerow([("'"+str(value)) if str(value).startswith(('=','+','-','@')) else value for value in values])

    async def scan(self):
        self.save_rows()
        settings=current_settings(self.config)
        direct=True
        keywords = split_keywords(self.config.get('filter_keywords', ''))
        if direct:
            from auto_campaign import Ledger, pending_jobs
            ledger = Ledger()
            if settings.processing_mode=='apply' and len(pending_jobs(self.rows, ledger, True, settings=settings)) >= settings.batch_size:
                self.report('ready_to_apply', '待处理队列已就绪。')
                return
        cycle = int(self.config.get('campaign_scan_cycle', 0))
        if self.config.get('campaign_authorized'):
            self.config.set('campaign_scan_cycle', cycle + 1)
        # Each pass covers every city before moving to the next keyword.
        terms=settings.search_terms if not settings.search_url else ['自定义搜索']
        for term in terms:
            for city in (settings.cities if not settings.search_url else ['自定义']):
                await self.wait_control()
                if len(self.rows)-self.initial_count>=settings.collect_limit:return
                code=CITY_CODES.get(city,'')
                if not direct and len(self.rows) - self.initial_count >= 120:
                    self.report('awaiting_confirmation', f'首批已采集 {len(self.rows)} 条，已按匹配度排序；请先核验清单，本流程未投递。')
                    return
                url=settings.search_url or ('https://www.zhaopin.com/sou/jl'+code+'/?'+urlencode({'kw':term}))
                self.report('scanning', f'{"广投采集" if direct else "采集"}：正在搜索 {city} / {term}；已采集 {len(self.rows)} 条。')
                await self.page.goto(url, wait_until='domcontentloaded', timeout=60000)
                await self.read_nickname()
                await self.context.storage_state(path=str(ROOT / self.state_file))
                try:
                    await self.page.wait_for_selector('.job-list-panel .job-card, .joblist-box__item', timeout=20000)
                except Exception:
                    body = await self.page.locator('body').inner_text()
                    if any(word in body for word in ('安全验证', '请完成验证', '访问异常')):
                        self.report('needs_user', '搜索页需要你本人完成站点验证，完成后将继续。')
                        await self.page.wait_for_selector('.job-list-panel .job-card, .joblist-box__item', timeout=0)
                    else:
                        continue
                cards = await self.page.query_selector_all('.job-list-panel .job-card')
                split_layout = bool(cards)
                if not split_layout:
                    cards = await self.page.query_selector_all('.joblist-box__item')
                offset = 12 * (cycle % 5) if self.config.get('campaign_authorized') else 0
                if split_layout and offset:
                    for _ in range(3):
                        if len(cards) >= offset + 12:
                            break
                        previous_count = len(cards)
                        await self._scroll_split_list()
                        await asyncio.sleep(1)
                        cards = await self.page.query_selector_all('.job-list-panel .job-card')
                        if len(cards) <= previous_count:
                            break
                selected = cards[offset:offset + 12] or cards[:12]
                for card in selected:
                    await self.wait_control()
                    if len(self.rows)-self.initial_count>=settings.collect_limit:return
                    info = await self.extract_job_info(card) if split_layout else await self.legacy_info(card)
                    # Pre-filter obvious unrelated titles to avoid unnecessary detail requests.
                    key = f"{info.get('company')}|{info.get('title')}|{info.get('location')}"
                    previous = next((row for row in self.rows if f"{row.get('company')}|{row.get('title')}|{row.get('location')}" == key), None)
                    if (key in self.seen and (not direct or (previous and previous.get('url')))) or self.db.is_job_applied(info):
                        continue
                    self.seen.add(key)
                    allowed,reason=check_job(info,settings,final=False)
                    preliminary={'review_status':'direct_pending' if allowed else 'excluded'}
                    detail_read=allowed
                    if detail_read:
                        info = await self.fetch_job_detail_split(info, card) if split_layout else await self.legacy_detail(info)
                    if split_layout:
                        link = await card.query_selector('a[href*="/jobdetail/"]')
                        if not link and detail_read:
                            link = await self.page.query_selector('.job-detail-panel a[href*="/jobdetail/"]')
                        if link:
                            href = urljoin('https://www.zhaopin.com/', await link.get_attribute('href') or '')
                            if urlparse(href).hostname in ('www.zhaopin.com', 'jobs.zhaopin.com'):
                                info['url'] = href.replace('http://', 'https://', 1)
                        if detail_read and '/jobdetail/' in self.page.url and urlparse(self.page.url).hostname == 'www.zhaopin.com':
                            info['url'] = self.page.url
                    if detail_read:
                        if split_layout and settings.favorite_policy!='all':
                            info['is_favorite']=None
                            for label,state in (('取消收藏',True),('已收藏',True),('收藏',False)):
                                button=self.page.get_by_role('button',name=label,exact=True).first
                                if await button.is_visible():info['is_favorite']=state;break
                        allowed,reason=check_job(info,settings,final=True)
                    info.update(review_status='direct_pending',score=0,reasons=['待按配置处理'],uncertainties=[])
                    if not allowed:mark_filter(info,reason,filter_signature(settings))
                    if info.get('already_applied_on_site'):
                        info.update(review_status='excluded', score=0, reasons=['站点显示已投递/已沟通，跳过'], uncertainties=[])
                    info.update(id=hashlib.sha256(key.encode()).hexdigest()[:16], search_url=url,
                                application_result='待本人确认，未投递', captured_at=datetime.now().isoformat())
                    if previous is not None:
                        previous.update(info)
                    else:
                        self.rows.append(info)
                    self.save_rows()
                    if settings.processing_mode=='apply' and len(pending_jobs(self.rows, ledger, True, settings=settings)) >= settings.batch_size:
                        self.report('ready_to_apply', '已收齐本批待处理职位。')
                        return
                    await asyncio.sleep(0.3 if info['review_status'] == 'excluded' else 1.5)
        self.report('collected',f'本轮采集完成，共{len(self.rows)}条。')

    async def run_preview(self):
        try:
            await self.init_browser()
            if await self.login():
                self.login_page=self.page
                self.page=await self.context.new_page()
                from auto_campaign import Campaign
                await Campaign(self).run()
        except Exception as error:
            self.report('error','任务停止：'+str(error))
            raise
        finally:
            if self.context:
                try:await self.context.storage_state(path=str(ROOT/self.state_file))
                except Exception:pass
                await self.context.close()
            if self.playwright:await self.playwright.stop()

if __name__=='__main__':
    asyncio.run(Preview().run_preview())
