import argparse
import base64
import hashlib
import io
from datetime import datetime, timedelta, timezone
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
from uuid import uuid4
import zipfile
from urllib.request import urlopen

from implantacao import gravar_json, executar, validar_configuracao, verificar_fontes


class ErroAWS(RuntimeError):
    def __init__(self, operacao, codigo):
        self.codigo = codigo
        super().__init__(f"AWS {operacao}: {codigo}. Confira permissões e a sessão do laboratório.")


class AWS:
    def __init__(self, perfil="streamguard", regiao="us-east-1"):
        self.perfil, self.regiao = perfil, regiao

    def chamar(self, servico, operacao, dados=None, dry_run=False, arquivo_zip=None):
        comando = ["aws", "--profile", self.perfil, "--region", self.regiao,
                   "--no-cli-pager", servico, operacao, "--output", "json"]
        if dados:
            comando += ["--cli-input-json", json.dumps(dados)]
        if arquivo_zip is not None:
            comando += ["--zip-file", "fileb://" + str(Path(arquivo_zip).resolve())]
        if dry_run:
            comando.append("--dry-run")
        try:
            resultado = subprocess.run(comando, capture_output=True, text=True, encoding="utf-8", timeout=1200,
                                       env={**os.environ, "AWS_PAGER": "", "AWS_CLI_AUTO_PROMPT": "off"})
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError(f"AWS {operacao}: comando indisponível ou comunicação interrompida.") from None
        if resultado.returncode:
            erro = re.search(r"\(([^)]+)\)", resultado.stderr)
            codigo = erro.group(1) if erro else "FalhaCLI"
            if dry_run and codigo == "DryRunOperation":
                return {}
            raise ErroAWS(operacao, codigo)
        return json.loads(resultado.stdout) if resultado.stdout.strip() else {}


def rede_autorizada(valor):
    try:
        rede = ipaddress.ip_network(valor, strict=False)
    except ValueError:
        raise ValueError("Informe o IPv4 público de saída da rede, preferencialmente com /32.") from None
    if rede.version != 4 or rede.prefixlen != 32 or (not rede.network_address.is_global or rede.network_address.is_multicast):
        raise ValueError("A homologação exige um IPv4 público /32; IP privado, faixa ampla e 0.0.0.0/0 não são permitidos.")
    return str(rede)


def detectar_rede():
    encontrados = []
    for fonte in ("https://checkip.amazonaws.com", "https://api.ipify.org"):
        try:
            with urlopen(fonte, timeout=15) as resposta:
                ip = resposta.read(128).decode("ascii").strip()
            rede = rede_autorizada(ip)
            encontrados.append((rede, fonte))
        except (OSError, ValueError, UnicodeError):
            continue
    if not encontrados:
        raise ValueError("Não foi possível detectar um IPv4 público confiável; nenhuma regra será aplicada.")
    if len({rede for rede, _ in encontrados}) != 1:
        raise ValueError("As fontes retornaram IPs diferentes; confira a conexão antes de confirmar qualquer regra.")
    return encontrados[0][0], ", ".join(fonte for _, fonte in encontrados)


def nome_bucket(identificador, tentativa=0, agora=None):
    agora = agora or datetime.now(timezone(timedelta(hours=-3)))
    return f"streamguard-{agora:%d%m%y}-{identificador[:16]}-{tentativa}"


def descobrir(aws, chave, rede, perfil_instancia, subnet_id=None, nome_chave=None):
    identidade = aws.chamar("sts", "get-caller-identity")
    perfil = aws.chamar("iam", "get-instance-profile", {"InstanceProfileName": perfil_instancia})["InstanceProfile"]
    if not perfil.get("Roles"):
        raise ValueError("O instance profile precisa conter uma Role autorizada do laboratório.")
    chaves = aws.chamar("ec2", "describe-key-pairs")["KeyPairs"]
    nome_chave = nome_chave or chave.stem
    if nome_chave not in {c["KeyName"] for c in chaves}:
        raise ValueError("Chave EC2 não identificada pelo nome do arquivo; informe --nome-chave existente.")
    filtros = [{"Name": "subnet-id", "Values": [subnet_id]}] if subnet_id else [{"Name": "default-for-az", "Values": ["true"]}]
    subnets = aws.chamar("ec2", "describe-subnets", {"Filters": filtros})["Subnets"]
    escolhida = None
    for subnet in sorted(subnets, key=lambda s: s["SubnetId"]):
        if subnet.get("State") != "available":
            continue
        tabelas = aws.chamar("ec2", "describe-route-tables", {"Filters": [{"Name": "vpc-id", "Values": [subnet["VpcId"]]}]})["RouteTables"]
        explicitas = [t for t in tabelas if any(a.get("SubnetId") == subnet["SubnetId"] for a in t["Associations"])]
        aplicaveis = explicitas or [t for t in tabelas if any(a.get("Main") for a in t["Associations"])]
        if any(r.get("DestinationCidrBlock") == "0.0.0.0/0" and r.get("GatewayId", "").startswith("igw-") and r.get("State") == "active"
               for t in aplicaveis for r in t["Routes"]):
            escolhida = subnet
            break
    if not escolhida:
        raise ValueError("Nenhuma subnet existente com rota ativa para Internet Gateway. Informe --subnet sem alterar a rede automaticamente.")
    parametro = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
    ami = aws.chamar("ssm", "get-parameter", {"Name": parametro})["Parameter"]["Value"]
    imagem = aws.chamar("ec2", "describe-images", {"ImageIds": [ami]})["Images"][0]
    if imagem.get("OwnerId") != "099720109477" or imagem.get("Architecture") != "x86_64" or imagem.get("State") != "available":
        raise ValueError("A imagem resolvida não corresponde à imagem Canonical x86_64 disponível esperada.")
    return {"conta": identidade["Account"], "rede": rede, "subnet": escolhida["SubnetId"], "vpc": escolhida["VpcId"],
            "ami": ami, "dispositivo": imagem["RootDeviceName"], "perfil_instancia": perfil["InstanceProfileName"],
            "role_lambda": perfil["Roles"][0]["Arn"], "nome_chave": nome_chave}


class Provisionamento:
    def __init__(self, aws, pasta, estado):
        self.aws, self.pasta, self.estado = aws, pasta, estado

    def salvar(self):
        gravar_json(self.pasta / "implantacao.json", self.estado)

    def tags(self):
        return [{"Key": "Projeto", "Value": "StreamGuard"}, {"Key": "Implantacao", "Value": self.estado["id"]}]

    def parametros_instancia(self, grupo):
        p = self.estado["parametros"]
        return {"ImageId": p["ami"], "InstanceType": "t3.small", "MinCount": 1, "MaxCount": 1,
                "KeyName": p["nome_chave"], "IamInstanceProfile": {"Name": p["perfil_instancia"]},
                "NetworkInterfaces": [{"DeviceIndex": 0, "SubnetId": p["subnet"], "Groups": [grupo], "AssociatePublicIpAddress": True}],
                "BlockDeviceMappings": [{"DeviceName": p["dispositivo"], "Ebs": {"VolumeSize": 30, "VolumeType": "gp3", "Encrypted": True, "DeleteOnTermination": True}}],
                "MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled", "HttpPutResponseHopLimit": 2},
                "CreditSpecification": {"CpuCredits": "standard"}, "ClientToken": self.estado["id"],
                "TagSpecifications": [{"ResourceType": tipo, "Tags": self.tags() + [{"Key": "Name", "Value": "streamguard-" + self.estado["id"][:12]}]} for tipo in ("instance", "volume")]}

    def verificar_permissao_ec2(self):
        p = self.estado["parametros"]
        grupos = self.aws.chamar("ec2", "describe-security-groups", {"Filters": [{"Name": "vpc-id", "Values": [p["vpc"]]}, {"Name": "group-name", "Values": ["default"]}]})["SecurityGroups"]
        if len(grupos) != 1:
            raise ValueError("Grupo default da VPC não identificado para o dry-run, sem criar recursos.")
        self.aws.chamar("ec2", "run-instances", self.parametros_instancia(grupos[0]["GroupId"]), dry_run=True)

    def bucket(self):
        s = self.estado
        if s.get("bucket_pendente"):
            raise RuntimeError("Criação do bucket com resultado pendente. Confira esse nome no console antes de repetir; nenhum bucket existente será adotado automaticamente.")
        if not s.get("bucket"):
            for tentativa in range(10):
                nome = nome_bucket(s["id"], tentativa, datetime.fromisoformat(s["data"]))
                try:
                    self.aws.chamar("s3api", "head-bucket", {"Bucket": nome})
                except ErroAWS as erro:
                    if erro.codigo in ("403", "AccessDenied", "Forbidden"):
                        continue
                    if erro.codigo not in ("404", "NoSuchBucket", "NotFound"):
                        raise
                else:
                    continue
                s["bucket_pendente"] = nome
                self.salvar()
                try:
                    self.aws.chamar("s3api", "create-bucket", {"Bucket": nome, "ObjectOwnership": "BucketOwnerEnforced"})
                except ErroAWS as erro:
                    if erro.codigo == "BucketAlreadyExists":
                        s.pop("bucket_pendente")
                        self.salvar()
                        continue
                    raise
                s["bucket"] = nome
                s.pop("bucket_pendente")
                self.salvar()
                break
            else:
                raise RuntimeError("Não foi possível reservar um nome novo após dez tentativas.")
        nome = s["bucket"]
        comum = {"Bucket": nome, "ExpectedBucketOwner": s["parametros"]["conta"]}
        self.aws.chamar("s3api", "head-bucket", comum)
        self.aws.chamar("s3api", "put-bucket-tagging", {**comum, "Tagging": {"TagSet": self.tags()}})
        self.aws.chamar("s3api", "put-public-access-block", {**comum, "PublicAccessBlockConfiguration": {k: True for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")}})
        self.aws.chamar("s3api", "put-bucket-encryption", {**comum, "ServerSideEncryptionConfiguration": {"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}})
        return nome

    def grupo(self):
        p = self.estado["parametros"]
        nome = "streamguard-" + self.estado["id"]
        grupos = self.aws.chamar("ec2", "describe-security-groups", {"Filters": [{"Name": "vpc-id", "Values": [p["vpc"]]}, {"Name": "group-name", "Values": [nome]}]})["SecurityGroups"]
        if not grupos:
            self.aws.chamar("ec2", "create-security-group", {"GroupName": nome, "Description": "StreamGuard: rede autorizada", "VpcId": p["vpc"], "TagSpecifications": [{"ResourceType": "security-group", "Tags": self.tags()}]})
            grupos = self.aws.chamar("ec2", "describe-security-groups", {"Filters": [{"Name": "vpc-id", "Values": [p["vpc"]]}, {"Name": "group-name", "Values": [nome]}]})["SecurityGroups"]
        if len(grupos) != 1 or {t["Key"]: t["Value"] for t in grupos[0].get("Tags", [])}.get("Implantacao") != self.estado["id"]:
            raise ValueError("Grupo existente não pertence inequivocamente a esta implantação.")
        grupo = grupos[0]
        esperadas = [{"IpProtocol": "tcp", "FromPort": porta, "ToPort": porta, "IpRanges": [{"CidrIp": p["rede"]}]} for porta in (22, 3333)]
        presentes = set()
        for regra in grupo.get("IpPermissions", []):
            if regra.get("Ipv6Ranges") or regra.get("UserIdGroupPairs") or regra.get("PrefixListIds"):
                raise ValueError("Entrada desconhecida no grupo desta implantação; revisão manual necessária.")
            for faixa in regra.get("IpRanges", []):
                item = (regra.get("IpProtocol"), regra.get("FromPort"), regra.get("ToPort"), faixa["CidrIp"])
                if item not in {("tcp", porta, porta, p["rede"]) for porta in (22, 3333)}:
                    raise ValueError("Entrada desconhecida no grupo; nenhuma regra será substituída.")
                presentes.add(item)
        faltantes = [r for r in esperadas if ("tcp", r["FromPort"], r["ToPort"], p["rede"]) not in presentes]
        if faltantes:
            self.aws.chamar("ec2", "authorize-security-group-ingress", {"GroupId": grupo["GroupId"], "IpPermissions": faltantes})
        self.estado["grupo"] = grupo["GroupId"]
        self.salvar()
        return grupo["GroupId"]

    def instancia(self, grupo):
        instancia = self.estado.get("instancia")
        if not instancia:
            encontrados = self.aws.chamar("ec2", "describe-instances", {"Filters": [{"Name": "client-token", "Values": [self.estado["id"]]}]})["Reservations"]
            existentes = [i for r in encontrados for i in r["Instances"]]
            if len(existentes) > 1:
                raise ValueError("Mais de uma instância corresponde ao token; revisão manual necessária.")
            if existentes:
                instancia = existentes[0]["InstanceId"]
            else:
                if self.estado.get("instancia_pendente"):
                    raise RuntimeError("Criação da EC2 com resultado incerto. Confira o token no console; nenhuma substituta será criada automaticamente.")
                # O estado é persistido antes do POST, inclusive se sua resposta for perdida.
                self.estado["instancia_pendente"] = True
                self.salvar()
                try:
                    dados = self.aws.chamar("ec2", "run-instances", self.parametros_instancia(grupo))
                except ErroAWS as erro:
                    if erro.codigo in ("UnauthorizedOperation", "InvalidParameterValue", "InsufficientInstanceCapacity"):
                        self.estado.pop("instancia_pendente")
                        self.salvar()
                    raise
                instancia = dados["Instances"][0]["InstanceId"]
        self.estado["instancia"] = instancia
        self.estado.pop("instancia_pendente", None)
        self.salvar()
        for _ in range(90):
            dado = self.aws.chamar("ec2", "describe-instances", {"InstanceIds": [instancia]})["Reservations"][0]["Instances"][0]
            if {t["Key"]: t["Value"] for t in dado.get("Tags", [])}.get("Implantacao") != self.estado["id"]:
                raise ValueError("A instância retornada não pertence a esta implantação.")
            if dado["State"]["Name"] in ("terminated", "shutting-down", "stopped", "stopping"):
                raise RuntimeError("Instância não está disponível; nenhuma substituta será criada. Use outra pasta para uma implantação nova.")
            if dado["State"]["Name"] == "running" and dado.get("PublicIpAddress"):
                self.estado["ip"] = str(ipaddress.IPv4Address(dado["PublicIpAddress"]))
                self.salvar()
                return self.estado["ip"]
            time.sleep(5)
        raise RuntimeError("A instância ainda não ficou disponível. Reexecute com a mesma pasta para continuar.")


def fontes_ec2(raiz):
    """Empacota somente fontes operacionais; .env, chaves e dados não são enviados."""
    verificar_fontes(raiz.parent)
    captura = ("provisionar.py", "implantacao.py", "instalar.sh", "iniciar-ec2.sh",
               "compose.ec2.yaml", "Dockerfile.api", "Dockerfile.java", "preparar-captura.py",
               "executar.py", "api_captura.py", "script-captura.py", "lotes.py", "enviar-bronze.py",
               "configuracao_captura.py", "simulacao.py", "requirements.txt")
    site = raiz.parent / "stream-guard-site-institucional"
    arquivos = [(raiz / nome, "stream-guard-script-captura/" + nome) for nome in captura]
    for nome in ("app.js", "package.json"):
        arquivos.append((site / nome, "stream-guard-site-institucional/" + nome))
    for pasta in (site / "src", site / "public"):
        for arquivo in sorted(pasta.rglob("*")):
            if arquivo.is_symlink():
                raise ValueError("Link simbólico nas fontes da API; revise antes de implantar.")
            if arquivo.is_file() and not any(parte.startswith(".") for parte in arquivo.relative_to(site).parts):
                arquivos.append((arquivo, "stream-guard-site-institucional/" + arquivo.relative_to(site).as_posix()))
    for nome in ("lambda_function.py", "tratamento.py"):
        arquivos.append((raiz.parent / "stream-guard-etl" / nome, "stream-guard-etl/" + nome))
    conteudo = io.BytesIO()
    with tarfile.open(fileobj=conteudo, mode="w:gz") as pacote:
        for arquivo, nome in arquivos:
            if arquivo.is_symlink() or not arquivo.is_file():
                raise ValueError("Fonte ausente ou com link simbólico: " + nome)
            pacote.add(arquivo, arcname=nome, recursive=False)
    return base64.b64encode(conteudo.getvalue()).decode("ascii")


def implantar(pasta, estado, chave, bootstrap, fontes):
    ip = estado["ip"]
    ssh = ["ssh", "-i", str(chave), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new",
           "-o", "UserKnownHostsFile=" + str(pasta / "known_hosts"), "ubuntu@" + ip]
    for tentativa in range(60):
        try:
            executar([*ssh, "true"], timeout=20)
            break
        except RuntimeError:
            if tentativa == 59:
                raise RuntimeError("SSH indisponível. Confira a chave EC2, rede autorizada e IP; reexecute com a mesma pasta.") from None
            time.sleep(5)
    # Reserva a pasta pelo plano antes da transferência, permitindo retomar após falhas.
    identidade = {"id": estado["id"], "bucket": estado["bucket"], "origem": f"http://{ip}:3333"}
    protecao = ("from pathlib import Path; import json; "
                "p=Path.home()/'streamguard'; m=p/'implantacao-central.json'; "
                "assert not p.exists() or not any(p.iterdir()) or "
                "(m.is_file() and json.loads(m.read_text()).get('id')=='" + estado["id"] + "'), "
                "'Pasta EC2 preexistente; revise sem substituir arquivos'; "
                "p.mkdir(exist_ok=True); p.chmod(0o700); "
                "m.write_text(" + repr(json.dumps(identidade)) + ") if not m.exists() else None")
    import shlex
    executar([*ssh, "python3 -c " + shlex.quote(protecao)], timeout=30)
    executar([*ssh, 'base64 --decode | tar -xzf - -C "$HOME/streamguard"'], entrada=fontes, timeout=180)
    config = {"id": estado["id"], "bucket": estado["bucket"], "regiao": "us-east-1", "origem": f"http://{ip}:3333", **bootstrap}
    validar_configuracao(config)
    executar([*ssh, 'bash "$HOME/streamguard/stream-guard-script-captura/iniciar-ec2.sh"'], entrada=json.dumps(config), timeout=3600)
    estado["implantado"] = True
    gravar_json(pasta / "implantacao.json", estado)
    try:
        with urlopen(config["origem"], timeout=15) as resposta:
            if resposta.status != 200:
                raise RuntimeError("A API não retornou HTTP 200 no endereço externo.")
    except (OSError, ValueError):
        raise RuntimeError("API instalada, mas sem acesso HTTP pelo computador local. Confira IP, porta 3333 e rede autorizada antes de continuar.") from None
    conexao = {"implantacao": estado["id"], "S3_BUCKET": estado["bucket"], "AWS_DEFAULT_REGION": "us-east-1",
               "WEB_DATA_VIZ_URL": config["origem"], "AWS_PROFILE": estado.get("perfil_local", "streamguard")}
    gravar_json(pasta / "conexao-captura.json", conexao)
    print("API/MySQL instalados e endereço HTTP acessível pelo computador local.")


def configurar_lambda(aws, pasta, estado, raiz):
    nome = "streamguard-" + estado["id"][:12] + "-etl"
    regra = nome + "-5min"
    camada = "arn:aws:lambda:us-east-1:336392948345:layer:AWSSDKPandas-Python312:32"
    pacote = pasta / "lambda.zip"
    # Datas fixas no ZIP permitem conferir se o código já foi enviado em outra tentativa.
    with zipfile.ZipFile(pacote, "w", compression=zipfile.ZIP_DEFLATED) as arquivo:
        for modulo in ("lambda_function.py", "tratamento.py"):
            info = zipfile.ZipInfo(modulo, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            arquivo.writestr(info, (raiz.parent / "stream-guard-etl" / modulo).read_bytes())
    pacote.chmod(0o600)
    digest = base64.b64encode(hashlib.sha256(pacote.read_bytes()).digest()).decode("ascii")
    tags = {"Projeto": "StreamGuard", "Implantacao": estado["id"]}
    configuracao = {"FunctionName": nome, "Runtime": "python3.12", "Role": estado["parametros"]["role_lambda"],
                    "Handler": "lambda_function.lambda_handler", "Timeout": 180, "MemorySize": 1024,
                    "Environment": {"Variables": {"S3_BUCKET": estado["bucket"], "GOLD_JANELA_MINUTOS": "5", "RETENCAO_DIAS": "90"}},
                    "Layers": [camada]}
    try:
        atual = aws.chamar("lambda", "get-function", {"FunctionName": nome})
    except ErroAWS as erro:
        if erro.codigo != "ResourceNotFoundException":
            raise
        aws.chamar("lambda", "create-function", {**configuracao, "Architectures": ["x86_64"], "Tags": tags}, arquivo_zip=pacote)
    else:
        if atual.get("Tags", {}).get("Implantacao") != estado["id"]:
            raise ValueError("Lambda existente sem identificação deste plano; nenhuma alteração aplicada.")
        esperar_lambda(aws, nome)
        if atual["Configuration"]["CodeSha256"] != digest:
            aws.chamar("lambda", "update-function-code", {"FunctionName": nome}, arquivo_zip=pacote)
            esperar_lambda(aws, nome)
        aws.chamar("lambda", "update-function-configuration", configuracao)
    dados = esperar_lambda(aws, nome)
    if dados["CodeSha256"] != digest:
        raise RuntimeError("O código retornado pela Lambda difere do pacote enviado.")
    aws.chamar("lambda", "put-function-concurrency", {"FunctionName": nome, "ReservedConcurrentExecutions": 1})
    estado["lambda"] = nome
    gravar_json(pasta / "implantacao.json", estado)
    try:
        existente = aws.chamar("events", "describe-rule", {"Name": regra})
    except ErroAWS as erro:
        if erro.codigo != "ResourceNotFoundException":
            raise
    else:
        marcacoes = aws.chamar("events", "list-tags-for-resource", {"ResourceARN": existente["Arn"]})
        if {t["Key"]: t["Value"] for t in marcacoes.get("Tags", [])}.get("Implantacao") != estado["id"]:
            raise ValueError("Regra existente sem identificação deste plano; revise sem substituir.")
    resultado = aws.chamar("events", "put-rule", {"Name": regra, "ScheduleExpression": "rate(5 minutes)", "State": "DISABLED",
                                                   "Tags": [{"Key": k, "Value": v} for k, v in tags.items()]})
    arn_regra = resultado["RuleArn"]
    permissao = {"FunctionName": nome, "StatementId": "streamguard-agendamento", "Action": "lambda:InvokeFunction",
                 "Principal": "events.amazonaws.com", "SourceArn": arn_regra, "SourceAccount": estado["parametros"]["conta"]}
    try:
        politica = json.loads(aws.chamar("lambda", "get-policy", {"FunctionName": nome})["Policy"])
    except ErroAWS as erro:
        if erro.codigo != "ResourceNotFoundException":
            raise
        politica = {"Statement": []}
    existentes = [item for item in politica["Statement"] if item.get("Sid") == permissao["StatementId"]]
    if existentes:
        item = existentes[0]
        if (item.get("Effect") != "Allow" or item.get("Principal") != {"Service": "events.amazonaws.com"}
                or item.get("Action") != "lambda:InvokeFunction"
                or item.get("Condition", {}).get("ArnLike", {}).get("AWS:SourceArn") != arn_regra
                or item.get("Condition", {}).get("StringEquals", {}).get("AWS:SourceAccount") != estado["parametros"]["conta"]):
            raise ValueError("Permissão de agendamento divergente; não foi substituída.")
    else:
        aws.chamar("lambda", "add-permission", permissao)
    alvos = aws.chamar("events", "put-targets", {"Rule": regra, "Targets": [{"Id": "etl", "Arn": dados["FunctionArn"]}]})
    if alvos.get("FailedEntryCount", 0):
        raise RuntimeError("EventBridge não confirmou o destino; a regra permanece desativada.")
    aws.chamar("events", "enable-rule", {"Name": regra})
    estado["regra_lambda"] = regra
    gravar_json(pasta / "implantacao.json", estado)
    print("Lambda configurada e agendada a cada 5 minutos. O processamento de dados será conferido após a primeira captura.")


def esperar_lambda(aws, nome):
    for _ in range(60):
        dados = aws.chamar("lambda", "get-function-configuration", {"FunctionName": nome})
        if dados.get("State") == "Failed" or dados.get("LastUpdateStatus") == "Failed":
            raise RuntimeError("A Lambda informou falha de configuração; consulte seu estado antes de repetir.")
        if dados.get("State") == "Active" and dados.get("LastUpdateStatus") != "InProgress":
            return dados
        time.sleep(5)
    raise RuntimeError("A Lambda ainda não ficou disponível; reexecute com a mesma pasta de implantação.")


def main():
    parser = argparse.ArgumentParser(description="Planeja uma implantação nova; --executar pede confirmação e cria recursos AWS.")
    parser.add_argument("--executar", action="store_true")
    parser.add_argument("--planejar", action="store_true", help="Somente consultas e dry-run; não cria recursos.")
    parser.add_argument("--somente-infra", action="store_true",
                        help="Cria somente bucket, rede e EC2; não instala a ETL antiga nem os serviços. Use no fluxo Lambda v2.")
    parser.add_argument("--diretorio", type=Path, default=Path.home() / ".streamguard/implantacao")
    parser.add_argument("--perfil", default="streamguard")
    parser.add_argument("--rede")
    parser.add_argument("--chave-ssh", type=Path)
    parser.add_argument("--nome-chave")
    parser.add_argument("--perfil-instancia", default="LabInstanceProfile")
    parser.add_argument("--subnet")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        raise ValueError("Use Python 3.10 ou superior.")
    if args.planejar:
        args.executar = False
    if args.chave_ssh is None:
        args.chave_ssh = Path(input("Caminho local da chave SSH (.pem): ").strip().strip('"'))
    args.chave_ssh = args.chave_ssh.expanduser().resolve()
    raiz = Path(__file__).resolve().parent
    fontes = None if args.somente_infra else fontes_ec2(raiz)
    for comando in ("aws", "ssh"):
        if not shutil.which(comando):
            raise ValueError(f"Instale {comando} antes de executar.")
    if not args.chave_ssh.is_file():
        raise ValueError("Informe o arquivo privado da chave EC2 existente; ele não será enviado à instância.")
    pasta = args.diretorio.expanduser().resolve()
    if any((p / ".git").exists() for p in (pasta, *pasta.parents)):
        raise ValueError("Use uma pasta de implantação fora dos repositórios Git.")
    pasta.mkdir(parents=True, exist_ok=True)
    pasta.chmod(0o700)
    trava = pasta / "execucao.lock"
    try:
        arquivo = trava.open("x")
    except FileExistsError:
        raise ValueError("Existe uma execução registrada. Confirme que terminou antes de revisar a trava local.") from None
    try:
        with arquivo:
            arquivo.write(str(os.getpid()))
        caminho = pasta / "implantacao.json"
        estado = json.loads(caminho.read_text()) if caminho.exists() else {"id": uuid4().hex, "data": datetime.now(timezone(timedelta(hours=-3))).isoformat()}
        if not isinstance(estado, dict) or not re.fullmatch(r"[a-f0-9]{32}", estado.get("id", "")) or not isinstance(estado.get("data"), str):
            raise ValueError("Estado local inválido; preserve a pasta para revisão antes de provisionar.")
        datetime.fromisoformat(estado["data"])
        aws = AWS(args.perfil)
        rede, fonte = (rede_autorizada(args.rede), "informado por --rede") if args.rede else detectar_rede()
        print(f"Rede de entrada planejada: {rede} (TCP 3333 e SSH 22). Fonte: {fonte}.")
        parametros = descobrir(aws, args.chave_ssh, rede, args.perfil_instancia, args.subnet, args.nome_chave)
        if "parametros" in estado:
            for nome in ("conta", "rede", "subnet", "vpc", "perfil_instancia", "nome_chave"):
                if estado["parametros"][nome] != parametros[nome]:
                    raise ValueError("O plano existente diverge da conta/rede/chave informada. Não foi alterado; use uma pasta nova para outra implantação.")
        else:
            estado["parametros"] = parametros
        estado["parametros"]["role_lambda"] = parametros["role_lambda"]
        estado["perfil_local"] = args.perfil
        provisionamento = Provisionamento(aws, pasta, estado)
        provisionamento.salvar()
        provisionamento.verificar_permissao_ec2()
        if not args.somente_infra:
            aws.chamar("lambda", "get-layer-version", {"LayerName": "arn:aws:lambda:us-east-1:336392948345:layer:AWSSDKPandas-Python312", "VersionNumber": 32})
            print("ETL planejada: Lambda Python 3.12, 1024 MiB, timeout 180 s, camada Pandas e execução a cada 5 minutos.")
        print(f"Conta confirmada (final {parametros['conta'][-4:]}), perfil {args.perfil}, região us-east-1.")
        print(f"Plano {estado['id']}: bucket novo {estado.get('bucket') or nome_bucket(estado['id'], agora=datetime.fromisoformat(estado['data']))}; EC2 Ubuntu 24.04 t3.small, EBS gp3 30 GiB; grupo exclusivo TCP 22/3333 de {rede}.")
        print("Role existente: " + parametros["perfil_instancia"] + ". MySQL sem porta pública; HTTP acadêmico sem criptografia; Jira desativado.")
        print("Dry-run EC2 aprovado. Criação S3, autorização efetiva da Role no bucket e instalação só serão comprovadas na execução real.")
        if not args.executar:
            print("Somente plano; nenhum recurso criado. Use a mesma pasta e --executar após autorização.")
            return
        if input("Confirme TCP 3333 e SSH 22 exclusivamente de " + rede + ". Para criar/continuar esta implantação, digite CRIAR " + estado["id"] + ": ").strip() != "CRIAR " + estado["id"]:
            print("Execução cancelada sem provisionamento.")
            return
        bootstrap = {}
        if not estado.get("implantado") and not args.somente_infra:
            bootstrap = {"empresa": input("Razão social da empresa inicial: ").strip(), "cnpj": input("CNPJ (14 dígitos): ").strip(),
                         "nome": input("Nome do Admin inicial: ").strip(), "email": input("E-mail do Admin inicial: ").strip(),
                         "senha": getpass.getpass("Senha pessoal do Admin (não será gravada no plano): ")}
            validar_configuracao({"id": estado["id"], "bucket": "streamguard-validacao", "regiao": "us-east-1", "origem": "http://127.0.0.1:3333", **bootstrap})
        provisionamento.bucket()
        grupo = provisionamento.grupo()
        provisionamento.instancia(grupo)
        if args.somente_infra:
            estado["infraestrutura_pronta"] = True
            provisionamento.salvar()
            print(f"Infraestrutura pronta: EC2 {estado['instancia']}, IP {estado['ip']}, bucket {estado['bucket']}.")
            print("Nenhum serviço ou ETL foi instalado. Continue pelas etapas de aplicação e Lambda do Guia_de_Instalacao.pdf.")
            return
        implantar(pasta, estado, args.chave_ssh.resolve(), bootstrap, fontes)
        configurar_lambda(aws, pasta, estado, raiz)
        captura_local = pasta / "captura"
        captura_local.mkdir(exist_ok=True)
        for nome in ("preparar-captura.py", "implantacao.py", "executar.py", "api_captura.py", "script-captura.py",
                     "lotes.py", "enviar-bronze.py", "configuracao_captura.py", "simulacao.py", "requirements.txt"):
            shutil.copyfile(raiz / nome, captura_local / nome)
        executar([sys.executable, "-B", str(captura_local / "preparar-captura.py"), "--conexao", str(pasta / "conexao-captura.json")], timeout=1200)
        python_captura = captura_local / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        print("Captura configurada no computador local. Execute: \"" + str(python_captura) + "\" \"" + str(captura_local / "executar.py") + "\"")
        print(f"Implantação {estado['id']}: EC2 {estado['instancia']}, bucket {estado['bucket']}, API http://{estado['ip']}:3333.")
    finally:
        trava.unlink()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError) as erro:
        print(str(erro) if isinstance(erro, (ValueError, RuntimeError)) else "Falha de configuração local; confira arquivos/permissões e reexecute com a mesma pasta.")
        raise SystemExit(1)
