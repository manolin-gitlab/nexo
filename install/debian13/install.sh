#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "${1:-}" = "--install-deps" ]; then
    [ "$#" -eq 1 ] || { echo 'Usa --install-deps sin otros argumentos.' >&2; exit 2; }
    [ "$(id -u)" -eq 0 ] || { echo 'Ejecuta con sudo o como root.' >&2; exit 1; }
    . /etc/os-release
    [ "${ID:-}" = debian ] && [ "${VERSION_ID%%.*}" = 13 ] || {
        echo 'Este instalador requiere Debian 13. No convierte ni actualiza el sistema operativo.' >&2
        exit 1
    }
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 nginx openssl ca-certificates
    exit 0
fi
command -v python3 >/dev/null 2>&1 || {
    echo 'Falta Python 3. Ejecuta primero: sudo ./install.sh --install-deps' >&2
    exit 1
}
exec python3 "$HERE/install.py" "$@"
