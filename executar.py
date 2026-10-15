import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


RAIZ = Path(__file__).resolve().parent


def iniciar(nome, *argumentos):
    # Usa o mesmo Python da .venv e permite encerramento controlado no Windows.
    bootstrap = (
        "import runpy,signal,sys; "
        "hasattr(signal,'SIGBREAK') and signal.signal(signal.SIGBREAK,signal.default_int_handler); "
        "p=sys.argv[1];sys.argv=sys.argv[1:];runpy.run_path(p,run_name='__main__')"
    )
    opcoes = {"cwd": str(RAIZ)}
    if os.name == "nt":
        opcoes["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        opcoes["start_new_session"] = True
    return subprocess.Popen(
        [sys.executable, "-u", "-c", bootstrap, str(RAIZ / nome), *argumentos],
        **opcoes,
    )


def verificar_servicos(servicos):
    # Interrompe o fluxo se a captura ou o remetente tiverem encerrado.
    for nome, processo in servicos:
        if processo.poll() is not None:
            raise RuntimeError(f"{nome} encerrou com código {processo.returncode}. Confira a saída acima.")


def encerrar(nome, processo):
    if processo is None or processo.poll() is not None:
        return
    print(f"Encerrando {nome}...", flush=True)
    try:
        if os.name == "nt":
            processo.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(processo.pid, signal.SIGINT)
        processo.wait(timeout=15)
    except (ProcessLookupError, OSError):
        pass
    except subprocess.TimeoutExpired:
        # Força o encerramento apenas se o processo não responder.
        # Nesse caso, pode ser necessário recuperar um lote aberto ou remover uma trava.
        print(f"{nome} não respondeu; encerrando à força.", flush=True)
        processo.kill()
        processo.wait()


def main():
    parser = argparse.ArgumentParser(description="Executa a captura local e o envio dos lotes para a Bronze.")
    parser.add_argument("--intervalo-envio", type=int, default=15,
                        help="Segundos entre verificações de lotes pendentes (padrão: 15).")
    argumentos = parser.parse_args()
    if argumentos.intervalo_envio <= 0:
        raise ValueError("O intervalo deve ser positivo.")

    for nome in ("script-captura.py", "lotes.py", "enviar-bronze.py"):
        if not (RAIZ / nome).is_file():
            raise FileNotFoundError(f"Arquivo obrigatório ausente: {nome}")

    # Impede que duas automações iniciem a captura na mesma pasta.
    caminho_trava = RAIZ / "automacao.lock"
    try:
        trava = caminho_trava.open("x", encoding="utf-8")
    except FileExistsError:
        raise RuntimeError("Existe automacao.lock. Confira se outra automação está rodando.")

    captura = remetente = None
    try:
        with trava:
            trava.write(str(os.getpid()))
            trava.flush()
            print("Iniciando captura e envio. A API configurada no .env deve estar disponível.", flush=True)
            captura = iniciar("script-captura.py")
            remetente = iniciar("enviar-bronze.py", "--continuo", "--intervalo", str(argumentos.intervalo_envio))
            servicos = [("Captura", captura), ("Envio", remetente)]

            # Silver e Gold são processadas pelo serviço agendado na EC2.
            while True:
                verificar_servicos(servicos)
                time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nEncerramento solicitado.", flush=True)
    finally:
        # Fecha a captura antes do remetente, preservando o último lote pendente.
        encerrar("captura", captura)
        encerrar("envio", remetente)
        caminho_trava.unlink()
        print("Automação encerrada. Lotes pendentes serão enviados na próxima execução.", flush=True)


if __name__ == "__main__":
    main()
