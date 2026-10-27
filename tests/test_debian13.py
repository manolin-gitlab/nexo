import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('debian_installer', ROOT/'install/debian13/install.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def payload(directory):
    bundle = Path(directory)/'bundle'; (bundle/'payload/panel').mkdir(parents=True)
    (bundle/'payload/agent').mkdir()
    (bundle/'payload/panel/index.html').write_text('<title>Nexo IPTV</title>')
    (bundle/'payload/agent/nexo_monitor.py').write_text('# agent fixture')
    (bundle/'payload/agent/nexo-monitor.service').write_text('ExecStart=/usr/bin/python3 /opt/nexo-monitor/nexo_monitor.py\n')
    files = {p.relative_to(bundle).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (bundle/'payload').rglob('*') if p.is_file()}
    (bundle/'manifest.json').write_text(json.dumps({'files':files}))
    return bundle


class Installer(unittest.TestCase):
    def plan(self):
        return m.make_plan('https://nexo.example.com:8443','/etc/ssl/fullchain.pem','/etc/ssl/private/key.pem')

    def test_exact_target_not_ubuntu_or_debian12(self):
        self.assertTrue(m.debian13('ID=debian\nVERSION_ID="13"\n'))
        self.assertTrue(m.debian13('ID=debian\nVERSION_ID="13.1"\n'))
        self.assertFalse(m.debian13('ID=ubuntu\nID_LIKE=debian\nVERSION_ID="24.04"'))
        self.assertFalse(m.debian13('ID=debian\nVERSION_ID=12'))

    def test_origin_and_config_injection_rejected(self):
        for origin in ['http://nexo.example.com','https://u:p@nexo.example.com','https://nexo.example.com/secret','https://nexo.example.com?token=x','https://nexo.example.com:9108','https://bad;host.example.com']:
            with self.assertRaises(ValueError):m.make_plan(origin,'/etc/cert.pem','/etc/key.pem')
        for value in ['/etc/key;evil.pem','/etc/key\n.pem','/etc/../key.pem','relative.pem']:
            with self.assertRaises(ValueError):m.make_plan('https://nexo.example.com','/etc/cert.pem',value)
        self.assertEqual(self.plan()['origin'],'https://nexo.example.com:8443')

    def test_payload_tamper_extra_file_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=payload(tmp);self.assertEqual(m.verify_payload(b),3)
            p=b/'payload/panel/index.html';p.write_text('changed')
            with self.assertRaises(ValueError):m.verify_payload(b)
        with tempfile.TemporaryDirectory() as tmp:
            b=payload(tmp);(b/'payload/panel/extra').write_text('extra')
            with self.assertRaises(ValueError):m.verify_payload(b)
        with tempfile.TemporaryDirectory() as tmp:
            b=payload(tmp);(b/'payload/panel/link').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):m.verify_payload(b)

    def test_generated_config_separates_basic_and_bearer(self):
        config=m.nginx_config(self.plan())
        self.assertIn('listen 8443 ssl;',config)
        self.assertIn('auth_basic "Nexo IPTV";',config)
        self.assertEqual(config.count('auth_basic off;'),2)
        self.assertIn('proxy_set_header Authorization $http_authorization;',config)
        self.assertNotIn('NEXO_TOKEN',config)

    def test_exclusive_creation_and_rollback_preserve_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tx=m.Transaction(Path(tmp));(Path(tmp)/'keep').write_text('existing')
            with self.assertRaises(FileExistsError):tx.write('/keep','replacement')
            (Path(tmp)/'alias').symlink_to(Path(tmp)/'keep')
            with self.assertRaises(FileExistsError):tx.write('/alias','replacement')
            tx.mkdir('/nexo');tx.write('/nexo/private','secret',0o600)
            self.assertEqual(tx.rollback(),[])
            self.assertEqual((Path(tmp)/'keep').read_text(),'existing')
            self.assertTrue((Path(tmp)/'alias').is_symlink())

    def test_installed_directory_modes_ignore_restrictive_caller_umask(self):
        with tempfile.TemporaryDirectory() as tmp:
            tx=m.Transaction(Path(tmp));previous=os.umask(0o077)
            try:
                public=tx.mkdir('/public',0o755);private=tx.mkdir('/private',0o750)
            finally:
                os.umask(previous)
            self.assertEqual(public.stat().st_mode&0o777,0o755)
            self.assertEqual(private.stat().st_mode&0o777,0o750)

    def simulate(self, fail=None):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        b=payload(tmp.name);target=Path(tmp.name)/'target';target.mkdir()
        for directory in ('opt','etc/nginx/sites-available','etc/nginx/sites-enabled','etc/systemd/system'):
            (target/directory).mkdir(parents=True,exist_ok=True)
        (target/'etc/nginx/sites-enabled/default').write_text('unchanged')
        calls=[];health_tokens=[];failed=False
        def runner(argv,input_text=None,check=True):
            nonlocal failed
            calls.append((argv,input_text))
            if fail and fail in ' '.join(argv) and not failed:
                failed=True;raise RuntimeError('simulated failure')
            return SimpleNamespace(returncode=0,stdout='$6$FAKE_SALT$FAKE_HASH\n' if 'passwd' in argv else '')
        def health(token):
            health_tokens.append(token)
            if fail=='health':raise RuntimeError('simulated health failure')
        with patch.object(m.os,'chown'):
            if fail:
                with self.assertRaises(RuntimeError):m.install(b,self.plan(),m.Transaction(target),runner,health,os.getgid())
            else:
                self.assertEqual(m.install(b,self.plan(),m.Transaction(target),runner,health,os.getgid()),self.plan()['origin'])
        return target,calls,health_tokens

    def test_success_private_files_and_service_health_before_reload(self):
        target,calls,tokens=self.simulate()
        env=(target/'etc/nexo-monitor.env').read_text()
        self.assertIn('NEXO_TOKEN='+tokens[0],env)
        self.assertIn('NEXO_BIND=127.0.0.1',env)
        for name in ('etc/nexo-monitor.env','etc/nexo-iptv/access.txt'):
            self.assertEqual((target/name).stat().st_mode&0o777,0o600)
        self.assertEqual((target/'etc/nexo-iptv/htpasswd').stat().st_mode&0o777,0o640)
        commands=[' '.join(c[0]) for c in calls]
        self.assertLess(next(i for i,c in enumerate(commands) if 'nginx -t' in c),next(i for i,c in enumerate(commands) if 'useradd' in c))
        self.assertTrue(commands[-1].endswith('reload nginx'))
        self.assertNotIn(tokens[0],' '.join(commands))
        self.assertEqual((target/'etc/nginx/sites-enabled/default').read_text(),'unchanged')

    def test_nginx_validation_failure_rolls_back_before_user_creation(self):
        target,calls,_=self.simulate('nginx -t')
        self.assertFalse((target/'opt/nexo-iptv').exists())
        self.assertFalse(any('useradd' in ' '.join(c[0]) for c in calls))
        self.assertTrue((target/'etc/nginx/sites-enabled/default').exists())

    def test_health_failure_disables_only_own_new_service(self):
        target,calls,_=self.simulate('health')
        commands=[' '.join(c[0]) for c in calls]
        self.assertTrue(any('disable --now nexo-monitor.service' in c for c in commands))
        self.assertFalse(any(c.endswith('reload nginx') for c in commands))
        self.assertFalse((target/'etc/nexo-iptv').exists())

    def test_nginx_reload_failure_restores_previous_configuration(self):
        target,calls,_=self.simulate('reload nginx')
        commands=[' '.join(c[0]) for c in calls]
        self.assertEqual(sum(c.endswith('reload nginx') for c in commands),2)
        self.assertFalse((target/'etc/nginx/sites-enabled/nexo-iptv.conf').exists())
        self.assertEqual((target/'etc/nginx/sites-enabled/default').read_text(),'unchanged')

    def test_openssl_hash_uses_stdin(self):
        result=m.run([m.TOOLS['openssl'],'passwd','-6','-stdin'],input_text='synthetic-test-password-only\n')
        self.assertTrue(result.stdout.startswith('$6$'))


if __name__=='__main__':unittest.main()
