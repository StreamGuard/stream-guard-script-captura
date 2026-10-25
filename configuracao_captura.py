import math
import json
from uuid import uuid4
import os
from pathlib import Path

MODOS = ("real", "simulado")


def obter_modo(valor=None):
    modo = valor if valor is not None else os.environ.get("CAPTURA_MODO", os.environ.get("STREAMGUARD_MODO", "real"))
    if modo not in MODOS:
        raise ValueError("Modo inválido: use real ou simulado em --modo, CAPTURA_MODO ou STREAMGUARD_MODO.")
    return modo


def obter_diretorio_lotes(modo):
    nome = "lotes" if obter_modo(modo) == "real" else "lotes-simulados"
    return Path(__file__).resolve().parent / "csvs" / nome


def adicionar_argumentos(parser, simulacao=True):
    parser.add_argument("--modo", choices=MODOS, default=None,
                        help="Modo de captura; padrão: CAPTURA_MODO, STREAMGUARD_MODO ou real.")
    if simulacao:
        parser.add_argument("--semente", type=int, default=None,
                            help="Semente opcional para as séries sintéticas.")
        parser.add_argument("--duracao-fase", type=float, default=None,
                            help="Duração em segundos de cada fase simulada; padrão: 120.")


def parametros_simulacao(argumentos, modo):
    if modo == "real":
        return {}
    semente = argumentos.semente
    if semente is None:
        texto = os.getenv("SIMULACAO_SEMENTE", "").strip()
        semente = int(texto) if texto else None
    duracao = argumentos.duracao_fase
    if duracao is None:
        duracao = float(os.getenv("SIMULACAO_DURACAO_FASE", "120"))
    if not math.isfinite(duracao) or duracao < 1:
        raise ValueError("A duração de cada fase simulada deve ser finita e de pelo menos um segundo.")
    return {"semente": semente, "duracao_fase": duracao}


def caminho_identidade():
    return Path(__file__).resolve().parent / "identidade-agente.json"


def carregar_identidade(hostname):
    caminho = caminho_identidade()
    if caminho.exists():
        try:
            identidade = json.loads(caminho.read_text(encoding="utf-8"))
            if (set(identidade) != {"codigo_agente", "confirmado", "codigos_anteriores"} or
                    not isinstance(identidade["codigo_agente"], str) or
                    not identidade["codigo_agente"].strip() or len(identidade["codigo_agente"]) > 64 or
                    type(identidade["confirmado"]) is not bool or
                    not isinstance(identidade["codigos_anteriores"], list) or
                    len(identidade["codigos_anteriores"]) > 2 or
                    any(not isinstance(c, str) or not c.strip() or len(c) > 64 for c in identidade["codigos_anteriores"])):
                raise ValueError()
            return identidade
        except (OSError, ValueError, TypeError):
            raise ValueError("Identidade local inválida; preserve o arquivo e revise o vínculo antes de continuar.") from None
    import hashlib
    legado = f"SERVIDOR_{hostname}"
    anteriores = [c for c in (legado, "SIMULADO_" + hashlib.sha256(legado.encode("utf-8")).hexdigest()[:32]) if len(c) <= 64]
    identidade = {"codigo_agente": "AGENTE_" + uuid4().hex, "confirmado": False, "codigos_anteriores": anteriores}
    caminho.parent.mkdir(parents=True, exist_ok=True)
    try:
        with caminho.open("x", encoding="utf-8") as arquivo:
            json.dump(identidade, arquivo, ensure_ascii=False)
        caminho.chmod(0o600)
    except FileExistsError:
        return carregar_identidade(hostname)
    except OSError:
        raise ValueError("Não foi possível persistir a identidade local da captura.") from None
    return identidade


def confirmar_identidade(identidade, codigo):
    if not isinstance(codigo, str) or not codigo.strip() or len(codigo) > 64:
        raise ValueError("A API não retornou um código de agente válido.")
    if identidade["confirmado"] and codigo != identidade["codigo_agente"]:
        raise ValueError("A API retornou uma identidade diferente da instalação.")
    if codigo not in [identidade["codigo_agente"], *identidade["codigos_anteriores"]]:
        raise ValueError("A API retornou um vínculo desconhecido para a instalação.")
    dados = {"codigo_agente": codigo, "confirmado": True, "codigos_anteriores": []}
    caminho = caminho_identidade()
    temporario = caminho.with_suffix(".json.tmp")
    try:
        with temporario.open("w", encoding="utf-8") as arquivo:
            json.dump(dados, arquivo, ensure_ascii=False)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        temporario.chmod(0o600)
        temporario.replace(caminho)
    except OSError:
        raise ValueError("Não foi possível confirmar a identidade local; não inicie a captura.") from None
