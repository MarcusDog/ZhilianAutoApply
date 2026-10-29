"""Cold-start the actual portable EXE with a new account directory, then clean up."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request

def api(base,path,method='GET',data=None):
    req=urllib.request.Request(base+path,method=method,data=None if method=='GET' else json.dumps(data or {}).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=8) as r:return json.load(r)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('exe');parser.add_argument('--browser-worker',action='store_true')
    parser.add_argument('--output',default='smoke-result.json');args=parser.parse_args()
    exe=Path(args.exe).resolve()
    with tempfile.TemporaryDirectory(prefix='ApplyDesk-new-account-') as directory:
        env=dict(os.environ,AUTOAPPLY_DATA_DIR=directory,PATH=os.path.join(os.environ['WINDIR'],'System32'))
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}';pid=None;result={}
        try:
            launch=subprocess.run([str(exe),'--no-browser','--port',str(port)],env=env,capture_output=True,timeout=100)
            if launch.returncode:
                error=Path(directory)/'desktop_error.log'
                raise RuntimeError(error.read_text(encoding='utf-8') if error.exists() else str(launch.returncode))
            pid=int((Path(directory)/'server.pid').read_text())
            info=api(base,'/api/desktop/info');assert info['frozen']
            result['fresh_account']=api(base,'/api/settings')['settings']['processing_mode']=='preview'
            result['diagnostics']=api(base,'/api/diagnostics')
            time.sleep(2)
            for path in ('/','/assets/app.js','/assets/style.css'):
                with urllib.request.urlopen(base+path) as r:assert r.status==200
            result['resources_survive_launcher_exit']=True
            second=subprocess.run([str(exe),'--no-browser','--port',str(port)],env=env,capture_output=True,timeout=60)
            assert second.returncode==0
            result['same_server_reused']=int((Path(directory)/'server.pid').read_text())==pid
            check=Path(directory)/'browser-check.json'
            tested=subprocess.run([str(exe),'--self-test',str(check)],env=env,capture_output=True,timeout=90)
            assert tested.returncode==0
            result['browser_check']=json.loads(check.read_text(encoding='utf-8'))['browser_title']
            if args.browser_worker:
                settings=api(base,'/api/settings')['settings'];settings['loop_enabled']=False
                api(base,'/api/settings','PUT',settings)
                api(base,'/api/control/start','POST')
                for _ in range(60):
                    status=api(base,'/api/status')
                    if status['progress'].get('stage')=='needs_login':break
                    if status['progress'].get('stage')=='error':raise RuntimeError(status['progress']['message'])
                    time.sleep(.5)
                else:raise RuntimeError('Worker never reached login')
                worker_pid=status['process']['pid']
                api(base,'/api/control/pause','POST');assert api(base,'/api/status')['paused']
                api(base,'/api/control/start','POST')
                assert api(base,'/api/status')['process']['pid']==worker_pid
                api(base,'/api/control/stop','POST');assert not api(base,'/api/status')['process']['running']
                result['worker_login_pause_resume_stop']=True
            Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=False))
        finally:
            if pid:
                subprocess.run(['taskkill','/PID',str(pid),'/T','/F'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
                time.sleep(1)

if __name__=='__main__':main()
