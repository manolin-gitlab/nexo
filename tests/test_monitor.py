import datetime as dt
import ast
import http.client
import importlib.util
import json
from pathlib import Path
import secrets
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

spec=importlib.util.spec_from_file_location('agent',Path(__file__).resolve().parents[1]/'agent/nexo_monitor.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Metrics(unittest.TestCase):
    def test_python38_syntax(self):
        ast.parse((Path(__file__).resolve().parents[1]/'agent/nexo_monitor.py').read_text(),feature_version=(3,8))

    def test_cpu_and_counter_reset(self):
        self.assertEqual(m.cpu_counters('cpu  10 0 10 70 10 0 0 0 999 999'),(100,80))
        self.assertEqual(m.cpu_percent((100,80),(200,130)),50)
        self.assertIsNone(m.cpu_percent((200,100),(200,100)))
        self.assertIsNone(m.cpu_percent((200,100),(100,80)))

    def test_mem_available(self):
        v=m.mem_stats('MemTotal: 1000 kB\nMemAvailable: 250 kB\nMemFree: 10 kB\n')
        self.assertEqual(v['used_percent'],75)
        self.assertEqual(v['used_bytes'],750*1024)

    def test_shmem_not_subtracted_twice_and_swap(self):
        v=m.mem_stats('MemTotal: 1000 kB\nMemAvailable: 600 kB\nShmem: 300 kB\nSwapTotal: 500 kB\nSwapFree: 400 kB\n')
        self.assertEqual(v['used_percent'],40)
        self.assertEqual(v['available_bytes'],600*1024)
        self.assertEqual(v['shmem_bytes'],300*1024)
        self.assertEqual(v['swap_used_bytes'],100*1024)

    def test_scheduler_load_is_not_cpu_percent(self):
        v=m.scheduler_stats('cpu 1 0 1 5 0 0 0 0\nprocs_running 19\nprocs_blocked 2\n','19.55 15.76 14.54 19/500 123')
        self.assertEqual(v['load_average'],[19.55,15.76,14.54])
        self.assertEqual(v['runnable_tasks'],19)
        self.assertEqual(v['blocked_tasks'],2)
        self.assertNotIn('load_percent',v)

    def test_cpu_iowait_steal_and_counter_reset(self):
        a='cpu 10 0 10 70 10 0 0 0 999 999'
        b='cpu 30 0 30 110 20 0 0 10 999 999'
        self.assertEqual(m.cpu_waits(a,b),{'iowait_percent':10,'steal_percent':10})
        self.assertIsNone(m.cpu_waits(b,a)['steal_percent'])

    def test_pressure_partial_kernel_and_invalid_values(self):
        some='some avg10=1.20 avg60=0.80 avg300=0.40 total=12000'
        full='full avg10=0.10 avg60=0.05 avg300=0.00 total=200'
        self.assertEqual(m.pressure_stats(some)['some']['total_us'],12000)
        self.assertNotIn('full',m.pressure_stats(some+'\n'+full,cpu=True))
        self.assertEqual(m.pressure_stats(some+'\n'+full)['full']['avg60'],.05)
        for value in ['nan','-1','101']:
            with self.assertRaises(ValueError):m.pressure_stats(some.replace('1.20',value))

    def test_mount_escapes_inheritance_and_missing_mount(self):
        table=m.mount_table('root / ext4 rw 0 0\ntmpfs /srv/cache\\040live tmpfs rw 0 0\n')
        self.assertEqual(table['/srv/cache live'],'tmpfs')
        self.assertEqual(m.mount_check('/media/movies',table),{'path':'/media/movies','mounted':False,'filesystem':None})
        self.assertIsNone(m.mount_check('/media/movies',None)['mounted'])
        with tempfile.TemporaryDirectory() as tmp:
            row=m.disk_stats(tmp,{'/':'ext4',tmp:'tmpfs'})
            self.assertEqual(row['storage_kind'],'memory')
            self.assertEqual(row['mount_point'],tmp)
            self.assertIn('inodes_used_percent',row)

    def test_expected_missing_mount_does_not_report_root_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc=Path(tmp);(proc/'self').mkdir();(proc/'self/mounts').write_text('root / ext4 rw 0 0\n')
            collector=m.Collector(proc=proc,disks=['/media/movies'],expected_mounts=['/media/movies'])
            with patch.object(m.os,'statvfs',side_effect=AssertionError('must not stat missing mount')):
                row=collector.sample()
            self.assertFalse(row['mount_checks'][0]['mounted'])
            self.assertEqual(row['disks'],[])

    def test_network_rates(self):
        self.assertEqual(m.net_rates((100,200),(1100,2200),2),(4000,8000))
        self.assertEqual(m.net_rates((100,200),(10,2200),2),(None,None))
        self.assertEqual(m.net_rates(None,(1,2),2),(None,None))

    def test_tcp_filter_state_and_ports(self):
        raw='header\n 0: 0100007F:0050 010200C0:C001 01 extra\n 1: 0100007F:01BB 010200C0:C002 01 extra\n 2: 0100007F:0016 010200C0:C003 01 extra\n 3: 0100007F:0050 010200C0:C004 06 extra\n'
        self.assertEqual(m.tcp_peers(raw,{80,443}),{'192.0.2.1':2})
        self.assertEqual(m.decode_endpoint('00000000000000000000000001000000:01BB',True),('::1',443))

    def test_sessions_fresh_stale_and_duplicate(self):
        now=dt.datetime.now(dt.timezone.utc)
        row={'id':'a','ip':'192.0.2.1','lat':-2,'lon':-79,'bitrate_bps':4000000}
        with tempfile.NamedTemporaryFile(mode='w+',suffix='.json') as f:
            def put(timestamp,rows):
                f.seek(0);json.dump({'timestamp':timestamp.isoformat(),'sessions':rows},f);f.truncate();f.flush()
            put(now,[row]);sessions,_,status=m.read_sessions(f.name,now)
            self.assertEqual(status,'available');self.assertEqual(sessions[0]['lat'],-2)
            put(now-dt.timedelta(seconds=16),[row]);self.assertEqual(m.read_sessions(f.name,now)[2],'stale')
            put(now,[row,row]);self.assertEqual(m.read_sessions(f.name,now)[2],'error')
        self.assertEqual(m.read_sessions(None),(None,None,'not_configured'))

    def test_process_counters_and_pid_reuse(self):
        def stat(user, start=1234):
            values=['S']+['0']*21
            values[11],values[12],values[19],values[21]=str(user),'50',str(start),'10'
            return '123 (ffmpeg (worker)) '+' '.join(values)
        raw=m.process_stat(stat(100),4096)
        self.assertEqual(raw['name'],'ffmpeg (worker)')
        self.assertEqual(raw['rss_bytes'],40960)
        with tempfile.TemporaryDirectory() as tmp:
            proc=Path(tmp);(proc/'123').mkdir();p=proc/'123/stat';p.write_text(stat(100))
            rows,previous,_,_=m.process_metrics(proc,{},0,4,100,4096)
            self.assertIsNone(rows[0]['cpu_percent'])
            p.write_text(stat(200))
            rows,_,_,_=m.process_metrics(proc,previous,2,4,100,4096)
            self.assertEqual(rows[0]['cpu_percent'],12.5)
            self.assertEqual(set(rows[0]),{'pid','name','cpu_percent','rss_bytes'})
            p.write_text(stat(200,9999))
            rows,_,_,_=m.process_metrics(proc,previous,2,4,100,4096)
            self.assertIsNone(rows[0]['cpu_percent'])

    def test_real_linux_snapshot(self):
        data=m.Collector().sample()
        self.assertEqual(data['schema_version'],1)
        self.assertGreater(data['memory']['total_bytes'],0)
        self.assertEqual(data['sessions_status'],'not_configured')
        self.assertIsNone(data['sessions'])
        self.assertIsNone(data['cpu']['percent'])
        self.assertIsNone(data['network']['rx_bps'])

    def test_process_scan_is_cached_for_ten_seconds_with_its_own_timestamp(self):
        with patch.object(m,'process_metrics',return_value=([],{},0,False)) as scan:
            collector=m.Collector()
            first=collector.sample();second=collector.sample();collector.sample()
            self.assertEqual(scan.call_count,1)
            self.assertEqual(first['processes_timestamp'],second['processes_timestamp'])
            collector.process_time-=10
            collector.sample();self.assertEqual(scan.call_count,2)

class Auth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.token=secrets.token_hex(32);cls.origin='https://panel.example.com'
        cls.collector=m.Collector();cls.collector.sample()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),m.handler_class(cls.collector,cls.token,cls.origin))
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close()
    def request(self,method='GET',headers=None,path='/v1/snapshot'):
        c=http.client.HTTPConnection(*self.server.server_address,timeout=2);c.request(method,path,headers=headers or {})
        r=c.getresponse();result=(r.status,dict(r.getheaders()),r.read());c.close();return result
    def test_no_auth(self):
        self.assertEqual(self.request()[0],401)
    def test_wrong_auth(self):
        self.assertEqual(self.request(headers={'Authorization':'Bearer wrong'})[0],401)
    def test_unicode_auth_denied(self):
        self.assertEqual(self.request(headers={'Authorization':'Bearer é'})[0],401)
    def test_wrong_origin(self):
        self.assertEqual(self.request(headers={'Authorization':'Bearer '+self.token,'Origin':'https://evil.example'})[0],403)
    def test_valid_snapshot_and_cache(self):
        status,headers,body=self.request(headers={'Authorization':'Bearer '+self.token,'Origin':self.origin})
        self.assertEqual(status,200);self.assertEqual(headers['Access-Control-Allow-Origin'],self.origin)
        self.assertEqual(headers['Cache-Control'],'no-store');self.assertEqual(json.loads(body)['schema_version'],1)
    def test_preflight(self):
        status,headers,_=self.request('OPTIONS',{'Origin':self.origin,'Access-Control-Request-Method':'GET','Access-Control-Request-Headers':'authorization'})
        self.assertEqual(status,204);self.assertNotEqual(headers['Access-Control-Allow-Origin'],'*')
        self.assertEqual(self.request('OPTIONS',{'Origin':'https://evil.example','Access-Control-Request-Method':'GET'})[0],403)
    def test_read_only_and_unknown_route(self):
        self.assertEqual(self.request('POST',{'Authorization':'Bearer '+self.token})[0],501)
        self.assertEqual(self.request(headers={'Authorization':'Bearer '+self.token},path='/unknown')[0],404)

if __name__=='__main__':unittest.main()
