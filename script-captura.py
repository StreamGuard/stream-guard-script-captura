import platform
import socket
import requests
import os
import psutil
import time

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
        {"tipo": "DISCO", "nome": "Disco raiz"}
    ]

def montar_metricas():
    return {
        "CPU": [
            {"tipo": "USO_CPU", "nome": "Uso da CPU", "unidade": "%"},
            {"tipo": "USO_CPU_NUCLEO", "nome": "Uso da CPU por núcleo", "unidade": "%"},
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
            console.print(f"[red]Erro ao cadastrar componente {componente['nome']}[/red]")

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

if __name__ == "__main__":
    realizar_login()
