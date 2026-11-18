#!/usr/bin/env bash
set -euo pipefail

# LOCAL: Bash no Ubuntu ou Git Bash no Windows. Não execute com sudo.
RAIZ="$(cd "$(dirname "$0")" && pwd)"
PERFIL=streamguard
PREPARAR_LOCAL=0
ARGUMENTOS=()
while (( $# )); do
    case "$1" in
        --preparar-local) PREPARAR_LOCAL=1 ;;
        --perfil)
            (( $# >= 2 )) || { echo 'Informe o nome após --perfil.' >&2; exit 1; }
            PERFIL="$2"; ARGUMENTOS+=("$1" "$2"); shift ;;
        --perfil=*) PERFIL="${1#*=}"; ARGUMENTOS+=("$1") ;;
        --help|-h)
            cat <<'AJUDA'
Uso: bash instalar-completo.sh [opções]
Verifica ferramentas locais, instala as ausentes e valida o perfil AWS.
  --preparar-local       Encerra após preparar o computador e validar AWS.
  --perfil NOME          Perfil AWS; padrão: streamguard.
  --planejar             Prepara fontes e plano sem criar recursos AWS.
  --executar             Instala o ambiente, com confirmação no terminal.
  --chave-ssh CAMINHO     Chave privada EC2 local.
  --diretorio CAMINHO     Pasta de estado da implantação, fora do Git.
  --tipo-instancia TIPO   t3.small (padrão) ou t3.medium.
As demais opções do provisionar.py também são aceitas.
AJUDA
            exit 0 ;;
        *) ARGUMENTOS+=("$1") ;;
    esac
    shift
done
[[ -n "$PERFIL" ]] || { echo 'O perfil AWS não pode ser vazio.' >&2; exit 1; }
export PYTHONDONTWRITEBYTECODE=1 AWS_PAGER="" AWS_CLI_AUTO_PROMPT=off
PYTHON=()

selecionar_python() {
    PYTHON=()
    for comando in python3 python; do
        if command -v "$comando" >/dev/null &&
           "$comando" -c 'import sys,venv,ensurepip; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
            PYTHON=("$comando"); return 0
        fi
    done
    if command -v py >/dev/null &&
       py -3 -c 'import sys,venv,ensurepip; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        PYTHON=(py -3); return 0
    fi
    return 1
}

aws_v2() {
    command -v aws >/dev/null && [[ "$(aws --version 2>&1)" == aws-cli/2.* ]]
}

case "$(uname -s)" in
    Linux)
        [[ -r /etc/os-release ]] || { echo 'Sistema Linux não identificado.' >&2; exit 1; }
        . /etc/os-release
        [[ "$ID" == ubuntu ]] || { echo 'A instalação automática Linux requer Ubuntu.' >&2; exit 1; }
        PACOTES=()
        selecionar_python || PACOTES+=(python3 python3-venv)
        command -v git >/dev/null || PACOTES+=(git)
        command -v ssh >/dev/null || PACOTES+=(openssh-client)
        INSTALAR_AWS=0
        if ! aws_v2; then
            [[ "$(uname -m)" == x86_64 ]] || { echo 'O instalador AWS deste guia requer Ubuntu x86-64.' >&2; exit 1; }
            INSTALAR_AWS=1
            command -v curl >/dev/null || PACOTES+=(curl)
            command -v unzip >/dev/null || PACOTES+=(unzip)
            [[ -s /etc/ssl/certs/ca-certificates.crt ]] || PACOTES+=(ca-certificates)
        fi
        SUDO=()
        if (( ${#PACOTES[@]} || INSTALAR_AWS )); then
            if (( EUID != 0 )); then
                command -v sudo >/dev/null || { echo 'A instalação das ferramentas precisa de sudo.' >&2; exit 1; }
                SUDO=(sudo)
            fi
        fi
        if (( ${#PACOTES[@]} )); then
            echo "Instalando ferramentas ausentes: ${PACOTES[*]}"
            "${SUDO[@]}" apt-get update
            "${SUDO[@]}" apt-get install -y "${PACOTES[@]}"
        fi
        if (( INSTALAR_AWS )); then
            echo 'Instalando AWS CLI v2 pelo instalador oficial.'
            PASTA_AWS="$(mktemp -d)"
            trap 'rm -rf -- "$PASTA_AWS"' EXIT
            curl --fail --location --silent --show-error --retry 3 \
                https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip \
                -o "$PASTA_AWS/awscliv2.zip"
            unzip -q "$PASTA_AWS/awscliv2.zip" -d "$PASTA_AWS"
            if [[ -d /usr/local/aws-cli ]]; then
                "${SUDO[@]}" "$PASTA_AWS/aws/install" --update
            else
                "${SUDO[@]}" "$PASTA_AWS/aws/install"
            fi
            rm -rf -- "$PASTA_AWS"
            trap - EXIT
            hash -r
        fi
        ;;
    MINGW*|MSYS*)
        # Git Bash é o ponto de entrada no Windows.
        FALTA_PYTHON=0; FALTA_AWS=0
        selecionar_python || FALTA_PYTHON=1
        aws_v2 || FALTA_AWS=1
        if (( FALTA_PYTHON || FALTA_AWS )); then
            command -v winget.exe >/dev/null || {
                echo 'Instale/atualize o Instalador de Aplicativo (WinGet) da Microsoft e execute novamente.' >&2
                exit 1
            }
            if (( FALTA_PYTHON )); then
                winget.exe install --id Python.Python.3.12 --exact --source winget \
                    --accept-source-agreements --accept-package-agreements --silent
            fi
            if (( FALTA_AWS )); then
                winget.exe install --id Amazon.AWSCLI --exact --source winget \
                    --accept-source-agreements --accept-package-agreements --silent
            fi
            # Atualiza apenas o PATH deste processo; não altera configurações do usuário.
            while IFS= read -r caminho; do
                caminho="${caminho%$'\r'}"
                [[ -z "$caminho" ]] || export PATH="$(cygpath -u "$caminho"):$PATH"
            done < <(powershell.exe -NoProfile -Command \
                '[Environment]::GetEnvironmentVariable("Path","Machine").Split(";"); [Environment]::GetEnvironmentVariable("Path","User").Split(";")')
            hash -r
        fi
        ;;
    *) echo 'Use Ubuntu/Bash ou Windows/Git Bash, conforme o guia.' >&2; exit 1 ;;
esac

selecionar_python || { echo 'Python 3.10+ com pip/venv ainda indisponível; confira a instalação e reabra o terminal.' >&2; exit 1; }
for comando in git ssh; do
    command -v "$comando" >/dev/null || { echo "$comando ainda indisponível; confira a instalação e reabra o terminal." >&2; exit 1; }
done
aws_v2 || { echo 'AWS CLI v2 ainda indisponível; confira a instalação e reabra o terminal.' >&2; exit 1; }
echo 'Ferramentas locais conferidas: Python com venv, Git, SSH e AWS CLI v2.'
echo "Validando autenticação AWS do perfil $PERFIL..."
if ! aws sts get-caller-identity --profile "$PERFIL" --region us-east-1 >/dev/null; then
    echo 'Acesso AWS não confirmado. Configure ou renove o perfil em ~/.aws/credentials e execute novamente.' >&2
    echo 'Nenhuma credencial foi alterada e nenhum recurso AWS foi criado.' >&2
    exit 1
fi
echo 'Autenticação AWS confirmada. Credenciais existentes preservadas.'
if (( PREPARAR_LOCAL )); then
    echo 'Preparação local concluída; nenhum recurso AWS foi criado.'
    exit 0
fi

# Fontes existentes são preservadas: não faz checkout, pull ou limpeza.
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
exec "${PYTHON[@]}" "$RAIZ/provisionar.py" --executar "${ARGUMENTOS[@]}"
