import base64
from contextlib import closing
import http.client
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
m=load('trial_install',ROOT/'trial/install.py')
t=load('trial_server',ROOT/'trial/nexo_trial.py')
api=load('trial_api_test',ROOT/'backend/nexo_api.py')


class Runtime(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'panel').mkdir()
        (self.root/'panel/index.html').write_text('trial page')
        self.db=api.Catalog(self.root/'catalog.sqlite3')
        collector=SimpleNamespace(get=lambda:{'schema_version':1,'test_metric':True})
        updates=SimpleNamespace(status=lambda force=False:{'state':'current','available':False,'forced':force})
        self.server=ThreadingHTTPServer(('127.0.0.1',0),t.handler_class(self.root,api,self.db,collector,'administrador','a'*48,8088,updates))
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)

    def request(self,path='/',method='GET',body=None,auth=True,host='localhost:8088',origin=None):
        headers={'Host':host}
        if auth:headers['Authorization']='Basic '+base64.b64encode(('administrador:'+('a'*48)).encode()).decode()
        if origin:headers['Origin']=origin
        if method=='PUT':headers.update({'Content-Type':'application/json','If-Match':'"0"'})
        if method=='POST':headers['Content-Type']='application/json'
        connection=http.client.HTTPConnection(*self.server.server_address,timeout=3)
        try:
            connection.request(method,path,body,headers);r=connection.getresponse()
            return r.status,dict(r.headers),r.read()
        finally:connection.close()

    def test_auth_host_and_origin_are_enforced(self):
        self.assertEqual(self.request(auth=False)[0],401)
        self.assertIn('Basic',self.request(auth=False)[1]['WWW-Authenticate'])
        self.assertEqual(self.request(host='attacker.example')[0],403)
        self.assertEqual(self.request(origin='http://attacker.example')[0],403)

    def test_panel_metrics_and_catalog_use_one_authenticated_origin(self):
        self.assertEqual(self.request()[2],b'trial page')
        self.assertTrue(json.loads(self.request('/v1/snapshot')[2])['test_metric'])
        self.assertEqual(self.request('/api/v1/catalog')[0],200)
        self.assertEqual(self.request('/api/v1/catalog','PUT',json.dumps(api.EMPTY),origin='http://localhost:8088')[0],200)

    def test_private_files_traversal_and_maintenance_writes_are_denied(self):
        for path in ('/../catalog.sqlite3','/%2e%2e/catalog.sqlite3','/.env','/installed.json'):
            self.assertEqual(self.request(path)[0],404)
        (self.root/'.maintenance').write_text('updating')
        self.assertEqual(self.request('/api/v1/catalog','PUT','{}',origin='http://localhost:8088')[0],503)

    def test_update_check_is_authenticated_and_has_no_install_endpoint(self):
        self.assertEqual(self.request('/api/v1/updates',auth=False)[0],401)
        self.assertEqual(json.loads(self.request('/api/v1/updates')[2])['state'],'current')
        self.assertEqual(self.request('/api/v1/updates/check','POST','{}')[0],403)
        self.assertTrue(json.loads(self.request('/api/v1/updates/check','POST','{}',origin='http://localhost:8088')[2])['forced'])
        self.assertEqual(self.request('/api/v1/updates/apply','POST','{}',origin='http://localhost:8088')[0],405)


class Installation(unittest.TestCase):
    def target(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name)
        for name in ('opt','var/lib','etc/systemd/system'):(root/name).mkdir(parents=True,exist_ok=True)
        return root

    def test_fresh_install_private_credentials_and_one_service(self):
        root=self.target();calls=[]
        runner=lambda argv,**kwargs:calls.append(argv)
        with patch.object(m.os,'chown'):
            env=m.install(ROOT,'a'*40,tx=m.common.Transaction(root),runner=runner,check_health=lambda *_:None,account_ids=(os.getuid(),os.getgid()))
        self.assertIn('NEXO_TEST_PASSWORD='+env['NEXO_TEST_PASSWORD'],(root/'etc/nexo-test.env').read_text())
        self.assertEqual((root/'etc/nexo-test-access.txt').stat().st_mode&0o777,0o600)
        self.assertEqual((root/'var/lib/nexo-test').stat().st_mode&0o777,0o700)
        self.assertIn('name="nexo-test" content="loopback"',(root/'opt/nexo-test/panel/index.html').read_text())
        self.assertTrue(any(c[-2:]==['--now','nexo-test.service'] for c in calls))
        self.assertFalse(any('nginx' in ' '.join(c) for c in calls))

    def test_start_failure_rolls_back_only_test_installation(self):
        root=self.target();(root/'etc/nexo-api.env').write_text('preserve existing installation')
        def runner(argv,**kwargs):
            if 'enable' in argv:raise RuntimeError('start failed')
        with patch.object(m.os,'chown'):
            with self.assertRaises(RuntimeError):m.install(ROOT,'a'*40,tx=m.common.Transaction(root),runner=runner,check_health=lambda *_:None,account_ids=(os.getuid(),os.getgid()))
        self.assertFalse((root/'opt/nexo-test').exists())
        self.assertEqual((root/'etc/nexo-api.env').read_text(),'preserve existing installation')

    def setup_update(self):
        root=self.target();code=root/'opt/nexo-test';m.prepare(ROOT,code,'trial','a'*40)
        (code/'sentinel').write_text('old code')
        state=root/'var/lib/nexo-test';state.mkdir(mode=0o700)
        catalog=api.Catalog(state/'catalog.sqlite3');catalog.write(api.EMPTY,0)
        env=root/'etc/nexo-test.env';env.write_text('NEXO_TEST_PORT=8088\nNEXO_TEST_USER=administrador\nNEXO_TEST_PASSWORD='+('a'*48)+'\n');env.chmod(0o600)
        return root,code,catalog,env

    def test_update_preserves_data_credentials_and_backup(self):
        root,code,catalog,env=self.setup_update();before=env.read_bytes();calls=[]
        def health(*_):self.assertTrue((code/'.maintenance').exists())
        result=m.update(ROOT,'b'*40,'trial',runner=lambda argv:calls.append(argv),check_health=health,root=root)
        self.assertEqual(json.loads((code/'installed.json').read_text())['source_commit'],'b'*40)
        self.assertEqual(catalog.read()['revision'],1);self.assertEqual(env.read_bytes(),before)
        self.assertTrue(Path(result['backup']).is_file());self.assertFalse((code/'.maintenance').exists())
        self.assertTrue((Path(result['previous_code'])/'sentinel').is_file())

    def test_failed_health_restores_code_and_database(self):
        root,code,catalog,env=self.setup_update();before=env.read_bytes()
        def fail(*_):
            catalog.write(api.EMPTY,1)
            raise RuntimeError('new version failed health')
        with self.assertRaises(RuntimeError):m.update(ROOT,'b'*40,'trial',runner=lambda *_:None,check_health=fail,root=root)
        self.assertEqual(catalog.read()['revision'],1)
        self.assertTrue((code/'sentinel').is_file());self.assertFalse((code/'.maintenance').exists())
        self.assertEqual(env.read_bytes(),before)
        self.assertEqual(json.loads((code/'installed.json').read_text())['source_commit'],'a'*40)

    def test_preexisting_maintenance_marker_is_preserved(self):
        root,code,catalog,env=self.setup_update();(code/'.maintenance').write_text('another operation')
        calls=[]
        with self.assertRaises(RuntimeError):m.update(ROOT,'b'*40,'trial',runner=lambda argv:calls.append(argv),root=root)
        self.assertTrue((code/'.maintenance').is_file());self.assertEqual(calls,[])

    def test_production_06_upgrade_is_adopted_once_without_erasing_catalog(self):
        root=self.target();code=root/'opt/nexo-iptv';m.prepare(ROOT,code,'production',None)
        (code/'installed.json').unlink()
        api_file=code/'backend/nexo_api.py';api_file.write_text(api_file.read_text().replace("VERSION = '0.6.1'","VERSION = '0.6.0'"))
        state=root/'var/lib/nexo-iptv';state.mkdir(mode=0o700)
        catalog=api.Catalog(state/'catalog.sqlite3');catalog.write(api.EMPTY,0)
        (root/'etc/nexo-api.env').write_text('NEXO_API_TOKEN='+('a'*64)+'\n')
        calls=[]
        m.update(ROOT,'b'*40,'production',runner=lambda argv:calls.append(argv),check_health=lambda *_:None,root=root)
        self.assertEqual(catalog.read()['revision'],1)
        self.assertEqual(json.loads((code/'installed.json').read_text())['source_commit'],'b'*40)
        self.assertTrue(any(c[-1]=='nexo-api.service' for c in calls))
        self.assertTrue(any(c[-1]=='nexo-monitor.service' for c in calls))


if __name__=='__main__':unittest.main()
