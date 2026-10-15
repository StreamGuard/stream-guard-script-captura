from pathlib import Path
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
load_dotenv()

server_id = 0
empresa_id = 0
nome_empresa = "Stream Guard"

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

            response = requests.post(
                f"{os.getenv('WEB_DATA_VIZ_URL')}/metricas/cadastrarMetrica",
                json=payload
            )

            if response.status_code in (200, 201):
                metricas_cadastradas.append(response.json())

    return metricas_cadastradas

console = Console()

# Autentica o usuário e identifica empresa, equipamento e componentes.
def realizar_login():
    console.clear()

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
def iniciar_monitoramento(configuracoes, intervalo_processos=40, intervalo_atualizacao=60):
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
    proximas_coletas = {tipo: agora for tipo in configuracoes_por_tipo}
    proxima_coleta_processos = agora
    proxima_atualizacao = agora + intervalo_atualizacao

    # Usa lotes de um minuto; mantém os CSVs antigos intactos.
    diretorio_lotes = Path(__file__).resolve().parent / "csvs" / "lotes"
    lotes = LotesCaptura(server_id, headers, diretorio_lotes, intervalo=60)
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

            if coletar_processos:
                processos = preparar_processos()

            if tipos_devidos:
                dados = capturar_metricas(tipos_devidos)
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
                if not set(tipos_devidos) & tipos_com_medicao_temporizada:
                    time.sleep(1)

                registros = capturar_processos(processos, timestamp)
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
    url = f"{os.getenv('WEB_DATA_VIZ_URL')}/metricas/configuracoes/{server_id}"

    response = requests.get(url, timeout=10)
    response.raise_for_status()

    configuracoes = response.json()

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

if __name__ == "__main__":
    if realizar_login():
        configuracoes = buscar_configuracoes()
        iniciar_monitoramento(configuracoes)