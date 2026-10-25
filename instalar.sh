#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -eq 0 ]]; then
    echo 'Execute como usuário normal, sem sudo.' >&2
    exit 1
fi

RAIZ="$(cd "$(dirname "$0")" && pwd)"
source /etc/os-release
if [[ "$ID" != ubuntu || "$VERSION_ID" != 24.04 ]]; then
    echo 'Este instalador utiliza Ubuntu 24.04 LTS; não altera outras distribuições automaticamente.' >&2
    exit 1
fi
for arquivo in compose.ec2.yaml Dockerfile.api Dockerfile.java implantacao.py; do
    [[ -f "$RAIZ/$arquivo" ]] || { echo "Arquivo necessário ausente: $arquivo" >&2; exit 1; }
done
sudo -n true
pacotes=()
command -v docker >/dev/null || pacotes+=(docker.io)
docker compose version >/dev/null 2>&1 || pacotes+=(docker-compose-v2)
python3 -c 'import venv,ensurepip' >/dev/null 2>&1 || pacotes+=(python3-venv)
if (( ${#pacotes[@]} )); then
    sudo apt-get update </dev/null
    sudo apt-get install -y "${pacotes[@]}" </dev/null
fi
sudo systemctl enable --now docker </dev/null
exec python3 "$RAIZ/implantacao.py" instalar
