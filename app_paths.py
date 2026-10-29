"""Portable resources and private writable data, with no machine-specific paths."""
import os
from pathlib import Path
import sys

FROZEN = bool(getattr(sys, 'frozen', False))
RESOURCE_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))

def resolve_data_dir():
    explicit = os.environ.get('AUTOAPPLY_DATA_DIR')
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
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ZhilianApplyDesk'

DATA_DIR = resolve_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

def worker_command(script='preview_jobs.py'):
    if FROZEN:
        return [sys.executable, '--worker', 'preview']
    return [sys.executable, str(RESOURCE_DIR / script)]
