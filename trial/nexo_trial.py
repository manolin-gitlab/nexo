#!/usr/bin/env python3
"""Local-only test appliance. Use an SSH tunnel; never a public web listener."""
import base64
import binascii
import hmac
import importlib.util
import mimetypes
import os
from pathlib import Path
import re
import signal
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import unquote, urlsplit


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def handler_class(root, catalog_module, catalog, collector, username, password, port, updates=None, streaming=None):
    root = Path(root); origin = 'http://localhost:'+str(port)
    parent = catalog_module.handler_class(catalog, '', origin, updates, root/'.maintenance', streaming)
    expected = (username + ':' + password).encode('utf-8')
    class Handler(parent):
        def send_response(self, code, message=None):
            super().send_response(code, message)
            if code == 401:
                self.send_header('WWW-Authenticate', 'Basic realm="Nexo test", charset="UTF-8"')

        def authorized(self):
            if self.headers.get_all('Host') != ['localhost:'+str(port)]:
                self.send_json(403, {'error': 'use_localhost_tunnel'}); return False
            if self.headers.get('Origin') not in (None, origin):
                self.send_json(403, {'error': 'origin_denied'}); return False
            try:
                scheme, value = self.headers.get('Authorization', '').split(' ', 1)
                supplied = base64.b64decode(value, validate=True) if scheme.lower() == 'basic' else b''
            except (ValueError, binascii.Error):
                supplied = b''
            if not hmac.compare_digest(supplied, expected):
                self.send_json(401, {'error': 'unauthorized'}); return False
            return True

        def do_GET(self):
            if self.path.startswith('/tv/'):
                if streaming: streaming.handle(self, 'localhost:'+str(port))
                else: self.send_json(404, {'error': 'not_found'})
                return
            if self.path.startswith('/api/'):
                super().do_GET(); return
            if not self.authorized(): return
            if self.path == '/v1/snapshot':
                data = dict(collector.get())
                if streaming:
                    status = streaming.status()
                    data.update(sessions=[{'id': s['id'], 'client': s['client'], 'channel': s['channel'],
                                           'ip': s['ip'], 'started_at': s['started'], 'bitrate_bps': None,
                                           'lat': None, 'lon': None, 'location': ''} for s in status['sessions']],
                                sessions_status='available', sessions_timestamp=data['timestamp'])
                    data['streaming'] = {'live': sum(w['state']=='live' for w in status['workers']),
                                         'starting': sum(w['state']=='starting' for w in status['workers']),
                                         'failed': sum(w['state'] not in ('live','starting') for w in status['workers'])}
                self.send_json(200, data); return
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc:
                self.send_json(404, {'error': 'not_found'}); return
            name = unquote(parsed.path).lstrip('/') or 'index.html'
            path = root/'panel'/name
            base = (root/'panel').resolve()
            if (not path.resolve().is_relative_to(base) or any(part.startswith('.') for part in Path(name).parts)
                    or path.is_symlink() or not path.is_file()):
                self.send_json(404, {'error': 'not_found'}); return
            raw = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(name)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers(); self.wfile.write(raw)
    return Handler


def main():
    os.umask(0o077)
    root = Path(__file__).resolve().parent
    api = load('nexo_trial_catalog', root/'backend/nexo_api.py')
    monitor = load('nexo_trial_monitor', root/'agent/nexo_monitor.py')
    updater = load('nexo_trial_updates', root/'backend/updates.py')
    username = os.environ.get('NEXO_TEST_USER', 'administrador')
    password = os.environ.get('NEXO_TEST_PASSWORD', '')
    port = int(os.environ.get('NEXO_TEST_PORT', '8088'))
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', username) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', password):
        raise ValueError('Configura las credenciales privadas de prueba.')
    if not 1024 <= port <= 65535: raise ValueError('Puerto no válido.')
    catalog = api.Catalog(os.environ.get('NEXO_TEST_DB', '/var/lib/nexo-test/catalog.sqlite3'))
    collector = monitor.Collector(name='Nexo — servidor de prueba', disks=['/'], ports={port})
    collector.sample(); threading.Thread(target=collector.run, daemon=True).start()
    updates = updater.Manager(root/'installed.json', catalog.path.parent/'update-baseline.json')
    limits = load('nexo_trial_http_limits', root/'backend/http_limits.py')
    streaming = None
    if os.getenv('NEXO_TEST_STREAMING') == '1':
        relay_module = load('nexo_trial_relay', root/'backend/streaming.py')
        streaming = relay_module.Relay(catalog, catalog.path.parent, os.getenv('NEXO_STREAM_KEY', ''))
    server = limits.BoundedHTTPServer(('127.0.0.1', port), handler_class(root, api, catalog, collector, username, password, port, updates, streaming))
    print('Nexo test ready on loopback. Access through an SSH tunnel.', flush=True)
    def stop(*_): raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, stop)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        if streaming: streaming.close()
        server.server_close()


if __name__ == '__main__': main()
