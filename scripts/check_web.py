"""Exercise the local UI with disposable data; take account-free documentation screenshots."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

async def check(base,root):
    from playwright.async_api import async_playwright
    errors=[]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(channel='chrome',headless=True)
        page=await browser.new_page(viewport={'width':1440,'height':1050})
        page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto(base)
        await page.wait_for_selector('#cityGrid input',state='attached')
        await page.locator('[name=apply_interval_seconds]').fill('17')
        async with page.expect_response(lambda r:r.url.endswith('/api/settings') and r.request.method=='PUT') as response:
            await page.get_by_role('button',name='保存设置',exact=True).click()
        assert (await (await response.value).json())['settings']['apply_interval_seconds']==17
        await page.screenshot(path=str(root/'docs'/'workbench.png'),full_page=True)
        await page.locator('[data-tab=filters]').click()
        await page.locator('[name=filter_keywords]').fill('外包\n兼职')
        await page.locator('[name=salary_min]').fill('18000')
        async with page.expect_response(lambda r:r.url.endswith('/api/settings') and r.request.method=='PUT') as response:
            await page.get_by_role('button',name='保存设置',exact=True).click()
        assert (await response.value).status==200
        await page.screenshot(path=str(root/'docs'/'filters.png'),full_page=True)
        await page.set_viewport_size({'width':390,'height':844})
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
        await page.reload()
        await page.wait_for_selector('#cityGrid input',state='attached')
        assert await page.locator('[name=apply_interval_seconds]').input_value()=='17'
        assert await page.locator('[name=filter_keywords]').input_value()=='外包\n兼职'
        assert not errors,errors
        await browser.close()
    print('WEB_UI_SAVE_RELOAD_AND_MOBILE_OK')

def main():
    root=Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix='apply-desk-ui-') as directory:
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        env=dict(os.environ,AUTOAPPLY_DATA_DIR=directory,PYTHONIOENCODING='utf-8')
        base=f'http://127.0.0.1:{port}'
        with (Path(directory)/'ui-server.log').open('w',encoding='utf-8') as log:
            p=subprocess.Popen([sys.executable,str(root/'launcher.py'),'--server','--port',str(port)],cwd=root,
                    env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                for _ in range(90):
                    try:
                        with urllib.request.urlopen(base+'/api/status',timeout=1) as r:json.load(r)
                        break
                    except Exception:time.sleep(.5)
                else:raise RuntimeError('Local QA server did not start')
                asyncio.run(check(base,root))
            finally:
                if os.name=='nt':
                    subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    p.terminate()
                p.wait(timeout=15)

if __name__=='__main__':main()
