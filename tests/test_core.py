import asyncio
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from pydantic import ValidationError
from settings import Settings, schedule_due, CN
from job_filters import check_job, salary_range, filter_signature
from auto_campaign import Ledger, pending_jobs

class CoreTests(unittest.TestCase):
    def test_settings_reject_invalid_options(self):
        for data in ({'batch_size':0},{'apply_interval_seconds':-1},{'schedule_time':'25:00'},
                     {'salary_min':20000,'salary_max':10000},{'search_url':'https://evil.example/sou/'},
                     {'cities':['不存在']},{'search_terms':[]},{'unknown_field':True}):
            with self.subTest(data=data),self.assertRaises(ValidationError):Settings(**data)

    def test_salary_units_ranges_and_annual(self):
        for text,expected in [('18K-25K',(18000,25000)),('1.5-2.5万',(15000,25000)),
                              ('15000-25000元/月',(15000,25000)),('24-36万/年',(20000,30000)),
                              ('15-20k·13薪',(15000,20000))]:
            self.assertEqual(salary_range(text),expected,text)
        self.assertIsNone(salary_range('面议'))
        self.assertIsNone(salary_range('200元/天'))

    def test_blacklist_and_whitelist_modes(self):
        job={'title':'Python开发','company':'示例','description':'RAG开发，包含商业保险','salary':'20K-25K'}
        self.assertFalse(check_job(job,Settings(filter_keywords='保险'),True)[0])
        self.assertTrue(check_job(job,Settings(keyword_mode='off',filter_keywords='保险'),True)[0])
        self.assertTrue(check_job(job,Settings(keyword_mode='whitelist',filter_keywords='rag'),True)[0])
        self.assertFalse(check_job(job,Settings(keyword_mode='whitelist',filter_keywords='Java'),True)[0])

    def test_title_details_salary_and_favorite(self):
        job={'title':'AI工程师','description':'Python RAG','salary':'18-25k','is_favorite':True}
        self.assertTrue(check_job(job,Settings(title_keywords=['AI'],detail_keywords=['RAG'],salary_min=20000,favorite_policy='only'),True)[0])
        for settings in (Settings(title_keywords=['销售']),Settings(detail_keywords=['C++']),
                         Settings(salary_min=30000),Settings(favorite_policy='exclude')):
            self.assertFalse(check_job(job,settings,True)[0])
        self.assertFalse(check_job(dict(job,is_favorite=None),Settings(favorite_policy='only'),True)[0])
        self.assertFalse(check_job(dict(job,description=''),Settings(),True)[0])
        self.assertFalse(check_job(dict(job,salary='面议'),Settings(allow_unknown_salary=False),True)[0])

    def test_schedule_is_once_daily_and_respects_time(self):
        s=Settings(schedule_enabled=True,schedule_time='09:00')
        self.assertFalse(schedule_due(s,'',datetime(2026,10,29,8,59,tzinfo=CN)))
        self.assertTrue(schedule_due(s,'',datetime(2026,10,29,9,0,tzinfo=CN)))
        self.assertFalse(schedule_due(s,'2026-10-29',datetime(2026,10,29,12,tzinfo=CN)))

    def test_durable_caps_and_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=Ledger(Path(d)/'ledger.db');batch=ledger.new_batch()
            jobs=[dict(title='岗位',company='公司',url=f'https://www.zhaopin.com/jobdetail/T{i}.htm') for i in range(5)]
            self.assertTrue(ledger.reserve(jobs[0],batch,limit=2))
            ledger.result(jobs[0],'unknown','不自动重试')
            self.assertTrue(ledger.reserve(jobs[1],batch,limit=2))
            self.assertFalse(ledger.reserve(jobs[2],batch,limit=2))
            second=ledger.new_batch()
            self.assertFalse(ledger.reserve(jobs[2],second,daily_limit=2))
            self.assertFalse(ledger.reserve(jobs[2],second,total_limit=2))
            self.assertFalse(Ledger(Path(d)/'ledger.db').reserve(jobs[0],second))
            self.assertEqual(len(pending_jobs(jobs,ledger,True,settings=Settings())),3)

    def test_final_filter_rejections_retry_only_after_filter_change(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=Ledger(Path(d)/'l.db');s=Settings(detail_keywords=['RAG'])
            job=dict(title='AI',company='示例',url='https://www.zhaopin.com/jobdetail/F.htm',
                     review_status='excluded',filter_signature=filter_signature(s))
            self.assertEqual(pending_jobs([job],ledger,True,settings=s),[])
            self.assertEqual(pending_jobs([job],ledger,True,settings=Settings()),[job])

class PauseTests(unittest.IsolatedAsyncioTestCase):
    async def test_pause_waits_and_resume_releases_same_worker(self):
        from preview_jobs import Preview
        from unittest.mock import Mock
        worker=Preview.__new__(Preview);worker.config=Mock();worker.report=Mock()
        paused=True
        worker.config.get.side_effect=lambda key,default=None:paused if key=='campaign_paused' else default
        task=asyncio.create_task(worker.wait_control())
        await asyncio.sleep(.05)
        self.assertFalse(task.done())
        paused=False
        await asyncio.wait_for(task,2)
        self.assertTrue(task.done())
