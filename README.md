# StreamGuard — Captura

Agente Python para monitorar CPU, memória, disco, rede e processos durante transmissões ao vivo, com modos real e simulado.

## Funcionamento

O agente autentica na API Web, identifica o equipamento e seus componentes e consulta as configurações de coleta do MySQL pela API. Os dados seguem o fluxo:

```text
Captura real ou simulada → lotes CSV → S3 Bronze → stream-guard-etl
```

- `script-captura.py`: autenticação, configurações e captura com `psutil`.
- `lotes.py`: gravação e fechamento dos lotes.
- `enviar-bronze.py`: envio dos CSVs e do marcador de conclusão.
- `executar.py`: coordenação da captura e do remetente.
- `simulacao.py`: séries sintéticas com `random`, sem medir ou sobrecarregar os recursos físicos.
- `configuracao_captura.py`: seleção do modo e dos diretórios compartilhados.

Cada métrica utiliza seu intervalo configurado. As configurações são atualizadas a cada 60 segundos; processos são coletados a cada 40 segundos. Os lotes fecham aproximadamente a cada minuto, e o remetente verifica pendências a cada 15 segundos.

O tratamento Bronze → Silver → Gold pertence exclusivamente ao repositório `stream-guard-etl`.

## Instalação e configuração

Requer Python 3.10 ou superior, acesso à API e credenciais AWS para o envio. As dependências são `psutil`, `requests`, `rich`, `python-dotenv` e `boto3`.

No PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

No Linux, use `python3 -m venv .venv` e `.venv/bin/python -m pip install -r requirements.txt`.

Crie um `.env` na raiz, sem substituir configurações existentes. O `.env.example` contém o endereço inicial da API.

| Variável | Uso |
|---|---|
| `WEB_DATA_VIZ_URL` | Endereço da API, incluindo protocolo e porta. |
| `USUARIO_EMAIL`, `USUARIO_SENHA` | Login da aplicação; quando ausentes, o agente solicita login interativo. |
| `S3_BUCKET` | Bucket de destino, sem `s3://`. |
| `AWS_DEFAULT_REGION` | Região do bucket; padrão `us-east-1`. |
| `AWS_PROFILE` | Perfil AWS local. Omita quando utilizar uma função IAM na EC2. |
| `STREAMGUARD_MODO` | `real` ou `simulado`; padrão `real`. A opção `--modo` tem precedência. |
| `SIMULACAO_SEMENTE` | Semente inteira opcional para as séries sintéticas. |
| `SIMULACAO_DURACAO_FASE` | Duração de cada fase simulada, em segundos; padrão 120. |

O agente exige configurações válidas para as métricas em `config_metrica`, consultadas por `GET /metricas/configuracoes/:servidor_id`. Credenciais privadas ficam fora do Git.

## Execução

Na raiz do repositório:

```powershell
.\.venv\Scripts\python.exe executar.py
```

No Linux, use `.venv/bin/python executar.py`. Sem configuração de modo, o comando inicia a captura real e o envio ao S3. A opção `--intervalo-envio` altera somente a frequência do remetente.

Para selecionar explicitamente:

```powershell
.\.venv\Scripts\python.exe executar.py --modo real
.\.venv\Scripts\python.exe executar.py --modo simulado --semente 42 --duracao-fase 120
```

O modo simulado usa a API e o envio reais quando executado com `executar.py`. Ele cadastra um equipamento identificado por `SIMULADO-<hostname>`, com código de agente próprio. Esse equipamento precisa possuir suas configurações válidas de métricas na API, assim como um equipamento real; não são copiados ou sobrescritos os parâmetros da máquina real.

A simulação percorre normal, elevada, crítica e recuperação, repetindo o ciclo. As transições são progressivas; CPU/RAM permanecem elevadas durante parte das fases. São modelados oito processadores lógicos, 16 GiB de RAM, um volume de 8 GiB e rede de 1 Gbit/s. Esses valores representam um perfil virtual, não o hardware físico. A recuperação do disco inclui limpeza de arquivos.

Uma semente reproduz as séries quando os instantes de consulta são os mesmos. Timestamps e identificadores de lotes continuam próprios de cada execução. Os intervalos e a atualização das configurações pela API são os mesmos nos dois modos.

Para gravar lotes sem iniciar o envio, execute `script-captura.py --modo real` ou `script-captura.py --modo simulado`. Para enviar pendências separadamente, use `enviar-bronze.py --modo real` ou `enviar-bronze.py --modo simulado`. As opções de semente e duração também são aceitas pelo script de captura.

Encerre com Ctrl+C e aguarde o fechamento dos processos; não execute dois coordenadores na mesma pasta.

## Dados e implantação

Os CSVs `monitoramento_v2_<id>.csv` e `processos_<id>.csv` mantêm o mesmo schema em ambos os modos. Os lotes reais ficam em `csvs/lotes/`; os simulados, em `csvs/lotes-simulados/`. Cada diretório contém `abertos/`, `pendentes/` e `enviados/`. Arquivos enviados são preservados localmente; falhas deixam os lotes pendentes para nova tentativa.

O destino segue os caminhos:

```text
bronze/monitoramento_v2_<id>/<lote>.csv
bronze/processos_<id>/<lote>.csv
bronze/lotes-concluidos/<lote>.json
```

Para implantação conjunta, disponha captura, ETL e site lado a lado. `iniciar-ec2.sh` prepara os repositórios; `instalar.sh` instala API/MySQL em Docker e delega o agendamento à ETL. Esses comandos alteram o ambiente e exigem Ubuntu, systemd e recursos AWS disponíveis.

A API utiliza `develop`; captura e ETL utilizam `main`. As variáveis `STREAMGUARD_CAPTURA_BRANCH` e `STREAMGUARD_ETL_BRANCH` permitem selecionar outras branches. A ETL utiliza seu próprio `.env` e ambiente Python.

Lotes simulados têm identificação `simulado_`. Os marcadores Bronze contêm `lote`, `concluido_em_utc` e `objetos` em ambos os modos. Os nomes de processos também são identificados como `SIMULADO`. O remetente impede enviar um lote cuja identificação não corresponda ao modo selecionado. A separação por equipamento evita misturar identidades reais e sintéticas na Silver; os caminhos S3 e as colunas CSV permanecem iguais.

A disponibilidade das leituras reais depende do sistema operacional e das permissões de acesso. O primeiro cadastro do equipamento simulado e o envio pela infraestrutura real precisam ser homologados com a API e a AWS disponíveis.
