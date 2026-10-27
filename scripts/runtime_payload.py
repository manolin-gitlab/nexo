"""Prepare application code only; do not copy private configuration or data."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil


def update_module(root):
    spec = importlib.util.spec_from_file_location('nexo_update_metadata', Path(root)/'backend/updates.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def metadata(root, kind, commit=None):
    root = Path(root); m = update_module(root)
    version = m.release(json.loads((root/'nexo-release.json').read_text()))
    if version['update_protocol'] != 1 or version['catalog_schema'] != 1:
        raise ValueError('Esta instalación solo admite protocolo y esquema 1.')
    if commit is not None and not m.SHA.fullmatch(commit):
        raise ValueError('Revisión de origen no válida.')
    hashes = {}
    candidates = [root/'nexo-release.json', root/'trial/nexo_trial.py']
    for folder in ('dist', 'backend', 'agent'):
        candidates.extend((root/folder).glob('*'))
    for path in sorted(candidates):
        name = path.relative_to(root).as_posix()
        if not m.source_path(name, kind):
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError('Falta un archivo de código normal: '+name)
        raw = path.read_bytes()
        hashes[name] = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    required = {'nexo-release.json', 'dist/index.html', 'dist/catalog-client.js', 'dist/monitor.js',
                'backend/nexo_api.py', 'backend/updates.py', 'agent/nexo_monitor.py'}
    if kind == 'trial': required.add('trial/nexo_trial.py')
    if not required <= set(hashes): raise ValueError('Faltan componentes de la aplicación.')
    return {**version, 'source_commit': commit, 'source_files': hashes, 'install_kind': kind}


def prepare(root, target, kind, commit=None):
    root, target = Path(root), Path(target)
    info = metadata(root, kind, commit)
    target.mkdir(mode=0o755)
    for folder in ('panel', 'backend', 'agent'):
        (target/folder).mkdir(mode=0o755)
    for name in info['source_files']:
        path = root/name
        if name.startswith('dist/'):
            destination = target/'panel'/path.name
        elif name.startswith(('backend/', 'agent/')):
            destination = target/name
        elif name == 'trial/nexo_trial.py':
            destination = target/'nexo_trial.py'
        else:
            continue
        shutil.copyfile(path, destination); destination.chmod(0o644)
    downloads = target/'panel/downloads'; downloads.mkdir()
    for name in ('nexo-monitor.zip',):
        p = root/'dist/downloads'/name
        if p.is_file() and not p.is_symlink():
            shutil.copyfile(p, downloads/name); (downloads/name).chmod(0o644)
    index = target/'panel/index.html'
    html = index.read_text()
    marker = '<meta name="nexo-catalog" content="local">'
    if html.count(marker) != 1:
        raise ValueError('El panel no tiene el marcador de almacenamiento esperado.')
    html = html.replace(marker, '<meta name="nexo-catalog" content="server">')
    import re
    html = re.sub(r'<a\b[^>]*\bdata-debian-installer\b[^>]*>.*?</a>', '', html)
    if kind == 'trial':
        html = html.replace('</head>', '<meta name="nexo-test" content="loopback"></head>')
        html = html.replace('<body>', '<body><div class="trial-banner">MODO DE PRUEBA · acceso local por túnel SSH</div>')
    index.write_text(html)
    (target/'installed.json').write_text(json.dumps(info, indent=2)+'\n')
    for path in target.rglob('*'):
        path.chmod(0o755 if path.is_dir() else 0o644)
    target.chmod(0o755)
    return info
