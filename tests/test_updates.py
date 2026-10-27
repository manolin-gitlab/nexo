import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('update_checks',ROOT/'backend/updates.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)

    def manager(self,head='b'*40,installed='a'*40,latest=None,sources=None,tree=None):
        info={'version':'0.6.1','update_protocol':1,'catalog_schema':1,'source_commit':installed,'install_kind':'trial','source_files':sources or {}}
        path=self.folder/'installed.json';path.write_text(json.dumps(info))
        self.calls=[]
        def fetch(url):
            self.calls.append(url)
            if '/git/ref/' in url:return {'object':{'sha':head}}
            if url.endswith('nexo-release.json'):return latest or {'version':'0.6.1','update_protocol':1,'catalog_schema':1}
            if '/git/commits/' in url:return {'tree':{'sha':'c'*40}}
            if '/git/trees/' in url:return {'tree':tree or [],'truncated':False}
            raise AssertionError(url)
        return m.Manager(path,self.folder/'baseline.json',fetcher=fetch)

    def test_new_commit_is_available_even_without_version_bump(self):
        manager=self.manager();status=manager.inspect()
        self.assertTrue(status['available']);self.assertEqual(status['state'],'available')
        self.assertEqual(status['latest_commit'],'b'*40)
        self.assertIn('/compare/',''+status['changes_url'])

    def test_same_commit_uses_one_request(self):
        manager=self.manager(head='a'*40)
        self.assertEqual(manager.inspect()['state'],'current');self.assertEqual(len(self.calls),1)

    def test_command_pins_the_reviewed_revision_and_installation_kind(self):
        manager=self.manager();manager.check();manager.last_attempt=manager.clock()
        command=manager.status()['command']
        self.assertIn('/'+('b'*40)+'/install-quick.sh',command)
        self.assertIn('--revision '+('b'*40),command)
        self.assertTrue(command.endswith('--kind trial'))

    def test_incompatible_schema_and_downgrade_are_not_suggested_as_safe(self):
        for latest in ({'version':'0.7.0','update_protocol':1,'catalog_schema':2},
                       {'version':'0.5.0','update_protocol':1,'catalog_schema':1}):
            result=self.manager(latest=latest).inspect()
            self.assertEqual(result['state'],'incompatible');self.assertFalse(result['compatible'])

    def test_packaged_install_records_baseline_only_if_all_source_hashes_match(self):
        sources={'dist/app.js':'d'*40}
        manager=self.manager(installed=None,sources=sources,tree=[{'path':'dist/app.js','sha':'d'*40,'type':'blob'}])
        self.assertEqual(manager.inspect()['state'],'current');self.assertEqual(manager.baseline(),'b'*40)
        manager=self.manager(installed=None,sources=sources,tree=[{'path':'dist/app.js','sha':'e'*40,'type':'blob'}])
        (self.folder/'baseline.json').unlink()
        self.assertTrue(manager.inspect()['available']);self.assertIsNone(manager.baseline())

    def test_network_or_malformed_head_is_unknown_not_current(self):
        manager=self.manager(head='not-a-sha');manager.check()
        self.assertEqual(manager.result['state'],'error');self.assertIsNone(manager.result['available'])

    def test_checks_are_cached_and_forced_checks_are_throttled(self):
        manager=self.manager();manager.status()
        for _ in range(100):
            with manager.lock: busy=manager.busy
            if not busy:break
            time.sleep(.001)
        count=len(self.calls)
        for _ in range(10):manager.status(force=True)
        self.assertEqual(len(self.calls),count)


if __name__=='__main__':unittest.main()
