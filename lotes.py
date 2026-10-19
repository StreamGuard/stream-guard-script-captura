import csv
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class LotesCaptura:
    def __init__(self, servidor_id, campos_metricas, diretorio, intervalo=60):
        if intervalo <= 0:
            raise ValueError("O intervalo dos lotes deve ser positivo.")
        self.servidor_id = servidor_id
        self.campos_metricas = campos_metricas
        self.intervalo = intervalo
        self.raiz = Path(diretorio)
        self.abertos = self.raiz / "abertos"
        self.pendentes = self.raiz / "pendentes"
        self.abertos.mkdir(parents=True, exist_ok=True)
        self.pendentes.mkdir(parents=True, exist_ok=True)
        # Pastas de execuções interrompidas são preservadas para recuperação.
        if any(self.abertos.iterdir()):
            print(f"Aviso: existem lotes antigos não finalizados em {self.abertos}")
        self.pasta = None

    def iniciar_lote(self):
        # Horário UTC e UUID evitam misturar lotes de máquinas ou execuções distintas.
        horario = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        nome = f"servidor_{self.servidor_id}_{horario}_{uuid4().hex}"
        self.pasta = self.abertos / nome
        self.pasta.mkdir()
        self.inicio = time.monotonic()

    def verificar_prazo(self):
        # O prazo fecha o lote; não muda os intervalos das métricas ou dos processos.
        if self.pasta is not None and time.monotonic() - self.inicio >= self.intervalo:
            self.fechar()

    def gravar(self, nome, campos, registros):
        if not registros:
            return
        if self.pasta is None:
            self.iniciar_lote()
        caminho = self.pasta / nome
        novo = not caminho.exists()
        # Fecha o arquivo após cada escrita, antes de qualquer troca de pasta.
        with caminho.open("a", newline="", encoding="utf-8") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=campos)
            if novo:
                escritor.writeheader()
            escritor.writerows(registros)

    def gravar_metricas(self, dados):
        self.gravar(f"monitoramento_v2_{self.servidor_id}.csv", self.campos_metricas, [dados])

    def gravar_processos(self, registros):
        campos = ["servidor_id", "timestamp", "pid", "nome", "status", "uso_cpu", "ram_bytes"]
        self.gravar(f"processos_{self.servidor_id}.csv", campos, registros)

    def fechar(self):
        if self.pasta is None:
            return
        # A renomeação no mesmo disco entrega a pasta completa ao futuro remetente.
        destino = self.pendentes / self.pasta.name
        self.pasta.rename(destino)
        self.pasta = None
        print(f"Lote fechado e aguardando envio: {destino}")
