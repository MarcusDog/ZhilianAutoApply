"""Local Web control plane for a single persistent browser worker."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from runtime_paths import DATA_DIR, RESOURCE_DIR, FROZEN, build_worker_command
from project_identity import APP_ID, APP_NAME, VERSION
from config_store import get_user_config
from workbench_settings import ApplicationSettings, CITY_CODES, CN, load_workbench_settings, store_workbench_settings, is_start_schedule_due

for stream in (sys.stdout,sys.stderr):
    if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='replace',line_buffering=True)

from dao.database import init_database
init_database()
config=get_user_config()

class TaskController:
    def __init__(self):
        self.process=None;self.output=[];self.lock=threading.RLock();self.started_at=None;self.stopped_at=None
    @property
    def running(self):return self.process is not None and self.process.poll() is None
    def start(self):
        with self.lock:
            if self.running:return False
            env=dict(os.environ,AUTOAPPLY_DATA_DIR=str(DATA_DIR),PYTHONIOENCODING='utf-8',PYTHONUNBUFFERED='1')
            env.pop('PYINSTALLER_RESET_ENVIRONMENT',None)
            self.process=subprocess.Popen(build_worker_command(),cwd=DATA_DIR,env=env,stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',bufsize=1,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.started_at=datetime.now(CN).isoformat();self.stopped_at=None;self.output=[]
            threading.Thread(target=self._read_output,args=(self.process,),daemon=True).start()
            return True
    def _read_output(self,process):
        for line in process.stdout:
            line=line.rstrip()
            if not line:continue
            with self.lock:self.output=(self.output+[line])[-300:]
            try:print(line,flush=True)
            except (UnicodeError,OSError):pass
        process.stdout.close()
    def stop(self):
        if self.running:
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(self.process.pid),'/T','/F'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
            else:self.process.terminate()
            self.process.wait(timeout=15)
        self.stopped_at=datetime.now(CN).isoformat()
    def status(self):
        with self.lock:return dict(running=self.running,pid=self.process.pid if self.running else None,
                                  started_at=self.started_at,stopped_at=self.stopped_at,logs=list(self.output))

manager=TaskController()

def read_json(name,fallback):
    try:return json.loads((DATA_DIR/name).read_text(encoding='utf-8'))
    except (OSError,ValueError):return fallback

def begin(mode=None):
    config.load_from_db();s=load_workbench_settings(config)
    if mode:s=s.model_copy(update={'processing_mode':mode});store_workbench_settings(config,s)
    config.update({'campaign_paused':False,'campaign_authorized':s.processing_mode=='apply','campaign_no_filter':True})
    created=manager.start()
    return {'ok':True,'created':created,'message':'任务已启动' if created else '已恢复当前任务'}

async def scheduler():
    while True:
        try:
            config.load_from_db();s=load_workbench_settings(config)
            if is_start_schedule_due(s,config.get('schedule_last_date','')):
                config.set('schedule_last_date',datetime.now(CN).date().isoformat())
                begin()
        except Exception as error:print('定时启动检查失败：'+str(error),flush=True)
        await asyncio.sleep(10)

@asynccontextmanager
async def lifespan(app):
    from dao.database import init_database
    init_database()
    task=asyncio.create_task(scheduler())
    yield
    task.cancel()
    try:await task
    except asyncio.CancelledError:pass
    manager.stop()

app=FastAPI(title=APP_NAME,version=VERSION,lifespan=lifespan)

@app.middleware('http')
async def local_mutations(request:Request,call_next):
    # Reject cross-site browser writes to a localhost account-control service.
    origin=request.headers.get('origin')
    if request.method not in ('GET','HEAD','OPTIONS') and origin:
        from urllib.parse import urlparse
        if urlparse(origin).netloc!=request.headers.get('host'):
            return JSONResponse({'detail':'不允许跨站修改本机设置'},status_code=403)
    return await call_next(request)

@app.get('/')
@app.get('/preview')
def index():return FileResponse(RESOURCE_DIR/'web'/'index.html')

@app.get('/assets/{name}')
def assets(name:str):
    if name not in ('app.js','style.css'):raise HTTPException(404)
    return FileResponse(RESOURCE_DIR/'web'/name)

@app.get('/api/settings')
def get_settings():
    config.load_from_db()
    return {'settings':load_workbench_settings(config).model_dump(),'cities':CITY_CODES}

@app.put('/api/settings')
def put_settings(settings:ApplicationSettings):
    store_workbench_settings(config,settings)
    return {'ok':True,'settings':settings.model_dump()}

class StartRequest(BaseModel):
    mode:str|None=None

@app.post('/api/control/start')
def start(payload:StartRequest=StartRequest()):
    if payload.mode not in (None,'apply','preview'):raise HTTPException(422,'未知处理方式')
    return begin(payload.mode)

@app.post('/api/control/pause')
def pause():
    config.load_from_db();config.update({'campaign_paused':True,'schedule_last_date':datetime.now(CN).date().isoformat()})
    return {'ok':True,'message':'已暂停；已发起的单次提交会先记录结果，随后等待恢复'}

@app.post('/api/control/stop')
def stop():
    pause();manager.stop()
    return {'ok':True,'message':'任务与程序浏览器已关闭，记录已保留'}

@app.get('/api/status')
@app.get('/api/preview/status')
def status():
    from application_runner import SubmissionJournal
    config.load_from_db();p=manager.status();ledger=SubmissionJournal()
    with ledger.connect() as db:
        recent=[dict(zip(('title','company','url','status','evidence','updated_at'),row)) for row in db.execute(
            'SELECT title,company,url,status,evidence,updated_at FROM submissions ORDER BY updated_at DESC LIMIT 100')]
    return {'process':p,'campaign':read_json('campaign_status.json',{}),'progress':read_json('preview_status.json',{}),
            'paused':config.get('campaign_paused',False),'counts':ledger.counts(),'today':ledger.today_count(),
            'recent':recent,'candidate_count':len(read_json('candidate_jobs.json',[])),
            'data_directory':str(DATA_DIR),'schedule_last_date':config.get('schedule_last_date','')}

@app.get('/api/candidates')
def candidates():return {'candidates':read_json('candidate_jobs.json',[])}

@app.get('/api/export')
def export():
    path=DATA_DIR/'候选岗位清单.csv'
    if not path.exists():raise HTTPException(404,'尚无可导出的岗位')
    return FileResponse(path,filename='候选岗位清单.csv')

@app.get('/api/desktop/info')
def info():return {'application':APP_ID,'name':APP_NAME,'version':VERSION,'frozen':FROZEN,'data_directory':str(DATA_DIR)}

@app.get('/api/diagnostics')
def diagnostics():
    from playwright._impl._driver import compute_driver_executable
    node,cli=compute_driver_executable()
    return {'driver_exists':Path(node).is_file(),'driver_cli_exists':Path(cli).is_file(),
            'web_exists':(RESOURCE_DIR/'web'/'index.html').is_file(),'frozen':FROZEN,
            'data_directory':str(DATA_DIR),'resource_directory':str(RESOURCE_DIR)}
