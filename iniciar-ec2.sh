#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -eq 0 ]]; then
    echo 'Execute como usuário Ubuntu normal; sudo será usado somente na instalação.' >&2
    exit 1
fi

RAIZ="$(cd "$(dirname "$0")" && pwd)"
sudo -n true
if ! command -v git >/dev/null || ! command -v python3 >/dev/null; then
    sudo apt-get update </dev/null
    sudo apt-get install -y git python3 </dev/null
fi

# Recebe a configuração pelo SSH, sem gravar a senha inicial em arquivo.
export GIT_TERMINAL_PROMPT=0
exec python3 "$RAIZ/implantacao.py" bootstrap
