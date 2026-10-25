import argparse
import json
from pathlib import Path
import sys

from implantacao import executar, ler_env


def configurar(raiz, conexao, modo):
    obrigatorias = ("S3_BUCKET", "AWS_DEFAULT_REGION", "WEB_DATA_VIZ_URL", "AWS_PROFILE")
    if set(conexao) != {"implantacao", *obrigatorias} or any(not isinstance(v, str) or not v or any(c in v for c in "\r\n\0") for v in conexao.values()):
        raise ValueError("Arquivo de conexão inválido; use o produzido pelo provisionamento.")
    from implantacao import validar_configuracao
    validar_configuracao({"id": conexao["implantacao"], "bucket": conexao["S3_BUCKET"], "regiao": conexao["AWS_DEFAULT_REGION"], "origem": conexao["WEB_DATA_VIZ_URL"]})
    if modo not in ("real", "simulado"):
        raise ValueError("Modo deve ser real ou simulado.")
    valores = {k: conexao[k] for k in obrigatorias}
    valores["CAPTURA_MODO"] = modo
    destino = raiz / ".env"
    if destino.exists():
        atual = ler_env(destino)
        if any(atual.get(k) != v for k, v in valores.items()):
            raise ValueError(".env existente preservado: há divergência de implantação/modo. Revise o arquivo privado ou use um checkout separado; a identidade não foi alterada.")
        return False
    modelo = (raiz / ".env.example").read_text(encoding="utf-8-sig")
    linhas = []
    for linha in modelo.splitlines():
        chave = linha.split("=", 1)[0].strip()
        if chave in valores and not linha.lstrip().startswith("#"):
            linhas.append(chave + "=" + valores.pop(chave))
        else:
            linhas.append(linha)
    linhas.extend(k + "=" + v for k, v in valores.items())
    with destino.open("x", encoding="utf-8", newline="\n") as arquivo:
        destino.chmod(0o600)
        arquivo.write("\n".join(linhas) + "\n")
    return True


def main():
    parser = argparse.ArgumentParser(description="Configura somente a máquina monitorada; não instala serviços centrais nem inicia captura.")
    parser.add_argument("--conexao", type=Path, required=True)
    parser.add_argument("--modo", choices=("real", "simulado"), default="real")
    parser.add_argument("--somente-config", action="store_true")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        raise ValueError("Use Python 3.10 ou superior.")
    raiz = Path(__file__).resolve().parent
    configurar(raiz, json.loads(args.conexao.read_text(encoding="utf-8-sig")), args.modo)
    if not args.somente_config:
        python = raiz / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        if not python.exists():
            executar([sys.executable, "-m", "venv", str(raiz / ".venv")])
        executar([str(python), "-m", "pip", "install", "-r", str(raiz / "requirements.txt")])
    print("Configuração da captura pronta; nenhum serviço central ou envio foi iniciado.")
    print("Execute com o Python da .venv: executar.py. O login será solicitado pelo agente; não configure servidor_id.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as erro:
        print(str(erro) if isinstance(erro, (ValueError, RuntimeError)) else "Não foi possível preparar a captura; arquivos privados existentes são preservados.", file=sys.stderr)
        raise SystemExit(1)
