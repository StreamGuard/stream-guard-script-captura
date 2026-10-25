import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from configuracao_captura import adicionar_argumentos, obter_modo, obter_diretorio_lotes


def enviar_lote(s3, bucket, lote, enviados, modo="real"):
    modo = obter_modo(modo)
    if lote.name.startswith("simulado_") != (modo == "simulado"):
        raise ValueError("O modo do remetente não corresponde à identificação do lote.")
    # Aceita apenas os CSVs gerados pela captura, sem arquivos desconhecidos.
    arquivos = sorted(lote.iterdir())
    if not arquivos:
        raise ValueError(f"Lote vazio: {lote.name}")
    for arquivo in arquivos:
        reconhecido = (
            arquivo.name.startswith("monitoramento_v2_")
            or arquivo.name.startswith("processos_")
        )
        if not arquivo.is_file() or arquivo.suffix != ".csv" or not reconhecido:
            raise ValueError(f"Arquivo inesperado no lote: {arquivo.name}")

    destino = enviados / lote.name
    if destino.exists():
        raise ValueError(f"Já existe uma pasta enviada com o nome {lote.name}.")

    objetos = []
    for arquivo in arquivos:
        # O mesmo lote mantém a mesma chave em todas as tentativas de envio.
        chave = f"bronze/{arquivo.stem}/{lote.name}.csv"
        s3.upload_file(str(arquivo), bucket, chave)
        objetos.append({"chave": chave, "bytes": arquivo.stat().st_size})
        print(f"Enviado: s3://{bucket}/{chave}")

    # O marcador informa que todos os objetos deste lote foram enviados.
    marcador = {
        "lote": lote.name,
        "concluido_em_utc": datetime.now(timezone.utc).isoformat(),
        "objetos": objetos,
    }
    s3.put_object(
        Bucket=bucket,
        Key=f"bronze/lotes-concluidos/{lote.name}.json",
        Body=json.dumps(marcador, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )

    # Preserva os arquivos locais; muda de pasta apenas após confirmar os uploads.
    lote.rename(destino)
    print(f"Lote concluído e arquivado localmente: {lote.name}")


def enviar_pendentes(s3, bucket, pendentes, enviados, modo="real"):
    lotes = sorted(pasta for pasta in pendentes.iterdir() if pasta.is_dir())
    if not lotes:
        print("Nenhum lote pendente.")
        return 0

    falhas = 0
    for lote in lotes:
        try:
            enviar_lote(s3, bucket, lote, enviados, modo=modo)
        except (BotoCoreError, ClientError, OSError, ValueError) as erro:
            # Mantém o lote para nova tentativa; não apaga arquivos nem dados do S3.
            falhas += 1
            print(f"Falha no lote {lote.name}: {erro}")
            print("O lote permanece em pendentes.")
    return falhas


def main():
    parser = argparse.ArgumentParser(description="Envia lotes fechados à Bronze.")
    parser.add_argument("--continuo", action="store_true")
    parser.add_argument("--intervalo", type=int, default=15)
    adicionar_argumentos(parser, simulacao=False)
    argumentos = parser.parse_args()
    if argumentos.intervalo <= 0:
        raise ValueError("O intervalo de verificação deve ser positivo.")

    raiz = Path(__file__).resolve().parent
    load_dotenv(raiz / ".env")
    bucket = os.getenv("S3_BUCKET")
    if not bucket:
        raise ValueError("Configure S3_BUCKET no .env.")

    modo = obter_modo(argumentos.modo)
    pasta_lotes = obter_diretorio_lotes(modo)
    print(f"Remetente em modo {modo.upper()}. Lotes em: {pasta_lotes}")
    pendentes = pasta_lotes / "pendentes"
    enviados = pasta_lotes / "enviados"
    pendentes.mkdir(parents=True, exist_ok=True)
    enviados.mkdir(exist_ok=True)

    # Impede dois remetentes de manipular as mesmas pastas simultaneamente.
    caminho_trava = pasta_lotes / "envio.lock"
    try:
        trava = caminho_trava.open("x", encoding="utf-8")
    except FileExistsError:
        raise RuntimeError("Já existe envio.lock. Confira se outro remetente está executando.")

    try:
        with trava:
            trava.write(str(os.getpid()))
            trava.flush()
            sessao = boto3.Session(
                profile_name=os.getenv("AWS_PROFILE"),
                region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
            )
            s3 = sessao.client("s3")
            while True:
                falhas = enviar_pendentes(s3, bucket, pendentes, enviados, modo=modo)
                if not argumentos.continuo:
                    if falhas:
                        raise SystemExit(1)
                    break
                # Espera entre verificações, sem interromper o agente de captura separado.
                time.sleep(argumentos.intervalo)
    except KeyboardInterrupt:
        print("\nEnvio encerrado; os lotes restantes serão retomados depois.")
    finally:
        caminho_trava.unlink()


if __name__ == "__main__":
    main()