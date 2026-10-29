"""Desktop executable: launch/reuse local service, resume the worker, open dashboard."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import webbrowser

from runtime_paths import DATA_DIR, FROZEN, RESOURCE_DIR
from project_identity import APP_ID, APP_NAME


def restore_stream(name, handle_id, fallback):
    stream = getattr(sys, name)
    if stream is None and os.name == 'nt':
        import ctypes
        import msvcrt
        kernel = ctypes.windll.kernel32
        kernel.GetStdHandle.restype = ctypes.c_void_p
        handle = kernel.GetStdHandle(handle_id)
        if handle and handle != ctypes.c_void_p(-1).value:
            try:
                descriptor = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
                stream = os.fdopen(descriptor, 'w', encoding='utf-8', errors='replace', buffering=1)
            except OSError:
                pass
    if stream is None:
        stream = (DATA_DIR / fallback).open('a', encoding='utf-8', buffering=1)
    setattr(sys, name, stream)
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)


def api(base, path, method='GET'):
    request = urllib.request.Request(base + path, data=b'' if method == 'POST' else None, method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def service_status(base):
    try:
        status = api(base, '/api/preview/status')
        info=api(base,'/api/desktop/info')
        if (info.get('application')!=APP_ID or not isinstance(status.get('process'),dict)
                or Path(info.get('data_directory','')).resolve()!=DATA_DIR.resolve()):
            raise RuntimeError('管理端口被其他程序占用，请使用 --port 指定其他端口。')
        return status
    except (json.JSONDecodeError,TimeoutError) as error:
        raise RuntimeError('该端口未返回工作台数据。') from error
    except urllib.error.URLError as error:
        if isinstance(error, urllib.error.HTTPError):
            raise RuntimeError('管理端口返回了其他服务的页面。') from error
        return None


def launch(port=8765, no_browser=False):
    base = f'http://127.0.0.1:{port}'
    # Serialize double-clicks; a second launch reuses the service and worker.
    with (DATA_DIR / '.desktop-launch.lock').open('a+b') as lock:
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        if os.name=='nt':
            import msvcrt
            for attempt in range(120):
                try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1);break
                except OSError:
                    if attempt==119:raise RuntimeError('另一个启动窗口仍在准备，请稍后重试。')
                    time.sleep(.5)
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX)
        # Another local app may already use the default port. Never attach to it.
        for candidate in range(port, min(port+20,65536)):
            base=f'http://127.0.0.1:{candidate}'
            try:
                status=service_status(base)
                port=candidate
                break
            except RuntimeError:
                continue
        else:
            raise RuntimeError('连续20个本地端口已被其他服务占用，请用 --port 更换端口。')
        if status is None:
            command = [sys.executable] if FROZEN else [sys.executable, str(RESOURCE_DIR / 'launcher.py')]
            command += ['--server', '--port', str(port)]
            env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1',
                       AUTOAPPLY_DATA_DIR=str(DATA_DIR))
            env.pop('PYINSTALLER_RESET_ENVIRONMENT',None)
            with (DATA_DIR / 'server.stdout.log').open('ab') as out, (DATA_DIR / 'server.stderr.log').open('ab') as err:
                server = subprocess.Popen(command, cwd=DATA_DIR, env=env, stdout=out, stderr=err,
                                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            (DATA_DIR / 'server.pid').write_text(str(server.pid), encoding='ascii')
            for _ in range(120):
                status = service_status(base)
                if status is not None:
                    break
                if server.poll() is not None:
                    raise RuntimeError('管理服务未能启动，请查看数据目录中的server.stderr.log。')
                time.sleep(0.5)
            else:
                raise RuntimeError('管理服务启动超时，请稍后重试。')
        settings=api(base,'/api/settings')['settings']
        if settings.get('auto_start',False):
            api(base,'/api/control/start','POST')
        if not no_browser:
            webbrowser.open(base + '/preview')
        result = {'started': True, 'dashboard': base + '/preview', 'data_directory': str(DATA_DIR)}
        (DATA_DIR / 'desktop_last_launch.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False), flush=True)


async def self_test(output):
    from playwright.async_api import async_playwright
    from dao.database import init_database
    from job_collector import PositionCollector
    from application_runner import ResumeApplicationService
    init_database()
    worker = PositionCollector()
    campaign = ResumeApplicationService(worker)
    async with async_playwright() as playwright:
        browser=None
        for channel in ('chrome','msedge',None):
            try:
                browser=await playwright.chromium.launch(headless=True,**({'channel':channel} if channel else {}));break
            except Exception:pass
        if browser is None:raise RuntimeError('请安装Chrome/Edge或Playwright Chromium')
        page = await browser.new_page()
        await page.set_content('<title>ZhilianAutoApply bundled browser test</title>')
        title = await page.title()
        await browser.close()
    result = {'frozen': FROZEN, 'data_directory': str(DATA_DIR), 'browser_title': title,
              'resources': (RESOURCE_DIR/'web'/'index.html').exists(),
              'ledger': campaign.ledger.counts()}
    Path(output).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')


def run_launcher():
    parser = argparse.ArgumentParser()
    parser.add_argument('--server', action='store_true')
    parser.add_argument('--worker', choices=('preview',))
    parser.add_argument('--self-test')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--data-dir', help='使用现有数据目录，保留账号和去重记录')
    args = parser.parse_args()
    os.chdir(DATA_DIR)
    restore_stream('stdout', -11, 'desktop.stdout.log')
    restore_stream('stderr', -12, 'desktop.stderr.log')
    try:
        if args.server:
            import uvicorn
            import web_server as application
            uvicorn.run(application.app, host='127.0.0.1', port=args.port, loop='asyncio', http='h11', ws='none')
        elif args.worker:
            from job_collector import PositionCollector
            asyncio.run(PositionCollector().run_collection_task())
        elif args.self_test:
            asyncio.run(self_test(args.self_test))
        else:
            launch(args.port, args.no_browser)
    except Exception as error:
        traceback.print_exc()
        (DATA_DIR / 'desktop_error.log').write_text(traceback.format_exc(), encoding='utf-8')
        if not (args.server or args.worker or args.self_test):
            if os.name=='nt' and not args.no_browser:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None,str(error)+'\n\n日志目录：'+str(DATA_DIR),'智联自动投递简历：启动失败',0x10)
        raise SystemExit(1)


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    run_launcher()
