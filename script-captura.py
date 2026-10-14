import platform
import socket
import requests
import os
import psutil
import time
from datetime import datetime
import csv

from rich.console import Console
from rich.text import Text
from rich.prompt import Prompt
from rich.align import Align
from dotenv import load_dotenv

load_dotenv()

server_id = 0
empresa_id = 0
nome_empresa = "Stream Guard"

def montar_componentes():
    return [
        {"tipo": "CPU", "nome": "CPU"},
        {"tipo": "RAM", "nome": "RAM"},
        {"tipo": "DISCO", "nome": "Disco raiz"},
        {"tipo": "REDE", "nome": "Rede da máquina"}
    ]

def montar_metricas():
    return {
        "CPU": [
            {"tipo": "USO_CPU", "nome": "Uso da CPU", "unidade": "%"},
            {"tipo": "CARGA_CPU_1M", "nome": "Carga da CPU - 1 minuto", "unidade": "load"},
            {"tipo": "CARGA_CPU_5M", "nome": "Carga da CPU - 5 minutos", "unidade": "load"},
            {"tipo": "CARGA_CPU_15M", "nome": "Carga da CPU - 15 minutos", "unidade": "load"}
        ],
        "RAM": [
            {"tipo": "USO_RAM", "nome": "Uso da RAM", "unidade": "%"},
            {"tipo": "RAM_DISPONIVEL", "nome": "RAM disponível", "unidade": "bytes"}
        ],
        "DISCO": [
            {"tipo": "TAXA_LEITURA_DISCO", "nome": "Taxa de leitura do disco", "unidade": "bytes/s"},
            {"tipo": "TAXA_ESCRITA_DISCO", "nome": "Taxa de escrita do disco", "unidade": "bytes/s"},
            {"tipo": "USO_DISCO", "nome": "Uso do disco", "unidade": "%"}
        ],
        "REDE": [
            {"tipo": "TAXA_ENVIO_REDE", "nome": "Taxa de envio da rede", "unidade": "bytes/s"},
            {"tipo": "TAXA_RECEBIMENTO_REDE", "nome": "Taxa de recebimento da rede", "unidade": "bytes/s"}
        ]
    }

def cadastrar_servidor():
    server_info = {
        "empresa_id": empresa_id,
        "codigo_agente": f"SERVIDOR_{socket.gethostname()}",
        "hostname": socket.gethostname(),
        "sistema_operacional": platform.system(),
        "versao_so": platform.version(),
        "arquitetura": platform.machine(),
    }

    response = requests.post(f"{os.getenv('WEB_DATA_VIZ_URL')}/servidores/cadastrar", json=server_info)

    if response.status_code in (200, 201):
        return response.json()

    console.print(f"[red]Erro ao cadastrar servidor: {response.status_code}[/red]")
    return None

def cadastrar_componentes():
    componentes = montar_componentes()
    componentes_cadastrados = []

    for componente in componentes:
        body = {
            "servidor_id": server_id,
            "tipo": componente["tipo"],
            "nome": componente["nome"]
        }

        response = requests.post(
            f"{os.getenv('WEB_DATA_VIZ_URL')}/componentes/cadastrar",
            json=body
        )

        if response.status_code == 201:
            dados_comp = response.json()
            if "tipo" not in dados_comp:
                dados_comp["tipo"] = componente["tipo"]
            componentes_cadastrados.append(dados_comp)
        else:
         console.print(
        f"[red]Erro ao cadastrar componente {componente['nome']} "
        f"(HTTP {response.status_code}): {response.text}[/red]"
    )

    return componentes_cadastrados

def cadastrar_metricas(componentes):
    metricas_modelo = montar_metricas()
    metricas_cadastradas = []

    for componente in componentes:
        tipo_componente = componente.get("tipo")
        componente_id = componente.get("id")

        if not componente_id:
            continue

        metricas_componente = metricas_modelo.get(tipo_componente, [])
        for metrica in metricas_componente:
            payload = {
                "componente_id": componente_id,
                "tipo": metrica["tipo"],
                "nome": metrica["nome"],
                "unidade": metrica["unidade"]
            }

            response = requests.post(
                f"{os.getenv('WEB_DATA_VIZ_URL')}/metricas/cadastrarMetrica",
                json=payload
            )

            if response.status_code in (200, 201):
                metricas_cadastradas.append(response.json())

    return metricas_cadastradas

console = Console()

def realizar_login():
    console.clear()

    # Cabeçalho
    console.print()
    console.print(Align.center(Text("STREAM GUARD", style="bold cyan")))
    console.print(Align.center(Text("Monitoramento inteligente de servidores", style="dim")))
    console.print()

    # Login
    email = Prompt.ask("[bold]E-mail[/bold]")
    senha = Prompt.ask("[bold]Senha[/bold]", password=True)
    console.print()

    # Autenticação
    with console.status("[cyan]Conectando ao Stream Guard...[/cyan]", spinner="dots"):
        try:
            response = requests.post(
                f"{os.getenv('WEB_DATA_VIZ_URL')}/usuarios/autenticar",
                json={"emailServer": email, "senhaServer": senha}
            )
        except requests.exceptions.RequestException as e:
            console.print(f"[red]✗[/red] Erro de conexão com a API: {e}")
            return None

    if response.status_code != 200:
        console.print("[red]✗[/red] Falha na autenticação.")
        return None

    console.print("[green]✓[/green] Usuário autenticado.")

    dados_login = response.json()

    global empresa_id
    empresa_id = dados_login.get("empresa_id")

    # Empresa
    nome_empresa = dados_login.get("nome_empresa", "Empresa Desconhecida")
    console.print(f"[green]✓[/green] Empresa identificada: [bold]{nome_empresa}[/bold]")

    # Servidor
    with console.status("[cyan]Cadastrando servidor...[/cyan]", spinner="dots"):
        servidor = cadastrar_servidor()

    if not servidor:
        return None

    global server_id
    server_id = servidor["id"]

    console.print(f"[green]✓[/green] Servidor identificado: [bold]{servidor.get('hostname', socket.gethostname())}[/bold]")

    # Componentes
    with console.status("[cyan]Detectando componentes...[/cyan]", spinner="dots"):
        componentes = cadastrar_componentes()

    for componente in componentes:
        console.print(f"[green]✓[/green] {componente.get('nome', 'Componente')} detectado.")

    # Métricas
    with console.status("[cyan]Configurando métricas...[/cyan]", spinner="dots"):
        metricas = cadastrar_metricas(componentes)

    console.print(f"[green]✓[/green] {len(metricas)} métricas configuradas.")

    # Finalização
    console.print()
    with console.status("[cyan]Iniciando monitoramento...[/cyan]", spinner="dots"):
        time.sleep(1.5)

    console.print("[green]✓[/green] Monitoramento iniciado.")

    console.print()
    console.print(Align.center(Text("Seu sistema já está sendo monitorado!", style="bold green")))
    console.print()
    console.print(Align.center(Text("O Stream Guard está pronto para proteger seu ambiente.", style="dim")))
    console.print()

    return empresa_id

def calcular_taxa(contador_inicial, contador_final, campo, segundos):
    if contador_inicial is None or contador_final is None:
        return None

    diferenca = getattr(contador_final, campo) - getattr(contador_inicial, campo)
    if diferenca < 0:
        return None

    return diferenca / segundos


def capturar_metricas():
    disco_inicial = psutil.disk_io_counters()
    rede_inicial = psutil.net_io_counters()
    inicio = time.monotonic()

    uso_cpu = psutil.cpu_percent(interval=1)

    disco_final = psutil.disk_io_counters()
    rede_final = psutil.net_io_counters()
    segundos = time.monotonic() - inicio

    if hasattr(psutil, "getloadavg"):
        carga_1m, carga_5m, carga_15m = psutil.getloadavg()
    else:
        carga_1m = carga_5m = carga_15m = 0.0

    memoria = psutil.virtual_memory()
    uso_ram = memoria.percent
    ram_disponivel = memoria.available

    raiz_disco = os.path.abspath(os.sep)
    disco = psutil.disk_usage(raiz_disco)
    uso_disco = disco.percent

    taxa_leitura = calcular_taxa(disco_inicial, disco_final, "read_bytes", segundos)
    taxa_escrita = calcular_taxa(disco_inicial, disco_final, "write_bytes", segundos)
    taxa_envio = calcular_taxa(rede_inicial, rede_final, "bytes_sent", segundos)
    taxa_recebimento = calcular_taxa(rede_inicial, rede_final, "bytes_recv", segundos)

    return {
        "servidor_id": server_id,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "uso_cpu": uso_cpu,
        "carga_1m": carga_1m,
        "carga_5m": carga_5m,
        "carga_15m": carga_15m,
        "uso_ram": uso_ram,
        "ram_disponivel": ram_disponivel,
        "uso_disco": uso_disco,
        "taxa_leitura_disco": taxa_leitura,
        "taxa_escrita_disco": taxa_escrita,
        "taxa_envio_rede": taxa_envio,
        "taxa_recebimento_rede": taxa_recebimento
    }

def iniciar_monitoramento(intervalo_segundos=5):
    diretorio = "csvs"
    os.makedirs(diretorio, exist_ok=True)
    caminho_arquivo = os.path.join(diretorio, f"monitoramento_v2_{server_id}.csv")

    headers = [
        "servidor_id", "timestamp", "uso_cpu", "carga_1m",
        "carga_5m", "carga_15m", "uso_ram", "ram_disponivel",
        "uso_disco", "taxa_leitura_disco", "taxa_escrita_disco",
        "taxa_envio_rede", "taxa_recebimento_rede"
    ]

    if os.path.exists(caminho_arquivo):
        with open(caminho_arquivo, newline='', encoding='utf-8') as arquivo:
            cabecalho_existente = next(csv.reader(arquivo), None)
        if cabecalho_existente != headers:
            raise ValueError(f"Cabeçalho incompatível no arquivo {caminho_arquivo}")

    if not os.path.exists(caminho_arquivo):
        with open(caminho_arquivo, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()

    print(f"Monitoramento ativo. Gravando dados em '{caminho_arquivo}'...")
    try:
        while True:
            dados = capturar_metricas()

            with open(caminho_arquivo, mode='a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writerow(dados)

            time.sleep(intervalo_segundos)

    except KeyboardInterrupt:
        print("\nMonitoramento encerrado pelo usuário.")

if __name__ == "__main__":
    if realizar_login():
        iniciar_monitoramento()
