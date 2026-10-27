#!/bin/sh
set -eu
if [ "${1:-}" = --help ]; then
    echo 'Nexo de prueba: install-quick.sh [--port 8088] [--check]'
    echo 'Actualizar conservando datos: install-quick.sh --update [--kind trial|production]'
    exit 0
fi
[ "$(id -u)" -eq 0 ] || { echo 'Ejecuta como root.' >&2; exit 1; }
. /etc/os-release
[ "${ID:-}" = debian ] && [ "${VERSION_ID%%.*}" = 13 ] || {
    echo 'Se requiere Debian 13. No se instalará Nexo en este sistema.' >&2; exit 1;
}
[ -d /run/systemd/system ] || { echo 'Se requiere una VM con systemd.' >&2; exit 1; }
umask 077
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 curl ca-certificates passwd
work=$(mktemp -d /tmp/nexo-quick.XXXXXXXX)
trap 'rm -rf -- "$work"' EXIT HUP INT TERM
requested=
expect_revision=no
for argument in "$@"; do
    if [ "$expect_revision" = yes ]; then requested=$argument; expect_revision=no; continue; fi
    case "$argument" in
        --revision) expect_revision=yes ;;
        --revision=*) requested=${argument#--revision=} ;;
    esac
done
[ "$expect_revision" = no ] || { echo 'Falta la revisión solicitada.' >&2; exit 1; }
if [ -n "$requested" ]; then
    commit=$(python3 - "$requested" <<'PYREV'
import re,sys
if not re.fullmatch('[0-9a-f]{40}',sys.argv[1]):raise SystemExit('Revisión solicitada no válida.')
print(sys.argv[1])
PYREV
)
else
curl --fail --silent --show-error --location --proto '=https' --max-time 60 --max-filesize 2000000 \
  https://api.github.com/repos/manolin-gitlab/nexo/git/ref/heads/main -o "$work/ref.json"
commit=$(python3 - "$work/ref.json" <<'PY'
import json,re,sys
value=json.load(open(sys.argv[1]))['object']['sha']
if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{40}',value):raise SystemExit('Revisión de GitHub no válida.')
print(value)
PY
)
fi
curl --fail --silent --show-error --location --proto '=https' --max-time 120 --max-filesize 10000000 \
  "https://codeload.github.com/manolin-gitlab/nexo/tar.gz/$commit" -o "$work/source.tar.gz"
python3 - "$work/source.tar.gz" "$work" "$commit" <<'PY'
import pathlib,sys,tarfile
archive,destination,commit=sys.argv[1:]
prefix='nexo-'+commit
with tarfile.open(archive,'r:gz') as source:
    members=source.getmembers()
    if len(members)>2000 or sum(m.size for m in members)>50000000:raise SystemExit('Archivo de origen demasiado grande.')
    for m in members:
        p=pathlib.PurePosixPath(m.name)
        if not p.parts or p.parts[0]!=prefix or p.is_absolute() or '..' in p.parts or (not m.isfile() and not m.isdir()):
            raise SystemExit('Archivo de origen no válido.')
    source.extractall(destination,filter='data')
PY
python3 "$work/nexo-$commit/trial/install.py" --source-commit "$commit" "$@"
