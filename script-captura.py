import platform
import socket
import requests
import os
import psutil

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

        "DISK": [
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
        "hostname": socket.gethostname(),
        "sistema_operacional": platform.system(),
        "versao_so": platform.version(),
        "arquitetura": platform.machine(),
    }

    # Cadastra servidor no Banco de dados
    # TODO: Necessita de ENDPOINT na Web-Data-Viz
    response = requests.post(f"{os.getenv('WEB_DATA_VIZ_URL')}/servidor", json=server_info)

    return response.json()["id"]

def cadastrar_componentes(server_id):
    componentes = montar_componentes()

    response = requests.post(
        f"{os.getenv('WEB_DATA_VIZ_URL')}/servidor/{server_id}/componentes",
        json=componentes
    )
    return response.json()

def cadastrar_metricas(componentes):
    metricas = montar_metricas()
    metricas_cadastradas = []

    for componente in componentes:
        tipo_componente = componente["tipo"]
        componente_id = componente["id"]

        metricas_componente = metricas.get(tipo_componente, [])
        for metrica in metricas_componente:
            response = requests.post(
                f"{os.getenv('WEB_DATA_VIZ_URL')}/componentes/{componente_id}/metricas",
                json=metrica
            )
            metricas_cadastradas.append(response.json())

    return metricas_cadastradas


