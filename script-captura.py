import platform
import socket
import requests
import os
import psutil

from rich.console import Console
from rich.text import Text
from rich.prompt import Prompt
from rich.align import Align
import time

from dotenv import load_dotenv
load_dotenv()

server_id = 0
empresa_id = 0
nome_empresa = "Stream Guard"

def montar_componentes():
    return [
        {
            "tipo": "CPU",
            "nome": "CPU",
        },
        {
            "tipo": "RAM",
            "nome": "RAM",
        },
        {
            "tipo": "DISCO",
            "nome": "Disco raiz",
        }
    ]

def montar_metricas():
    return {
        "CPU": [
            {
                "tipo": "USO_CPU",
                "nome": "Uso da CPU",
                "unidade": "%"
            },
            {
                "tipo": "USO_CPU_NUCLEO",
                "nome": "Uso da CPU por núcleo",
                "unidade": "%"
            },
            {
                "tipo": "CARGA_CPU_1M",
                "nome": "Carga da CPU - 1 minuto",
                "unidade": "load"
            },
            {
                "tipo": "CARGA_CPU_5M",
                "nome": "Carga da CPU - 5 minutos",
                "unidade": "load"
            },
            {
                "tipo": "CARGA_CPU_15M",
                "nome": "Carga da CPU - 15 minutos",
                "unidade": "load"
            }
        ],

        "RAM": [
            {
                "tipo": "USO_RAM",
                "nome": "Uso da RAM",
                "unidade": "%"
            },
            {
                "tipo": "RAM_DISPONIVEL",
                "nome": "RAM disponível",
                "unidade": "bytes"
            }
        ],

        "DISCO": [
            {
                "tipo": "TAXA_LEITURA_DISCO",
                "nome": "Taxa de leitura do disco",
                "unidade": "bytes/s"
            },
            {
                "tipo": "TAXA_ESCRITA_DISCO",
                "nome": "Taxa de escrita do disco",
                "unidade": "bytes/s"
            },
            {
                "tipo": "USO_DISCO",
                "nome": "Uso do disco",
                "unidade": "%"
            }
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

    # Cadastra servidor no Banco de dados
    # TODO: Necessita de ENDPOINT na Web-Data-Viz
    response = requests.post(f"{os.getenv('WEB_DATA_VIZ_URL')}/servidores", json=server_info)

    return response.json()

def cadastrar_componentes():
    componentes = montar_componentes()
    componentes_cadastrados = []

    for componente in componentes:
        response = requests.post(
            f"{os.getenv('WEB_DATA_VIZ_URL')}/componente/{server_id}",
            json=componente
        )
        componentes_cadastrados.append(response.json())

    return componentes_cadastrados

def cadastrar_metricas(componentes):
    metricas = montar_metricas()
    metricas_cadastradas = []

    for componente in componentes:
        tipo_componente = componente["tipo"]
        componente_id = componente["id"]

        metricas_componente = metricas.get(tipo_componente, [])
        for metrica in metricas_componente:
            response = requests.post(
                f"{os.getenv('WEB_DATA_VIZ_URL')}/componente/{componente_id}/metricas",
                json=metrica
            )
            metricas_cadastradas.append(response.json())

    return metricas_cadastradas

console = Console()
def realizar_login():
    console.clear()

    # Cabeçalho
    console.print()
    console.print(Align.center(Text("STREAM GUARD", style="bold cyan")))
    console.print(Align.center(Text("Monitoramento inteligente de servidores",style="dim")))
    console.print()

    # Login
    email = Prompt.ask("[bold]E-mail[/bold]")
    senha = Prompt.ask("[bold]Senha[/bold]",password=True)
    console.print()

    # Autenticação
    with console.status("[cyan]Conectando ao Stream Guard...[/cyan]",spinner="dots"):
        response = requests.post(
            f"{os.getenv('WEB_DATA_VIZ_URL')}/usuarios/autenticar",
            json={
                "emailServer": email,
                "senhaServer": senha
            }
        )

    if response.status_code != 200:
        console.print("[red]✗[/red] Falha na autenticação.")
        return None

    console.print("[green]✓[/green] Usuário autenticado.")

    dados_login = response.json()

    global empresa_id
    empresa_id = dados_login["empresa_id"]

    # Empresa
    with console.status("[cyan]Identificando empresa...[/cyan]",spinner="dots"):
        nome_empresa = dados_login["nome_empresa"]

    console.print(f"[green]✓[/green] Empresa identificada: [bold]{nome_empresa}[/bold]")

    # Servidor
    with console.status("[cyan]Cadastrando servidor...[/cyan]",spinner="dots"):
        servidor = cadastrar_servidor()

    global server_id
    server_id = servidor["id"]

    console.print(f"[green]✓[/green] Servidor identificado: [bold]{servidor['hostname']}[/bold]")

    #TODO: Precisamos terminar os end points de cadastro para poder continuar a captura!
    return 0

    # Componentes
    with console.status("[cyan]Detectando componentes...[/cyan]",spinner="dots"):
        componentes = cadastrar_componentes()

    for componente in componentes:
        console.print(f"[green]✓[/green] {componente['nome']} detectado.")

    # Métricas
    with console.status("[cyan]Configurando métricas...[/cyan]",spinner="dots"):
        metricas = cadastrar_metricas(componentes)

    console.print(f"[green]✓[/green] {len(metricas)} métricas configuradas.")

    # Finalização
    console.print()
    with console.status("[cyan]Iniciando monitoramento...[/cyan]",spinner="dots"):
        time.sleep(1.5)

    console.print("[green]✓[/green] Monitoramento iniciado.")

    console.print()
    console.print(Align.center(Text("Seu sistema já está sendo monitorado!",style="bold green")))
    console.print()

    console.print(Align.center(Text("O Stream Guard está pronto para proteger seu ambiente.",style="dim")))
    console.print()
    return empresa_id


if __name__ == "__main__":
    realizar_login()
    #TODO: Após realizar o login, iniciaremos o monitoramento


