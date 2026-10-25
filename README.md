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
| `CAPTURA_MODO` | `real` ou `simulado`; padrão `real`. Precedência: `--modo` → `CAPTURA_MODO` → `STREAMGUARD_MODO` → `real`. A variável antiga continua aceita. |
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

O modo simulado usa a API e o envio reais quando executado com `executar.py`. Os dois modos reutilizam a mesma identidade da instalação e o ID devolvido pelo MySQL. O equipamento precisa possuir configurações válidas de métricas na API; configurações personalizadas são preservadas.

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

A instalação central é coordenada por `provisionar.py`, `iniciar-ec2.sh` e `instalar.sh`. Ela prepara os quatro repositórios na EC2, sem iniciar a captura nessa máquina. A API utiliza `develop`; captura, ETL e Java utilizam `main`.

Lotes simulados têm identificação `simulado_`. Os marcadores Bronze contêm `lote`, `concluido_em_utc` e `objetos` em ambos os modos. Os nomes de processos também são identificados como `SIMULADO`. O remetente impede enviar um lote cuja identificação não corresponda ao modo selecionado. A separação local dos lotes evita misturas acidentais no envio; ambos os modos usam o mesmo servidor, os mesmos caminhos S3 e as mesmas colunas CSV.

A disponibilidade das leituras reais depende do sistema operacional e das permissões de acesso. O cadastro do equipamento e o envio pela infraestrutura real precisam ser homologados com a API e a AWS disponíveis.


## Identificação e acesso à API

O agente autentica uma vez, conserva o cookie com requests.Session e usa o token CSRF do login nos cadastros. A API verifica a empresa no MySQL antes de cadastrar/reutilizar servidor, componente e métrica. Usuários da própria empresa podem executar o cadastro; a alteração administrativa de limites continua restrita a Admin. Não informe servidor_id no .env.

identidade-agente.json guarda somente a identidade da instalação, sem senha ou cookie. Para instalações novas, o código usa UUID; ele não é o servidor_id nem uma credencial. O código é independente do modo e permanece após reinícios e mudanças de hostname. Não copie esse arquivo entre máquinas. Na primeira associação, códigos legados real/simulado são consultados pelo endpoint existente; um único vínculo da mesma empresa pode ser reutilizado sem alterar o registro. Havendo múltiplos vínculos ou empresa incompatível, a inicialização para com erro. Arquivo inválido também exige revisão, sem reset automático. O MySQL continua sendo a fonte do ID.

Copie .env.example para .env apenas se .env ainda não existir. O modelo versionado é autoexplicativo; o arquivo preenchido permanece privado e ignorado. Git não ignora alterações pessoais de um arquivo já rastreado: por isso .env não deve ser versionado. Na revisão de commit, confira git ls-files -- .env e nunca publique senhas, cookies, certificados privados ou a identidade local.

Todos os pedidos têm timeout; redirecionamentos não são seguidos. Um 401 permite uma reautenticação e uma nova tentativa; 403/409 interrompem o cadastro. Falha no cadastro impede iniciar uma captura parcialmente configurada. Falhas posteriores na consulta de configurações preservam a última configuração válida, conforme o comportamento existente.

## Provisionamento de uma implantação nova

Execute no computador autorizado com Python 3.10+, AWS CLI v2 e OpenSSH. Configure o perfil temporário `streamguard` pelos meios do laboratório. Tenha o arquivo da chave SSH de um key pair EC2 existente. O instalador não cria Role, key pair, VPC ou subnet.

O comando abaixo consulta a identidade, resolve a imagem Canonical Ubuntu 24.04 LTS x86_64, identifica uma subnet existente com saída por Internet Gateway e faz dry-run EC2. Não cria recursos:

```powershell
python provisionar.py --chave-ssh C:/caminho/chave.pem --rede IP_PUBLICO_DA_REDE/32 --diretorio C:/caminho/implantacao-nova
```

Se o nome do key pair for diferente do nome do arquivo, informe `--nome-chave`. `--subnet` permite selecionar uma subnet existente. O instance profile padrão é `LabInstanceProfile`; `--perfil-instancia` seleciona outro já autorizado. `--perfil` seleciona o perfil AWS local. A região é `us-east-1`, a instância é `t3.medium`, com 30 GiB gp3 criptografados e créditos de CPU standard. Não são feitas perguntas sobre orçamento.

Após autorização para criar recursos, repita o comando com a mesma pasta e `--executar`. Será exigida a confirmação `CRIAR <identificador>` exibida no plano. A primeira instalação pede razão social, CNPJ, nome, e-mail e senha pessoal do Admin inicial (mínimo 12 caracteres). Sem `--rede`, o IPv4 público atual é detectado por checkip.amazonaws.com e api.ipify.org e exibido como `/32`; respostas divergentes interrompem o plano. IP privado e faixas amplas são recusados. A confirmação de criação informa explicitamente esse CIDR para TCP 3333 e SSH 22; nenhuma regra é aplicada no planejamento. A detecção é repetida em cada execução para evitar usar silenciosamente a rede de uma conexão anterior. Não são solicitados IDs de servidor, bucket manual, token Jira ou credenciais AWS permanentes. Use o e-mail de um Admin da empresa; o domínio reservado @streamguard.com não fornece o vínculo empresarial esperado pela captura no login atual.

O bucket recebe nome `streamguard-DDMMAA-<sufixo>-<tentativa>` e é sempre novo. Nomes existentes são ignorados; resultados de criação incertos interrompem a execução para conferência. O bucket recebe Block Public Access, propriedade BucketOwnerEnforced e criptografia SSE-S3. Nenhum bucket de outra implantação é esvaziado ou configurado.

O grupo exclusivo libera somente TCP 22 e 3333 para a rede informada; `0.0.0.0/0` é rejeitado. MySQL não publica porta. A EC2 recebe a Role existente, IMDSv2 obrigatório e hop limit 2 para os SDKs nos contêineres. Nenhuma chave AWS é copiada para a instância.

### Serviços centrais

O bootstrap usa Git com atualização somente fast-forward e recusa checkouts alterados, branches inesperadas ou commits locais desconhecidos. Antes de executar na AWS, os scripts desta automação precisam estar publicados na branch `main` da captura.

Docker Compose executa MySQL 8, Node 22/API e Java 21. A API usa `DB_HOST=banco`; Java consulta `http://api:3333` pela rede do Compose. Os volumes `dados_mysql` e `estado_java` preservam os dados e o controle de ocorrências nas reinicializações normais. Não execute comandos que removam esses volumes. A ETL mantém seu Python e o timer systemd já existente, a cada cinco minutos após o término da execução anterior.

O banco vazio recebe somente as tabelas do SQL atual, os quatro perfis e a empresa/Admin informados. Dados de exemplo e DROP DATABASE do arquivo acadêmico não são executados. O hash inicial utiliza o formato SHA-256 já esperado pelo login, sem migração de senhas. Banco não vazio sem confirmação local desta implantação é recusado; falhas durante o DDL exigem revisão, sem excluir ou escolher dados automaticamente. A API recebe um usuário MySQL próprio com SELECT/INSERT/UPDATE/DELETE.

`api.env`, `banco.env` e `java.env` ficam fora dos repositórios, com permissão restrita; o `.env` da ETL é criado apenas quando ausente. Arquivos existentes incompatíveis são preservados e interrompem a instalação. A sessão acadêmica usa `SESSION_COOKIE_SECURE=false`, `APP_ORIGIN` HTTP exata, HttpOnly, SameSite e CSRF existentes. HTTP não criptografa senhas ou cookies; o acesso fica limitado à rede autorizada.

A senha do Admin passa somente pela entrada privada do SSH e não fica no plano. As senhas técnicas e o segredo da sessão são gerados na EC2. O primeiro vínculo SSH usa accept-new e guarda known_hosts na pasta da implantação; mudanças posteriores de chave são recusadas. A chave inicial ainda precisa ser conferida pelo responsável pelo acesso.

Jira permanece com `JIRA_ENVIAR=false`. Seu token e a automação Jira → Slack serão configurados e validados separadamente, com autorização. Nenhuma credencial Jira é fornecida ao contêiner Web.

### Máquina de captura

Ao terminar, a automação produz `conexao-captura.json`, sem credenciais, na pasta de implantação do computador. Copie esse arquivo para a máquina monitorada e execute no checkout da captura:

```powershell
python preparar-captura.py --conexao C:/caminho/conexao-captura.json --modo real
.\.venv\Scripts\python.exe executar.py
```

No Linux, use `.venv/bin/python executar.py`. Para preparar o modo simulado, informe `--modo simulado`; para alternar depois, use `executar.py --modo simulado` ou ajuste `CAPTURA_MODO` no arquivo privado. A precedência continua `--modo → CAPTURA_MODO → STREAMGUARD_MODO → real`. O agente mantém a identidade e obtém o ID do MySQL pelo cadastro existente.

O preparador cria `.env` a partir do modelo e instala somente as dependências da captura. `--somente-config` não instala pacotes. Um `.env` existente divergente é preservado, sem atualização silenciosa de bucket, API ou modo. Reveja-o antes de mudar de implantação; não remova a identidade para tentar corrigir conflitos. O login pessoal continua interativo quando as credenciais não estão no ambiente. O perfil AWS local precisa permitir o envio ao bucket da implantação.

Distribua `.env.example` e o arquivo de conexão sem segredos. O `.env` preenchido permanece privado e ignorado; um arquivo rastreado pelo Git não fica protegido apenas por `.gitignore`.

### Reexecução e validação

Reexecute com a mesma pasta para continuar após uma falha. O plano preserva IDs, bucket e token de criação da EC2; recursos já confirmados não são duplicados. Não edite esse estado nem o reutilize para outra conta/rede. Após destruir manualmente a infraestrutura no console, use uma pasta nova para outra implantação. Não há automação de destruição.

A instalação verifica a consulta da API ao MySQL, o agendamento da ETL, a leitura S3 pela Role e a permanência dos três contêineres. Um bucket novo aguarda o primeiro lote para produzir Silver/Gold. Escrita S3, resultados do processamento, envio Jira/Slack, construção das imagens e comportamento na EC2 precisam ser homologados no ambiente real; testes mockados não comprovam essas operações.

O perfil local precisa de STS GetCallerIdentity; leituras EC2 de imagem/rede/key pairs; SSM GetParameter público; IAM GetInstanceProfile e PassRole; EC2 RunInstances/CreateTags/CreateSecurityGroup/AuthorizeSecurityGroupIngress; S3 HeadBucket/CreateBucket/PutBucketTagging/PutBucketPublicAccessBlock/PutEncryptionConfiguration. A Role da EC2 precisa listar/ler Bronze e Silver e publicar Silver/Gold/qualidade; o perfil da captura precisa publicar CSVs e marcadores Bronze. Em termos de IAM, HeadBucket/ListObjectsV2 exigem `s3:ListBucket`; a criação com ObjectOwnership explícito também exige `s3:PutBucketOwnershipControls`. O processamento usa `s3:GetObject` e `s3:PutObject`; uploads multipart podem precisar de `s3:AbortMultipartUpload` para encerrar tentativas falhas. O dry-run EC2 não comprova todas as permissões S3 nem as permissões efetivas da Role. O laboratório pode bloquear ações; o instalador informa a falha e não modifica IAM automaticamente.

Diagnóstico na EC2: `sudo docker compose --project-name streamguard -f ~/streamguard/compose.yaml ps` e `systemctl status streamguard-etl.timer`. Examine arquivos privados e logs somente com cuidado para não divulgar credenciais; não execute `docker compose config` em relatórios públicos, pois expande variáveis de ambiente.

Referências: [RunInstances e client token](https://docs.aws.amazon.com/cli/latest/reference/ec2/run-instances.html), [imagens Ubuntu por parâmetro público](https://ubuntu.com/aws/docs/aws-how-to/instances/find-ubuntu-images/) e [configuração de serviços Compose](https://docs.docker.com/reference/compose-file/services/).
