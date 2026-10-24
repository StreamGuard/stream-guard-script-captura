# StreamGuard — Captura

Agente Python para monitorar CPU, memória, disco, rede e processos durante transmissões ao vivo.

## Funcionamento

O agente autentica na API Web, identifica o equipamento e seus componentes e consulta as configurações de coleta do MySQL pela API. Os dados seguem o fluxo:

```text
Captura real → lotes CSV → S3 Bronze → stream-guard-etl
```

- `script-captura.py`: autenticação, configurações e captura com `psutil`.
- `lotes.py`: gravação e fechamento dos lotes.
- `enviar-bronze.py`: envio dos CSVs e do marcador de conclusão.
- `executar.py`: coordenação da captura e do remetente.

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

O agente exige configurações válidas para as métricas em `config_metrica`, consultadas por `GET /metricas/configuracoes/:servidor_id`. Credenciais privadas ficam fora do Git.

## Execução

Na raiz do repositório:

```powershell
.\.venv\Scripts\python.exe executar.py
```

No Linux, use `.venv/bin/python executar.py`. O comando inicia a captura real e o envio ao S3. A opção `--intervalo-envio` altera somente a frequência do remetente.

Para gravar lotes sem iniciar o envio, execute `script-captura.py`. Para enviar os lotes pendentes separadamente, execute `enviar-bronze.py`. Encerre com Ctrl+C e aguarde o fechamento dos processos; não execute duas capturas na mesma pasta.

## Dados e implantação

Os CSVs `monitoramento_v2_<id>.csv` e `processos_<id>.csv` ficam em `csvs/lotes/`, separados em `abertos/`, `pendentes/` e `enviados/`. Arquivos enviados são preservados localmente; falhas deixam os lotes pendentes para nova tentativa.

O destino segue os caminhos:

```text
bronze/monitoramento_v2_<id>/<lote>.csv
bronze/processos_<id>/<lote>.csv
bronze/lotes-concluidos/<lote>.json
```

Para implantação conjunta, disponha captura, ETL e site lado a lado. `iniciar-ec2.sh` prepara os repositórios; `instalar.sh` instala API/MySQL em Docker e delega o agendamento à ETL. Esses comandos alteram o ambiente e exigem Ubuntu, systemd e recursos AWS disponíveis.

A API utiliza `develop`; captura e ETL utilizam `main`. As variáveis `STREAMGUARD_CAPTURA_BRANCH` e `STREAMGUARD_ETL_BRANCH` permitem selecionar outras branches. A ETL utiliza seu próprio `.env` e ambiente Python.

A disponibilidade das leituras depende do sistema operacional e das permissões de acesso. A instalação e os serviços precisam ser homologados na infraestrutura real.
