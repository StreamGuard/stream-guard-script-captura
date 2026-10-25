from pathlib import Path
import argparse
from api_captura import ApiCaptura
from configuracao_captura import adicionar_argumentos, obter_modo, obter_diretorio_lotes, parametros_simulacao, carregar_identidade, confirmar_identidade
from simulacao import SimuladorMetricas
from lotes import LotesCaptura
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

# Carrega o endereço da API e as credenciais de login do .env.
load_dotenv(Path(__file__).resolve().parent / ".env")

server_id = 0
empresa_id = 0
nome_empresa = "Stream Guard"
api = None

# Define os recursos cadastrados para a máquina monitorada.
def montar_componentes():
    return [
        {"tipo": "CPU", "nome": "CPU"},
        {"tipo": "RAM", "nome": "RAM"},
        {"tipo": "DISCO", "nome": "Disco raiz"},
        {"tipo": "REDE", "nome": "Rede da máquina"}
    ]

# Define os tipos e unidades das métricas conhecidas pelo agente.
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

# Solicita à API o cadastro ou a identificação do equipamento existente.
def cadastrar_servidor(modo="real"):
    identidade = carregar_identidade(socket.gethostname())
    server_info = {
        "codigo_agente": identidade["codigo_agente"],
        "hostname": socket.gethostname(),
        "sistema_operacional": platform.system(),
        "versao_so": platform.version(),
        "arquitetura": platform.machine(),
    }

    if not identidade["confirmado"]:
        server_info["codigos_anteriores"] = identidade["codigos_anteriores"]
    dados = api.pedir("POST", "/servidores/cadastrar", server_info)
    if (not isinstance(dados, dict) or type(dados.get("id")) is not int or dados["id"] <= 0 or
            type(dados.get("empresa_id")) is not int or dados["empresa_id"] != empresa_id):
        raise ValueError("A API não confirmou o servidor da empresa autenticada.")
    confirmar_identidade(identidade, dados.get("codigo_agente"))
    return dados

# Solicita os componentes e guarda os IDs retornados pela API.
def cadastrar_componentes():
    componentes = montar_componentes()
    componentes_cadastrados = []

    for componente in componentes:
        body = {
            "servidor_id": server_id,
            "tipo": componente["tipo"],
            "nome": componente["nome"]
        }

        dados_comp = api.pedir("POST", "/componentes/cadastrar", body)
        if not isinstance(dados_comp, dict) or type(dados_comp.get("id")) is not int or dados_comp["id"] <= 0:
            raise ValueError("A API não confirmou o cadastro do componente.")
        dados_comp.setdefault("tipo", componente["tipo"])
        componentes_cadastrados.append(dados_comp)

    return componentes_cadastrados

# Vincula as métricas aos componentes identificados.
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

            dados = api.pedir("POST", "/metricas/cadastrarMetrica", payload)
            if not isinstance(dados, dict) or dados.get("componente_id") != componente_id or dados.get("tipo") != metrica["tipo"]:
                raise ValueError("A API não confirmou o cadastro da métrica.")
            metricas_cadastradas.append(dados)

    return metricas_cadastradas

console = Console()

# Autentica o usuário e identifica empresa, equipamento e componentes.
def realizar_login(modo="real"):
    console.clear()
    if modo == "simulado":
        console.print("[yellow]MODO SIMULADO: métricas sintéticas com a identidade desta instalação.[/yellow]")

    # Cabeçalho
    console.print()
    console.print(Align.center(Text("STREAM GUARD", style="bold cyan")))
    console.print(Align.center(Text("Monitoramento inteligente de servidores", style="dim")))
    console.print()

    # Login
    email = os.getenv("USUARIO_EMAIL")
    senha = os.getenv("USUARIO_SENHA")

    if not email or not senha:
        email = Prompt.ask("[bold]E-mail[/bold]")
        senha = Prompt.ask("[bold]Senha[/bold]", password=True)
        console.print()

    global api, empresa_id
    if api is not None:
        api.fechar()
    api = ApiCaptura(os.getenv("WEB_DATA_VIZ_URL", ""))
    with console.status("[cyan]Conectando ao Stream Guard...[/cyan]", spinner="dots"):
        dados_login = api.autenticar(email, senha)
    empresa_id = dados_login.get("empresa_id")
    if type(empresa_id) is not int or empresa_id <= 0:
        raise ValueError("O usuário autenticado não possui uma empresa disponível para captura.")
    console.print("[green]✓[/green] Usuário autenticado.")

    # Empresa
    nome_empresa = dados_login.get("nome_empresa", "Empresa Desconhecida")
    console.print(f"[green]✓[/green] Empresa identificada: [bold]{nome_empresa}[/bold]")

    # Servidor
    with console.status("[cyan]Cadastrando servidor...[/cyan]", spinner="dots"):
        servidor = cadastrar_servidor(modo)

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

# Converte a diferença dos contadores em bytes por segundo.
def calcular_taxa(contador_inicial, contador_final, campo, segundos):
    if contador_inicial is None or contador_final is None:
        return None

    diferenca = getattr(contador_final, campo) - getattr(contador_inicial, campo)
    if diferenca < 0:
        return None

    return diferenca / segundos


# Faz a primeira leitura necessária para medir CPU dos processos.
def preparar_processos():
    processos = []
    for processo in psutil.process_iter():
        try:
            processo.cpu_percent(interval=None)
            processos.append(processo)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return processos


# Consulta processos preparados; ignora os encerrados ou sem permissão.
def capturar_processos(processos, timestamp):
    registros = []
    for processo in processos:
        try:
            with processo.oneshot():
                registros.append({
                    "servidor_id": server_id,
                    "timestamp": timestamp,
                    "pid": processo.pid,
                    "nome": processo.name(),
                    "status": processo.status(),
                    "uso_cpu": processo.cpu_percent(interval=None),
                    "ram_bytes": processo.memory_info().rss
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return registros


# Função anterior de gravação; o monitoramento em lotes usa LotesCaptura.
def gravar_processos(caminho, registros):
    headers = ["servidor_id", "timestamp", "pid", "nome", "status", "uso_cpu", "ram_bytes"]
    if os.path.exists(caminho):
        with open(caminho, newline='', encoding='utf-8') as arquivo:
            if next(csv.reader(arquivo), None) != headers:
                raise ValueError(f"Cabeçalho incompatível no arquivo {caminho}")
    escrever_cabecalho = not os.path.exists(caminho)
    with open(caminho, mode='a', newline='', encoding='utf-8') as arquivo:
        writer = csv.DictWriter(arquivo, fieldnames=headers)
        if escrever_cabecalho:
            writer.writeheader()
        writer.writerows(registros)


# Relaciona os nomes das métricas no banco às colunas dos CSVs.
def obter_campos_metricas():
    return {
        "USO_CPU": "uso_cpu",
        "CARGA_CPU_1M": "carga_1m",
        "CARGA_CPU_5M": "carga_5m",
        "CARGA_CPU_15M": "carga_15m",
        "USO_RAM": "uso_ram",
        "RAM_DISPONIVEL": "ram_disponivel",
        "USO_DISCO": "uso_disco",
        "TAXA_LEITURA_DISCO": "taxa_leitura_disco",
        "TAXA_ESCRITA_DISCO": "taxa_escrita_disco",
        "TAXA_ENVIO_REDE": "taxa_envio_rede",
        "TAXA_RECEBIMENTO_REDE": "taxa_recebimento_rede"
    }


# Consulta os recursos necessários às métricas solicitadas nesta rodada.
def capturar_metricas(tipos):
    campos = obter_campos_metricas()
    # O conjunto evita tipos repetidos e permite comparar grupos de métricas.
    tipos = set(tipos)

    desconhecidos = tipos - campos.keys()
    if desconhecidos:
        raise ValueError(f"Métricas não suportadas: {desconhecidos}")

    valores = {}

    tipos_io_disco = {"TAXA_LEITURA_DISCO", "TAXA_ESCRITA_DISCO"}
    tipos_rede = {"TAXA_ENVIO_REDE", "TAXA_RECEBIMENTO_REDE"}
    tipos_carga = {"CARGA_CPU_1M", "CARGA_CPU_5M", "CARGA_CPU_15M"}

    medir_io_disco = bool(tipos & tipos_io_disco)
    medir_rede = bool(tipos & tipos_rede)

    # Lê contadores somente quando alguma taxa correspondente foi solicitada.
    disco_inicial = psutil.disk_io_counters() if medir_io_disco else None
    rede_inicial = psutil.net_io_counters() if medir_rede else None
    inicio = time.monotonic()

    # Usa uma janela de um segundo para medir CPU ou calcular taxas.
    if "USO_CPU" in tipos:
        valores["uso_cpu"] = psutil.cpu_percent(interval=1)
    elif medir_io_disco or medir_rede:
        time.sleep(1)

    disco_final = psutil.disk_io_counters() if medir_io_disco else None
    rede_final = psutil.net_io_counters() if medir_rede else None
    segundos = time.monotonic() - inicio

    # Cargas compartilham uma consulta; valores sem suporte ficam ausentes.
    if tipos & tipos_carga:
        cargas = (
            psutil.getloadavg()
            if hasattr(psutil, "getloadavg")
            else (None, None, None)
        )
        valores.update(zip(
            ["carga_1m", "carga_5m", "carga_15m"],
            cargas
        ))

    # Uma consulta de memória fornece os dois indicadores.
    if tipos & {"USO_RAM", "RAM_DISPONIVEL"}:
        memoria = psutil.virtual_memory()
        valores["uso_ram"] = memoria.percent
        valores["ram_disponivel"] = memoria.available

    # Mede a ocupação do disco raiz, separadamente das taxas de leitura/escrita.
    if "USO_DISCO" in tipos:
        raiz_disco = os.path.abspath(os.sep)
        valores["uso_disco"] = psutil.disk_usage(raiz_disco).percent

    contadores = {
        "TAXA_LEITURA_DISCO": (disco_inicial, disco_final, "read_bytes"),
        "TAXA_ESCRITA_DISCO": (disco_inicial, disco_final, "write_bytes"),
        "TAXA_ENVIO_REDE": (rede_inicial, rede_final, "bytes_sent"),
        "TAXA_RECEBIMENTO_REDE": (rede_inicial, rede_final, "bytes_recv")
    }

    for tipo in tipos & contadores.keys():
        contador_inicial, contador_final, campo = contadores[tipo]
        valores[campos[tipo]] = calcular_taxa(
            contador_inicial, contador_final, campo, segundos
        )

    dados = {
        "servidor_id": server_id,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    # Retorna apenas as métricas solicitadas, mesmo em consultas compartilhadas.
    for tipo in tipos:
        campo = campos[tipo]
        dados[campo] = valores[campo]

    return dados

# Coordena os prazos de coleta, a atualização pela API e a gravação em lotes.
def iniciar_monitoramento(configuracoes, intervalo_processos=40, intervalo_atualizacao=60, simulador=None, modo="real"):
    modo = obter_modo(modo)
    if (modo == "simulado") != (simulador is not None):
        raise ValueError("O modo de captura deve corresponder ao gerador selecionado.")
    campos = obter_campos_metricas()
    headers = [
        "servidor_id", "timestamp", "uso_cpu", "carga_1m",
        "carga_5m", "carga_15m", "uso_ram", "ram_disponivel",
        "uso_disco", "taxa_leitura_disco", "taxa_escrita_disco",
        "taxa_envio_rede", "taxa_recebimento_rede"
    ]

    # Permite localizar os parâmetros de cada métrica pelo seu tipo.
    configuracoes_por_tipo = {}
    for configuracao in configuracoes:
        tipo = configuracao["tipo"]
        if tipo not in campos:
            raise ValueError(f"Métrica não suportada: {tipo}")
        configuracoes_por_tipo[tipo] = configuracao

    agora = time.monotonic()
    inicio_simulacao = agora
    cenario_anterior = None
    proximas_coletas = {tipo: agora for tipo in configuracoes_por_tipo}
    proxima_coleta_processos = agora
    proxima_atualizacao = agora + intervalo_atualizacao

    # Usa lotes de um minuto; mantém os CSVs antigos intactos.
    diretorio_lotes = obter_diretorio_lotes(modo)
    lotes = LotesCaptura(server_id, headers, diretorio_lotes, intervalo=60, modo=modo)
    print(f"Monitoramento ativo. Lotes em: {diretorio_lotes}")

    try:
        while True:
            lotes.verificar_prazo()
            agora = time.monotonic()

            # Atualiza os parâmetros sem reiniciar o agente.
            if agora >= proxima_atualizacao:
                try:
                    # Prepara e valida a atualização antes de substituir a anterior.
                    novas_configuracoes = buscar_configuracoes()
                    novas_por_tipo = {}

                    for configuracao in novas_configuracoes:
                        tipo = configuracao["tipo"]
                        if tipo not in campos:
                            raise ValueError(f"Métrica não suportada: {tipo}")
                        novas_por_tipo[tipo] = configuracao

                    momento_atualizacao = time.monotonic()
                    novos_prazos = {}

                    for tipo, configuracao in novas_por_tipo.items():
                        anterior = configuracoes_por_tipo.get(tipo)
                        # Preserva prazos inalterados; configurações novas ficam disponíveis agora.
                        if anterior == configuracao:
                            novos_prazos[tipo] = proximas_coletas[tipo]
                        else:
                            novos_prazos[tipo] = momento_atualizacao

                    configuracoes_por_tipo = novas_por_tipo
                    proximas_coletas = novos_prazos
                    print("Configurações atualizadas pela API.")

                # Falhas na atualização não descartam a última configuração válida.
                except (requests.exceptions.RequestException, ValueError, KeyError) as erro:
                    print(f"Falha ao atualizar configurações: {erro}")
                    print("Mantendo a última configuração válida.")

                proxima_atualizacao = time.monotonic() + intervalo_atualizacao

            agora = time.monotonic()
            # Registra o início da rodada, sem usar o horário fixo da inicialização.
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # Seleciona apenas métricas habilitadas cujo prazo chegou.
            tipos_devidos = [
                tipo
                for tipo, configuracao in configuracoes_por_tipo.items()
                if configuracao["habilitado"] == 1
                and agora >= proximas_coletas[tipo]
            ]
            coletar_processos = agora >= proxima_coleta_processos

            if simulador is not None and (tipos_devidos or coletar_processos):
                simulador.atualizar(agora - inicio_simulacao)
                if simulador.cenario != cenario_anterior:
                    print(f"[SIMULADO] Cenário: {simulador.cenario}")
                    cenario_anterior = simulador.cenario

            if coletar_processos and simulador is None:
                processos = preparar_processos()

            if tipos_devidos:
                dados = (capturar_metricas(tipos_devidos) if simulador is None else
                         simulador.capturar_metricas(tipos_devidos, campos, server_id, timestamp))
                dados["timestamp"] = timestamp

                # Colunas não medidas nesta rodada ficam vazias, não recebem zero.
                lotes.gravar_metricas(dados)

                for tipo in tipos_devidos:
                    intervalo = configuracoes_por_tipo[tipo]["intervalo_coleta"]
                    proximas_coletas[tipo] = agora + intervalo

            # Garante uma janela de medição para processos mesmo sem CPU/rede/disco gerais.
            if coletar_processos:
                tipos_com_medicao_temporizada = {
                    "USO_CPU", "TAXA_LEITURA_DISCO", "TAXA_ESCRITA_DISCO",
                    "TAXA_ENVIO_REDE", "TAXA_RECEBIMENTO_REDE"
                }
                if simulador is None and not set(tipos_devidos) & tipos_com_medicao_temporizada:
                    time.sleep(1)

                registros = (capturar_processos(processos, timestamp) if simulador is None else
                             simulador.capturar_processos(server_id, timestamp))
                lotes.gravar_processos(registros)
                proxima_coleta_processos = agora + intervalo_processos

            # Evita um laço sem pausa enquanto aguarda os próximos prazos.
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nMonitoramento encerrado pelo usuário.")
    finally:
        # Finaliza também o lote parcial ao encerrar normalmente ou ocorrer erro.
        lotes.fechar()

# Lê e valida os parâmetros da tabela config_metrica por meio da API.
def buscar_configuracoes():
    configuracoes = api.pedir("GET", f"/metricas/configuracoes/{server_id}")

    if not isinstance(configuracoes, list) or not configuracoes:
        raise ValueError("A API não retornou configurações de métricas.")

    # Rejeita configurações repetidas para não escolher uma arbitrariamente.
    tipos_encontrados = set()

    for configuracao in configuracoes:
        tipo = configuracao["tipo"]

        if tipo in tipos_encontrados:
            raise ValueError(f"Mais de uma configuração para a métrica {tipo}.")

        tipos_encontrados.add(tipo)

        if configuracao["id_config_metrica"] is None:
            raise ValueError(f"A métrica {tipo} não possui configuração.")

        if configuracao["habilitado"] not in (0, 1):
            raise ValueError(f"Habilitação inválida para a métrica {tipo}.")

        intervalo = configuracao["intervalo_coleta"]

        if not isinstance(intervalo, int) or intervalo <= 0:
            raise ValueError(f"Intervalo inválido para a métrica {tipo}.")

    return configuracoes

def main():
    parser = argparse.ArgumentParser(description="Captura real ou simulada do StreamGuard.")
    adicionar_argumentos(parser)
    argumentos = parser.parse_args()
    try:
        modo = obter_modo(argumentos.modo)
        parametros = parametros_simulacao(argumentos, modo)
        if realizar_login(modo):
            configuracoes = buscar_configuracoes()
            simulador = SimuladorMetricas(**parametros) if modo == "simulado" else None
            iniciar_monitoramento(configuracoes, simulador=simulador, modo=modo)
    except ValueError as erro:
        console.print(f"[red]Não foi possível iniciar a captura: {erro}[/red]")
        raise SystemExit(1) from None
    finally:
        if api is not None:
            api.fechar()


if __name__ == "__main__":
    main()