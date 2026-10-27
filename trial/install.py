#!/usr/bin/env python3
"""Install the local test appliance or update an existing Nexo installation."""
import argparse
import base64
from contextlib import closing
import fcntl
import grp
import http.client
import importlib.util
import json
import os
from pathlib import Path
import platform
import pwd
import re
import secrets
import shutil
import socket
import sqlite3
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from runtime_payload import metadata, prepare

spec = importlib.util.spec_from_file_location('nexo_install_common', ROOT/'install/debian13/install.py')
common = importlib.util.module_from_spec(spec); spec.loader.exec_module(common)
KINDS = {
    'trial': {'code': '/opt/nexo-test', 'db': '/var/lib/nexo-test/catalog.sqlite3',
              'services': ['nexo-test.service'], 'env': '/etc/nexo-test.env'},
    'production': {'code': '/opt/nexo-iptv', 'db': '/var/lib/nexo-iptv/catalog.sqlite3',
                   'services': ['nexo-api.service', 'nexo-monitor.service'], 'env': '/etc/nexo-api.env'}
}


def host_check():
    if not common.debian13(Path('/etc/os-release').read_text()) or sys.version_info < (3, 13):
        raise ValueError('Se requiere Debian 13 con Python 3.13 o superior.')
    if os.geteuid() != 0 or not Path('/run/systemd/system').is_dir():
        raise ValueError('Ejecuta como root en una VM iniciada con systemd.')
    if platform.machine() not in ('x86_64', 'aarch64'):
        raise ValueError('Arquitecturas admitidas: amd64 y arm64.')
    metadata(ROOT, 'trial')


def read_env(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        if line and not line.startswith('#'):
            key, value = line.split('=', 1); result[key] = value
    return result


def health(kind, env):
    if kind == 'trial':
        port = int(env['NEXO_TEST_PORT'])
        credentials = base64.b64encode((env['NEXO_TEST_USER']+':'+env['NEXO_TEST_PASSWORD']).encode()).decode()
        headers = {'Authorization': 'Basic '+credentials, 'Host': 'localhost:'+str(port)}
        checks = [(port, '/api/v1/healthz', headers)]
    else:
        monitor = read_env('/etc/nexo-monitor.env')
        checks = [(9109, '/api/v1/healthz', {'Authorization': 'Bearer '+env['NEXO_API_TOKEN']}),
                  (9108, '/healthz', {'Authorization': 'Bearer '+monitor['NEXO_TOKEN']})]
    for port, path, headers in checks:
        for attempt in range(10):
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
            try:
                connection.request('GET', path, headers=headers)
                response = connection.getresponse()
                if response.status == 200 and json.loads(response.read(4096)).get('status') == 'ok': break
            except (OSError, ValueError, http.client.HTTPException): pass
            finally: connection.close()
            time.sleep(.2)
        else: raise RuntimeError('Falló la comprobación de salud del servicio Nexo.')


def fresh_check(port):
    if not 1024 <= port <= 65535: raise ValueError('Usa un puerto entre 1024 y 65535.')
    paths = ['/opt/nexo-test', '/var/lib/nexo-test', '/etc/nexo-test.env', '/etc/nexo-test-access.txt',
             '/etc/systemd/system/nexo-test.service', '/usr/lib/systemd/system/nexo-test.service', '/lib/systemd/system/nexo-test.service']
    if any(os.path.lexists(path) for path in paths):
        raise ValueError('Ya existe una instalación de prueba. Usa --update; no se sobrescriben sus datos.')
    for lookup in (pwd.getpwnam, grp.getgrnam):
        try: lookup('nexo-test')
        except KeyError: continue
        raise ValueError('La cuenta/grupo nexo-test ya existe.')
    with socket.socket() as sock: sock.bind(('127.0.0.1', port))


def install(source, commit, port=8088, tx=None, runner=common.run, check_health=health, account_ids=None):
    tx = tx or common.Transaction()
    created = started = False
    try:
        code = tx.path('/opt/nexo-test')
        if os.path.lexists(code): raise FileExistsError(str(code))
        tx.created.append(code); prepare(source, code, 'trial', commit)
        data = tx.mkdir('/var/lib/nexo-test', 0o700)
        password = secrets.token_urlsafe(32)
        env = {'NEXO_TEST_USER': 'administrador', 'NEXO_TEST_PASSWORD': password,
               'NEXO_TEST_PORT': str(port), 'NEXO_TEST_DB': '/var/lib/nexo-test/catalog.sqlite3'}
        tx.write('/etc/nexo-test.env', ''.join(k+'='+v+'\n' for k,v in env.items()), 0o600)
        tx.write('/etc/nexo-test-access.txt', 'URL: http://localhost:'+str(port)+'\nUsuario: administrador\nContraseña: '+password+
                 '\nAcceso mediante túnel SSH; no abrir el puerto en el firewall.\n', 0o600)
        tx.write('/etc/systemd/system/nexo-test.service', (Path(source)/'trial/nexo-test.service').read_text())
        runner(['/usr/sbin/useradd','--system','--user-group','--no-create-home','--home-dir','/nonexistent','--shell','/usr/sbin/nologin','nexo-test'])
        created = True
        uid,gid = account_ids if account_ids else (pwd.getpwnam('nexo-test').pw_uid,grp.getgrnam('nexo-test').gr_gid)
        os.chown(data,uid,gid)
        runner(['/usr/bin/systemctl','daemon-reload'])
        started = True; runner(['/usr/bin/systemctl','enable','--now','nexo-test.service'])
        check_health('trial',env)
        return env
    except Exception:
        if started:
            try: runner(['/usr/bin/systemctl','disable','--now','nexo-test.service'])
            except Exception: print('Revisa el servicio nexo-test: no pudo detenerse.',file=sys.stderr)
        errors = tx.rollback()
        if created:
            for command in ('userdel','groupdel'):
                try: runner(['/usr/sbin/'+command,'nexo-test'],check=False)
                except Exception: errors.append('cuenta nexo-test')
        try: runner(['/usr/bin/systemctl','daemon-reload'])
        except Exception: errors.append('recarga de unidades')
        if errors: print('Reversión incompleta: '+', '.join(errors),file=sys.stderr)
        raise


def backup_database(path, target):
    fd = os.open(target, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600); os.close(fd)
    with closing(sqlite3.connect(str(path), timeout=5)) as source:
        if source.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] != 0:
            raise RuntimeError('La base tiene conexiones activas; no se puede actualizar con seguridad.')
        started = time.monotonic()
        def progress(*_):
            if time.monotonic()-started>30: raise RuntimeError('El respaldo superó 30 segundos.')
        with closing(sqlite3.connect(str(target))) as destination:
            source.backup(destination,pages=100,progress=progress,sleep=.05)
            if destination.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise RuntimeError('El respaldo de SQLite no pasó la comprobación de integridad.')


def update(source, commit, kind, runner=common.run, check_health=health, root=Path('/')):
    root=Path(root); config=KINDS[kind]
    code=root/config['code'].lstrip('/'); database=root/config['db'].lstrip('/')
    if code.is_symlink() or not code.is_dir() or code.stat().st_uid!=os.geteuid() or code.stat().st_mode&0o022:
        raise ValueError('La carpeta de aplicación no es una instalación de confianza.')
    installed_file=code/'installed.json'
    if installed_file.is_file():
        installed=json.loads(installed_file.read_text())
    elif kind=='production' and (code/'backend/nexo_api.py').is_file():
        # One-time adoption of the published 0.6 catalog installation; never execute its source as root.
        import ast
        tree=ast.parse((code/'backend/nexo_api.py').read_text())
        versions=[node.value.value for node in tree.body if isinstance(node,ast.Assign) and isinstance(node.value,ast.Constant)
                  and any(isinstance(target,ast.Name) and target.id=='VERSION' for target in node.targets)]
        if versions!=['0.6.0']:raise ValueError('La instalación anterior requiere una migración manual.')
        installed={'version':'0.6.0','update_protocol':1,'catalog_schema':1,'install_kind':'production'}
    else:raise ValueError('Faltan los metadatos de la instalación.')
    latest=metadata(source,kind,commit)
    if not isinstance(installed.get('version'),str) or not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}',installed['version']):
        raise ValueError('Versión instalada no válida.')
    if installed.get('install_kind')!=kind or installed.get('update_protocol')!=1 or installed.get('catalog_schema')!=1:
        raise ValueError('Esta instalación requiere una migración manual.')
    if tuple(map(int,latest['version'].split('.')))<tuple(map(int,installed['version'].split('.'))):
        raise ValueError('Se rechaza una versión anterior.')
    if database.is_symlink() or not database.is_file(): raise ValueError('No se encontró una base de datos normal.')
    env=read_env(root/config['env'].lstrip('/'))
    state=database.stat(); suffix=secrets.token_hex(6)
    stage=code.with_name(code.name+'.next-'+suffix); previous=code.with_name(code.name+'.previous-'+suffix)
    backup_parent=root/'var/backups/nexo'
    if backup_parent.is_symlink(): raise ValueError('Ruta de respaldo no válida.')
    backup_parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if backup_parent.stat().st_uid!=os.geteuid() or backup_parent.stat().st_mode&0o077:
        raise ValueError('La carpeta de respaldos debe ser privada y pertenecer a root.')
    backup_dir=backup_parent/(time.strftime('%Y%m%d-%H%M%S')+'-'+suffix);backup_dir.mkdir(mode=0o700)
    backup=backup_dir/'catalog.sqlite3'
    stop_attempted=swapped=backup_ready=marker_created=False
    try:
        prepare(source,stage,kind,commit)
        (stage/'.maintenance').write_text('updating\n')
        maintenance=code/'.maintenance'
        fd=os.open(maintenance,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644);os.close(fd);marker_created=True
        stop_attempted=True
        for service in config['services']:runner(['/usr/bin/systemctl','stop',service])
        backup_database(database,backup);backup_ready=True
        code.rename(previous);stage.rename(code);swapped=True
        for service in config['services']:runner(['/usr/bin/systemctl','start',service])
        check_health(kind,env)
        (backup_dir/'code-path.txt').write_text(str(previous)+'\n')
        (previous/'.maintenance').unlink();(code/'.maintenance').unlink()
        return {'backup':str(backup),'previous_code':str(previous),'commit':commit}
    except Exception as error:
        cleanup=[]
        if stop_attempted:
            for service in config['services']:
                try:runner(['/usr/bin/systemctl','stop',service])
                except Exception:cleanup.append('detener '+service)
        if swapped:
            try:shutil.rmtree(code);previous.rename(code)
            except Exception:cleanup.append('restaurar código')
        elif previous.exists() and not code.exists():
            try:previous.rename(code)
            except Exception:cleanup.append('restaurar código')
        if backup_ready:
            try:
                temp=database.with_name(database.name+'.restore-'+suffix)
                fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'wb') as output, backup.open('rb') as source_file:
                    os.fchown(output.fileno(),state.st_uid,state.st_gid);shutil.copyfileobj(source_file,output)
                for ending in ('-wal','-shm'):database.with_name(database.name+ending).unlink(missing_ok=True)
                temp.replace(database)
            except Exception:cleanup.append('restaurar base de datos')
        if marker_created and code.exists(): (code/'.maintenance').unlink(missing_ok=True)
        if stage.exists():shutil.rmtree(stage)
        if stop_attempted:
            for service in config['services']:
                try:runner(['/usr/bin/systemctl','start',service])
                except Exception:cleanup.append('arrancar '+service)
        if cleanup:raise RuntimeError('Actualización fallida; reversión incompleta: '+', '.join(cleanup)) from error
        raise RuntimeError('Actualización fallida; se restauró la instalación anterior. Respaldo: '+str(backup_dir)) from error


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--update',action='store_true')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--kind',choices=tuple(KINDS))
    parser.add_argument('--port',type=int,default=8088)
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--revision')
    args=parser.parse_args()
    try:
        os.umask(0o077);host_check()
        if not re.fullmatch(r'[0-9a-f]{40}',args.source_commit):raise ValueError('Revisión de origen no válida.')
        if args.revision and args.revision!=args.source_commit:raise ValueError('La revisión descargada no coincide con la solicitada.')
        fd=os.open('/run/lock/nexo-install-update.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'r+') as lock:
            if os.fstat(lock.fileno()).st_uid!=0 or os.fstat(lock.fileno()).st_mode&0o022:
                raise ValueError('El archivo de bloqueo no es seguro.')
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if args.update:
                kinds=[args.kind] if args.kind else [k for k,v in KINDS.items() if (Path(v['code'])/'installed.json').is_file()]
                if len(kinds)!=1:raise ValueError('Indica --kind trial o --kind production para elegir la instalación.')
                if args.check:print('Revisión y sistema válidos. La actualización no se ejecutó.');return 0
                result=update(ROOT,args.source_commit,kinds[0]);print('Actualizado: '+args.source_commit[:12]+'\nRespaldo: '+result['backup']);return 0
            if args.kind and args.kind!='trial':raise ValueError('La instalación rápida crea solo el entorno de prueba.')
            fresh_check(args.port)
            if args.check:print('Requisitos correctos. No se instaló Nexo.');return 0
            install(ROOT,args.source_commit,args.port)
            print('Nexo de prueba instalado.\n1. Crea un túnel SSH: puerto local '+str(args.port)+' → 127.0.0.1:'+str(args.port)+
                  '\n2. Abre http://localhost:'+str(args.port)+'\n3. Consulta las credenciales con: cat /etc/nexo-test-access.txt')
            return 0
    except (OSError,ValueError,RuntimeError,KeyError) as error:
        print('No se completó la operación: '+str(error),file=sys.stderr);return 1


if __name__=='__main__':sys.exit(main())
