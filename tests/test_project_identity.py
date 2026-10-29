import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from project_identity import APP_ID, APP_NAME, VERSION
from runtime_paths import find_user_data_directory


class ProjectIdentityTests(unittest.TestCase):
    def test_identity_is_shared_by_api(self):
        from web_server import app, info
        self.assertEqual(app.title,APP_NAME)
        self.assertEqual(info()['application'],APP_ID)
        self.assertEqual(info()['version'],VERSION)

    def test_explicit_directory_preserves_existing_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sys,'argv',['ZhilianAutoApply.exe','--data-dir',directory]):
                self.assertEqual(find_user_data_directory(),Path(directory).resolve())
