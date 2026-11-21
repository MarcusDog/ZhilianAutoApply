"""Parse job cards and detail panels in the split-view search page."""

import asyncio
import logging
import re

logger = logging.getLogger(__name__)


class ZhilianBrowserSession:
    """Shared parsing for the collector's current browser page."""

    async def parse_position_summary(self, job_element):
        """提取新版分栏页面左侧 .job-card 的基础信息"""
        info = {
            "title": "未知",
            "company": "未知",
            "url": None,
            "job_hash2": None,
            "salary": None,
            "location": None,
            "description": None,
            "requirements": None,
            "experience": None,
            "education": None,
            "category": None,
            "registered_capital": None,
            "company_scale": None
        }
        try:
            return await self._extract_job_info_new(job_element, info)
        except Exception as e:
            logger.error(f"提取职位信息失败: {e}")
            return info

    # 学历关键词（新版 .job-card__skill-tag 启发式解析用）
    _EDU_KEYWORDS = ('博士', '硕士', '本科', '大专', '中专', '高中', '初中', '学历不限')

    # 经验关键词模式（如 5-10年 / 3年以上 / 经验不限 / 应届生）
    _EXP_RE = re.compile(r'\d+[-~]\d+年|\d+年以上|\d+年以下|经验不限|应届|在校生|经验应届')

    async def _extract_job_info_new(self, card, info):
        """提取新版分栏页面左侧 .job-card 的基础信息

        新版卡片无 jobdetail 链接（公司链接指向 companydetail），
        因此 url/job_hash2 为空，查重时 DAO 自动回退到 标题+公司名 的 job_hash。
        """
        # 职位标题：.job-card__title-clamp .vue-clamp__text（文本被截断时用 aria-label 补全）
        try:
            title_elem = await card.query_selector('.job-detail-summary__title-text')
            if title_elem:
                info["title"] = ((await title_elem.text_content()) or '').strip()
                if not info["title"]:
                    aria = await title_elem.get_attribute('aria-label')
                    info["title"] = (aria or '').strip()
            if not info["title"] or info["title"] == '未知':
                aria_span = await card.query_selector('.job-card__title-clamp span[aria-label]')
                if aria_span:
                    info["title"] = (await aria_span.get_attribute('aria-label') or '').strip() or info["title"]
        except Exception as e:
            logger.warning(f"提取新版职位标题失败: {e}")

        # 薪资
        try:
            salary_elem = await card.query_selector('.job-card__salary')
            if salary_elem:
                info["salary"] = ((await salary_elem.text_content()) or '').strip()
        except Exception:
            pass

        # 公司名称：.job-card__company-name（title 属性更完整）
        try:
            company_elem = await card.query_selector('.job-card__company-name')
            if company_elem:
                title_attr = await company_elem.get_attribute('title')
                info["company"] = (title_attr or (await company_elem.text_content()) or '').strip()
        except Exception:
            pass

        # 工作地点：.job-card__location span（title 属性更完整）
        try:
            loc_elem = await card.query_selector('.job-card__location span')
            if loc_elem:
                title_attr = await loc_elem.get_attribute('title')
                info["location"] = (title_attr or (await loc_elem.text_content()) or '').strip()
        except Exception:
            pass

        # 技能标签：前两个通常为 学历/经验（部分卡片缺经验），按关键词启发式解析
        try:
            tags = await card.query_selector_all('.job-card__skill-tag')
            for tag in tags[:3]:
                text = ((await tag.text_content()) or '').strip()
                if not text:
                    continue
                if not info["education"] and any(kw in text for kw in self._EDU_KEYWORDS):
                    info["education"] = text
                elif not info["experience"] and self._EXP_RE.search(text):
                    info["experience"] = text
        except Exception:
            pass

        return info

    async def open_position_details(self, job_info, job_element):
        """新版分栏页面：点击左侧卡片，从右侧详情面板提取详细信息

        新版页面不再跳转 /jobdetail/xxx.htm，而是右侧 .job-detail-panel 展开详情。
        注意：新版详情面板无"注册资本"信息，仅有公司规模（如 20-99人）与行业。
        """
        try:
            # 1. 点击左侧卡片，触发右侧详情渲染
            await job_element.scroll_into_view_if_needed()
            await job_element.click()
            # 等待右侧详情标题出现并稳定
            await self.page.wait_for_selector('.job-detail-summary__title-text', timeout=8000)
            await asyncio.sleep(1.5)

            panel = await self.page.query_selector('.job-detail-panel')
            if not panel:
                logger.warning("右侧详情面板未渲染")
                return job_info

            print(f"  ✅ 右侧详情已展开: {job_info.get('title')}")

            # 2. 职位标题
            try:
                title_elem = await panel.query_selector('.job-detail-summary__title-text')
                if title_elem:
                    text = ((await title_elem.text_content()) or '').strip()
                    if text:
                        job_info['title'] = text
            except Exception:
                pass

            # 3. 薪资
            try:
                salary_elem = await panel.query_selector('.job-detail-summary__salary')
                if salary_elem:
                    text = ((await salary_elem.text_content()) or '').strip()
                    if text:
                        job_info['salary'] = text
            except Exception:
                pass

            # 4. 标签列表（地点/经验/学历，如 北京·海淀区 / 经验不限 / 本科）
            try:
                tags = await panel.query_selector_all('.job-detail-summary__tags li span')
                for tag in tags:
                    text = ((await tag.text_content()) or '').strip()
                    if not text:
                        continue
                    if any(kw in text for kw in self._EDU_KEYWORDS):
                        job_info['education'] = text
                    elif self._EXP_RE.search(text):
                        job_info['experience'] = text
                    elif '·' in text or not job_info.get('location'):
                        # 地点标签（如 北京·海淀区），或首个未知标签视为地点
                        if not job_info.get('location') or '·' in text:
                            job_info['location'] = text.replace('·', ' ').strip()
            except Exception as e:
                logger.warning(f"提取新版详情标签失败: {e}")

            # 5. 公司名称
            try:
                company_elem = await panel.query_selector('.job-detail-summary__company-name')
                if company_elem:
                    text = ((await company_elem.text_content()) or '').strip()
                    if text:
                        job_info['company'] = text
            except Exception:
                pass

            # 6. 公司规模/行业（如 "20-99人 · 商务服务业"）
            try:
                desc_elem = await panel.query_selector('.job-company-info__desc')
                if desc_elem:
                    text = ((await desc_elem.text_content()) or '').strip()
                    if text:
                        m = re.search(r'([\d\-~]+人)', text)
                        if m:
                            job_info['company_scale'] = m.group(1)
                        # 行业作为 category 补充（清洗掉"已审核"等状态标记与多余空白）
                        parts = [p.strip() for p in text.split('·') if p.strip()]
                        if parts and not job_info.get('category'):
                            raw_cat = re.sub(r'\s+', ' ', parts[-1])
                            raw_cat = raw_cat.replace('已审核', '').replace('认证', '').strip()
                            if raw_cat:
                                job_info['category'] = raw_cat
            except Exception:
                pass

            # 7. 岗位描述
            try:
                desc_elem = await panel.query_selector('.job-description__content')
                if desc_elem:
                    text = ((await desc_elem.text_content()) or '').strip()
                    if len(text) > 30:
                        job_info['description'] = text
            except Exception:
                pass

            # 打印提取到的关键信息
            print(f"  职位: {job_info['title']}")
            print(f"  薪资: {job_info.get('salary')}")
            print(f"  公司: {job_info.get('company')}")
            print(f"  地点: {job_info.get('location')}")
            print(f"  经验: {job_info.get('experience')}")
            print(f"  学历: {job_info.get('education')}")
            print(f"  公司规模/人数: {job_info.get('company_scale') or '未获取'}")
            print(f"  注册资本: 未获取（新版页面无此信息）")
            if job_info.get('description'):
                desc_preview = job_info['description'][:100] + "..." if len(job_info['description']) > 100 else \
                    job_info['description']
                print(f"  描述预览: {desc_preview}")

            return job_info

        except Exception as e:
            logger.error(f"新版详情提取失败: {e}")
            return job_info

    async def _scroll_split_list(self):
        """新版分栏页滚动加载：优先滚左侧列表容器；容器随 window 滚动时用真实滚轮渐进滚动

        注意：智联新版左侧列表通常不独立滚动（overflow: visible），整个页面随 window 滚动，
        需用真实滚轮事件（mouse.wheel）渐进滚动才能触发下拉加载；直接设置 scrollTop 无效。
        """
        # 1. 优先尝试滚动左侧列表容器（部分新版布局列表自身滚动）
        for sel in ['.job-list-panel', '.job-split-layout__left', '.job-list-sort-scroll']:
            try:
                el = await self.page.query_selector(sel)
                if el:
                    moved = await el.evaluate('''e => {
                        if (e.scrollHeight > e.clientHeight + 20) {
                            e.scrollTop = e.scrollHeight;
                            return true;
                        }
                        return false;
                    }''')
                    if moved:
                        return
            except Exception:
                continue

        # 2. 列表随 window 滚动：把鼠标移到左侧列表区域，用真实滚轮渐进滚动触发加载
        try:
            await self.page.mouse.move(500, 850)
        except Exception:
            pass
        for _ in range(20):
            try:
                y0 = await self.page.evaluate('window.scrollY')
                await self.page.mouse.wheel(0, 800)
                await asyncio.sleep(0.4)
                y1 = await self.page.evaluate('window.scrollY')
            except Exception:
                break
            # scrollY 不再增加说明已滚到底，停止
            if y1 <= y0:
                break
