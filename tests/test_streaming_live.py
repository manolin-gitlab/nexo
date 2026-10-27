"""Real local FFmpeg -> trial -> two HLS leases -> ffprobe. No external sources."""
import base64
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from runtime_payload import prepare

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
api=load('live_relay_catalog',ROOT/'backend/nexo_api.py')


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Requires system FFmpeg and ffprobe')
class RealPlayback(unittest.TestCase):
    def test_shared_ingest_playable_segments_revocation_and_clean_shutdown(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);media=root/'fixture.ts'
            result=subprocess.run(['/usr/bin/ffmpeg','-nostdin','-hide_banner','-loglevel','error',
                '-f','lavfi','-i','testsrc=size=160x90:rate=10','-f','lavfi','-i','sine=frequency=440:sample_rate=48000',
                '-t','40','-c:v','libx264','-threads','1','-preset','ultrafast','-pix_fmt','yuv420p',
                '-g','20','-c:a','aac','-b:a','32k','-f','mpegts',str(media)],capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0,'Could not generate local test signal')
            raw=media.read_bytes()
            class Source(BaseHTTPRequestHandler):
                def log_message(self,*_):pass
                def do_GET(self):
                    self.send_response(200);self.send_header('Content-Type','video/mp2t');self.end_headers()
                    try:
                        size=188*7
                        for pos in range(0,len(raw),size):
                            self.wfile.write(raw[pos:pos+size]);self.wfile.flush()
                            time.sleep(size/(len(raw)/40))
                    except (OSError,ValueError):pass
            source=ThreadingHTTPServer(('127.0.0.1',0),Source);source.daemon_threads=True
            threading.Thread(target=source.serve_forever,daemon=True).start()
            proc=None;worker_pid=None
            try:
                runtime=root/'runtime';prepare(ROOT,runtime,'trial','a'*40)
                state=root/'data';state.mkdir(mode=0o700)
                db=api.Catalog(state/'catalog.sqlite3')
                db.write({'clients':[{'id':'c','name':'Test subscriber','packageId':'p','expires':'2099-01-01','connections':2,'enabled':True}],
                    'packages':[{'id':'p','name':'Test package','channels':['ch']}],
                    'channels':[{'id':'ch','name':'Local synthetic signal','url':'http://127.0.0.1:'+str(source.server_port)+'/live.ts','group':'Lab','enabled':True}]},0)
                with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                origin='http://localhost:'+str(port)
                env={**os.environ,'NEXO_TEST_USER':'testadmin','NEXO_TEST_PASSWORD':'a'*43,
                     'NEXO_TEST_PORT':str(port),'NEXO_TEST_DB':str(db.path),
                     'NEXO_TEST_STREAMING':'1','NEXO_STREAM_KEY':'b'*43,'PYTHONDONTWRITEBYTECODE':'1'}
                proc=subprocess.Popen([sys.executable,str(runtime/'nexo_trial.py')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                auth='Basic '+base64.b64encode(('testadmin:'+('a'*43)).encode()).decode()
                def request(path,method='GET',body=None,admin=False,timeout=18):
                    headers={'Host':'localhost:'+str(port)}
                    if admin:headers['Authorization']=auth
                    if body is not None:headers.update(Origin=origin,**{'Content-Type':'application/json'});body=json.dumps(body)
                    c=http.client.HTTPConnection('127.0.0.1',port,timeout=timeout)
                    try:c.request(method,path,body,headers);r=c.getresponse();return r.status,dict(r.headers),r.read()
                    finally:c.close()
                for _ in range(40):
                    try:
                        if request('/api/v1/healthz',admin=True)[0]==200:break
                    except OSError:pass
                    if proc.poll() is not None:self.fail('Trial process failed to start: '+proc.stderr.read(2000).decode())
                    time.sleep(.1)
                else:self.fail('Trial did not start')
                self.assertEqual(request('/')[0],401)
                status,_,body=request('/api/v1/streaming/playlist','POST',{'client_id':'c'},admin=True)
                self.assertEqual(status,200)
                playlist=json.loads(body)['playlist'];self.assertNotIn(str(source.server_port),playlist)
                master=playlist.splitlines()[-1].removeprefix(origin)
                status,headers,_=request(master)
                self.assertEqual(status,302,'Source did not become ready within startup timeout')
                first=master.rsplit('/',1)[0]+'/'+headers['Location']
                status,_,manifest=request(first)
                self.assertEqual(status,200)
                segment=next(line for line in manifest.decode().splitlines() if line and not line.startswith('#'))
                segment_path=first.rsplit('/',1)[0]+'/'+segment
                status,_,video=request(segment_path);self.assertEqual(status,200);self.assertGreater(len(video),188)
                sample=root/'received.ts';sample.write_bytes(video)
                probe=subprocess.run(['/usr/bin/ffprobe','-v','error','-show_entries','stream=codec_name','-of','json',str(sample)],capture_output=True,text=True,timeout=10)
                self.assertEqual(probe.returncode,0);self.assertIn('h264',{s['codec_name'] for s in json.loads(probe.stdout)['streams']})
                status,headers,_=request(master);self.assertEqual(status,302)
                state_before=json.loads(request('/api/v1/streaming',admin=True)[2])
                self.assertEqual(len(state_before['workers']),1);self.assertEqual(len(state_before['sessions']),2)
                worker_pid=state_before['workers'][0]['pid'];self.assertIsInstance(worker_pid,int)
                self.assertEqual(request(master)[0],429)
                metrics=json.loads(request('/v1/snapshot',admin=True)[2]);self.assertEqual(len(metrics['sessions']),2)
                self.assertEqual(metrics['streaming']['live'],1)
                changed=db.read();changed['catalog']['clients'][0]['enabled']=False;db.write(changed['catalog'],changed['revision'])
                time.sleep(.6);self.assertEqual(request(segment_path)[0],403)
                # Losing a session never exposes the source URL as a fallback.
                denied=request(segment_path)[2];self.assertNotIn(b'live.ts',denied)
            finally:
                if proc is not None:
                    proc.terminate()
                    try:proc.wait(timeout=12)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)
                    if proc.stderr:proc.stderr.close()
                source.shutdown();source.server_close()
            if worker_pid:
                with self.assertRaises(ProcessLookupError):os.kill(worker_pid,0)


if __name__=='__main__':unittest.main()
