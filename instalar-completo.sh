#!/usr/bin/env bash
set -euo pipefail

# LOCAL: Bash no Ubuntu ou Git Bash no Windows. Não execute com sudo.
RAIZ="$(cd "$(dirname "$0")" && pwd)"
PYTHON=()
for comando in python3 python; do
    if command -v "$comando" >/dev/null && "$comando" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        PYTHON=("$comando")
        break
    fi
done
if (( ${#PYTHON[@]} == 0 )) && command -v py >/dev/null && py -3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    PYTHON=(py -3)
fi
(( ${#PYTHON[@]} )) || { echo 'Instale Python 3.10 ou superior e reabra o terminal.' >&2; exit 1; }
export PYTHONDONTWRITEBYTECODE=1
for argumento in "$@"; do
    if [[ "$argumento" == --help || "$argumento" == -h ]]; then
        exec "${PYTHON[@]}" "$RAIZ/provisionar.py" "$@"
    fi
done
if ! "${PYTHON[@]}" -c 'import venv, ensurepip' >/dev/null 2>&1; then
    echo 'Python sem suporte a ambiente virtual. No Ubuntu, instale python3-venv; no Windows, confira a instalação do Python.' >&2
    exit 1
fi
for comando in git aws ssh; do
    command -v "$comando" >/dev/null || { echo "Instale $comando e reabra o terminal." >&2; exit 1; }
done

# Os três repositórios ficam lado a lado. Fontes existentes são preservadas.
for repositorio in stream-guard-site-institucional stream-guard-etl; do
    destino="$(dirname "$RAIZ")/$repositorio"
    branch=main
    if [[ "$repositorio" == stream-guard-site-institucional ]]; then
        branch="${STREAMGUARD_SITE_REF:-develop}"
    fi
    if [[ ! -e "$destino" ]]; then
        git clone --branch "$branch" --single-branch "https://github.com/StreamGuard/$repositorio.git" "$destino"
    fi
done

# A criação ainda exige a confirmação CRIAR exibida pelo provisionador.
# --planejar faz somente as consultas e o dry-run, sem criar recursos.
exec "${PYTHON[@]}" "$RAIZ/provisionar.py" --executar "$@"
