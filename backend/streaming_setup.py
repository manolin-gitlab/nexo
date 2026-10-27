#!/usr/bin/env python3
"""Enable/disable experimental HLS in the existing SSH trial; run as root."""
import argparse
import os
from pathlib import Path
import secrets
import stat
import tempfile


def configure(path, enabled):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('El entorno debe ser un archivo privado del usuario que configura el servicio.')
    if path.parent.is_symlink() or path.parent.stat().st_mode & 0o022:
        raise ValueError('El directorio de configuración no es seguro.')
    rows = path.read_text().splitlines()
    values = dict(row.split('=', 1) for row in rows if row and not row.startswith('#'))
    if not {'NEXO_TEST_USER', 'NEXO_TEST_PASSWORD', 'NEXO_TEST_DB'} <= set(values):
        raise ValueError('No es una configuración del entorno de prueba Nexo.')
    values['NEXO_TEST_STREAMING'] = '1' if enabled else '0'
    if not values.get('NEXO_STREAM_KEY'): values['NEXO_STREAM_KEY'] = secrets.token_urlsafe(32)
    fd, temporary = tempfile.mkstemp(prefix='.nexo-stream-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(''.join(k+'='+v+'\n' for k,v in values.items()))
            output.flush(); os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--enable', action='store_true')
    group.add_argument('--disable', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0: parser.error('Ejecuta como root.')
    if args.enable and not os.access('/usr/bin/ffmpeg', os.X_OK): parser.error('Instala primero ffmpeg con apt.')
    try: configure('/etc/nexo-test.env', args.enable)
    except (OSError, ValueError): parser.error('No se pudo modificar de forma segura /etc/nexo-test.env.')
    print('Configuración guardada. Aplica con: systemctl restart nexo-test.service')


if __name__ == '__main__': main()
