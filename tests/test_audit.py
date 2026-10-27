import ast
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('audit',ROOT/'audit/xui_structure.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class Audit(unittest.TestCase):
    def test_python38_syntax(self):
        ast.parse((ROOT/'audit/xui_structure.py').read_text(),feature_version=(3,8))

    def test_resource_deltas_and_pid_reuse(self):
        def sample(at,ticks,rx,start=11):
            return {'at':at,'cpu':(100+100*at,50+50*at),
                'memory':{'MemTotal':1000,'MemAvailable':250},
                'interfaces':{'eth0':{'rx_bytes':rx,'tx_bytes':rx*2}},
                'processes':{(7,start):{'pid':7,'name':'ffmpeg','ticks':ticks,'rss_bytes':123}}}
        before=sample(0,100,1000);after=sample(2,200,2000)
        with patch.object(m.os,'sysconf',return_value=100):
            row=m.interval_report(before,after,4)
            self.assertEqual(row['cpu_percent'],50)
            self.assertEqual(row['memory_bytes']['used_percent'],75)
            self.assertEqual(row['interfaces'][0]['rx_bps'],4000)
            self.assertEqual(row['top_processes'][0]['cpu_percent_total_capacity'],12.5)
            after['interfaces']['eth0']['rx_bytes']=0
            self.assertIsNone(m.interval_report(before,after,4)['interfaces'][0]['rx_bps'])
            after=sample(2,200,2000,99)
            self.assertIsNone(m.interval_report(before,after,4)['top_processes'][0]['cpu_percent_total_capacity'])

    def test_inventory_excludes_media_and_secrets_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'content').mkdir();(root/'content/private-movie.php').write_text('private')
            (root/'admin').mkdir();(root/'admin/panel.php').write_text('<?php SECRET_SOURCE')
            (root/'config-token.php').write_text('secret');(root/'alias.php').symlink_to(root/'admin/panel.php')
            data=m.inventory(root);raw=json.dumps(data)
            self.assertNotIn('private-movie',raw);self.assertNotIn('SECRET_SOURCE',raw)
            self.assertNotIn('config-token',raw);self.assertNotIn('alias.php',[p['path'] for p in data['files']])
            self.assertIn('admin/panel.php',[p['path'] for p in data['files']])

    def test_private_output_no_overwrite_or_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'report.json';m.write_report({'ok':True},p)
            self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600)
            with self.assertRaises(FileExistsError):m.write_report({},p)
            link=Path(tmp)/'link';link.symlink_to(p)
            with self.assertRaises(FileExistsError):m.write_report({},link)
            self.assertEqual(json.loads(p.read_text()),{'ok':True})

    def test_socket_summary_does_not_export_remote_ips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'net').mkdir()
            (root/'net/tcp').write_text('header\n0: 00000000:0050 00000000:0000 0A\n1: 0100007F:0050 010200C0:C001 01\n2: 0100007F:0050 020200C0:C001 01\n')
            data=m.connections(root);self.assertEqual(data['tcp_established_total'],2)
            self.assertEqual(data['tcp_listeners'],[{'protocol':'tcp','port':80}])
            self.assertNotIn('010200C0',json.dumps(data))
            self.assertEqual(data['tcp_established_by_local_port'][0]['sockets'],2)

    def test_database_only_queries_metadata_without_defaults(self):
        calls=[]
        def command(args):
            calls.append(args);sql=args[-1]
            raw='xui\nother_private\n' if sql=='SHOW DATABASES' else 'streams\tname\tvarchar(100)\tYES\t\n' if '.COLUMNS' in sql else ''
            return {'returncode':0,'stdout':raw,'available':True}
        with patch.object(m.shutil,'which',return_value='/usr/bin/mysql'),patch.object(m,'command',side_effect=command):
            report=m.database_schema()
        self.assertEqual([s['schema'] for s in report['schemas']],['xui'])
        self.assertTrue(all('--no-defaults' in args and '--protocol=socket' in args for args in calls))
        self.assertTrue(all(args[-1]=='SHOW DATABASES' or 'FROM information_schema.' in args[-1] for args in calls))
        self.assertTrue(all('COLUMN_DEFAULT' not in args[-1] for args in calls))
        self.assertNotIn('other_private',json.dumps(report))

    def test_local_auth_failure_continues_without_credentials(self):
        with patch.object(m.shutil,'which',return_value='/usr/bin/mysql'),patch.object(m,'command',return_value={'returncode':1,'stdout':''}):
            report=m.database_schema()
        self.assertFalse(report['available'])
        self.assertIn('no credentials',report['reason'])

    def test_database_child_does_not_inherit_mysql_credentials(self):
        completed=SimpleNamespace(returncode=1,stdout='')
        with patch.dict(os.environ,{'MYSQL_PWD':'PRIVATE_VALUE','MYSQL_HOST':'remote.invalid','MYSQL_TEST_LOGIN_FILE':'/private/file'}), \
             patch.object(m.shutil,'which',return_value='/usr/bin/mysql'), \
             patch.object(m.subprocess,'run',return_value=completed) as run:
            m.command(['/usr/bin/mysql','--no-defaults','--protocol=socket','--password=','-e','SHOW DATABASES'])
        env=run.call_args.kwargs['env']
        self.assertNotIn('MYSQL_PWD',env);self.assertNotIn('MYSQL_HOST',env)
        self.assertEqual(env['MYSQL_TEST_LOGIN_FILE'],os.devnull)


if __name__=='__main__':unittest.main()
