#!/usr/bin/env python3
"""Initial Nexo panel, catalog API and read-only monitor installation on Debian 13. No pip."""
import argparse
import grp
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import pwd
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

VERSION = '0.6.1'
ACCOUNT = 'nexo-monitor'
ACCOUNTS = (ACCOUNT, 'nexo-api')
TARGETS = ['/opt/nexo-iptv', '/etc/nexo-iptv', '/etc/nexo-monitor.env',
           '/etc/systemd/system/nexo-monitor.service', '/etc/systemd/system/nexo-api.service',
           '/etc/nexo-api.env', '/var/lib/nexo-iptv',
           '/etc/nginx/sites-available/nexo-iptv.conf', '/etc/nginx/sites-enabled/nexo-iptv.conf']
TOOLS = {'nginx': '/usr/sbin/nginx', 'openssl': '/usr/bin/openssl',
         'systemctl': '/usr/bin/systemctl', 'useradd': '/usr/sbin/useradd',
         'userdel': '/usr/sbin/userdel', 'groupdel': '/usr/sbin/groupdel'}


def run(argv, input_text=None, check=True):
    p = subprocess.run(argv, input=input_text, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=30, check=False)
    if check and p.returncode:
        # Never echo stdin, credentials, hashes or configuration contents.
        raise RuntimeError('Falló {} (código {}).'.format(Path(argv[0]).name, p.returncode))
    return p


def debian13(text):
    values = {}
    for line in text.splitlines():
        if '=' in line:
            k, v = line.split('=', 1); values[k] = v.strip().strip('"')
    return values.get('ID') == 'debian' and values.get('VERSION_ID', '').split('.')[0] == '13'


def make_plan(origin, cert, key, username='administrador'):
    u = urlsplit(origin)
    host = u.hostname or ''
    if (u.scheme != 'https' or not host or u.username or u.password or u.path not in ('', '/')
            or u.query or u.fragment or len(host) > 253
            or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in host.split('.'))):
        raise ValueError('Usa un origen HTTPS con nombre DNS, sin usuario, ruta o parámetros.')
    port = u.port or 443
    if not 1 <= port <= 65535 or port in (9108, 9109):
        raise ValueError('Puerto HTTPS no válido o en conflicto con servicios locales (9108/9109).')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,32}', username):
        raise ValueError('Nombre de administrador no válido.')
    for path in (cert, key):
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(path)) or '..' in Path(path).parts:
            raise ValueError('Usa rutas absolutas de certificado/clave sin espacios ni caracteres especiales.')
    return {'origin': 'https://' + host + (':' + str(port) if port != 443 else ''),
            'host': host, 'port': port, 'cert': str(cert), 'key': str(key), 'username': username}


def verify_payload(bundle):
    manifest_file = bundle / 'manifest.json'
    if manifest_file.stat().st_size > 100_000:
        raise ValueError('Manifiesto demasiado grande.')
    manifest = json.loads(manifest_file.read_text())
    files = manifest.get('files')
    if not isinstance(files, dict) or not 1 <= len(files) <= 500:
        raise ValueError('Manifiesto no válido.')
    actual = set(); total = 0
    for p in (bundle / 'payload').rglob('*'):
        if p.is_symlink():
            raise ValueError('El paquete contiene un enlace simbólico.')
        if p.is_file():
            actual.add(p.relative_to(bundle).as_posix())
    if actual != set(files):
        raise ValueError('El contenido del paquete no coincide con el manifiesto.')
    for name, digest in files.items():
        rel = Path(name)
        if rel.is_absolute() or '..' in rel.parts or rel.parts[0] != 'payload':
            raise ValueError('Ruta de paquete no válida.')
        p = bundle / rel; size = p.stat().st_size; total += size
        if size > 10_000_000 or total > 50_000_000 or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            raise ValueError('Fallo de integridad del paquete.')
    for required in ('payload/panel/index.html', 'payload/agent/nexo_monitor.py', 'payload/agent/nexo-monitor.service',
                     'payload/installed.json','payload/backend/updates.py', 'payload/backend/nexo_api.py', 'payload/backend/nexo-api.service', 'payload/panel/catalog-client.js'):
        if required not in files:
            raise ValueError('Faltan componentes obligatorios.')
    return len(files)


def check_tls(plan, runner=run):
    if not Path(plan['cert']).is_file() or not Path(plan['key']).is_file():
        raise ValueError('No se encontró el certificado o su clave privada.')
    openssl = TOOLS['openssl']
    runner([openssl, 'x509', '-in', plan['cert'], '-noout', '-checkend', '0'])
    runner([openssl, 'verify', '-purpose', 'sslserver', '-verify_hostname', plan['host'],
            '-untrusted', plan['cert'], plan['cert']])
    certificate = runner([openssl, 'x509', '-in', plan['cert'], '-pubkey', '-noout']).stdout.strip()
    public_key = runner([openssl, 'pkey', '-in', plan['key'], '-passin', 'pass:', '-pubout']).stdout.strip()
    if not certificate or certificate != public_key:
        raise ValueError('El certificado y la clave privada no coinciden.')


def port_free(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, port))


def preflight(bundle, plan=None):
    if not debian13(Path('/etc/os-release').read_text()):
        raise ValueError('Se requiere Debian 13; no se realizará ninguna instalación en este sistema.')
    if sys.version_info < (3, 13):
        raise ValueError('Se requiere el Python 3.13 o superior de Debian 13.')
    if platform.machine() not in ('x86_64', 'aarch64'):
        raise ValueError('Objetivos iniciales: Debian 13 amd64 o arm64.')
    if not Path('/run/systemd/system').is_dir():
        raise ValueError('Se necesita un sistema iniciado con systemd (VM o servidor).')
    for name, path in TOOLS.items():
        if not os.access(path, os.X_OK):
            raise ValueError('Falta {}. Ejecuta primero --install-deps.'.format(name))
    count = verify_payload(bundle)
    for path in TARGETS + [prefix + '/' + name + '.service' for prefix in ('/usr/lib/systemd/system', '/lib/systemd/system') for name in ACCOUNTS]:
        if os.path.lexists(path):
            raise ValueError('Ya existe {}. Este instalador inicial no sobrescribe instalaciones.'.format(path))
    for account in ACCOUNTS:
        for lookup in (pwd.getpwnam, grp.getgrnam):
            try:
                lookup(account)
            except KeyError:
                continue
            raise ValueError('La cuenta o grupo '+account+' ya existe. Revisa la instalación anterior.')
    grp.getgrnam('www-data')
    for directory in ('/etc/nginx/sites-available', '/etc/nginx/sites-enabled', '/opt', '/var/lib', '/etc/systemd/system'):
        if not Path(directory).is_dir() or Path(directory).is_symlink():
            raise ValueError('La ruta requerida no existe o no es un directorio normal: ' + directory)
    if any(p.name != 'default' for p in Path('/etc/nginx/sites-enabled').iterdir()):
        raise ValueError('Hay otros sitios Nginx habilitados. Usa una VM dedicada para esta instalación inicial.')
    run([TOOLS['nginx'], '-t'])
    run([TOOLS['systemctl'], 'is-active', '--quiet', 'nginx'])
    port_free('127.0.0.1', 9108)
    port_free('127.0.0.1', 9109)
    if plan:
        check_tls(plan)
        port_free('0.0.0.0', plan['port'])
    return count


def nginx_config(plan):
    return '''# Managed by the initial Nexo Debian 13 installer.
server {
    listen %(port)s ssl;
    server_name %(host)s;
    ssl_certificate %(cert)s;
    ssl_certificate_key %(key)s;
    ssl_protocols TLSv1.2 TLSv1.3;
    root /opt/nexo-iptv/panel;
    index index.html;
    access_log off;
    server_tokens off;
    auth_basic "Nexo IPTV";
    auth_basic_user_file /etc/nexo-iptv/htpasswd;
    add_header X-Content-Type-Options nosniff always;
    location / { try_files $uri $uri/ =404; }
    location = /index.html { add_header Cache-Control "no-cache" always; }
    location = /v1/snapshot {
        auth_basic off;
        proxy_pass http://127.0.0.1:9108;
        proxy_set_header Authorization $http_authorization;
        proxy_set_header Origin $http_origin;
        proxy_read_timeout 5s;
        proxy_buffering off;
    }
    location = /healthz {
        auth_basic off;
        proxy_pass http://127.0.0.1:9108;
        proxy_set_header Authorization $http_authorization;
        proxy_set_header Origin $http_origin;
        proxy_read_timeout 5s;
    }
    location ^~ /api/v1/ {
        # Inherits HTTP Basic; the browser never receives the internal API token.
        include /etc/nexo-iptv/api-proxy.conf;
        proxy_pass http://127.0.0.1:9109;
        proxy_set_header Origin $http_origin;
        proxy_set_header Cookie "";
        proxy_read_timeout 10s;
        proxy_buffering off;
        client_max_body_size 5000000;
        client_body_timeout 10s;
    }
    location ~ /\\. { deny all; }
}
''' % plan


class Transaction:
    """Own only newly created Nexo paths. Never overwrite or back up another app."""
    def __init__(self, root=Path('/')):
        self.root = Path(root); self.created = []

    def path(self, absolute):
        return self.root / absolute.lstrip('/')

    def mkdir(self, absolute, mode=0o755):
        p = self.path(absolute); p.mkdir(mode=mode); self.created.append(p)
        os.chmod(p, mode); return p

    def write(self, absolute, text, mode=0o644):
        p = self.path(absolute)
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        self.created.append(p)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.chmod(p, mode); return p

    def copytree(self, source, absolute):
        p = self.path(absolute)
        if os.path.lexists(p):
            raise FileExistsError(str(p))
        p.mkdir(); self.created.append(p)
        shutil.copytree(source, p, dirs_exist_ok=True)
        for entry in [p] + list(p.rglob('*')):
            os.chmod(entry, 0o755 if entry.is_dir() else 0o644)

    def symlink(self, target, absolute):
        p = self.path(absolute); p.symlink_to(target); self.created.append(p)

    def rollback(self):
        errors = []
        for p in reversed(self.created):
            try:
                if p.is_dir() and not p.is_symlink(): shutil.rmtree(p)
                else: p.unlink(missing_ok=True)
            except OSError:
                errors.append(str(p))
        return errors


def service_health(port, path, token):
    for attempt in range(5):
        c = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
        try:
            c.request('GET', path, headers={'Authorization': 'Bearer ' + token})
            r = c.getresponse(); data = r.read(4096)
            if r.status == 200 and json.loads(data).get('status') == 'ok':
                return
        except (OSError, ValueError, http.client.HTTPException):
            pass
        finally:
            c.close()
        time.sleep(.2)
    raise RuntimeError('Un servicio Nexo no respondió correctamente; se revierte la instalación.')


def services_health(token, api_token):
    service_health(9108, '/healthz', token)
    service_health(9109, '/api/v1/healthz', api_token)


def install(bundle, plan, tx=None, runner=run, health=services_health, nginx_gid=None, api_uid=None):
    tx = tx or Transaction()
    nginx_gid = grp.getgrnam('www-data').gr_gid if nginx_gid is None else nginx_gid
    users_created, services_attempted = [], []
    reload_attempted = False
    try:
        tx.mkdir('/opt/nexo-iptv')
        tx.write('/opt/nexo-iptv/installed.json',(bundle/'payload/installed.json').read_text())
        for component in ('panel', 'agent', 'backend'):
            tx.copytree(bundle / 'payload' / component, '/opt/nexo-iptv/' + component)
        state_dir = tx.mkdir('/var/lib/nexo-iptv', 0o700)
        private_dir = tx.mkdir('/etc/nexo-iptv', 0o750)
        os.chown(private_dir, 0, nginx_gid)
        token, api_token, password = secrets.token_hex(32), secrets.token_hex(32), secrets.token_urlsafe(24)
        password_hash = runner([TOOLS['openssl'], 'passwd', '-6', '-stdin'], input_text=password + '\n').stdout.strip()
        if not password_hash.startswith('$6$') or '\n' in password_hash:
            raise RuntimeError('No se pudo generar el hash de acceso.')
        auth_file = tx.write('/etc/nexo-iptv/htpasswd', plan['username'] + ':' + password_hash + '\n', 0o640)
        os.chown(auth_file, 0, nginx_gid)
        tx.write('/etc/nexo-iptv/access.txt', 'URL: '+plan['origin']+'\nUsuario web: '+plan['username']+
                 '\nContraseña web: '+password+'\nOrigen del agente: '+plan['origin']+'\nToken del agente: '+token+'\n', 0o600)
        tx.write('/etc/nexo-monitor.env', 'NEXO_TOKEN='+token+'\nNEXO_ALLOWED_ORIGIN='+plan['origin']+
                 '\nNEXO_SERVER_NAME=Nodo-Debian-13\nNEXO_BIND=127.0.0.1\nNEXO_PORT=9108\nNEXO_STREAM_PORTS=80,443,8080\nNEXO_DISKS=/\n', 0o600)
        tx.write('/etc/nexo-api.env', 'NEXO_API_TOKEN='+api_token+'\nNEXO_API_ORIGIN='+plan['origin']+
                 '\nNEXO_API_BIND=127.0.0.1\nNEXO_API_PORT=9109\nNEXO_API_DB=/var/lib/nexo-iptv/catalog.sqlite3\n', 0o600)
        tx.write('/etc/nexo-iptv/api-proxy.conf', 'proxy_set_header Authorization "Bearer '+api_token+'";\n', 0o600)
        unit = (bundle / 'payload/agent/nexo-monitor.service').read_text().replace('/opt/nexo-monitor/nexo_monitor.py', '/opt/nexo-iptv/agent/nexo_monitor.py')
        tx.write('/etc/systemd/system/nexo-monitor.service', unit)
        tx.write('/etc/systemd/system/nexo-api.service', (bundle / 'payload/backend/nexo-api.service').read_text())
        tx.write('/etc/nginx/sites-available/nexo-iptv.conf', nginx_config(plan))
        tx.symlink('../sites-available/nexo-iptv.conf', '/etc/nginx/sites-enabled/nexo-iptv.conf')
        runner([TOOLS['nginx'], '-t'])
        for account in ACCOUNTS:
            runner([TOOLS['useradd'], '--system', '--user-group', '--no-create-home', '--home-dir', '/nonexistent',
                    '--shell', '/usr/sbin/nologin', account])
            users_created.append(account)
        uid = pwd.getpwnam('nexo-api').pw_uid if api_uid is None else api_uid
        gid = grp.getgrnam('nexo-api').gr_gid if api_uid is None else api_uid
        os.chown(state_dir, uid, gid)
        runner([TOOLS['systemctl'], 'daemon-reload'])
        for account in ACCOUNTS:
            services_attempted.append(account + '.service')
            runner([TOOLS['systemctl'], 'enable', '--now', account + '.service'])
        health(token, api_token)
        reload_attempted = True
        runner([TOOLS['systemctl'], 'reload', 'nginx'])
        return plan['origin']
    except Exception:
        cleanup = []
        for service in reversed(services_attempted):
            try: runner([TOOLS['systemctl'], 'disable', '--now', service])
            except Exception: cleanup.append('servicio ' + service)
        cleanup.extend(tx.rollback())
        for account in reversed(users_created):
            try: runner([TOOLS['userdel'], account])
            except Exception: cleanup.append('cuenta ' + account)
            try: runner([TOOLS['groupdel'], account], check=False)
            except Exception: cleanup.append('grupo ' + account)
        if services_attempted:
            try: runner([TOOLS['systemctl'], 'daemon-reload'])
            except Exception: cleanup.append('recarga de unidades')
        if reload_attempted:
            try: runner([TOOLS['systemctl'], 'reload', 'nginx'])
            except Exception: cleanup.append('recarga de nginx tras rollback')
        if cleanup:
            print('Reversión incompleta; revisa: '+', '.join(cleanup), file=sys.stderr)
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Comprobar requisitos sin instalar (predeterminado).')
    mode.add_argument('--apply', action='store_true', help='Instalar panel, catálogo central y agente, y recargar Nginx.')
    p.add_argument('--origin', help='Origen HTTPS del nuevo panel, p. ej. https://nexo.ejemplo.com')
    p.add_argument('--tls-cert', help='Ruta a fullchain.pem ya existente y válido.')
    p.add_argument('--tls-key', help='Ruta a privkey.pem ya existente y sin contraseña.')
    p.add_argument('--admin-user', default='administrador')
    args = p.parse_args()
    try:
        supplied = [args.origin, args.tls_cert, args.tls_key]
        if (any(supplied) and not all(supplied)) or (args.apply and not all(supplied)):
            raise ValueError('Indica juntos --origin, --tls-cert y --tls-key; son obligatorios con --apply.')
        if args.apply and os.geteuid() != 0:
            raise ValueError('La instalación requiere root. Usa sudo.')
        plan = make_plan(*supplied, username=args.admin_user) if all(supplied) else None
        bundle = Path(__file__).resolve().parent
        count = preflight(bundle, plan)
        print('Debian 13, dependencias e integridad: correctos ({} archivos).'.format(count))
        if not args.apply:
            print('Comprobación terminada. No se instalaron archivos, cuentas ni servicios.')
            if not plan: print('Falta validar dominio y certificado: añade --origin, --tls-cert y --tls-key.')
            return 0
        origin = install(bundle, plan)
        print('Panel, catálogo central y agente instalados: '+origin)
        print('Credenciales generadas: /etc/nexo-iptv/access.txt (root, 600). No se muestran en pantalla.')
        print('En el panel, usa Conectar servidor con ese mismo origen y el token del archivo privado.')
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as e:
        print('No se completó la operación: '+str(e), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
