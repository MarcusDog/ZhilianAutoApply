import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from main import app, manager, config
from settings import Settings, save_settings

class ApiTests(unittest.TestCase):
    def setUp(self):
        from dao.database import init_database
        init_database();save_settings(config,Settings())

    def test_fresh_web_settings_and_persistence(self):
        with patch.object(manager,'stop'),TestClient(app) as c:
            self.assertEqual(c.get('/').status_code,200)
            self.assertTrue(c.get('/api/diagnostics').json()['driver_exists'])
            data=c.get('/api/settings').json()['settings']
            self.assertEqual(data['processing_mode'],'preview')
            data.update(batch_size=7,apply_interval_seconds=23,filter_keywords='外包',favorite_policy='only')
            self.assertEqual(c.put('/api/settings',json=data).status_code,200)
            self.assertEqual(c.get('/api/settings').json()['settings']['batch_size'],7)
            data['daily_limit']=-1
            self.assertEqual(c.put('/api/settings',json=data).status_code,422)

    def test_controls_reuse_worker_and_pause(self):
        with patch.object(manager,'start',return_value=False) as start,patch.object(manager,'stop'),TestClient(app) as c:
            self.assertTrue(c.post('/api/control/start',json={'mode':'apply'}).json()['ok'])
            self.assertTrue(config.get('campaign_authorized'))
            self.assertFalse(c.post('/api/control/start',json={}).json()['created'])
            c.post('/api/control/pause')
            self.assertTrue(c.get('/api/status').json()['paused'])
            c.post('/api/control/start',json={})
            self.assertFalse(c.get('/api/status').json()['paused'])
            self.assertEqual(start.call_count,3)

    def test_cross_site_writes_rejected(self):
        with patch.object(manager,'stop'),TestClient(app) as c:
            self.assertEqual(c.post('/api/control/start',json={},headers={'Origin':'https://evil.example'}).status_code,403)

    def test_reader_survives_console_encoding_errors(self):
        from main import ApplyManager
        from types import SimpleNamespace
        m=ApplyManager();p=SimpleNamespace(stdout=__import__('io').StringIO('成功✅\n继续\n'))
        with patch('builtins.print',side_effect=UnicodeError('console encoding')):m._read_output(p)
        self.assertEqual(m.output,['成功✅','继续'])
