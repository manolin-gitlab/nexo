#!/usr/bin/env python3
"""Read-only Linux telemetry agent. Python 3.8+, standard library only."""
import argparse
import datetime as dt
import hmac
import ipaddress
import json
import math
import os
from pathlib import Path
import platform
import re
import socket
import ssl
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

VERSION = '0.5.0'

def utcnow():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z')

def finite(value, minimum=0):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value >= minimum

def cpu_counters(text):
    values = [int(n) for n in text.splitlines()[0].split()[1:9]]
    if len(values) < 5:
        raise ValueError('CPU counters unavailable')
    return sum(values), values[3] + values[4]

def cpu_percent(before, after):
    total, idle = after[0] - before[0], after[1] - before[1]
    if total <= 0 or idle < 0 or idle > total:
        return None
    return round(100 * (total - idle) / total, 2)

def mem_stats(text):
    fields = {k: int(v.split()[0]) * 1024 for k, v in (line.split(':', 1) for line in text.splitlines())}
    total, available = fields['MemTotal'], fields.get('MemAvailable')
    if available is None or total <= 0:
        raise ValueError('MemAvailable unavailable')
    used = max(0, total - available)
    swap_total, swap_free = fields.get('SwapTotal'), fields.get('SwapFree')
    return {'total_bytes': total, 'used_bytes': used, 'available_bytes': available,
            'used_percent': round(100 * used / total, 2), 'shmem_bytes': fields.get('Shmem'),
            'swap_total_bytes': swap_total,
            'swap_used_bytes': max(0, swap_total - swap_free) if swap_total is not None and swap_free is not None else None}

def scheduler_stats(stat_text, load_text):
    load = [float(v) for v in load_text.split()[:3]]
    if len(load) != 3 or not all(finite(v) for v in load):
        raise ValueError('Invalid load average')
    result = {'load_average': load, 'runnable_tasks': None, 'blocked_tasks': None}
    for line in stat_text.splitlines()[1:]:
        parts = line.split()
        if len(parts) == 2 and parts[0] in {'procs_running', 'procs_blocked'}:
            value = int(parts[1])
            if value >= 0:
                result['runnable_tasks' if parts[0] == 'procs_running' else 'blocked_tasks'] = value
    return result

def cpu_waits(before, after):
    a = [int(v) for v in before.splitlines()[0].split()[1:9]]
    b = [int(v) for v in after.splitlines()[0].split()[1:9]]
    if len(a) != 8 or len(b) != 8:
        raise ValueError('CPU counters unavailable')
    total = sum(b) - sum(a)
    return {key: round(100 * (b[i] - a[i]) / total, 3) if total > 0 and 0 <= b[i] - a[i] <= total else None
            for key, i in [('iowait_percent', 4), ('steal_percent', 7)]}

def pressure_stats(text, cpu=False):
    rows = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0] not in {'some', 'full'} or (cpu and parts[0] == 'full'):
            continue
        raw = dict(p.split('=', 1) for p in parts[1:] if '=' in p)
        values = {k: float(raw[k]) for k in ('avg10', 'avg60', 'avg300')}
        total = int(raw['total'])
        if not all(finite(v) and v <= 100 for v in values.values()) or total < 0:
            raise ValueError('Invalid pressure counters')
        rows[parts[0]] = dict(values, total_us=total)
    if 'some' not in rows:
        raise ValueError('Pressure unavailable')
    return rows

def mount_table(text):
    result = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        target = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), parts[1])
        if target.startswith('/'):
            result[os.path.normpath(target)] = parts[2]
    return result

def mount_check(path, table):
    mounted = path in table if table is not None else None
    return {'path': path, 'mounted': mounted, 'filesystem': table.get(path) if mounted else None}

def disk_stats(path, table):
    st = os.statvfs(path)
    total, used = st.f_blocks * st.f_frsize, (st.f_blocks - st.f_bfree) * st.f_frsize
    parents = [p for p in (table or {}) if path == p or path.startswith(p.rstrip('/') + '/')]
    mount = max(parents, key=len) if parents else None
    filesystem = table.get(mount) if mount else None
    return {'path': path, 'total_bytes': total, 'used_bytes': used, 'available_bytes': st.f_bavail * st.f_frsize,
            'used_percent': round(used * 100 / total, 2) if total else None,
            'filesystem': filesystem, 'mount_point': mount,
            'storage_kind': 'memory' if filesystem == 'tmpfs' else 'filesystem' if filesystem else 'unknown',
            'inodes_total': st.f_files, 'inodes_free': st.f_ffree,
            'inodes_used_percent': round(100 * max(0, st.f_files - st.f_ffree) / st.f_files, 2) if st.f_files else None}

def net_counters(text):
    result = {}
    for line in text.splitlines()[2:]:
        name, rest = line.split(':', 1)
        parts = rest.split()
        result[name.strip()] = (int(parts[0]), int(parts[8]))
    return result

def net_rates(before, after, elapsed):
    if elapsed <= 0 or before is None or after[0] < before[0] or after[1] < before[1]:
        return (None, None)
    return tuple(round((after[i] - before[i]) * 8 / elapsed, 2) for i in (0, 1))

def decode_endpoint(value, ipv6=False):
    address, port = value.split(':')
    raw = bytes.fromhex(address)
    if ipv6:
        # /proc/net/tcp6 stores each native-endian 32-bit word separately.
        raw = b''.join(struct.pack('!I', int.from_bytes(raw[i:i+4], byteorder=os.sys.byteorder)) for i in range(0, 16, 4))
    else:
        raw = int(address, 16).to_bytes(4, byteorder=os.sys.byteorder)
    return socket.inet_ntop(socket.AF_INET6 if ipv6 else socket.AF_INET, raw), int(port, 16)

def tcp_peers(text, ports, ipv6=False):
    result = {}
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4 or parts[3] != '01':
            continue
        _, local_port = decode_endpoint(parts[1], ipv6)
        if local_port not in ports:
            continue
        remote_ip, _ = decode_endpoint(parts[2], ipv6)
        result[remote_ip] = result.get(remote_ip, 0) + 1
    return result

def read_sessions(path, now=None):
    if not path:
        return None, None, 'not_configured'
    try:
        p = Path(path)
        if p.stat().st_size > 5_000_000:
            raise ValueError('Sessions file too large')
        data = json.loads(p.read_text())
        timestamp = dt.datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
        age = (now or dt.datetime.now(dt.timezone.utc)) - timestamp
        if not -5 <= age.total_seconds() <= 15:
            return None, data['timestamp'], 'stale'
        rows = data['sessions']
        if not isinstance(rows, list) or len(rows) > 10000:
            raise ValueError('Invalid sessions')
        seen, sessions = set(), []
        for row in rows:
            sid = str(row['id'])[:120]
            if not sid or sid in seen:
                raise ValueError('Duplicate session')
            seen.add(sid)
            item = {'id': sid, 'client': str(row.get('client', ''))[:120],
                    'channel': str(row.get('channel', ''))[:160], 'ip': str(ipaddress.ip_address(row['ip'])),
                    'started_at': None, 'bitrate_bps': None, 'lat': None, 'lon': None, 'location': str(row.get('location', ''))[:120]}
            if row.get('started_at'):
                start = dt.datetime.fromisoformat(row['started_at'].replace('Z', '+00:00'))
                if start.tzinfo is None:
                    raise ValueError('Timezone required')
                item['started_at'] = start.isoformat()
            if finite(row.get('bitrate_bps')):
                item['bitrate_bps'] = row['bitrate_bps']
            lat, lon = row.get('lat'), row.get('lon')
            if finite(lat, -90) and lat <= 90 and finite(lon, -180) and lon <= 180:
                item.update(lat=lat, lon=lon)
            sessions.append(item)
        return sessions, data['timestamp'], 'available'
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return None, None, 'error'

def process_stat(text, page_size):
    # comm may contain spaces and parentheses; field 3 starts after the last ')'.
    head, sep, tail = text.rpartition(')')
    if not sep or '(' not in head:
        raise ValueError('Invalid process stat')
    pid_text, name = head.split('(', 1)
    values = tail.split()
    return {'pid': int(pid_text.strip()), 'name': name[:120],
            'ticks': int(values[11]) + int(values[12]), 'start': int(values[19]),
            'rss_bytes': max(0, int(values[21])) * page_size}

def process_metrics(proc, previous, elapsed, cpu_count, ticks_per_second, page_size):
    counters, rows = {}, []
    skipped = 0
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = process_stat((entry / 'stat').read_text(), page_size)
            key = (raw['pid'], raw['start'])
            before = previous.get(key)
            percent = None
            if before is not None and elapsed > 0 and raw['ticks'] >= before:
                percent = round(min(100, (raw['ticks'] - before) / ticks_per_second / elapsed / max(cpu_count, 1) * 100), 3)
            counters[key] = raw['ticks']
            rows.append({'pid': raw['pid'], 'name': raw['name'], 'cpu_percent': percent, 'rss_bytes': raw['rss_bytes']})
        except (OSError, ValueError, IndexError):
            skipped += 1
    rows.sort(key=lambda r: (-(r['cpu_percent'] or 0), -r['rss_bytes'], r['pid']))
    return rows[:200], counters, skipped, len(rows) > 200

class Collector:
    def __init__(self, proc='/proc', ports=None, interfaces=None, disks=None, sessions_file=None, name=None, capacity=None, coordinates=None, expected_mounts=None):
        self.proc = Path(proc)
        self.ports = ports or {80, 443}
        self.interfaces = interfaces
        self.disks = disks or ['/']
        self.sessions_file = sessions_file
        self.name = name or socket.gethostname()
        self.capacity = capacity
        self.coordinates = coordinates
        self.expected_mounts = expected_mounts or []
        self.prev_cpu = self.prev_net = self.prev_time = None
        self.previous_stat = None
        self.previous_processes = {}
        self.process_time = None
        self.latest = None
        self.lock = threading.Lock()

    def sample(self):
        errors = []
        now = time.monotonic()
        data = {'schema_version': 1, 'agent_version': VERSION, 'timestamp': utcnow(),
                'server': {'id': socket.gethostname(), 'name': self.name, 'os': platform.system(), 'kernel': platform.release(),
                           'cpu_count': os.cpu_count(), 'lat': self.coordinates[0] if self.coordinates else None,
                           'lon': self.coordinates[1] if self.coordinates else None},
                'cpu': {'percent': None}, 'memory': None, 'disks': [], 'network': {'interfaces': [], 'rx_bps': None, 'tx_bps': None, 'capacity_bps': self.capacity},
                'connections': {'source': 'linux_tcp', 'ports': sorted(self.ports), 'established': None, 'peers': []},
                'processes': [], 'processes_status': 'unavailable', 'processes_truncated': False,
                'pressure': {}, 'mount_checks': [],
                'sessions': None, 'sessions_status': 'not_configured', 'sessions_timestamp': None, 'errors': errors}
        try:
            stat_text = (self.proc / 'stat').read_text()
            current = cpu_counters(stat_text)
            data['cpu']['percent'] = cpu_percent(self.prev_cpu, current) if self.prev_cpu else None
            self.prev_cpu = current
            data['cpu'].update(cpu_waits(self.previous_stat, stat_text) if self.previous_stat else {'iowait_percent': None, 'steal_percent': None})
            self.previous_stat = stat_text
            data['cpu'].update(scheduler_stats(stat_text, (self.proc / 'loadavg').read_text()))
            data['server']['uptime_seconds'] = float((self.proc / 'uptime').read_text().split()[0])
        except (OSError, ValueError, IndexError):
            errors.append('cpu_unavailable')
        try:
            data['memory'] = mem_stats((self.proc / 'meminfo').read_text())
        except (OSError, ValueError, KeyError):
            errors.append('memory_unavailable')
        for resource in ('cpu', 'memory', 'io'):
            try:
                data['pressure'][resource] = pressure_stats((self.proc / 'pressure' / resource).read_text(), cpu=resource == 'cpu')
            except (OSError, ValueError, KeyError):
                data['pressure'][resource] = None
        try:
            table = mount_table((self.proc / 'self/mounts').read_text())
            if not table:
                raise ValueError('Empty mount table')
        except (OSError, ValueError):
            table = None
        data['mount_checks'] = [mount_check(path, table) for path in self.expected_mounts]
        for path in self.disks:
            # An expected mount that disappeared must not become a healthy root-disk reading.
            if path in self.expected_mounts and (table is None or path not in table):
                continue
            try:
                data['disks'].append(disk_stats(path, table))
            except OSError:
                errors.append('disk_unavailable:' + path)
        try:
            current_net = net_counters((self.proc / 'net/dev').read_text())
            interfaces = self.interfaces
            if not interfaces:
                routes = (self.proc / 'net/route').read_text().splitlines()[1:]
                interfaces = sorted({line.split()[0] for line in routes if line.split()[1] == '00000000' and int(line.split()[3], 16) & 1})
                if not interfaces:
                    interfaces = [i for i in current_net if i != 'lo' and not i.startswith(('veth', 'docker', 'br-'))]
            if not interfaces:
                errors.append('network_unavailable')
            for iface in interfaces:
                if iface not in current_net:
                    errors.append('interface_unavailable:' + iface)
                    continue
                before = self.prev_net.get(iface) if self.prev_net else None
                rx, tx = net_rates(before, current_net[iface], now - self.prev_time if self.prev_time else 0)
                data['network']['interfaces'].append({'name': iface, 'rx_bps': rx, 'tx_bps': tx,
                                                     'rx_bytes': current_net[iface][0], 'tx_bytes': current_net[iface][1]})
            items = data['network']['interfaces']
            if items and all(i['rx_bps'] is not None and i['tx_bps'] is not None for i in items):
                data['network'].update(rx_bps=sum(i['rx_bps'] for i in items), tx_bps=sum(i['tx_bps'] for i in items))
            self.prev_net, self.prev_time = current_net, now
        except (OSError, ValueError, IndexError):
            errors.append('network_unavailable')
        peers = {}
        readable = 0
        for path, ipv6 in [('net/tcp', False), ('net/tcp6', True)]:
            try:
                counts = tcp_peers((self.proc / path).read_text(), self.ports, ipv6)
                readable += 1
                for ip, count in counts.items():
                    peers[ip] = peers.get(ip, 0) + count
            except (OSError, ValueError, IndexError):
                errors.append('tcp6_unavailable' if ipv6 else 'tcp4_unavailable')
        if readable:
            data['connections'].update(established=sum(peers.values()), peers=[{'ip': ip, 'count': count} for ip, count in sorted(peers.items(), key=lambda v: (-v[1], v[0]))[:1000]])
            data['connections']['truncated'] = len(peers) > 1000
        data['sessions'], data['sessions_timestamp'], data['sessions_status'] = read_sessions(self.sessions_file)
        try:
            procs, counters, skipped, truncated = process_metrics(self.proc, self.previous_processes,
                now - self.process_time if self.process_time is not None else 0,
                os.cpu_count() or 1, os.sysconf('SC_CLK_TCK'), os.sysconf('SC_PAGE_SIZE'))
            data['processes'] = procs
            data['processes_status'] = 'partial' if skipped else 'available'
            data['processes_truncated'] = truncated
            self.previous_processes, self.process_time = counters, now
        except (OSError, ValueError):
            errors.append('processes_unavailable')
        with self.lock:
            self.latest = data
        return data

    def get(self):
        with self.lock:
            return self.latest

    def run(self):
        while True:
            self.sample()
            time.sleep(2)


def handler_class(collector, token, origin):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'NexoMonitor'
        def log_message(self, *_):
            pass  # Do not log IPs, tokens or snapshots.
        def respond(self, status, payload=None):
            self.send_response(status)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if self.headers.get('Origin') == origin:
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
            if payload is not None:
                content = json.dumps(payload, allow_nan=False).encode()
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            if payload is not None:
                self.wfile.write(content)
        def do_OPTIONS(self):
            headers = {s.strip().lower() for s in self.headers.get('Access-Control-Request-Headers', '').split(',') if s.strip()}
            if self.headers.get('Origin') != origin or self.headers.get('Access-Control-Request-Method') != 'GET' or not headers <= {'authorization'}:
                self.respond(403, {'error': 'origin_or_method_denied'})
                return
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Access-Control-Allow-Methods', 'GET')
            self.send_header('Access-Control-Allow-Headers', 'Authorization')
            self.send_header('Access-Control-Max-Age', '300')
            self.send_header('Vary', 'Origin')
            self.end_headers()
        def do_GET(self):
            if self.headers.get('Origin') not in (None, origin):
                self.respond(403, {'error': 'origin_denied'})
                return
            if not hmac.compare_digest(self.headers.get('Authorization', '').encode('utf-8'), ('Bearer ' + token).encode('ascii')):
                self.respond(401, {'error': 'unauthorized'})
                return
            if self.path == '/healthz':
                self.respond(200, {'status': 'ok', 'version': VERSION})
            elif self.path == '/v1/snapshot':
                self.respond(200, collector.get())
            else:
                self.respond(404, {'error': 'not_found'})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='Print a real snapshot of this Linux host; do not serve HTTP')
    args = parser.parse_args()
    if platform.system() != 'Linux':
        parser.error('Linux is required')
    try:
        ports = {int(p.strip()) for p in os.getenv('NEXO_STREAM_PORTS', '80,443').split(',')}
        if not ports or not all(1 <= p <= 65535 for p in ports):
            raise ValueError('Invalid ports')
        interfaces = [s.strip() for s in os.getenv('NEXO_INTERFACES', '').split(',') if s.strip()] or None
        disks = [s.strip() for s in os.getenv('NEXO_DISKS', '/').split(',') if s.strip()]
        expected_mounts = [s.strip() for s in os.getenv('NEXO_EXPECTED_MOUNTS', '').split(',') if s.strip()]
        if len(disks) > 100 or len(expected_mounts) > 100 or any(not p.startswith('/') for p in disks + expected_mounts):
            raise ValueError('Use at most 100 absolute paths for disks and expected mounts')
        disks = list(dict.fromkeys(os.path.normpath(p) for p in disks))
        expected_mounts = list(dict.fromkeys(os.path.normpath(p) for p in expected_mounts))
        capacity = float(os.environ['NEXO_CAPACITY_BPS']) if os.getenv('NEXO_CAPACITY_BPS') else None
        if capacity is not None and (not finite(capacity) or capacity <= 0):
            raise ValueError('Invalid capacity')
        coordinates = None
        if os.getenv('NEXO_LAT') or os.getenv('NEXO_LON'):
            coordinates = (float(os.environ['NEXO_LAT']), float(os.environ['NEXO_LON']))
            if not all(math.isfinite(v) for v in coordinates) or not -90 <= coordinates[0] <= 90 or not -180 <= coordinates[1] <= 180:
                raise ValueError('Invalid coordinates')
        collector = Collector(ports=ports, interfaces=interfaces, disks=disks, sessions_file=os.getenv('NEXO_SESSIONS_FILE'),
                              name=os.getenv('NEXO_SERVER_NAME'), capacity=capacity, coordinates=coordinates, expected_mounts=expected_mounts)
        if args.once:
            collector.sample()
            time.sleep(1)
            print(json.dumps(collector.sample(), indent=2, allow_nan=False))
            return
        token = os.getenv('NEXO_TOKEN', '')
        if len(token) < 32 or len(token) > 4096 or not token.isascii() or any(c.isspace() for c in token):
            raise ValueError('Set NEXO_TOKEN to an ASCII random token of at least 32 characters')
        origin = os.getenv('NEXO_ALLOWED_ORIGIN', '')
        parsed = urlsplit(origin)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('NEXO_ALLOWED_ORIGIN must be the exact HTTPS panel origin, without trailing slash')
        bind = os.getenv('NEXO_BIND', '127.0.0.1')
        cert, key = os.getenv('NEXO_TLS_CERT'), os.getenv('NEXO_TLS_KEY')
        if not ipaddress.ip_address(bind).is_loopback and not (cert and key):
            raise ValueError('Non-loopback binding requires NEXO_TLS_CERT and NEXO_TLS_KEY. Use the loopback reverse-proxy setup by default.')
        collector.sample()
        threading.Thread(target=collector.run, daemon=True).start()
        class Server(ThreadingHTTPServer):
            address_family = socket.AF_INET6 if ':' in bind else socket.AF_INET
            daemon_threads = True
        server = Server((bind, int(os.getenv('NEXO_PORT', '9108'))), handler_class(collector, token, origin))
        if cert and key:
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.minimum_version = ssl.TLSVersion.TLSv1_2
            tls.load_cert_chain(cert, key)
            server.socket = tls.wrap_socket(server.socket, server_side=True)
        print('Nexo Monitor ready. Read-only telemetry; authentication required.', flush=True)
        server.serve_forever()
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))

if __name__ == '__main__':
    main()
