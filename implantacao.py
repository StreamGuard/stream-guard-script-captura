import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from urllib.parse import urlsplit


REPOSITORIOS = {"stream-guard-script-captura": "main", "stream-guard-site-institucional": "develop",
                "stream-guard-etl": "main", "StreamGuardLogs": "main"}


def executar(comando, entrada=None, timeout=1200):
    try:
        resultado = subprocess.run(comando, input=entrada, text=True, capture_output=True, timeout=timeout,
                                   stdin=subprocess.DEVNULL if entrada is None else None)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError(f"Falha ao executar {Path(comando[0]).name}; comando ausente ou tempo esgotado.") from None
    if resultado.returncode:
        # Saídas de SQL, Compose e SSH podem conter segredos; não as propagar para logs.
        if Path(comando[0]).stem == "ssh":
            erros = [l.removeprefix("STREAMGUARD_ERRO: ") for l in resultado.stderr.splitlines() if l.startswith("STREAMGUARD_ERRO: ")]
            if erros:
                raise RuntimeError("Instalação remota: " + erros[-1])
        raise RuntimeError(f"{Path(comando[0]).name} falhou (código {resultado.returncode}). Confira a etapa e seus arquivos privados; reexecute sem apagar dados.")
    return resultado.stdout


def gravar_json(caminho, dados):
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(caminho.suffix + ".tmp")
    with temporario.open("w", encoding="utf-8", newline="\n") as arquivo:
        temporario.chmod(0o600)
        json.dump(dados, arquivo, ensure_ascii=False, indent=2)
        arquivo.flush()
        os.fsync(arquivo.fileno())
    temporario.replace(caminho)


def ler_env(caminho):
    valores = {}
    for linha in Path(caminho).read_text(encoding="utf-8-sig").splitlines():
        if linha.strip() and not linha.lstrip().startswith("#") and "=" in linha:
            chave, valor = linha.split("=", 1)
            valor = valor.strip()
            if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
                valor = valor[1:-1]
            valores[chave.strip()] = valor
    return valores


def preservar_env(caminho, valores, obrigatorios=None, modelo=None):
    caminho = Path(caminho)
    if caminho.exists():
        atual = ler_env(caminho)
        if any(atual.get(k) != str(v) for k, v in (obrigatorios or valores).items()):
            raise ValueError(f"Configuração existente incompatível em {caminho.name}; arquivo preservado, revise antes de continuar.")
        return atual
    for chave, valor in valores.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", chave) or any(c in str(valor) for c in "\r\n\0"):
            raise ValueError("Configuração de ambiente inválida.")
    restantes = {k: str(v) for k, v in valores.items()}
    linhas = []
    if modelo:
        for linha in Path(modelo).read_text(encoding="utf-8-sig").splitlines():
            chave = linha.split("=", 1)[0].strip()
            if not linha.lstrip().startswith("#") and chave in restantes:
                linhas.append(chave + "=" + restantes.pop(chave))
            else:
                linhas.append(linha)
    linhas.extend(k + "=" + v for k, v in restantes.items())
    with caminho.open("x", encoding="utf-8", newline="\n") as arquivo:
        caminho.chmod(0o600)
        arquivo.write("# Configuração privada desta implantação. Não versionar.\n")
        arquivo.write("\n".join(linhas) + "\n")
    return ler_env(caminho)


def validar_configuracao(config):
    if not re.fullmatch(r"[a-f0-9]{32}", config.get("id", "")):
        raise ValueError("Identificador de implantação inválido.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", config.get("bucket", "")) or config.get("regiao") != "us-east-1":
        raise ValueError("Bucket/região de implantação inválidos.")
    url = urlsplit(config.get("origem", ""))
    if url.scheme != "http" or not url.hostname or url.port != 3333 or url.username or url.password or url.path or url.query or url.fragment:
        raise ValueError("Informe a origem HTTP da API na porta 3333, sem credenciais nem caminhos.")
    campos = ("empresa", "cnpj", "nome", "email", "senha")
    if any(c in config for c in campos):
        if any(not isinstance(config.get(c), str) or not config[c].strip() for c in campos):
            raise ValueError("Preencha empresa e Admin inicial completos.")
        if not re.fullmatch(r"\d{14}", config["cnpj"]) or len(config["empresa"]) > 100 or len(config["nome"]) > 100 or len(config["email"]) > 100 or "@" not in config["email"] or "." not in config["email"] or len(config["senha"]) < 12 or len(config["senha"]) > 1024:
            raise ValueError("Empresa/Admin inválidos; use CNPJ de 14 dígitos e senha pessoal de 12 a 1024 caracteres.")
        if "@streamguard.com" in config["email"]:
            raise ValueError("Use um e-mail do Admin da empresa: o domínio reservado @streamguard.com não retorna vínculo empresarial para a captura no login atual.")


def preparar_configuracoes(infra, config):
    validar_configuracao(config)
    marcador = infra / "implantacao-central.json"
    identidade = {"id": config["id"], "bucket": config["bucket"], "origem": config["origem"]}
    if marcador.exists() and json.loads(marcador.read_text()) != identidade:
        raise ValueError("A pasta central já pertence a outra implantação; nenhum arquivo será substituído.")
    if not marcador.exists() and any((infra / n).exists() for n in ("banco.env", "api.env", "java.env", "compose.yaml")):
        raise ValueError("Ambiente preexistente não gerenciado por esta implantação. Não será adotado automaticamente.")
    if not marcador.exists():
        gravar_json(marcador, identidade)
    banco = preservar_env(infra / "banco.env", {"MYSQL_DATABASE": "streamguard", "MYSQL_ROOT_PASSWORD": secrets.token_hex(24)}, {"MYSQL_DATABASE": "streamguard"})
    api_esperado = {"AMBIENTE_PROCESSO": "producao", "APP_HOST": "0.0.0.0", "APP_PORT": "3333", "APP_ORIGIN": config["origem"],
                    "DB_HOST": "banco", "DB_PORT": "3306", "DB_DATABASE": "streamguard", "DB_USER": "streamguard_api", "SESSION_COOKIE_SECURE": "false"}
    api = preservar_env(infra / "api.env", {**api_esperado, "DB_PASSWORD": secrets.token_hex(24), "SESSION_SECRET": secrets.token_hex(48), "SESSION_TTL_MINUTES": "30"}, api_esperado,
                        modelo=infra / "stream-guard-site-institucional/.env.example")
    if not re.fullmatch(r"[a-fA-F0-9]{48}", banco.get("MYSQL_ROOT_PASSWORD", "")) or not re.fullmatch(r"[a-fA-F0-9]{48}", api.get("DB_PASSWORD", "")) or len(api.get("SESSION_SECRET", "")) < 32:
        raise ValueError("Segredos privados centrais ausentes ou incompatíveis com o instalador; arquivos preservados.")
    preservar_env(infra / "java.env", {"S3_BUCKET": config["bucket"], "AWS_DEFAULT_REGION": config["regiao"], "WEB_DATA_VIZ_URL": "http://api:3333",
                  "CAPTURA_FUSO": "America/Sao_Paulo", "ESTADO_DIRETORIO": "/estado", "JIRA_ENVIAR": "false"}, modelo=infra / "StreamGuardLogs/.env.example")
    preservar_env(infra / "stream-guard-etl/.env", {"S3_BUCKET": config["bucket"], "AWS_DEFAULT_REGION": config["regiao"], "GOLD_JANELA_MINUTOS": "5"}, modelo=infra / "stream-guard-etl/.env.example")
    if "AWS_PROFILE" in ler_env(infra / "stream-guard-etl/.env"):
        raise ValueError("ETL na EC2 não deve conter AWS_PROFILE; arquivo preservado para revisão.")
    return api


def sql_inicial(fonte, config):
    validar_configuracao(config)
    if "senha" not in config:
        raise ValueError("O banco está vazio; informe os dados do Admin inicial ao executar novamente.")
    texto = Path(fonte).read_text(encoding="utf-8-sig")
    inicio = texto.index("CREATE TABLE empresa")
    fim = texto.index("-- Sample data", inicio)
    ddl = texto[inicio:fim]
    if re.search(r"\b(?:DROP|TRUNCATE|DELETE)\b", ddl, re.I):
        raise ValueError("O trecho inicial contém SQL destrutivo e não será executado.")
    def literal(valor):
        return "CONVERT(0x" + valor.encode("utf-8").hex() + " USING utf8mb4)"
    codigo = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    senha_hash = hashlib.sha256(config["senha"].encode("utf-8")).hexdigest()
    return ddl + "\nSTART TRANSACTION;\nINSERT INTO permissao (nome) VALUES ('Root'),('Admin'),('Gerente'),('User');\n" + \
        f"INSERT INTO empresa (razao_social,cnpj,codigo_empresa) VALUES ({literal(config['empresa'])},{literal(config['cnpj'])},'{codigo}');\n" + \
        f"INSERT INTO usuario(nome,cargo,email,senha_hash,fk_empresa,fk_permissao) VALUES ({literal(config['nome'])},'Admin',{literal(config['email'])},'{senha_hash}',LAST_INSERT_ID(),2);\nCOMMIT;\n"


def atualizar_repositorios(infra):
    for nome, branch in REPOSITORIOS.items():
        pasta = infra / nome
        url = "https://github.com/StreamGuard/" + nome + ".git"
        if not pasta.exists():
            executar(["git", "clone", "--branch", branch, "--single-branch", url, str(pasta)])
        else:
            if not (pasta / ".git").is_dir() or executar(["git", "-C", str(pasta), "remote", "get-url", "origin"]).strip() != url:
                raise ValueError("Checkout existente inesperado: " + nome)
            if executar(["git", "-C", str(pasta), "branch", "--show-current"]).strip() != branch or executar(["git", "-C", str(pasta), "status", "--porcelain"]).strip():
                raise ValueError("Branch inesperada ou alterações locais em " + nome + "; nada foi descartado.")
            executar(["git", "-C", str(pasta), "fetch", "origin"])
            executar(["git", "-C", str(pasta), "merge", "--ff-only", "origin/" + branch])
            if executar(["git", "-C", str(pasta), "rev-parse", "HEAD"]).strip() != executar(["git", "-C", str(pasta), "rev-parse", "origin/" + branch]).strip():
                raise ValueError("Há commits locais desconhecidos em " + nome + "; nada foi descartado.")


def instalar(infra, config):
    origem = infra / "stream-guard-script-captura"
    api = preparar_configuracoes(infra, config)
    executar(["bash", str(infra / "stream-guard-etl/instalar.sh"), "--validar-config"])
    destino_compose = infra / "compose.yaml"
    esperado = (origem / "compose.ec2.yaml").read_bytes()
    if destino_compose.exists() and destino_compose.read_bytes() != esperado:
        raise ValueError("compose.yaml existente difere do modelo; arquivo preservado para revisão.")
    if not destino_compose.exists():
        destino_compose.write_bytes(esperado)
    compose = ["sudo", "docker", "compose", "--project-name", "streamguard", "--project-directory", str(infra), "-f", str(destino_compose)]
    executar([*compose, "up", "-d", "--wait", "--wait-timeout", "240", "banco"])

    def mysql(sql):
        return executar([*compose, "exec", "-T", "banco", "sh", "-c", 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -N -u root streamguard'], entrada=sql)

    tabelas = int(mysql("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE();").strip())
    marcador = infra / "banco-inicializado.json"
    if not marcador.exists():
        if tabelas:
            raise ValueError("Banco não vazio sem confirmação desta implantação. Não executar SQL inicial; revise a tentativa anterior sem excluir registros.")
        mysql(sql_inicial(infra / "stream-guard-site-institucional/src/database/StreamGuard-ScripBD.sql", config))
        gravar_json(marcador, {"id": config["id"]})
    elif json.loads(marcador.read_text())["id"] != config["id"]:
        raise ValueError("Banco pertence a outra implantação.")
    elif tabelas != 8:
        raise ValueError("Estrutura MySQL inesperada; não aplicar inicialização ou migração automaticamente.")
    senha = api["DB_PASSWORD"]
    mysql(f"CREATE USER IF NOT EXISTS 'streamguard_api'@'%' IDENTIFIED BY '{senha}';\nALTER USER 'streamguard_api'@'%' IDENTIFIED BY '{senha}';\nGRANT SELECT, INSERT, UPDATE, DELETE ON streamguard.* TO 'streamguard_api'@'%';")
    executar([*compose, "up", "-d", "--build", "api"])
    verificar_api(compose)
    executar(["bash", str(infra / "stream-guard-etl/instalar.sh")])
    python = str(infra / "stream-guard-etl/.venv/bin/python")
    executar([python, "-c", "import boto3; s=boto3.client('s3',region_name='us-east-1'); s.list_objects_v2(Bucket=" + repr(config["bucket"]) + ",Prefix='bronze/',MaxKeys=1)"], timeout=90)
    executar([*compose, "up", "-d", "--build", "java"])
    time.sleep(5)
    processos = executar([*compose, "ps", "--format", "json"])
    registros = json.loads(processos) if processos.lstrip().startswith("[") else [json.loads(l) for l in processos.splitlines() if l.strip()]
    if {p.get("Service") for p in registros if p.get("State") == "running"} != {"banco", "api", "java"}:
        raise RuntimeError("Um serviço central não permaneceu em execução. Consulte o estado com docker compose ps; não remova os volumes.")
    hashes = {nome: executar(["git", "-C", str(infra / nome), "rev-parse", "HEAD"]).strip() for nome in REPOSITORIOS}
    gravar_json(infra / "versoes-implantadas.json", hashes)
    print("MySQL inicializado, sessão HTTP configurada, API consultando banco, ETL agendada, leitura S3 autorizada e Java iniciado sem Jira.")


def verificar_api(compose):
    codigo = "const h=require('http');const q=h.get('http://127.0.0.1:3333/metricas/configuracoes/1',r=>{let s='';r.on('data',x=>s+=x);r.on('end',()=>{try{process.exit(r.statusCode===200&&Array.isArray(JSON.parse(s))?0:1)}catch{process.exit(1)}})});q.setTimeout(5000,()=>{q.destroy();process.exit(1)});q.on('error',()=>process.exit(1));"
    for tentativa in range(20):
        try:
            executar([*compose, "exec", "-T", "api", "node", "-e", codigo], timeout=10)
            return
        except RuntimeError:
            if tentativa == 19:
                raise RuntimeError("API não confirmou consulta ao banco. Confira api.env e docker compose ps sem exibir segredos.") from None
            time.sleep(2)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("bootstrap", "instalar"):
        raise ValueError("Use iniciar-ec2.sh ou instalar.sh; configuração privada é recebida por stdin.")
    config = json.load(sys.stdin)
    validar_configuracao(config)
    infra = Path(__file__).resolve().parent.parent
    if any(c in str(infra) for c in (" ", "%", "\\")):
        raise ValueError("A instalação central requer caminho Linux sem espaços, % ou barras invertidas.")
    if sys.argv[1] == "bootstrap":
        atualizar_repositorios(infra)
        executar(["bash", str(infra / "stream-guard-script-captura/instalar.sh")], entrada=json.dumps(config), timeout=3600)
    else:
        instalar(infra, config)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError) as erro:
        print("STREAMGUARD_ERRO: " + (str(erro) if isinstance(erro, (ValueError, RuntimeError)) else "Configuração ou arquivo indisponível; nenhum dado será removido."), file=sys.stderr)
        raise SystemExit(1)
