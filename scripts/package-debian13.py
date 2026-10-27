"""Build an offline application payload; Debian dependencies are installed separately."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile

root = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(root/'scripts'))
from runtime_payload import metadata
download = root / 'dist/downloads'
download.mkdir(exist_ok=True)
output = download / 'nexo-debian13.tar.gz'
with tempfile.TemporaryDirectory(prefix='nexo-debian13-build-') as tmp:
    bundle = Path(tmp) / 'nexo-debian13'
    bundle.mkdir()
    for name in ('install.sh', 'install.py', 'README.md'):
        shutil.copyfile(root / 'install/debian13' / name, bundle / name)
    panel = bundle / 'payload/panel'
    for p in sorted((root / 'dist').rglob('*')):
        if not p.is_file() or p.is_symlink() or p.name.startswith('nexo-debian13'):
            continue
        target = panel / p.relative_to(root / 'dist')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
    # The installed panel does not download a recursive copy of this same archive.
    index = panel / 'index.html'
    s = index.read_text()
    import re
    s = re.sub(r'<a\b[^>]*\bdata-debian-installer\b[^>]*>.*?</a>', '', s)
    assert s.count('<meta name="nexo-catalog" content="local">') == 1
    s = s.replace('<meta name="nexo-catalog" content="local">', '<meta name="nexo-catalog" content="server">')
    index.write_text(s)
    agent = bundle / 'payload/agent'; agent.mkdir(parents=True)
    for name in ('nexo_monitor.py', 'nexo-monitor.service', 'README.md', 'nginx.conf.example'):
        shutil.copyfile(root / 'agent' / name, agent / name)
    backend = bundle / 'payload/backend'; backend.mkdir(parents=True)
    for name in ('nexo_api.py', 'updates.py', 'nexo-api.service', 'README.md'):
        shutil.copyfile(root / 'backend' / name, backend / name)
    (bundle/'payload/installed.json').write_text(json.dumps(metadata(root,'production'),indent=2)+'\n')
    files = {p.relative_to(bundle).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted((bundle / 'payload').rglob('*')) if p.is_file()}
    (bundle / 'manifest.json').write_text(json.dumps({'version': '0.6.1', 'target': 'debian-13', 'files': files}, indent=2)+'\n')
    with output.open('wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as compressed, \
         tarfile.open(fileobj=compressed, mode='w') as archive:
        for p in [bundle] + sorted(bundle.rglob('*')):
            info = archive.gettarinfo(str(p), p.relative_to(bundle.parent).as_posix())
            info.uid = info.gid = info.mtime = 0; info.uname = info.gname = 'root'
            info.mode = 0o755 if p.is_dir() or p.name == 'install.sh' else 0o644
            if p.is_file():
                with p.open('rb') as source: archive.addfile(info, source)
            else: archive.addfile(info)
digest = hashlib.sha256(output.read_bytes()).hexdigest()
(download / 'nexo-debian13.sha256').write_text(digest + '  nexo-debian13.tar.gz\n')
print('Debian 13 installer package ready: {} bytes, {} payload files.'.format(output.stat().st_size, len(files)))
