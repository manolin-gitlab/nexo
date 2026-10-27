"""Experimental on-demand HLS relay for the SSH trial. Never run as a public edge.

One FFmpeg per requested channel, shared segments, no encoding and no source URLs
in exported playlists. The catalog is authoritative; playback leases are volatile.
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


class Denied(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


def channel_key(channel_id):
    return hashlib.sha256(channel_id.encode()).hexdigest()


def encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def ffmpeg_command(binary, url, folder, segment_seconds=4):
    """Fixed argv; no shell, arbitrary flags, local input protocols or transcoding."""
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Solo se admiten fuentes HTTP(S) sin credenciales de usuario.')
    return [str(binary), '-nostdin', '-hide_banner', '-loglevel', 'error',
            '-rw_timeout', '10000000', '-protocol_whitelist', 'http,https,tcp,tls,crypto',
            '-i', url, '-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy', '-sn', '-dn',
            '-f', 'hls', '-hls_time', str(segment_seconds), '-hls_list_size', '6',
            '-hls_delete_threshold', '2', '-hls_start_number_source', 'epoch_us',
            '-hls_flags', 'delete_segments+temp_file+omit_endlist',
            '-hls_segment_filename', str(folder/'seg-%09d.ts'), str(folder/'index.m3u8')]


class Relay:
    def __init__(self, catalog, cache_parent, secret, *, binary='/usr/bin/ffmpeg',
                 max_channels=8, max_sessions=64, cache_bytes=512*1024**2,
                 min_free_bytes=256*1024**2, lease_seconds=30, idle_seconds=30,
                 stall_seconds=30, segment_seconds=4, popen=subprocess.Popen,
                 clock=time.monotonic, wall=time.time, autostart=True):
        if not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', secret):
            raise ValueError('Se necesita una clave aleatoria de reproducción de al menos 256 bits.')
        if not 1 <= max_channels <= 32 or not 1 <= max_sessions <= 256:
            raise ValueError('Límites experimentales: 32 canales y 256 sesiones como máximo.')
        if cache_bytes < 1024**2 or min_free_bytes < 0 or not 1 <= segment_seconds <= 10:
            raise ValueError('Límites de almacenamiento o segmentos no válidos.')
        parent = Path(cache_parent)
        if not parent.is_absolute() or parent.is_symlink() or not parent.is_dir():
            raise ValueError('La caché debe estar dentro de un directorio privado existente.')
        info = parent.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('El directorio de caché debe pertenecer al servicio y tener permisos 700.')
        if not Path(binary).is_file() or not os.access(binary, os.X_OK):
            raise ValueError('Falta FFmpeg del sistema. Instala el paquete ffmpeg de Debian.')
        self.catalog, self.secret, self.binary = catalog, secret.encode(), binary
        self.max_channels, self.max_sessions = max_channels, max_sessions
        self.cache_bytes, self.min_free_bytes = cache_bytes, min_free_bytes
        self.lease_seconds, self.idle_seconds, self.stall_seconds = lease_seconds, idle_seconds, stall_seconds
        self.segment_seconds, self.popen, self.clock, self.wall = segment_seconds, popen, clock, wall
        self.temp = tempfile.TemporaryDirectory(prefix='nexo-hls-', dir=parent)
        self.root = Path(self.temp.name)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.workers, self.sessions = {}, {}
        self.view, self.loaded_at = None, float('-inf')
        self.catalog_reads = 0
        self.used_bytes, self.storage_blocked, self.last_error = 0, False, None
        self.thread = None
        if autostart:
            self.thread = threading.Thread(target=self.run, daemon=True, name='nexo-relay')
            self.thread.start()

    def refresh(self):
        """At most two catalog reads/second, independent of segment request count.

        No stale authorization fallback if the next catalog read fails.
        The returned indexes are private and only used while holding self.lock.
        """
        if self.view is None or self.clock()-self.loaded_at >= .5:
            data = self.catalog.read()['catalog']
            self.view = {'clients': {r['id']: r for r in data['clients']},
                         'packages': {r['id']: frozenset(r['channels']) for r in data['packages']},
                         'channels': {channel_key(r['id']): r for r in data['channels']}}
            self.loaded_at = self.clock()
            self.catalog_reads += 1
        return self.view

    def client(self, client_id):
        row = self.refresh()['clients'].get(client_id)
        today = dt.datetime.fromtimestamp(self.wall(), ZoneInfo('America/Guayaquil')).date().isoformat()
        if not row or not row['enabled'] or row['expires'] < today:
            raise Denied(403, 'client_inactive')
        return row

    def allowed(self, client_id, key):
        client = self.client(client_id)
        view = self.refresh()
        channel = view['channels'].get(key)
        if not channel or not channel['enabled'] or channel['id'] not in view['packages'].get(client['packageId'], ()):
            raise Denied(403, 'channel_denied')
        return client, channel

    def issue(self, client_id):
        with self.lock:
            self.client(client_id)
            payload = encode(json.dumps({'c': client_id, 'e': int(self.wall())+7*86400,
                                         'n': secrets.token_urlsafe(12)}, separators=(',', ':')).encode())
            return payload+'.'+encode(hmac.digest(self.secret, payload.encode(), 'sha256'))

    def verify(self, token):
        try:
            if len(token) > 1024: raise ValueError()
            payload, signature = token.split('.')
            if not re.fullmatch(r'[A-Za-z0-9_-]+', payload): raise ValueError()
            if not hmac.compare_digest(signature, encode(hmac.digest(self.secret, payload.encode(), 'sha256'))):
                raise ValueError()
            obj = json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
            if type(obj.get('e')) is not int or not isinstance(obj.get('c'), str) or obj['e'] <= self.wall():
                raise ValueError()
            return obj['c']
        except (ValueError, TypeError, KeyError, UnicodeError, AttributeError):
            raise Denied(403, 'invalid_playback_token') from None

    def playlist(self, client_id, origin):
        with self.lock:
            token = self.issue(client_id)
            client = self.client(client_id)
            clean = lambda v: re.sub(r'[\x00-\x1f"\x7f]', ' ', v)
            lines = ['#EXTM3U']
            for key, ch in self.refresh()['channels'].items():
                if ch['enabled'] and ch['id'] in self.view['packages'].get(client['packageId'], ()):
                    lines += ['#EXTINF:-1 tvg-name="{}" group-title="{}",{}'.format(
                        clean(ch['name']), clean(ch['group']), clean(ch['name'])),
                        origin+'/tv/'+token+'/'+key+'/master.m3u8']
            if len(lines) == 1: raise Denied(409, 'empty_package')
            return '\n'.join(lines)+'\n'

    def prune(self):
        now = self.clock()
        self.sessions = {key: row for key, row in self.sessions.items() if now-row['seen'] < self.lease_seconds}

    def open_session(self, token, key, ip):
        with self.lock:
            if self.stop_event.is_set(): raise Denied(503, 'relay_stopping')
            client_id = self.verify(token)
            client, _ = self.allowed(client_id, key)
            self.prune()
            if (len(self.sessions) >= self.max_sessions or
                    sum(s['client_id'] == client_id for s in self.sessions.values()) >= client['connections']):
                raise Denied(429, 'session_limit')
            if key not in self.workers:
                if self.storage_blocked or len(self.workers) >= self.max_channels:
                    raise Denied(503, 'channel_capacity')
                self.workers[key] = {'process': None, 'folder': None, 'state': 'starting',
                                     'failures': 0, 'retry_at': 0, 'demand': self.clock(), 'url': None}
            sid = secrets.token_urlsafe(24)
            self.sessions[sid] = {'client_id': client_id, 'key': key, 'token_hash': hashlib.sha256(token.encode()).digest(),
                                  'seen': self.clock(), 'started': self.wall(), 'ip': ip}
            self.workers[key]['demand'] = self.clock()
            return sid

    def resolve(self, token, key, sid, filename):
        """Authorize every playlist AND segment. Return an open fd, not buffered video."""
        with self.lock:
            client_id = self.verify(token)
            self.allowed(client_id, key)
            self.prune()
            session = self.sessions.get(sid)
            if (not session or session['client_id'] != client_id or session['key'] != key or
                    not hmac.compare_digest(session['token_hash'], hashlib.sha256(token.encode()).digest())):
                raise Denied(403, 'session_expired')
            if filename != 'index.m3u8' and not re.fullmatch(r'seg-[0-9]{9,16}\.ts', filename):
                raise Denied(404, 'not_found')
            worker = self.workers.get(key)
            if not worker or worker['state'] != 'live' or not worker['folder']:
                raise Denied(503, 'source_starting_or_unavailable')
            try:
                fd = os.open(worker['folder']/filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                info = os.fstat(fd)
                maximum = 65536 if filename.endswith('.m3u8') else min(self.cache_bytes, 64*1024**2)
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= maximum:
                    os.close(fd); raise Denied(503, 'invalid_media_file')
            except FileNotFoundError:
                raise Denied(404, 'segment_expired') from None
            session['seen'] = worker['demand'] = self.clock()
            return fd, info.st_size

    def close_session(self, sid):
        with self.lock:
            return self.sessions.pop(sid, None) is not None

    def wait_ready(self, key, timeout=12):
        until = time.monotonic()+timeout
        while time.monotonic() < until and not self.stop_event.is_set():
            with self.lock:
                worker = self.workers.get(key)
                if worker and worker['state'] == 'live': return True
            self.stop_event.wait(.1)
        return False

    def handle(self, handler, host):
        return media_request(handler, self, host)

    def stop_worker(self, worker):
        p = worker['process']
        if p is not None:
            if p.poll() is None:
                try: os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError: pass
                try: p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try: os.killpg(p.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    p.wait(timeout=2)
            else: p.wait()
        worker['process'] = None
        if worker['folder'] is not None:
            shutil.rmtree(worker['folder'])
        worker['folder'] = None

    def failed(self, worker, code):
        self.stop_worker(worker)
        worker['failures'] += 1
        worker['retry_at'] = self.clock()+min(60, 2**min(worker['failures'], 6))
        worker['state'] = code

    def tick(self):
        """One supervisor; bounds process count, retention, restart rate and idle time."""
        with self.lock:
            self.prune()
            if not self.workers:
                self.used_bytes = 0
                return
            view = self.refresh()
            for sid, row in list(self.sessions.items()):
                try: self.allowed(row['client_id'], row['key'])
                except Denied: self.sessions.pop(sid)
            files = list(self.root.glob('*/*'))
            self.used_bytes = 0
            for path in files:
                try:
                    info = path.stat(follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode): self.used_bytes += info.st_size
                except FileNotFoundError: pass  # FFmpeg rotates segments atomically.
            self.storage_blocked = (self.used_bytes >= self.cache_bytes or
                                    shutil.disk_usage(self.root).free < self.min_free_bytes)
            now = self.clock()
            for key, worker in list(self.workers.items()):
                ch = view['channels'].get(key)
                demanded = any(s['key'] == key for s in self.sessions.values())
                if demanded: worker['demand'] = now
                if not ch or not ch['enabled'] or (not demanded and now-worker['demand'] >= self.idle_seconds):
                    self.stop_worker(worker); del self.workers[key]; continue
                if self.storage_blocked:
                    self.failed(worker, 'storage_limit'); continue
                p = worker['process']
                if p and (p.poll() is not None or ch['url'] != worker['url']):
                    self.failed(worker, 'source_failed'); continue
                if p:
                    playlist = worker['folder']/'index.m3u8'
                    try: age = self.wall()-playlist.stat().st_mtime
                    except FileNotFoundError: age = now-worker['started']
                    if age > self.stall_seconds:
                        self.failed(worker, 'source_stalled')
                    elif playlist.is_file():
                        worker['state'] = 'live'
                        if now-worker['started'] >= 60: worker['failures'] = 0
                    continue
                if not demanded or now < worker['retry_at']: continue
                folder = Path(tempfile.mkdtemp(prefix='ch-', dir=self.root))
                worker.update(folder=folder, started=now, url=ch['url'], state='starting')
                try:
                    worker['process'] = self.popen(ffmpeg_command(self.binary, ch['url'], folder, self.segment_seconds),
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
                except OSError:
                    self.failed(worker, 'spawn_failed')
            self.last_error = None

    def run(self):
        while not self.stop_event.is_set():
            try: self.tick()
            except Exception:
                # Fail closed on catalog/storage/supervisor errors; never reveal URLs.
                with self.lock:
                    self.last_error = 'supervisor_unavailable'
                    self.view = None
                    for worker in self.workers.values():
                        try: self.stop_worker(worker)
                        except OSError: pass
                        worker['state'] = 'supervisor_unavailable'
            self.stop_event.wait(.5)

    def status(self):
        with self.lock:
            self.prune()
            return {'enabled': True, 'experimental': True, 'mode': 'copy_on_demand',
                    'max_channels': self.max_channels, 'max_sessions': self.max_sessions,
                    'cache_bytes': self.cache_bytes, 'used_bytes': self.used_bytes,
                    'storage_blocked': self.storage_blocked, 'error': self.last_error,
                    'lease_seconds': self.lease_seconds, 'idle_seconds': self.idle_seconds,
                    'workers': [{'channel': self.view['channels'].get(key, {}).get('name', 'Canal'),
                                 'state': w['state'], 'pid': w['process'].pid if w['process'] else None,
                                 'failures': w['failures']} for key,w in self.workers.items()] if self.view else [],
                    'sessions': [{'id': sid, 'client': self.view['clients'].get(s['client_id'], {}).get('name', 'Cliente'),
                                  'channel': self.view['channels'].get(s['key'], {}).get('name', 'Canal'),
                                  'ip': s['ip'], 'started': dt.datetime.fromtimestamp(s['started'], dt.timezone.utc).isoformat()}
                                 for sid,s in self.sessions.items()] if self.view else []}

    def close(self):
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=10)
        with self.lock:
            for worker in self.workers.values(): self.stop_worker(worker)
            self.workers.clear(); self.sessions.clear()
            self.temp.cleanup()


def media_request(handler, relay, host):
    """Trial only: token authorization replaces admin Basic for /tv/, never /api/."""
    if handler.headers.get_all('Host') != [host]:
        handler.send_json(403, {'error': 'use_localhost_tunnel'}); return
    match = re.fullmatch(r'/tv/([A-Za-z0-9_.-]{1,1024})/([0-9a-f]{64})/(?:master\.m3u8|([A-Za-z0-9_-]{32})/(index\.m3u8|seg-[0-9]{9,16}\.ts))', handler.path)
    if not match or not relay:
        handler.send_json(404, {'error': 'not_found'}); return
    fd, sent = None, False
    try:
        token, key, sid, filename = match.groups()
        if sid is None:
            sid = relay.open_session(token, key, handler.client_address[0])
            if not relay.wait_ready(key):
                relay.close_session(sid)
                raise Denied(503, 'source_starting_or_unavailable')
            # Relative redirect makes subsequent playlist and segment URLs keep the lease.
            handler.send_response(302)
            handler.send_header('Location', sid+'/index.m3u8')
            handler.send_header('Cache-Control', 'no-store')
            handler.send_header('Referrer-Policy', 'no-referrer')
            handler.send_header('Content-Length', '0'); handler.end_headers(); return
        fd, size = relay.resolve(token, key, sid, filename)
        handler.send_response(200); sent = True
        handler.send_header('Content-Type', 'application/vnd.apple.mpegurl' if filename.endswith('.m3u8') else 'video/mp2t')
        handler.send_header('Content-Length', str(size))
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.send_header('Referrer-Policy', 'no-referrer')
        handler.end_headers()
        # Bounded transfer buffer; do not load a video segment into Python memory.
        with os.fdopen(fd, 'rb') as source:
            fd = None
            remaining = size
            while remaining:
                chunk = source.read(min(65536, remaining))
                if not chunk: break
                handler.wfile.write(chunk); remaining -= len(chunk)
    except Denied as exc:
        handler.send_json(exc.status, {'error': exc.code})
    except (OSError, ValueError):
        if not sent: handler.send_json(503, {'error': 'relay_unavailable'})
    finally:
        if fd is not None: os.close(fd)
