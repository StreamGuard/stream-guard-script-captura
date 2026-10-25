import argparse
from datetime import datetime, timedelta, timezone
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from uuid import uuid4
from urllib.request import urlopen

from implantacao import gravar_json, executar, validar_configuracao


class ErroAWS(RuntimeError):
    def __init__(self, operacao, codigo):
        self.codigo = codigo
        super().__init__(f"AWS {operacao}: {codigo}. Confira permissões e a sessão do laboratório.")


class AWS:
    def __init__(self, perfil="streamguard", regiao="us-east-1"):
        self.perfil, self.regiao = perfil, regiao

    def chamar(self, servico, operacao, dados=None, dry_run=False):
        comando = ["aws", "--profile", self.perfil, "--region", self.regiao,
                   "--no-cli-pager", servico, operacao, "--output", "json"]
        if dados:
            comando += ["--cli-input-json", json.dumps(dados)]
        if dry_run:
            comando.append("--dry-run")
        try:
            resultado = subprocess.run(comando, capture_output=True, text=True, timeout=1200,
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
            "ami": ami, "dispositivo": imagem["RootDeviceName"], "perfil_instancia": perfil["InstanceProfileName"], "nome_chave": nome_chave}


class Provisionamento:
    def __init__(self, aws, pasta, estado):
        self.aws, self.pasta, self.estado = aws, pasta, estado

    def salvar(self):
        gravar_json(self.pasta / "implantacao.json", self.estado)

    def tags(self):
        return [{"Key": "Projeto", "Value": "StreamGuard"}, {"Key": "Implantacao", "Value": self.estado["id"]}]

    def parametros_instancia(self, grupo):
        p = self.estado["parametros"]
        return {"ImageId": p["ami"], "InstanceType": "t3.medium", "MinCount": 1, "MaxCount": 1,
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


def implantar(pasta, estado, chave, bootstrap):
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
    config = {"id": estado["id"], "bucket": estado["bucket"], "regiao": "us-east-1", "origem": f"http://{ip}:3333", **bootstrap}
    validar_configuracao(config)
    comando = ('set -eu; sudo -n true; sudo apt-get update </dev/null; '
               'sudo apt-get install -y git python3 </dev/null; mkdir -p "$HOME/streamguard"; '
               'cd "$HOME/streamguard"; export GIT_TERMINAL_PROMPT=0; '
               'if [ ! -d stream-guard-script-captura ]; then git clone --branch main --single-branch '
               'https://github.com/StreamGuard/stream-guard-script-captura.git </dev/null; '
               'else cd stream-guard-script-captura; test "$(git branch --show-current)" = main; '
               'test "$(git remote get-url origin)" = https://github.com/StreamGuard/stream-guard-script-captura.git; '
               'test -z "$(git status --porcelain)"; git fetch origin </dev/null; '
               'git merge --ff-only origin/main </dev/null; test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"; cd ..; fi; '
               'exec bash stream-guard-script-captura/iniciar-ec2.sh')
    executar([*ssh, comando], entrada=json.dumps(config), timeout=3600)
    conexao = {"implantacao": estado["id"], "S3_BUCKET": estado["bucket"], "AWS_DEFAULT_REGION": "us-east-1",
               "WEB_DATA_VIZ_URL": config["origem"], "AWS_PROFILE": estado.get("perfil_local", "streamguard")}
    gravar_json(pasta / "conexao-captura.json", conexao)
    estado["implantado"] = True
    gravar_json(pasta / "implantacao.json", estado)
    print("Serviços centrais instalados; API/MySQL e leitura S3 verificados. Jira permanece desativado.")
    print("Configuração sem credenciais para o PC: " + str(pasta / "conexao-captura.json"))


def main():
    parser = argparse.ArgumentParser(description="Planeja uma implantação nova; --executar pede confirmação e cria recursos AWS.")
    parser.add_argument("--executar", action="store_true")
    parser.add_argument("--diretorio", type=Path, default=Path.home() / ".streamguard/implantacao")
    parser.add_argument("--perfil", default="streamguard")
    parser.add_argument("--rede")
    parser.add_argument("--chave-ssh", type=Path, required=True)
    parser.add_argument("--nome-chave")
    parser.add_argument("--perfil-instancia", default="LabInstanceProfile")
    parser.add_argument("--subnet")
    args = parser.parse_args()
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
        estado["perfil_local"] = args.perfil
        provisionamento = Provisionamento(aws, pasta, estado)
        provisionamento.salvar()
        provisionamento.verificar_permissao_ec2()
        print(f"Conta confirmada (final {parametros['conta'][-4:]}), perfil {args.perfil}, região us-east-1.")
        print(f"Plano {estado['id']}: bucket novo {estado.get('bucket') or nome_bucket(estado['id'], agora=datetime.fromisoformat(estado['data']))}; EC2 Ubuntu 24.04 t3.medium, EBS gp3 30 GiB; grupo exclusivo TCP 22/3333 de {rede}.")
        print("Role existente: " + parametros["perfil_instancia"] + ". MySQL sem porta pública; HTTP acadêmico sem criptografia; Jira desativado.")
        print("Dry-run EC2 aprovado. Criação S3, autorização efetiva da Role no bucket e instalação só serão comprovadas na execução real.")
        if not args.executar:
            print("Somente plano; nenhum recurso criado. Use a mesma pasta e --executar após autorização.")
            return
        if input("Confirme TCP 3333 e SSH 22 exclusivamente de " + rede + ". Para criar/continuar esta implantação, digite CRIAR " + estado["id"] + ": ").strip() != "CRIAR " + estado["id"]:
            print("Execução cancelada sem provisionamento.")
            return
        bootstrap = {}
        if not estado.get("implantado"):
            bootstrap = {"empresa": input("Razão social da empresa inicial: ").strip(), "cnpj": input("CNPJ (14 dígitos): ").strip(),
                         "nome": input("Nome do Admin inicial: ").strip(), "email": input("E-mail do Admin inicial: ").strip(),
                         "senha": getpass.getpass("Senha pessoal do Admin (não será gravada no plano): ")}
            validar_configuracao({"id": estado["id"], "bucket": "streamguard-validacao", "regiao": "us-east-1", "origem": "http://127.0.0.1:3333", **bootstrap})
        provisionamento.bucket()
        grupo = provisionamento.grupo()
        provisionamento.instancia(grupo)
        implantar(pasta, estado, args.chave_ssh.resolve(), bootstrap)
        print(f"Implantação {estado['id']}: EC2 {estado['instancia']}, bucket {estado['bucket']}, API http://{estado['ip']}:3333.")
    finally:
        trava.unlink()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError) as erro:
        print(str(erro) if isinstance(erro, (ValueError, RuntimeError)) else "Falha de configuração local; confira arquivos/permissões e reexecute com a mesma pasta.")
        raise SystemExit(1)
