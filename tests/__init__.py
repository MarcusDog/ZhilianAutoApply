"""Tests always use disposable data, never the account's real profile or ledger."""
import atexit
import os
import tempfile
_data=tempfile.TemporaryDirectory(prefix='apply-desk-test-')
os.environ['AUTOAPPLY_DATA_DIR']=_data.name
def cleanup():
    from dao.database import dispose_engine
    dispose_engine()
    _data.cleanup()
atexit.register(cleanup)
