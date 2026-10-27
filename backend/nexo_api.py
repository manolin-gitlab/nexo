#!/usr/bin/env python3
"""Nexo catalog API. Bind to loopback behind the authenticated HTTPS proxy."""
import argparse
from contextlib import closing
import datetime as dt
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

VERSION = '0.6.1'
MAX_BODY = 5_000_000
EMPTY = {'clients': [], 'channels': [], 'packages': []}


def text(value, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError('Texto vacío, demasiado largo o con caracteres de control.')
    value.encode('utf-8')
    return value


def valid_url(value):
    text(value, 4096)
    if any(c.isspace() or ord(c) == 127 for c in value):
        raise ValueError('La URL contiene espacios o controles.')
    u = urlsplit(value)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username is not None or u.password is not None:
        raise ValueError('Usa una URL HTTP o HTTPS sin credenciales.')
    if u.port is not None and not 1 <= u.port <= 65535:
        raise ValueError('Puerto no válido.')
    return value


def validate_catalog(data):
    if not isinstance(data, dict) or set(data) != set(EMPTY):
        raise ValueError('Se esperan clients, channels y packages.')
    result = {key: [] for key in EMPTY}
    ids = {key: set() for key in EMPTY}
    for kind in EMPTY:
        rows = data[kind]
        if not isinstance(rows, list) or len(rows) > 10000:
            raise ValueError('Límite de 10000 registros por categoría.')
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('Registro no válido.')
            item = {'id': text(row.get('id'), 128), 'name': text(row.get('name'))}
            if item['id'] in ids[kind]:
                raise ValueError('ID duplicado.')
            ids[kind].add(item['id'])
            if kind == 'channels':
                if set(row) != {'id', 'name', 'url', 'group', 'enabled'} or type(row.get('enabled')) is not bool:
                    raise ValueError('Canal no válido.')
                item.update(url=valid_url(row['url']), group=text(row['group'], 200), enabled=row['enabled'])
            elif kind == 'packages':
                channels = row.get('channels')
                if (set(row) != {'id', 'name', 'channels'} or not isinstance(channels, list)
                        or len(channels) > 10000 or any(not isinstance(v, str) for v in channels)
                        or len(set(channels)) != len(channels)):
                    raise ValueError('Paquete no válido o canales duplicados.')
                item['channels'] = list(channels)
            else:
                if (set(row) != {'id', 'name', 'packageId', 'expires', 'connections', 'enabled'}
                        or type(row.get('enabled')) is not bool or type(row.get('connections')) is not int
                        or not 1 <= row['connections'] <= 100):
                    raise ValueError('Cliente no válido.')
                expires = row.get('expires')
                if not isinstance(expires, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', expires):
                    raise ValueError('Fecha no válida.')
                dt.date.fromisoformat(expires)
                item.update(packageId=text(row.get('packageId'), 128), expires=expires,
                            connections=row['connections'], enabled=row['enabled'])
            result[kind].append(item)
    if any(not set(p['channels']) <= ids['channels'] for p in result['packages']):
        raise ValueError('El paquete referencia canales inexistentes.')
    if any(c['packageId'] not in ids['packages'] for c in result['clients']):
        raise ValueError('El cliente referencia un paquete inexistente.')
    return result


class Conflict(Exception):
    def __init__(self, revision):
        self.revision = revision


class Catalog:
    """One bounded catalog document, atomically replaced with revision checks."""
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute() or self.path.parent.is_symlink():
            raise ValueError('Usa una ruta absoluta de datos sin enlaces simbólicos.')
        if not self.path.parent.exists():
            self.path.parent.mkdir(mode=0o700, parents=True)
        if self.path.parent.stat().st_mode & 0o077:
            raise ValueError('El directorio de datos debe tener permisos 700.')
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError('La base de datos debe ser un archivo normal con permisos 600.')
        else:
            os.close(fd)
        with closing(self.connect()) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS catalog (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, document TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO catalog VALUES (1, 0, ?)', (json.dumps(EMPTY),))

    def connect(self):
        return sqlite3.connect(str(self.path), timeout=5, isolation_level=None)

    def read(self):
        with closing(self.connect()) as db:
            revision, raw = db.execute('SELECT revision, document FROM catalog WHERE id=1').fetchone()
        return {'revision': revision, 'catalog': json.loads(raw)}

    def write(self, data, expected):
        clean = validate_catalog(data)
        raw = json.dumps(clean, ensure_ascii=False, allow_nan=False)
        if len(raw.encode('utf-8')) > MAX_BODY:
            raise ValueError('El catálogo supera los 5 MB.')
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                revision = db.execute('SELECT revision FROM catalog WHERE id=1').fetchone()[0]
                if expected != revision:
                    raise Conflict(revision)
                db.execute('UPDATE catalog SET revision=?, document=? WHERE id=1', (revision + 1, raw))
                db.execute('COMMIT')
            except Exception:
                db.execute('ROLLBACK')
                raise
        return {'revision': revision + 1, 'catalog': clean}


def handler_class(catalog, token, origin, updates=None, maintenance_file=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'NexoCatalog'

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *_):
            pass

        def send_json(self, status, data):
            body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if 'revision' in data:
                self.send_header('ETag', '"{}"'.format(data['revision']))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            supplied = self.headers.get('Authorization', '').encode('utf-8')
            if not hmac.compare_digest(supplied, ('Bearer ' + token).encode('ascii')):
                self.send_json(401, {'error': 'unauthorized'})
                return False
            if self.headers.get('Origin') not in (None, origin):
                self.send_json(403, {'error': 'origin_denied'})
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            try:
                if self.path == '/api/v1/updates':
                    self.send_json(200, updates.status() if updates else {'state': 'unconfigured', 'available': None})
                elif self.path == '/api/v1/healthz':
                    catalog.read()
                    self.send_json(200, {'status': 'ok', 'version': VERSION})
                elif self.path == '/api/v1/catalog':
                    self.send_json(200, catalog.read())
                else:
                    self.send_json(404, {'error': 'not_found'})
            except (sqlite3.Error, OSError, ValueError):
                self.send_json(503, {'error': 'storage_unavailable'})

        def do_PUT(self):
            if not self.authorized():
                return
            if maintenance_file and Path(maintenance_file).exists():
                self.send_json(503, {'error': 'maintenance'})
                return
            if self.path != '/api/v1/catalog':
                self.send_json(404, {'error': 'not_found'})
                return
            if self.headers.get('Origin') != origin:
                self.send_json(403, {'error': 'origin_required'})
                return
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                self.send_json(400, {'error': 'content_length_required'})
                return
            if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                self.send_json(415, {'error': 'json_required'})
                return
            match = re.fullmatch(r'"(0|[1-9][0-9]{0,14})"', self.headers.get('If-Match', ''))
            if not match:
                self.send_json(428, {'error': 'revision_required'})
                return
            try:
                length = int(self.headers.get('Content-Length', ''))
                if not 0 < length <= MAX_BODY:
                    self.send_json(413, {'error': 'body_too_large'})
                    return
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError('Incomplete request')
                data = json.loads(raw.decode('utf-8'))
                self.send_json(200, catalog.write(data, int(match[1])))
            except Conflict as exc:
                self.send_json(409, {'error': 'revision_conflict', 'revision': exc.revision})
            except (ValueError, TypeError, UnicodeError, RecursionError):
                self.send_json(400, {'error': 'invalid_catalog'})
            except (sqlite3.Error, OSError):
                self.send_json(503, {'error': 'storage_unavailable'})

        def unsupported(self):
            if self.authorized():
                self.send_json(405, {'error': 'method_not_allowed'})

        def do_POST(self):
            if self.path != '/api/v1/updates/check':
                self.unsupported(); return
            if not self.authorized(): return
            if self.headers.get('Origin') != origin:
                self.send_json(403, {'error': 'origin_required'}); return
            if not updates:
                self.send_json(503, {'error': 'updates_unconfigured'}); return
            try:
                if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                    raise ValueError()
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 1024 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError()
                if json.loads(self.rfile.read(length)) != {}: raise ValueError()
            except (ValueError, TypeError, OSError):
                self.send_json(400, {'error': 'invalid_request'}); return
            self.send_json(200, updates.status(force=True))

        do_DELETE = do_PATCH = do_OPTIONS = unsupported
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    os.umask(0o077)
    try:
        token = os.environ.get('NEXO_API_TOKEN', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token):
            raise ValueError('Configura NEXO_API_TOKEN con 32 a 128 caracteres alfanuméricos, guion o guion bajo.')
        origin = os.environ.get('NEXO_API_ORIGIN', '')
        u = urlsplit(origin)
        if u.scheme != 'https' or not u.hostname or u.path or u.query or u.fragment or u.username or u.password:
            raise ValueError('NEXO_API_ORIGIN debe ser el origen HTTPS exacto, sin ruta.')
        bind = os.environ.get('NEXO_API_BIND', '127.0.0.1')
        if not ipaddress.ip_address(bind).is_loopback:
            raise ValueError('La API solo puede escuchar en loopback, detrás del proxy HTTPS autenticado.')
        port = int(os.environ.get('NEXO_API_PORT', '9109'))
        if not 1 <= port <= 65535:
            raise ValueError('Puerto de API no válido.')
        catalog = Catalog(os.environ.get('NEXO_API_DB', '/var/lib/nexo-iptv/catalog.sqlite3'))
        class Server(ThreadingHTTPServer):
            address_family = socket.AF_INET6 if ':' in bind else socket.AF_INET
            daemon_threads = True
        from updates import Manager
        root = Path(__file__).resolve().parent.parent
        updates = Manager(root/'installed.json', catalog.path.parent/'update-baseline.json') if (root/'installed.json').is_file() else None
        server = Server((bind, port), handler_class(catalog, token, origin, updates, root/'.maintenance'))
        print('Nexo Catalog ready. Loopback only; authenticated proxy required.', flush=True)
        server.serve_forever()
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
