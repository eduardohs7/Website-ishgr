# Backup e verificação de recuperação do PostgreSQL

Esta entrega oferece dois comandos de manutenção do backend. Não cria telas nem depende de SMTP ou do gateway de pagamentos.

`backup_database` lê o banco configurado no Django e gera uma nova pasta com `database.dump` (formato custom do PostgreSQL) e `manifest.json`. Cada execução usa um nome exclusivo e não sobrescreve backups anteriores. As contagens e o dump usam o mesmo snapshot de leitura, inclusive quando chegam novas inscrições durante a execução.

`verify_database_backup` confere o SHA-256, cria um banco `chags_restore_<identificador aleatório>` no servidor configurado, restaura a cópia e compara contagens de todas as tabelas públicas, definições das triggers e nomes/tipos/estado das constraints. Remove somente esse banco temporário ao terminar, inclusive se a restauração ou a comparação falhar. Não há opção para escolher um banco existente como destino.

## Antes de executar

- Instale os clientes `pg_dump` e `pg_restore` da mesma versão principal do servidor e do backup. Nesta etapa, PostgreSQL 17. Atualizações menores compatíveis da versão 17 são aceitas.
- Use as variáveis de conexão já documentadas no [guia Windows](desenvolvimento-windows.md) ou nos settings de produção. Não há senha padrão nem parâmetro de senha na linha de comando.
- Para gerar a cópia, a conta precisa ler todos os objetos do banco. Não é necessário `CREATEDB` para esse comando.
- Para o ensaio, a conta precisa conectar ao banco de manutenção `postgres`, ter `CREATEDB` e criar a extensão confiável `btree_gist` no banco novo. A role local/CI já atende esses requisitos sem ser superusuária.
- Em produção, use um servidor de ensaio separado, aprovado pelo CTIC. A role de runtime não deve receber `CREATEDB` para viabilizar um teste. Uma configuração Django específica de manutenção pode apontar ao servidor de ensaio por meio das variáveis `CHAGS_DB_*`.

Os comandos não exportam conteúdo de registros nem mensagens de erro completas do cliente para o terminal. A senha fica em um arquivo `pgpass` temporário, eliminado ao final; no Linux, esse arquivo recebe permissão `0600`. As opções TLS dos settings são preservadas, e variáveis `PG*` herdadas não substituem a conexão configurada. Opções de conexão além das suportadas pelo helper são recusadas.

## Windows / PowerShell

Na raiz do repositório, com Python e conexão ao PostgreSQL configurados:

```powershell
.\.venv\Scripts\python.exe backend\manage.py backup_database --pg-bin "C:\Program Files\PostgreSQL\17\bin"
```

O comando informa o caminho da pasta criada. Copie esse caminho para o segundo comando:

```powershell
.\.venv\Scripts\python.exe backend\manage.py verify_database_backup "C:\caminho\Website-ishgr\backend\.local\backups\chags-PASTA-CRIADA" --pg-bin "C:\Program Files\PostgreSQL\17\bin"
```

Substitua o caminho pelo exibido na sua execução; não use literalmente `chags-PASTA-CRIADA`. Se instalou outra versão principal do servidor, use os clientes correspondentes.

As permissões `0700/0600` aplicadas no Linux não configuram ACLs do Windows. Guarde as pastas do projeto, backups e arquivos temporários em locais restritos ao seu usuário e aos administradores autorizados, conferindo **Propriedades → Segurança**. Não use uma pasta compartilhada para guardar dados reais. Os testes de recuperação desta entrega foram executados no Linux; a execução nativa no Windows ainda precisa ser conferida.

## Linux / ambiente de nuvem

```bash
.venv/bin/python backend/manage.py backup_database
.venv/bin/python backend/manage.py verify_database_backup backend/.local/backups/chags-PASTA-CRIADA
```

O helper usa `--pg-bin`, depois `CHAGS_PG_BIN`, depois os clientes disponíveis no PATH; na nuvem deste projeto, também encontra o runtime PostgreSQL 17 instalado pelo setup. É possível fornecer `--output-dir` para uma pasta privada fora do repositório e `--database` para um alias definido nos settings.

Pastas novas de backup recebem `0700` e os dois arquivos recebem `0600` no Linux. Backups locais ficam em `backend/.local/backups/`, já ignorado pelo Git. Se usar outro destino, mantenha-o fora do Git e de qualquer diretório servido pela aplicação.

## Como interpretar o resultado

Backup criado não significa recuperação aprovada. Execute a verificação e aguarde a mensagem de sucesso. Checksum divergente impede qualquer conexão ou criação de banco. Uma falha de restore ou divergência no inventário reprova o ensaio; não trate essa cópia como verificada. Se a remoção do banco temporário falhar, o comando informa apenas seu nome e solicita limpeza pelo DBA.

Uma interrupção forçada do processo ou perda de conexão pode impedir a limpeza. O DBA deve conferir eventuais bancos temporários antes de repetir o ensaio e remover apenas os identificados como abandonados. Uma pasta sem os dois arquivos não é um backup concluído.

A comparação de contagens não compara cada campo de cada registro. O ensaio verifica que o PostgreSQL consegue restaurar o arquivo e que o inventário esperado foi recuperado. Os testes automatizados também conferem dados de uma conta fictícia, seu hash de senha e os snapshots da inscrição, além das proteções e da limpeza.

O SHA-256 identifica corrupção acidental, mas não autentica um arquivo recebido de terceiros. Restaure somente arquivos de origem confiável: um dump pode executar SQL no servidor de ensaio. O ensaio não inicia Django, workers ou serviços SMTP; mensagens e tokens restaurados permanecem inativos enquanto nenhuma aplicação utilizar esse banco.

## O que ainda falta para a operação real

O dump contém dados pessoais, hashes de senha, sessões, tokens e a fila de e-mails. Compressão do formato custom não é criptografia. Proteja o armazenamento e o transporte das cópias com os mecanismos aprovados pelo CTIC e limite quem pode acessá-las. O manifesto não inclui nomes/e-mails dos participantes, mas o dump os inclui.

Essa entrega não agenda backups, não envia arquivos para armazenamento externo e não apaga cópias por uma política de retenção presumida. O CTIC e a organização ainda precisam definir frequência, retenção, responsáveis, criptografia, cópia fora do servidor, monitoramento e objetivos de perda de dados/tempo de recuperação. A cópia local sozinha não protege contra perda do servidor.

O backup lógico não inclui roles e suas permissões, configurações do PostgreSQL, a chave Django, variáveis/segredos do serviço nem arquivos de `MEDIA_ROOT`. Esses itens precisam de proteção e procedimento próprios. A restauração usa o proprietário da conexão do ensaio, sem recriar donos/ACLs de produção.

Uma recuperação real sobre o ambiente de produção continua sendo uma operação do DBA, com aplicação/worker interrompidos, validação e plano de retorno. Não foi realizada nem automatizada por estes comandos. Use a versão do código e das migrações correspondente à cópia ao preparar uma recuperação.
