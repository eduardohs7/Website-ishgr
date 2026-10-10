# Executar o CHAGS 14 no Windows

Este guia usa PowerShell, Python 3.12 e PostgreSQL 17, instalados no seu computador. O servidor de desenvolvimento serve o site público e as telas do backend. Não é necessário fazer commit para testar; commit registra uma versão e push envia essa versão ao GitHub.

## 1. Obter o código e instalar os programas

Obtenha o código da branch **`feat/chags14-backend`** no GitHub. Com Git instalado, abra o PowerShell em uma pasta sua, por exemplo `C:\Projetos`, e execute:

```powershell
git clone --branch feat/chags14-backend https://github.com/eduardohs7/Website-ishgr.git
```

Também pode selecionar essa branch no GitHub e usar **Code → Download ZIP**, extraindo o pacote em uma pasta sua. O nome da pasta extraída pode incluir o nome da branch; ajuste os caminhos abaixo. O backend não aparece na `main` até a integração da branch. Se já possui uma cópia com modificações próprias, obtenha a nova versão em uma pasta separada, sem sobrescrevê-la. A nova pasta não traz sua `.venv` nem seu banco: recrie a `.venv` e configure a conexão com o banco local existente, sem repetir sua criação.

Instale Python **3.12, 64 bits**, pelo site https://www.python.org/downloads/windows/ (inclua o launcher `py`). Instale PostgreSQL **17** pelo instalador indicado em https://www.postgresql.org/download/windows/. Mantenha a porta `5432`, anote a senha escolhida para o administrador `postgres` e não precisa instalar complementos do Stack Builder. Abra um novo PowerShell depois da instalação.

## 2. Criar o banco local

Abra **SQL Shell (psql)** no menu Iniciar. Aceite servidor `localhost`, banco `postgres`, porta `5432` e usuário `postgres`; informe a senha escolhida na instalação. Execute uma linha por vez:

```sql
CREATE ROLE chags_dev LOGIN CREATEDB;
\password chags_dev
CREATE DATABASE chags_dev OWNER chags_dev;
\q
```

O comando `\password` pede uma nova senha para o usuário da aplicação, sem colocar a senha no comando. Guarde-a para a próxima etapa. `CREATEDB` permite criar o banco temporário dos testes locais; não é uma recomendação para a conta de produção. Se o banco e o usuário já existem, não repita sua criação.

## 3. Preparar o Python

Abra o PowerShell na pasta extraída (ajuste o caminho se necessário):

```powershell
cd C:\Projetos\Website-ishgr
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r backend\requirements-windows.txt
```

Usamos o executável diretamente, sem ativar a venv nem alterar a política de execução do PowerShell. O arquivo de dependências do Windows inclui os dados de fuso horário necessários nessa plataforma. Gunicorn é uma dependência de produção Linux; aqui executamos o servidor do Django.

## 4. Configurar a conexão

Execute este bloco no PowerShell aberto na raiz do projeto:

```powershell
$env:DJANGO_SETTINGS_MODULE = 'config.settings.development'
$env:CHAGS_DB_HOST = '127.0.0.1'
$env:CHAGS_DB_PORT = '5432'
$env:CHAGS_DB_NAME = 'chags_dev'
$env:CHAGS_DB_USER = 'chags_dev'
$senhaBanco = Read-Host 'Senha do usuario chags_dev' -AsSecureString
$env:CHAGS_DB_PASSWORD = [System.Net.NetworkCredential]::new('', $senhaBanco).Password
Remove-Variable senhaBanco
```

Essas variáveis duram apenas nessa janela. Repita este bloco em cada novo terminal que executar comandos do backend. Não copie a senha para o Git ou para a conversa. O `.env.example` documenta produção e não é carregado automaticamente: não é preciso copiá-lo para este procedimento.

## 5. Inicializar e abrir o site

```powershell
.\.venv\Scripts\python.exe backend\manage.py migrate
.\.venv\Scripts\python.exe backend\manage.py initialize_chags14
.\.venv\Scripts\python.exe backend\manage.py setup_registration_roles
.\.venv\Scripts\python.exe backend\manage.py check
.\.venv\Scripts\python.exe backend\manage.py runserver 127.0.0.1:8001
```

Deixe esse terminal aberto. No navegador **do seu computador**, digite `http://127.0.0.1:8001`. Para ver o cadastro, use `http://127.0.0.1:8001/conta/cadastro/`; para entrar, `/conta/entrar/`; após confirmar o e-mail, `/participante/`. `Ctrl+C` encerra o servidor.

O evento começa com inscrições fechadas, sem categorias e sem preços. Isso permite testar contas e perfis sem inventar condições comerciais. Pagamentos ainda aguardam a escolha do serviço.

## 6. Conferir o e-mail de confirmação

Abra um **segundo PowerShell**, entre na mesma pasta e repita o bloco de conexão da etapa 4. Execute:

```powershell
.\.venv\Scripts\python.exe backend\manage.py send_account_emails --watch --interval 2
```

Cadastre um participante fictício no navegador. Neste ambiente, as mensagens são gravadas em `backend\.local\emails`, sem envio pela internet. Abra a mensagem mais recente no Bloco de Notas e use o link ou o código de confirmação. Esses arquivos contêm códigos privados: não os compartilhe. Deixe o segundo terminal aberto para processar novas mensagens e recuperação de senha.

## Administração e testes opcionais

Em outro terminal com o bloco de conexão configurado, crie seu administrador interativamente:

```powershell
.\.venv\Scripts\python.exe backend\manage.py createsuperuser
```

A administração fica em `/gestao/`. Essa conta administra configurações; use uma conta comum separada para testar a experiência do participante.

Para executar a suíte automatizada:

```powershell
.\.venv\Scripts\python.exe backend\manage.py test accounts registrations operations config.tests --noinput
```

Os catálogos de tradução compilados já acompanham o código. Editar traduções exige instalar GNU gettext e recompilá-las posteriormente. Os scripts `.sh` do projeto preparam o ambiente Linux da nuvem e não são os comandos para Windows.

## Problemas comuns

- `py` não reconhecido: confirme a instalação do launcher Python e abra outro terminal.
- Conexão recusada na porta 5432: verifique se o serviço PostgreSQL está iniciado em Serviços do Windows e se a porta escolhida na instalação corresponde ao bloco de conexão.
- Falha de autenticação: use a senha de `chags_dev`, definida com `\password`, e confirme as variáveis na janela em uso.
- Erro de migração ou dependências: envie a mensagem de erro, removendo senhas e códigos de acesso.
- Porta 8001 ocupada: encerre outra instância do servidor. Manter essa porta evita desencontro com os links das mensagens locais.

As dependências foram resolvidas para Windows com hashes; a aplicação e a suíte foram validadas no ambiente Linux. A execução nativa no Windows precisa ser conferida no seu computador.
