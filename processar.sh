#!/usr/bin/env bash
set -euo pipefail

# Localiza o projeto sem depender da pasta atual do terminal.
cd "$(dirname "$0")"

# Evita que dois ciclos deste script publiquem ao mesmo tempo.
exec 9>.processamento.lock
flock -n 9 || exit 0

# Uma falha na Silver impede a execução da Gold neste ciclo.
.venv/bin/python -u etl.py
.venv/bin/python -u gerar-gold.py
