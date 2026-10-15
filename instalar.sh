#!/usr/bin/env bash
set -euo pipefail

# Execute como seu usuário normal. Apenas as operações do sistema usam sudo.
if [[ $EUID -eq 0 ]]; then
    echo 'Execute com bash instalar.sh, sem sudo.' >&2
    exit 1
fi

RAIZ="$(cd "$(dirname "$0")" && pwd)"
INFRA="$(dirname "$RAIZ")"
SITE="$INFRA/stream-guard-site-institucional"
USUARIO="$(id -un)"
GRUPO="$(id -gn)"

# Os repositórios devem estar lado a lado, como na instalação validada.
for arquivo in etl.py gerar-gold.py requirements.txt processar.sh compose.ec2.yaml Dockerfile.api dockerignore.api; do
    [[ -f "$RAIZ/$arquivo" ]] || { echo "Arquivo ausente: $arquivo" >&2; exit 1; }
done
[[ -f "$SITE/app.js" ]] || { echo "Repositório do site não encontrado em $SITE" >&2; exit 1; }
# Os caminhos são gravados nos arquivos do systemd.
if [[ "$INFRA" =~ [[:space:]] ]]; then
    echo 'Use uma pasta sem espaços para esta instalação na EC2.' >&2
    exit 1
fi

sudo -n true
# Instala somente dependências ausentes em uma EC2 Ubuntu com systemd.
pacotes=()
command -v docker >/dev/null || pacotes+=(docker.io)
docker compose version >/dev/null 2>&1 || pacotes+=(docker-compose-v2)
python3 -m venv --help >/dev/null 2>&1 || pacotes+=(python3-venv)
if (( ${#pacotes[@]} )); then
    sudo apt-get update
    sudo apt-get install -y "${pacotes[@]}"
fi
sudo systemctl enable --now docker

# Mantém as configurações existentes e cria exemplos mínimos quando necessário.
python3 - "$RAIZ" "$INFRA" <<'PY'
from pathlib import Path
import secrets
import sys

raiz, infra = map(Path, sys.argv[1:])
def criar_se_ausente(caminho, conteudo):
    try:
        with caminho.open('x') as arquivo:
            arquivo.write(conteudo)
        caminho.chmod(0o600)
    except FileExistsError:
        pass

criar_se_ausente(infra / 'banco.env',
    f'MYSQL_ROOT_PASSWORD={secrets.token_hex(24)}\nMYSQL_DATABASE=streamguard\n')
criar_se_ausente(infra / 'api.env',
    'AMBIENTE_PROCESSO=producao\nAPP_PORT=3333\nAPP_HOST=0.0.0.0\n'
    'DB_HOST=banco\nDB_PORT=3306\nDB_DATABASE=streamguard\n'
    f'DB_USER=streamguard_api\nDB_PASSWORD={secrets.token_hex(24)}\n')
criar_se_ausente(raiz / '.env',
    '# Configure o bucket do seu grupo. A EC2 utiliza a função IAM.\n'
    'S3_BUCKET=streamguard-grupo-10-04261\nAWS_DEFAULT_REGION=us-east-1\n'
    'GOLD_JANELA_MINUTOS=5\n')
PY

# Usa o volume existente. Não executa DROP DATABASE nem remove volumes.
if [[ -f "$INFRA/compose.yaml" ]]; then
    cp -n "$INFRA/compose.yaml" "$INFRA/compose.antes-instalador.yaml" || true
fi
cp "$RAIZ/compose.ec2.yaml" "$INFRA/compose.yaml"
cp "$RAIZ/Dockerfile.api" "$SITE/Dockerfile"
cp "$RAIZ/dockerignore.api" "$SITE/.dockerignore"
cd "$INFRA"
sudo docker compose up -d --wait --wait-timeout 180 banco

# Banco novo recebe a estrutura inicial. Banco existente permanece intacto.
tabelas="$(sudo docker compose exec -T --interactive=false banco sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -N -u root streamguard -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE();"')"
if [[ "$tabelas" == 0 ]]; then
    # O script original usa maiúsculas. Padroniza o nome do banco para Linux.
    python3 - "$SITE/src/database/StreamGuard-ScripBD.sql" <<'PY' | sudo docker compose exec -T banco sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -u root streamguard'
from pathlib import Path
import sys
sql = Path(sys.argv[1]).read_text(encoding='utf-8-sig')
sql = sql.replace('StreamGuard', 'streamguard')
sql = sql.replace('DROP DATABASE IF EXISTS streamguard;', '')
sql = sql.replace('CREATE DATABASE streamguard;', 'CREATE DATABASE IF NOT EXISTS streamguard;')
print(sql)
PY
fi

# Sincroniza a senha do usuário da API com api.env sem exibi-la no terminal.
python3 - "$INFRA/api.env" <<'PY' | sudo docker compose exec -T banco sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -u root'
from pathlib import Path
import sys
config = dict(linha.split('=', 1) for linha in Path(sys.argv[1]).read_text().splitlines()
              if linha.strip() and not linha.lstrip().startswith('#') and '=' in linha)
senha = config['DB_PASSWORD']
# As senhas geradas pelo instalador contêm apenas caracteres hexadecimais.
if not senha or any(c not in '0123456789abcdefABCDEF' for c in senha):
    raise SystemExit('DB_PASSWORD deve ser uma senha hexadecimal gerada pelo instalador.')
print(f"CREATE USER IF NOT EXISTS 'streamguard_api'@'%' IDENTIFIED BY '{senha}';")
print(f"ALTER USER 'streamguard_api'@'%' IDENTIFIED BY '{senha}';")
print("GRANT SELECT, INSERT, UPDATE, DELETE ON streamguard.* TO 'streamguard_api'@'%';")
PY
sudo docker compose up -d --build api

# Instala as bibliotecas no ambiente isolado do processamento.
cd "$RAIZ"
[[ -x .venv/bin/python ]] || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
chmod +x processar.sh

# Verifica as permissões mínimas de leitura antes de ativar a ETL.
.venv/bin/python - <<'PY'
import os
import boto3
from dotenv import load_dotenv
load_dotenv('.env')
perfil = os.getenv('AWS_PROFILE') or None
sessao = boto3.Session(profile_name=perfil, region_name=os.getenv('AWS_DEFAULT_REGION', 'us-east-1'))
sessao.client('s3').list_objects_v2(Bucket=os.environ['S3_BUCKET'], Prefix='bronze/', MaxKeys=1)
print('Leitura da Bronze autorizada.')
PY

# Registra o usuário e os caminhos desta instalação, sem fixar o nome ubuntu.
sudo tee /etc/systemd/system/streamguard-etl.service >/dev/null <<SERVICE
[Unit]
Description=Processamento Silver e Gold do StreamGuard
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=$USUARIO
Group=$GRUPO
WorkingDirectory=$RAIZ
ExecStart=/usr/bin/bash $RAIZ/processar.sh
SERVICE

sudo tee /etc/systemd/system/streamguard-etl.timer >/dev/null <<'TIMER'
[Unit]
Description=Agendamento da ETL do StreamGuard

[Timer]
OnBootSec=2min
OnUnitInactiveSec=5min
Unit=streamguard-etl.service

[Install]
WantedBy=timers.target
TIMER

sudo systemctl daemon-reload
sudo systemctl enable --now streamguard-etl.timer
# Executa um ciclo real para validar também leitura e escrita dos dados.
sudo systemctl start streamguard-etl.service
sudo journalctl -u streamguard-etl.service -n 8 --no-pager
sudo docker compose ps
printf '\nInstalação concluída. API e banco no Docker; ETL agendada no systemd.\n'
