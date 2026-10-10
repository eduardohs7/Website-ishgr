# Testes automáticos do backend no GitHub

O workflow **Backend CI**, em `.github/workflows/backend-ci.yml`, verifica o backend sem publicar o site, fazer merge ou acessar dados de produção. HTML, CSS e JavaScript não são modificados pela execução. A coleta de arquivos estáticos cria somente arquivos temporários do runner.

## Quando executa

- Push que altere arquivos em `backend/` ou o próprio workflow, em qualquer branch.
- Pull request destinado à `main`, quando esses mesmos arquivos forem alterados.
- Disparo manual por `workflow_dispatch`, quando disponível no GitHub.

Alterações exclusivas no front ou na documentação não iniciam essa suíte. Push e PR da mesma branch compartilham o grupo de concorrência: uma execução mais recente cancela a anterior, evitando dois jobs ativos para a mesma origem. O cancelamento de uma execução substituída não representa falha de teste.

O workflow usa o evento `pull_request`, sem permissões de escrita e sem `pull_request_target`. Ele não precisa de segredos SMTP, de pagamento, de produção ou de um token pessoal adicional. As ações de checkout/Python e a imagem oficial do PostgreSQL estão fixadas em revisões/digest para que uma mudança externa não altere silenciosamente o ambiente.

## O que verifica

1. Instala Python 3.12 e as dependências do lockfile Linux, exigindo os hashes dos pacotes.
2. Inicia PostgreSQL 17.11 em contêiner descartável, com porta aleatória acessível apenas no endereço local do runner.
3. Cria o banco e a role `chags_ci`, com `CREATEDB` para os testes, sem privilégios de superusuário ou criação de roles.
4. Executa `check --database default` e detecta mudanças de modelos sem migrações com `makemigrations --check --dry-run`.
5. Aplica as migrações e confirma que não restaram migrações pendentes.
6. Executa a suíte de `accounts`, `registrations`, `operations` e `config.tests`, incluindo os testes de concorrência com PostgreSQL.
7. Verifica a coleta dos arquivos estáticos usados pelo backend.
8. Remove o contêiner, seu volume e o arquivo local de credenciais, inclusive quando um passo anterior falha.

O helper `backend/scripts/ci-postgres.py` gera senhas aleatórias por execução. As informações locais ficam em `backend/.local/ci-postgres/state.json`, com permissões privadas, fora do Git. No GitHub, as senhas são mascaradas antes de serem usadas e as variáveis da conexão são disponibilizadas somente aos passos seguintes do job. Não há upload de banco, mensagens de e-mail ou credenciais como artefatos.

Essa role é exclusiva dos testes. A concessão de `CREATEDB` não é orientação para a conta de execução em produção. O limite do job é de 15 minutos; em caso de interrupção, a limpeza é tentada e a máquina temporária do GitHub é descartada ao encerrar a execução.

## Como conferir o resultado

No GitHub, abra **Actions → Backend CI** e escolha a execução da branch desejada. No pull request, o resultado aparece em **Checks**, com o job **Django e PostgreSQL**. Abra o passo que falhou para ver a mensagem e corrigir a causa; não aprove um merge presumindo que um job pendente ou cancelado passou.

A execução manual pela interface depende de o workflow já estar disponível na branch padrão, conforme as regras do GitHub. Enquanto a entrega está somente na branch do backend, push e atualização do PR são os disparos usados para validá-la.

Publicar o workflow disponibiliza os checks, mas **não configura proteção de branch**. Para impedir merges quando a suíte falhar, um administrador do repositório deve configurar uma regra/ruleset da `main` exigindo o check deste workflow após a primeira execução. Essa configuração administrativa não é feita pelo código e não foi aplicada nesta entrega.

## Reprodução local

Para validar os testes comuns no Windows, com o PostgreSQL local já configurado, use os comandos do [guia Windows](desenvolvimento-windows.md):

```powershell
.\.venv\Scripts\python.exe backend\manage.py test accounts registrations operations config.tests --noinput
```

O helper de contêiner é destinado ao CI Linux com Docker e não substitui a instalação local já preparada. Não é preciso instalar Docker no Windows para testar o backend.

O workflow usa settings de desenvolvimento com envio local de e-mails. Ele verifica o código e as migrações, mas não valida credenciais reais de SMTP/gateway, HTTPS do CTIC, execução nativa no Windows nem a conexão das telas mantidas pelo responsável do front.
