import base64
import concurrent.futures
import http.client
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
def load(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

m=load('streaming_tests',ROOT/'backend/streaming.py')
api=load('streaming_api_tests',ROOT/'backend/nexo_api.py')
trial=load('streaming_trial_tests',ROOT/'trial/nexo_trial.py')
limits=load('http_limits_tests',ROOT/'backend/http_limits.py')
setup=load('streaming_setup_tests',ROOT/'backend/streaming_setup.py')

def catalog():
    return {'clients':[{'id':'c1','name':'Cliente','packageId':'p1','expires':'2099-01-01','connections':2,'enabled':True}],
            'channels':[{'id':'ch1','name':'Canal','url':'http://127.0.0.1:19999/live.ts','group':'Prueba','enabled':True}],
            'packages':[{'id':'p1','name':'Paquete','channels':['ch1']}]}


class RelayFixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.db=api.Catalog(self.root/'catalog.sqlite3');self.db.write(catalog(),0)
        self.now=[100.0];self.wall=[1793100000.0]
        self.relay=m.Relay(self.db,self.root,'a'*43,binary='/usr/bin/true',autostart=False,
                           clock=lambda:self.now[0],wall=lambda:self.wall[0],min_free_bytes=0)
        self.addCleanup(self.relay.close)
        self.key=m.channel_key('ch1');self.token=self.relay.issue('c1')

    def deny(self,code,function,*args):
        with self.assertRaises(m.Denied) as error:function(*args)
        self.assertEqual(error.exception.code,code)

    def live(self):
        self.relay.open_session(self.token,self.key,'127.0.0.1')
        worker=self.relay.workers[self.key]
        folder=self.relay.root/'ch-test';folder.mkdir()
        (folder/'index.m3u8').write_text('#EXTM3U\nseg-000000001.ts\n')
        (folder/'seg-000000001.ts').write_bytes(b'video segment')
        worker.update(folder=folder,state='live')
        return next(iter(self.relay.sessions)),worker,folder

class RelayTests(RelayFixture):
    def test_signed_playlist_does_not_disclose_source_and_has_no_unauthorized_channels(self):
        value=self.relay.playlist('c1','http://localhost:8088')
        self.assertIn('/tv/',value);self.assertNotIn('19999',value);self.assertNotIn('live.ts',value)
        self.assertEqual(value.count('#EXTINF'),1)
        self.assertEqual(self.relay.verify(self.token),'c1')

    def test_tampered_expired_and_oversize_tokens_are_denied(self):
        for token in (self.token+'x','x'*1025,self.token.replace('.', '..')):
            self.deny('invalid_playback_token',self.relay.verify,token)
        self.wall[0]+=8*86400
        self.deny('invalid_playback_token',self.relay.verify,self.token)

    def test_shared_channel_and_atomic_concurrent_session_limit(self):
        def attempt(_):
            try:return self.relay.open_session(self.token,self.key,'127.0.0.1')
            except m.Denied:return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:rows=list(pool.map(attempt,range(20)))
        self.assertEqual(sum(bool(row) for row in rows),2)
        self.assertEqual(len(self.relay.workers),1)

    def test_suspension_revokes_existing_segment_access_within_half_second(self):
        sid,_,_=self.live()
        fd,size=self.relay.resolve(self.token,self.key,sid,'seg-000000001.ts');os.close(fd)
        self.assertEqual(size,13)
        data=catalog();data['clients'][0]['enabled']=False;self.db.write(data,1);self.now[0]+=.51
        self.deny('client_inactive',self.relay.resolve,self.token,self.key,sid,'seg-000000001.ts')

    def test_package_channel_and_client_expiry_are_authoritative(self):
        data=catalog();data['packages'][0]['channels']=[];self.db.write(data,1);self.now[0]+=.51
        self.deny('channel_denied',self.relay.open_session,self.token,self.key,'127.0.0.1')
        data=catalog();data['clients'][0]['expires']='2020-01-01';self.db.write(data,2);self.now[0]+=.51
        self.deny('client_inactive',self.relay.issue,'c1')

    def test_expired_leases_and_admin_stop_revoke_media(self):
        sid,_,_=self.live()
        self.assertTrue(self.relay.close_session(sid))
        self.deny('session_expired',self.relay.resolve,self.token,self.key,sid,'index.m3u8')
        sid=self.relay.open_session(self.token,self.key,'127.0.0.1');self.now[0]+=31
        self.deny('session_expired',self.relay.resolve,self.token,self.key,sid,'index.m3u8')

    def test_no_traversal_symlinks_temporary_files_or_cross_session_tokens(self):
        sid,_,folder=self.live()
        for name in ('../../catalog.sqlite3','seg-000000001.ts.tmp','.env','file.ts'):
            self.deny('not_found',self.relay.resolve,self.token,self.key,sid,name)
        (folder/'seg-000000002.ts').symlink_to(self.root/'catalog.sqlite3')
        with self.assertRaises(OSError):self.relay.resolve(self.token,self.key,sid,'seg-000000002.ts')
        other=self.relay.issue('c1')
        self.deny('session_expired',self.relay.resolve,other,self.key,sid,'index.m3u8')

    def test_catalog_failure_does_not_allow_cached_authorization(self):
        sid,_,_=self.live();self.now[0]+=.51
        with patch.object(self.db,'read',side_effect=sqlite3.OperationalError()):
            with self.assertRaises(sqlite3.Error):self.relay.resolve(self.token,self.key,sid,'index.m3u8')

    def test_idle_channel_removed_and_only_owned_cache_is_deleted(self):
        sid,worker,folder=self.live();(self.root/'preserve').write_text('other data')
        self.relay.close_session(sid);self.now[0]+=31
        self.relay.tick()
        self.assertFalse(folder.exists());self.assertEqual(self.relay.workers,{})
        self.assertEqual((self.root/'preserve').read_text(),'other data')

    def test_storage_threshold_stops_ingest_and_retains_no_segments(self):
        sid,worker,folder=self.live();self.relay.cache_bytes=1
        self.relay.tick()
        self.assertTrue(self.relay.storage_blocked)
        self.assertIsNone(worker['folder']);self.assertEqual(worker['state'],'storage_limit')
        self.deny('source_starting_or_unavailable',self.relay.resolve,self.token,self.key,sid,'index.m3u8')

    def test_restart_backoff_prevents_respawn_loop(self):
        self.relay.open_session(self.token,self.key,'127.0.0.1')
        with patch.object(self.relay,'popen',side_effect=OSError()) as spawn:
            self.relay.tick();self.relay.tick()
            self.assertEqual(spawn.call_count,1)
            self.now[0]+=2.1;self.relay.tick();self.assertEqual(spawn.call_count,2)
            self.now[0]+=3;self.relay.tick();self.assertEqual(spawn.call_count,2)

    def test_segment_authorization_does_not_read_sqlite_per_request(self):
        sid,_,_=self.live()
        with patch.object(self.db,'read',wraps=self.db.read) as read:
            for _ in range(1000):
                fd,_=self.relay.resolve(self.token,self.key,sid,'seg-000000001.ts');os.close(fd)
            self.assertEqual(read.call_count,0)
            self.now[0]+=.51
            fd,_=self.relay.resolve(self.token,self.key,sid,'seg-000000001.ts');os.close(fd)
            self.assertEqual(read.call_count,1)

    def test_ffmpeg_uses_streamcopy_and_bounded_shared_hls(self):
        cmd=m.ffmpeg_command('/usr/bin/ffmpeg','https://example.test/live',self.root)
        self.assertEqual(cmd[cmd.index('-c')+1],'copy')
        self.assertNotIn('libx264',cmd);self.assertNotIn('file',cmd[cmd.index('-protocol_whitelist')+1].split(','))
        self.assertIn('delete_segments+temp_file+omit_endlist',cmd)
        self.assertEqual(cmd[cmd.index('-hls_list_size')+1],'6')
        with self.assertRaises(ValueError):m.ffmpeg_command('/usr/bin/ffmpeg','file:///etc/passwd',self.root)

    def test_capacity_rejects_additional_channels_and_global_sessions(self):
        data=catalog();data['channels'].append({**data['channels'][0],'id':'ch2'})
        data['packages'][0]['channels'].append('ch2');self.db.write(data,1);self.now[0]+=.51
        self.relay.max_channels=1
        self.relay.open_session(self.token,self.key,'127.0.0.1')
        self.deny('channel_capacity',self.relay.open_session,self.token,m.channel_key('ch2'),'127.0.0.1')
        self.relay.max_sessions=1
        self.deny('session_limit',self.relay.open_session,self.token,self.key,'127.0.0.1')

    def test_stalled_source_is_terminated_reaped_and_retried_after_backoff(self):
        _,worker,folder=self.live()
        proc=subprocess.Popen(['/usr/bin/sleep','60'],start_new_session=True)
        worker.update(process=proc,started=self.now[0],url=catalog()['channels'][0]['url'])
        os.utime(folder/'index.m3u8',(self.wall[0]-31,self.wall[0]-31))
        self.relay.tick()
        self.assertIsNotNone(proc.poll());self.assertFalse(folder.exists())
        self.assertEqual(worker['state'],'source_stalled')
        with patch.object(self.relay,'popen',side_effect=OSError()) as spawn:
            self.relay.tick();self.assertEqual(spawn.call_count,0)
            self.now[0]+=2.1;self.relay.tick();self.assertEqual(spawn.call_count,1)

    def test_no_catalog_polling_when_relay_is_idle(self):
        with patch.object(self.db,'read',wraps=self.db.read) as read:
            for _ in range(20):self.now[0]+=.6;self.relay.tick()
            self.assertEqual(read.call_count,0)


class HttpTests(RelayFixture):
    # Reuse fixture only, not the parent's test methods.
    def setUp(self):
        super().setUp()
        (self.root/'panel').mkdir();(self.root/'panel/index.html').write_text('panel')
        self.server=limits.BoundedHTTPServer(('127.0.0.1',0),trial.handler_class(self.root,api,self.db,
            type('Collector',(),{'get':lambda s:{'timestamp':'2026-10-27T00:00:00Z'}})(),
            'admin','a'*43,8088,streaming=self.relay))
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)

    def request(self,path,method='GET',body=None,admin=False,origin=None,host='localhost:8088'):
        conn=http.client.HTTPConnection(*self.server.server_address,timeout=3)
        headers={'Host':host}
        if admin:headers['Authorization']='Basic '+base64.b64encode(('admin:'+('a'*43)).encode()).decode()
        if origin:headers['Origin']=origin
        if body is not None:headers['Content-Type']='application/json';body=json.dumps(body)
        try:
            conn.request(method,path,body,headers);r=conn.getresponse();return r.status,dict(r.headers),r.read()
        finally:conn.close()

    def test_token_media_does_not_open_admin_routes_or_require_admin_password(self):
        sid,_,_=self.live()
        path='/tv/'+self.token+'/'+self.key+'/'+sid+'/index.m3u8'
        self.assertEqual(self.request(path)[0],200)
        self.assertEqual(self.request('/api/v1/catalog')[0],401)
        self.assertEqual(self.request(path,host='attacker.example')[0],403)
        self.assertEqual(self.request(path.replace(self.token,self.token+'x'))[0],403)

    def test_export_and_stop_require_admin_origin_and_maintenance_clear(self):
        path='/api/v1/streaming/playlist'
        self.assertEqual(self.request(path,'POST',{'client_id':'c1'})[0],401)
        self.assertEqual(self.request(path,'POST',{'client_id':'c1'},admin=True)[0],403)
        self.assertEqual(self.request(path,'POST',{'client_id':'c1'},admin=True,origin='http://localhost:8088')[0],200)
        (self.root/'.maintenance').write_text('updating')
        self.assertEqual(self.request(path,'POST',{'client_id':'c1'},admin=True,origin='http://localhost:8088')[0],503)

    def test_admin_status_never_exposes_tokens_or_source_urls(self):
        self.live();status,_,raw=self.request('/api/v1/streaming',admin=True)
        self.assertEqual(status,200);self.assertNotIn(self.token.encode(),raw);self.assertNotIn(b'19999',raw)


class LimitsAndSetup(unittest.TestCase):
    def test_overload_rejected_then_capacity_recovers(self):
        class Handler(api.BaseHTTPRequestHandler):
            def do_GET(self):self.send_response(200);self.end_headers()
            def log_message(self,*_):pass
        server=limits.BoundedHTTPServer(('127.0.0.1',0),Handler)
        self.addCleanup(server.server_close)
        for _ in range(server.max_requests):server.slots.acquire()
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.addCleanup(server.shutdown)
        c=http.client.HTTPConnection(*server.server_address,timeout=2)
        c.request('GET','/');r=c.getresponse();self.assertEqual(r.status,503);r.read();c.close()
        server.slots.release()
        c=http.client.HTTPConnection(*server.server_address,timeout=2)
        c.request('GET','/');r=c.getresponse();self.assertEqual(r.status,200);r.read();c.close()

    def test_activation_preserves_credentials_and_key_and_refuses_unsafe_file(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'env';p.write_text('NEXO_TEST_USER=admin\nNEXO_TEST_PASSWORD=private\nNEXO_TEST_DB=/data/catalog.sqlite3\n');p.chmod(0o600)
            setup.configure(p,True)
            first=p.read_text();self.assertIn('NEXO_TEST_PASSWORD=private',first)
            key=next(r for r in first.splitlines() if r.startswith('NEXO_STREAM_KEY='))
            setup.configure(p,False);self.assertIn(key,p.read_text());self.assertIn('NEXO_TEST_STREAMING=0',p.read_text())
            p.chmod(0o644)
            with self.assertRaises(ValueError):setup.configure(p,True)


if __name__=='__main__':unittest.main()
