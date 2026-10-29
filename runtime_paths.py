"""Portable resources and private writable data, with no machine-specific paths."""
import os
from pathlib import Path
import sys

FROZEN = bool(getattr(sys, 'frozen', False))
RESOURCE_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))

def find_user_data_directory():
    explicit = os.environ.get('AUTOAPPLY_DATA_DIR')
    if '--data-dir' in sys.argv:
        index = sys.argv.index('--data-dir') + 1
        if index >= len(sys.argv):
            raise SystemExit('--data-dir 需要目录参数')
        explicit = sys.argv[index]
    if explicit:
        return Path(explicit).expanduser().resolve()
    base = Path(sys.executable).parent if FROZEN else RESOURCE_DIR
    candidate = base / 'data'
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / '.write-test'
        probe.write_text('ok', encoding='utf-8'); probe.unlink()
        return candidate
    except OSError:
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ZhilianAutoApply'

DATA_DIR = find_user_data_directory()
DATA_DIR.mkdir(parents=True, exist_ok=True)

def build_worker_command(script='job_collector.py'):
    if FROZEN:
        return [sys.executable, '--worker', 'preview']
    return [sys.executable, str(RESOURCE_DIR / script)]
