#!/usr/bin/env bash
set -euo pipefail

# Execute na EC2 Ubuntu como usuário normal, com função IAM de acesso ao seu bucket.
# A instância e o bucket precisam existir. Este script não cria recursos na conta AWS.
# Uso: bash iniciar-ec2.sh
# Informe o bucket que você criou. Não use o bucket de outro integrante.
if [[ $EUID -eq 0 ]]; then
    echo 'Execute sem sudo. O script solicitará os privilégios quando necessário.' >&2
    exit 1
fi

# Não imprime comandos nem credenciais durante a preparação.
sudo -n true
pacotes=()
command -v git >/dev/null || pacotes+=(git)
command -v python3 >/dev/null || pacotes+=(python3)
if (( ${#pacotes[@]} )); then
    sudo apt-get update
    sudo apt-get install -y "${pacotes[@]}"
fi

# Mantém os dois repositórios na estrutura esperada pelo instalador.
DESTINO="$HOME/streamguard"
mkdir -p "$DESTINO"
baixar() {
    local nome="$1" branch="$2"
    if [[ -e "$DESTINO/$nome" ]]; then
        [[ -d "$DESTINO/$nome/.git" ]] || { echo "A pasta $nome existe e não é um repositório Git." >&2; exit 1; }
        echo "Mantendo o repositório existente: $nome"
    else
        git clone --branch "$branch" --single-branch "https://github.com/StreamGuard/$nome.git" "$DESTINO/$nome"
    fi
}
baixar stream-guard-site-institucional develop
baixar stream-guard-script-captura feature/ajuste-script
cd "$DESTINO/stream-guard-script-captura"

# Pergunta apenas os dados específicos do ambiente. Não solicita chaves AWS.
read -r -p 'Nome do seu bucket S3 (sem s3://): ' BUCKET
read -r -p 'Região do bucket [us-east-1]: ' REGIAO
REGIAO="${REGIAO:-us-east-1}"
python3 - "$BUCKET" "$REGIAO" <<'PY'
from pathlib import Path
import re
import sys

bucket, regiao = sys.argv[1:]
if bucket == 'NOME_DO_SEU_BUCKET' or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket):
    raise SystemExit('Nome do bucket inválido. Informe o nome real, sem barras.')
if not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d+', regiao):
    raise SystemExit('Região inválida. Exemplo: us-east-1.')

caminho = Path('.env')
conteudo = caminho.read_text(encoding='utf-8-sig') if caminho.exists() else ''
# Preserva uma cópia antes de alterar a configuração existente.
backup = Path('.env.antes-bootstrap')
if caminho.exists() and not backup.exists():
    backup.write_text(conteudo, encoding='utf-8')
    backup.chmod(0o600)

# Ajusta somente os campos do processamento na EC2, preservando os demais.
novos = {'S3_BUCKET': bucket, 'AWS_DEFAULT_REGION': regiao}
linhas = []
for linha in conteudo.splitlines():
    chave = linha.split('=', 1)[0].strip()
    if chave == 'AWS_PROFILE':
        linhas.append('# AWS_PROFILE omitido na EC2 para utilizar a função IAM.')
    elif chave in novos:
        linhas.append(f'{chave}={novos.pop(chave)}')
    else:
        linhas.append(linha)
linhas.extend(f'{chave}={valor}' for chave, valor in novos.items())
caminho.write_text('\n'.join(linhas) + '\n', encoding='utf-8')
caminho.chmod(0o600)
PY

# Reutiliza a instalação validada. Uma falha interrompe o fluxo.
bash instalar.sh

# Verifica se a API responde e consegue consultar o banco inicializado.
# Não exige que haja dados de captura em um bucket novo.
.venv/bin/python - <<'PY'
import json
import time
from urllib.request import urlopen

url = 'http://localhost:3333/metricas/configuracoes/1'
for tentativa in range(15):
    try:
        with urlopen(url, timeout=5) as resposta:
            dados = json.load(resposta)
        if not isinstance(dados, list):
            raise ValueError('A API não retornou a lista de configurações esperada.')
        print('Teste aprovado: API acessível e consulta ao banco funcionando.')
        break
    except Exception:
        if tentativa == 14:
            raise SystemExit('A API não passou no teste. Consulte: sudo docker compose -f ../compose.yaml logs --tail 40 api')
        time.sleep(2)
PY

printf '\nPreparação concluída. A API usa a porta 3333 e a ETL está agendada.\n'
printf 'Em um bucket novo, os arquivos Silver/Gold dependem do primeiro envio de dados reais.\n'
printf 'O computador monitorado usa executar.py, com o endereço público desta API no .env.\n'
