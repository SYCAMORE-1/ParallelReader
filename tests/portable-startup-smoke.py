"""Launch the portable BAT/PowerShell path without host Python or saved environment."""
import argparse
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.request


def main():
    parser=argparse.ArgumentParser();parser.add_argument('release',type=Path);parser.add_argument('--expect-books',type=int,default=0);args=parser.parse_args()
    release=args.release.resolve();powershell=Path(os.environ['SystemRoot'])/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe'
    environment=os.environ.copy();environment['PATH']=str(Path(os.environ['SystemRoot'])/'System32')
    environment['PYTHONHOME']=r'Z:\nonexistent-host-python';environment['PYTHONPATH']=r'Z:\nonexistent-host-packages'
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix='reader-startup-') as temp:
        logfile=Path(temp)/'startup.log'
        with logfile.open('w',encoding='utf-8') as log:
            process=subprocess.Popen([str(powershell),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(release/'start.ps1'),'-Port',str(port),'-NoBrowser'],
                                     cwd=temp,env=environment,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                base=f'http://127.0.0.1:{port}'
                for _ in range(45):
                    try:
                        with opener.open(base+'/api/books',timeout=2) as response:books=json.load(response)
                        break
                    except Exception:
                        if process.poll() is not None:raise RuntimeError(logfile.read_text(encoding='utf-8',errors='replace'))
                        time.sleep(.5)
                else:raise RuntimeError('Portable startup timed out: '+logfile.read_text(encoding='utf-8',errors='replace'))
                if len(books)!=args.expect_books or any(not b.get('bookmark') for b in books):raise RuntimeError('Unexpected bookshelf/bookmark state')
                image_checked=False
                for summary in books:
                    with opener.open(base+'/api/books/'+summary['id'],timeout=15) as response:book=json.load(response)
                    if not book.get('illustrations'):continue
                    asset=book['illustrations'][0]['asset']
                    with opener.open(base+'/api/books/'+book['id']+'/images/'+asset,timeout=15) as response:
                        if not response.headers.get_content_type().startswith('image/'):raise RuntimeError('Missing cover/illustration')
                    image_checked=True;break
                with opener.open(base+'/api/library-progress',timeout=20) as response:progress=json.load(response)
                db=sqlite3.connect((release/'.data'/'reader.sqlite3').as_uri()+'?mode=ro',uri=True)
                try:
                    if db.execute("SELECT count(*) FROM book_translation_jobs WHERE state='running'").fetchone()[0]:raise RuntimeError('Migrated text jobs unexpectedly started')
                    states=[json.loads(row[0])['state'] for row in db.execute("SELECT value FROM language_cache WHERE key LIKE 'image-book-job:%'")]
                    if 'running' in states:raise RuntimeError('Migrated image jobs unexpectedly started')
                finally:db.close()
                print(json.dumps({'ok':True,'startupScript':True,'noHostPython':True,'unrelatedWorkingDirectory':True,'hostPythonEnvironmentIgnored':True,
                                  'books':len(books),'bookmarks':len(books),'originalImageEndpoint':image_checked,'backgroundJobsRemainPaused':True,'port':port}),flush=True)
            finally:
                quoted=str(release).replace("'","''")
                stop=f"$owner = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess; if ($owner) {{ $p=Get-CimInstance Win32_Process -Filter ('ProcessId='+$owner); if ($p.CommandLine.Contains('{quoted}') -and $p.CommandLine.Contains('--port {port}')) {{ Stop-Process -Id $owner }} else {{ throw 'Refusing to stop an unrelated process' }} }}"
                subprocess.run([str(powershell),'-NoProfile','-Command',stop],check=True,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
                try:process.wait(timeout=6)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)


if __name__=='__main__':main()
