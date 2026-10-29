import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, AsyncMock
from settings import Settings
from auto_campaign import Campaign, Ledger

class SubmissionGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_blacklist_hit_never_clicks_or_reserves(self):
        with tempfile.TemporaryDirectory() as directory:
            settings=Settings(processing_mode='apply',filter_keywords='外包')
            config=Mock();config.get.side_effect=lambda key,default=None:settings.model_dump() if key=='desktop_settings' else default
            def element(text,count=1):
                obj=Mock();obj.first=obj;obj.inner_text=AsyncMock(return_value=text)
                obj.count=AsyncMock(return_value=count);obj.wait_for=AsyncMock();obj.is_visible=AsyncMock(return_value=False)
                return obj
            actions=element('立即投递');page=Mock();page.goto=AsyncMock();page.close=AsyncMock();page.wait_for_selector=AsyncMock()
            page.get_by_role.return_value=element('')
            page.url='https://www.zhaopin.com/jobdetail/GATE.htm'
            page.locator.side_effect=lambda selector:{
                'body':element('当前职位详情'),
                '.summary-planes__action, .job-detail-summary':actions,
                '.summary-planes__title, .job-detail-summary__title-text':element('Python开发'),
                '.summary-planes__company-name, .job-detail-summary__company-name':element('示例公司'),
                '.summary-planes__salary, .job-detail-summary__salary':element('18-25K'),
                '.describtion-card, .job-description__content':element('外包项目开发'),
            }[selector]
            worker=SimpleNamespace(config=config,wait_control=AsyncMock(),db=Mock(),save_rows=Mock(),state_file='state.json',
                context=SimpleNamespace(new_page=AsyncMock(return_value=page),storage_state=AsyncMock()))
            worker.db.is_job_applied.return_value=False
            campaign=Campaign.__new__(Campaign);campaign.worker=worker;campaign.ledger=Ledger(Path(directory)/'l.db');campaign.report=Mock()
            job=dict(title='Python开发',company='示例公司',description='普通开发',url=page.url)
            await campaign.submit(job,campaign.ledger.new_batch())
            actions.get_by_role.assert_not_called()
            self.assertEqual(campaign.ledger.counts(),{})
            self.assertIn('外包',job['application_result'])

    async def test_preview_mode_cannot_create_submission_page(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Mock();config.get.side_effect=lambda key,default=None:Settings().model_dump() if key=='desktop_settings' else default
            worker=SimpleNamespace(config=config,wait_control=AsyncMock(),db=Mock(),context=Mock())
            worker.db.is_job_applied.return_value=False
            campaign=Campaign.__new__(Campaign);campaign.worker=worker;campaign.ledger=Ledger(Path(directory)/'l.db')
            await campaign.submit(dict(url='https://www.zhaopin.com/jobdetail/PREVIEW.htm'),1)
            worker.context.new_page.assert_not_called()
