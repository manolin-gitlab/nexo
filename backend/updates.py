"""Read-only GitHub update checks. Installing code always requires a root SSH command."""
import datetime as dt
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import threading
import time
import urllib.request

REPOSITORY = 'manolin-gitlab/nexo'
API = 'https://api.github.com/repos/' + REPOSITORY
RAW = 'https://raw.githubusercontent.com/' + REPOSITORY
SHA = re.compile(r'[0-9a-f]{40}')
COMMAND = 'curl -fsSL https://raw.githubusercontent.com/manolin-gitlab/nexo/main/install-quick.sh -o /root/nexo-instalar.sh && bash /root/nexo-instalar.sh --update'


def source_path(name, kind):
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts:
        return False
    if name == 'nexo-release.json' or (kind == 'trial' and name == 'trial/nexo_trial.py'):
        return True
    if len(p.parts) == 2 and p.parts[0] == 'backend' and p.suffix == '.py':
        return True
    if name == 'agent/nexo_monitor.py':
        return True
    return len(p.parts) == 2 and p.parts[0] == 'dist' and p.suffix in ('.html', '.js', '.css', '.svg', '.txt')


def release(data):
    if (not isinstance(data, dict) or not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}', str(data.get('version', '')))
            or type(data.get('update_protocol')) is not int or type(data.get('catalog_schema')) is not int):
        raise ValueError('Metadatos de versión no válidos.')
    return {k: data[k] for k in ('version', 'update_protocol', 'catalog_schema')}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def fetch_json(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Nexo-IPTV-updates/0.6.1', 'Accept': 'application/vnd.github+json'})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=5) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('Respuesta de GitHub demasiado grande.')
    return json.loads(raw)


class Manager:
    def __init__(self, installed_file, baseline_file, fetcher=fetch_json, clock=time.monotonic):
        self.installed_file = Path(installed_file)
        self.baseline_file = Path(baseline_file)
        self.fetcher, self.clock = fetcher, clock
        self.lock = threading.Lock()
        self.busy = False
        self.last_attempt = None
        self.installed = json.loads(self.installed_file.read_text())
        release(self.installed)
        self.result = {'state': 'unchecked', 'available': None, 'checked_at': None}

    def baseline(self):
        commit = self.installed.get('source_commit')
        if isinstance(commit, str) and SHA.fullmatch(commit):
            return commit
        fingerprint = hashlib.sha256(json.dumps(self.installed.get('source_files', {}), sort_keys=True).encode()).hexdigest()
        try:
            data = json.loads(self.baseline_file.read_text())
            if data.get('fingerprint') == fingerprint and SHA.fullmatch(data.get('commit', '')):
                return data['commit']
        except (OSError, ValueError, TypeError):
            pass
        return None

    def inspect(self):
        head = self.fetcher(API + '/git/ref/heads/main')['object']['sha']
        if not isinstance(head, str) or not SHA.fullmatch(head):
            raise ValueError('Revisión de GitHub no válida.')
        baseline = self.baseline()
        latest = release(self.installed) if head == baseline else release(self.fetcher(RAW + '/' + head + '/nexo-release.json'))
        compatible = (latest['update_protocol'] == self.installed['update_protocol'] == 1
                      and latest['catalog_schema'] == self.installed['catalog_schema'] == 1
                      and tuple(map(int, latest['version'].split('.'))) >= tuple(map(int, self.installed['version'].split('.'))))
        if baseline is None and self.installed.get('source_files'):
            tree_sha = self.fetcher(API + '/git/commits/' + head)['tree']['sha']
            if not SHA.fullmatch(tree_sha):
                raise ValueError('Árbol de GitHub no válido.')
            tree = self.fetcher(API + '/git/trees/' + tree_sha + '?recursive=1')
            if tree.get('truncated'):
                raise ValueError('No se pudo comparar el árbol completo.')
            remote = {row['path']: row['sha'] for row in tree['tree'] if row.get('type') == 'blob'
                      and source_path(row['path'], self.installed.get('install_kind', 'production'))}
            if remote == self.installed['source_files']:
                baseline = head
                fingerprint = hashlib.sha256(json.dumps(self.installed['source_files'], sort_keys=True).encode()).hexdigest()
                # Advisory baseline only; never used as authorization by the root updater.
                temp = self.baseline_file.with_name(self.baseline_file.name + '.tmp')
                if temp.is_symlink():
                    raise ValueError('Ruta de estado no válida.')
                with temp.open('w') as stream:
                    json.dump({'commit': head, 'fingerprint': fingerprint}, stream)
                temp.chmod(0o600); temp.replace(self.baseline_file)
        available = head != baseline
        return {'state': ('incompatible' if not compatible else 'available') if available else 'current',
                'available': available, 'compatible': compatible, 'latest_commit': head,
                'latest_version': latest['version'], 'installed_commit': baseline,
                'changes_url': 'https://github.com/' + REPOSITORY + ('/compare/' + baseline + '...' + head if baseline and available else '/commit/' + head),
                'checked_at': dt.datetime.now(dt.timezone.utc).isoformat()}

    def check(self):
        try:
            result = self.inspect()
        except Exception:
            result = {'state': 'error', 'available': None, 'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                      'message': 'No se pudo comprobar GitHub. Revisa la conexión HTTPS o espera si se alcanzó su límite de consultas.'}
        with self.lock:
            self.result = result; self.busy = False

    def status(self, force=False):
        with self.lock:
            now = self.clock()
            due = self.last_attempt is None or now - self.last_attempt >= (30 if force else 900)
            if due and not self.busy:
                self.busy = True; self.last_attempt = now
                threading.Thread(target=self.check, daemon=True).start()
            head = self.result.get('latest_commit')
            command = COMMAND
            if isinstance(head, str) and SHA.fullmatch(head):
                command = command.replace('/main/install-quick.sh', '/'+head+'/install-quick.sh')+' --revision '+head
            return {**self.result, 'checking': self.busy, 'installed_version': self.installed['version'],
                    'installed_commit': self.baseline(), 'repository': REPOSITORY, 'command': command+' --kind '+('trial' if self.installed.get('install_kind')=='trial' else 'production'),
                    'install_kind': self.installed.get('install_kind', 'production')}
