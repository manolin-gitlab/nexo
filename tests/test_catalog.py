import concurrent.futures
import copy
import http.client
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('catalog_api', ROOT/'backend/nexo_api.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def sample():
    return {'channels': [{'id': 'ch-1', 'name': 'Canal de prueba', 'url': 'https://media.example.test/live.m3u8', 'group': 'Pruebas', 'enabled': True}],
            'packages': [{'id': 'pk-1', 'name': 'Paquete de prueba', 'channels': ['ch-1']}],
            'clients': [{'id': 'cl-1', 'name': 'Cliente sintético', 'packageId': 'pk-1', 'expires': '2028-12-31', 'connections': 2, 'enabled': True}]}


class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'catalog.sqlite3'
        self.db = m.Catalog(self.path)

    def test_persistence_and_revision_survive_new_instance(self):
        self.assertEqual(self.db.read(), {'revision': 0, 'catalog': m.EMPTY})
        self.db.write(sample(), 0)
        self.assertEqual(m.Catalog(self.path).read(), {'revision': 1, 'catalog': sample()})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_conflicting_writers_cannot_overwrite(self):
        def write(name):
            data = sample(); data['clients'][0]['name'] = name
            try: return self.db.write(data, 0)
            except m.Conflict: return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, ['Primero', 'Segundo']))
        self.assertEqual(sum(v is not None for v in results), 1)
        self.assertEqual(self.db.read(), next(v for v in results if v is not None))

    def test_invalid_relations_dates_duplicates_and_unknown_fields_do_not_write(self):
        changes = [lambda d: d['clients'][0].update(packageId='missing'),
                   lambda d: d['packages'][0].update(channels=['missing']),
                   lambda d: d['packages'][0].update(channels=['ch-1', 'ch-1']),
                   lambda d: d['clients'][0].update(expires='2026-02-30'),
                   lambda d: d['clients'][0].update(connections=True),
                   lambda d: d['channels'][0].update(url='https://user:password@example.test/live'),
                   lambda d: d['channels'][0].update(url='file:///etc/passwd'),
                   lambda d: d['channels'][0].update(enabled=1),
                   lambda d: d['clients'][0].update(password='not-a-supported-field'),
                   lambda d: d['clients'].append(copy.deepcopy(d['clients'][0]))]
        for change in changes:
            data = sample(); change(data)
            with self.assertRaises(ValueError): self.db.write(data, 0)
        self.assertEqual(self.db.read()['revision'], 0)

    def test_names_are_data_and_foreign_keys_can_be_removed_atomically(self):
        data = sample(); data['clients'][0]['name'] = "'); DROP TABLE catalog; --"
        self.db.write(data, 0)
        self.assertEqual(self.db.read()['catalog'], data)
        self.db.write(m.EMPTY, 1)
        self.assertEqual(self.db.read()['catalog'], m.EMPTY)

    def test_insecure_directory_and_symlink_are_refused(self):
        directory = Path(self.tmp.name)/'public'; directory.mkdir(mode=0o755)
        with self.assertRaises(ValueError): m.Catalog(directory/'data.sqlite3')
        link = Path(self.tmp.name)/'alias.sqlite3'; link.symlink_to(self.path)
        with self.assertRaises(ValueError): m.Catalog(link)

    def test_cli_refuses_non_loopback_before_creating_database(self):
        path = Path(self.tmp.name)/'uncreated.sqlite3'
        env = os.environ.copy(); env.update(NEXO_API_BIND='0.0.0.0', NEXO_API_TOKEN='a'*64,
            NEXO_API_ORIGIN='https://panel.example.test', NEXO_API_DB=str(path))
        result = subprocess.run([sys.executable, str(ROOT/'backend/nexo_api.py')], env=env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(path.exists())


class API(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = m.Catalog(Path(self.tmp.name)/'catalog.sqlite3')
        self.token = 'a'*64; self.origin = 'https://panel.example.test'
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), m.handler_class(self.db, self.token, self.origin))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)

    def request(self, method='GET', body=None, headers=None, path='/api/v1/catalog', authenticated=True):
        h = {'Authorization': 'Bearer '+self.token} if authenticated else {}
        h.update(headers or {})
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request(method, path, body, h)
            response = connection.getresponse(); raw = response.read()
            return response.status, dict(response.headers), json.loads(raw)
        finally: connection.close()

    def put(self, data=None, revision='"0"', **kwargs):
        return self.request('PUT', json.dumps(sample() if data is None else data),
            {'Origin': self.origin, 'Content-Type': 'application/json', 'If-Match': revision}, **kwargs)

    def test_authenticated_roundtrip_and_no_cache_or_cors(self):
        status, headers, data = self.request()
        self.assertEqual(status, 200); self.assertEqual(headers['ETag'], '"0"')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(self.put()[0], 200)
        self.assertEqual(self.request()[2], {'revision': 1, 'catalog': sample()})
        self.assertEqual(self.request(path='/api/v1/healthz')[2]['status'], 'ok')

    def test_no_token_and_wrong_origin_are_denied(self):
        self.assertEqual(self.request(authenticated=False)[0], 401)
        self.assertEqual(self.request(headers={'Authorization': 'Bearer wrong'})[0], 401)
        self.assertEqual(self.request(headers={'Origin': 'https://other.example.test'})[0], 403)
        self.assertEqual(self.request('PUT', '{}', {'Content-Type': 'application/json', 'If-Match': '"0"'})[0], 403)

    def test_conflict_returns_revision_without_overwriting(self):
        self.put()
        status, _, data = self.put(m.EMPTY)
        self.assertEqual(status, 409); self.assertEqual(data['revision'], 1)
        self.assertEqual(self.request()[2]['catalog'], sample())

    def test_validation_preconditions_and_body_limits(self):
        self.assertEqual(self.put(revision='0')[0], 428)
        self.assertEqual(self.put({'clients': []})[0], 400)
        headers = {'Origin': self.origin, 'If-Match': '"0"', 'Content-Type': 'application/json'}
        self.assertEqual(self.request('PUT', '{', headers)[0], 400)
        self.assertEqual(self.request('PUT', '{}', {**headers, 'Content-Length': '5000001'})[0], 413)
        self.assertEqual(self.request('PUT', '{}', {**headers, 'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.db.read()['revision'], 0)

    def test_other_methods_and_paths_are_not_mutations(self):
        for method in ('POST', 'PATCH', 'DELETE', 'OPTIONS'):
            self.assertEqual(self.request(method)[0], 405)
        self.assertEqual(self.request(path='/api/v1/catalog?token=no')[0], 404)
        self.assertEqual(self.db.read()['revision'], 0)


if __name__ == '__main__': unittest.main()
