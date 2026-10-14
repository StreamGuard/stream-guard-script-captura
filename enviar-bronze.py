import hashlib
from pathlib import Path

import boto3
from dotenv import load_dotenv
import os


load_dotenv()

diretorio_csvs = Path(__file__).resolve().parent / "csvs"


def enviar_arquivo(cliente_s3, bucket, arquivo):
    resumo = hashlib.sha256(arquivo.read_bytes()).hexdigest()
    chave = f"bronze/{arquivo.stem}/{resumo}.csv"

    cliente_s3.upload_file(str(arquivo), bucket, chave)
    print(f"Enviado: {arquivo.name} → s3://{bucket}/{chave}")


def main():
    bucket = os.getenv("S3_BUCKET")
    perfil = os.getenv("AWS_PROFILE")
    regiao = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

    if not bucket:
        raise ValueError("Configure S3_BUCKET no arquivo .env.")

    arquivos = sorted(diretorio_csvs.glob("monitoramento_v2_*.csv"))
    arquivos += sorted(diretorio_csvs.glob("processos_*.csv"))

    if not arquivos:
        raise FileNotFoundError("Nenhum CSV de captura encontrado.")

    sessao = boto3.Session(profile_name=perfil, region_name=regiao)
    cliente_s3 = sessao.client("s3")

    for arquivo in arquivos:
        enviar_arquivo(cliente_s3, bucket, arquivo)


if __name__ == "__main__":
    main()