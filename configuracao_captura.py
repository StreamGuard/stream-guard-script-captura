import math
import os
from pathlib import Path

MODOS = ("real", "simulado")


def obter_modo(valor=None):
    modo = valor if valor is not None else os.getenv("STREAMGUARD_MODO", "real")
    if modo not in MODOS:
        raise ValueError("STREAMGUARD_MODO deve ser real ou simulado.")
    return modo


def obter_diretorio_lotes(modo):
    nome = "lotes" if obter_modo(modo) == "real" else "lotes-simulados"
    return Path(__file__).resolve().parent / "csvs" / nome


def adicionar_argumentos(parser, simulacao=True):
    parser.add_argument("--modo", choices=MODOS, default=None,
                        help="Modo de captura; padrão: STREAMGUARD_MODO ou real.")
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
