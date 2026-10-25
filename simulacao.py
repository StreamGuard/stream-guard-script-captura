import math
import random


class SimuladorMetricas:
    CPU_LOGICOS = 8
    RAM_TOTAL = 16 * 1024**3
    DISCO_TOTAL = 8 * 1024**3
    REDE_BYTES_SEGUNDO = 1000 * 1_000_000 / 8
    CENARIOS = ("normal", "elevada", "critica", "recuperacao")
    NORMAL = (50, 52, 60, 4, 3, 20, 10)
    ELEVADA = (78, 78, 80, 100, 60, 350, 120)
    CRITICA = (95, 94, 96, 180, 90, 650, 230)

    def __init__(self, semente=None, duracao_fase=120):
        if not math.isfinite(duracao_fase) or duracao_fase < 1:
            raise ValueError("A duração de cada fase deve ser finita e de pelo menos um segundo.")
        self.duracao_fase = duracao_fase
        self.random = random.Random(semente)
        self._instante = -1
        self._ruidos = [0.0] * 4
        self._ram = 52.0
        self._disco = 60.0
        self._cargas = [4.0] * 3
        self.estado = {}
        self.atualizar(0)

    @staticmethod
    def _transicao(inicio, fim, progresso):
        p = min(max(progresso, 0.0), 1.0)
        p = p * p * (3 - 2 * p)
        return tuple(a + (b - a) * p for a, b in zip(inicio, fim))

    def _perfil(self, instante):
        indice = int(instante // self.duracao_fase) % 4
        progresso = (instante % self.duracao_fase) / self.duracao_fase
        if indice == 0:
            alvo = self.NORMAL
        elif indice == 1:
            alvo = self._transicao(self.NORMAL, self.ELEVADA, progresso / 0.25)
        elif indice == 2:
            alvo = self._transicao(self.ELEVADA, self.CRITICA, progresso / 0.25)
        else:
            alvo = self._transicao(self.CRITICA, self.NORMAL, progresso)
        return self.CENARIOS[indice], alvo

    def atualizar(self, segundos):
        if not math.isfinite(segundos) or segundos < 0 or int(segundos) < self._instante:
            raise ValueError("O tempo da simulação deve ser finito, não negativo e crescente.")
        # Passos de um segundo mantêm a sequência independente das métricas solicitadas.
        for instante in range(self._instante + 1, int(segundos) + 1):
            self._passo(instante)
        self._instante = int(segundos)

    def _passo(self, instante):
        self.cenario, alvo = self._perfil(instante)
        for indice, amplitude in enumerate((7, 5, 20, 10)):
            self._ruidos[indice] = 0.85 * self._ruidos[indice] + 0.15 * self.random.uniform(-amplitude, amplitude)
        escala = 1 - 0.7 * min(max((alvo[0] - 85) / 10, 0), 1)
        cpu = round(min(max(alvo[0] + self._ruidos[0] * escala, 0), 98), 2)
        self._ram += (alvo[1] + self._ruidos[1] * escala - self._ram) * 0.04
        ram = round(min(max(self._ram, 0), 98), 2)
        escrita = max(0, alvo[4] * (1 + self.random.uniform(-0.04, 0.04))) * 1024**2
        leitura = max(0, alvo[3] * (1 + self.random.uniform(-0.04, 0.04))) * 1024**2
        # A ocupação cresce no máximo pelos bytes escritos; a recuperação inclui limpeza.
        delta = alvo[2] - self._disco
        limite = (escrita if delta >= 0 else 60 * 1024**2) / self.DISCO_TOTAL * 100
        self._disco += min(max(delta, -limite), limite)
        envio = min(max(alvo[5] + self._ruidos[2], 0), 750) * 1_000_000 / 8
        recebimento = min(max(alvo[6] + self._ruidos[3], 0), 250) * 1_000_000 / 8
        carga_alvo = cpu / 100 * self.CPU_LOGICOS * (1 + max(cpu - 85, 0) / 50)
        for indice, janela in enumerate((60, 300, 900)):
            self._cargas[indice] += (carga_alvo - self._cargas[indice]) * (1 - math.exp(-1 / janela))
        self.estado = {
            "uso_cpu": cpu,
            "carga_1m": round(self._cargas[0], 4),
            "carga_5m": round(self._cargas[1], 4),
            "carga_15m": round(self._cargas[2], 4),
            "uso_ram": ram,
            "ram_disponivel": int(self.RAM_TOTAL * (1 - ram / 100)),
            "uso_disco": round(self._disco, 4),
            "taxa_leitura_disco": round(leitura, 2),
            "taxa_escrita_disco": round(escrita, 2),
            "taxa_envio_rede": round(envio, 2),
            "taxa_recebimento_rede": round(recebimento, 2),
        }

    def capturar_metricas(self, tipos, campos, servidor_id, timestamp):
        desconhecidos = set(tipos) - campos.keys()
        if desconhecidos:
            raise ValueError(f"Métricas não suportadas: {desconhecidos}")
        return {"servidor_id": servidor_id, "timestamp": timestamp,
                **{campos[tipo]: self.estado[campos[tipo]] for tipo in tipos}}

    def capturar_processos(self, servidor_id, timestamp):
        pesos = (0.55, 0.25, 0.12, 0.08)
        nomes = ("encoder", "distribuidor", "cache", "sistema")
        cpu_total = round(self.estado["uso_cpu"] * self.CPU_LOGICOS * 100)
        ram_usada = self.RAM_TOTAL - self.estado["ram_disponivel"]
        ram_total = int(ram_usada * 0.9)
        cpus = [int(cpu_total * peso) for peso in pesos[:-1]]
        cpus.append(cpu_total - sum(cpus))
        rams = [int(ram_total * peso) for peso in pesos[:-1]]
        rams.append(ram_total - sum(rams))
        return [
            {"servidor_id": servidor_id, "timestamp": timestamp, "pid": 41001 + i,
             "nome": f"SIMULADO-{nome}", "status": "running",
             "uso_cpu": cpus[i] / 100, "ram_bytes": rams[i]}
            for i, nome in enumerate(nomes)
        ]
