# Backend CHAGS 14

Backend em Python 3.12, Django 5.2 LTS e PostgreSQL. O Django serve as páginas públicas preservadas e as primeiras telas reais do participante. Arquitetura e próximas entregas: [documento do projeto](../docs/arquitetura-backend.md).

## O que funciona nesta etapa

- Modelo customizado `accounts.User` desde a primeira migração, com UUID, nome completo, login por e-mail e campo de confirmação de e-mail.
- Normalização de e-mail sem remover pontos ou sufixos `+`; unicidade sem distinção de maiúsculas/minúsculas garantida no PostgreSQL, inclusive em operações concorrentes.
- Senhas com hash Django, contas inativas sem autenticação e contas comuns sem privilégios por padrão.
- Admin em `/gestao/`, com formulários adaptados ao e-mail. Contas, grupos e configuração comercial são restritos ao superusuário técnico; atendimento, indicadores e exportações têm permissões operacionais separadas.
- `/health/live/` verifica que a aplicação responde; `/health/ready/` consulta o banco e devolve 503 se a conexão falhar, sem expor dados de conexão. Essas rotas não verificam a conclusão das migrações; o setup as aplica separadamente.
- Settings separados: desenvolvimento local e base de produção que exige configuração explícita, HTTPS, cookies seguros e conexão de banco com verificação TLS.
- Cadastro, confirmação de e-mail, login/logout, recuperação de senha e perfil próprio. A área do participante requer conta ativa e e-mail confirmado; sessões administrativas técnicas continuam separadas desse requisito.
- Perfil com nome, instituição, país ISO e idioma preferido PT/EN/ES. O e-mail não pode ser alterado pelo formulário de perfil nesta etapa. A área do participante e as mensagens de confirmação/recuperação estão traduzidas em português, inglês e espanhol. A preferência passa a valer nas telas e é preservada em cada entrega de e-mail.
- Tokens aleatórios com validade e consumo único, inclusive sob concorrência, com hashes no registro de autenticação e vínculo ao e-mail/senha atuais. Confirmação exige POST: visitar o link não confirma a conta. Nova solicitação revoga o código anterior.
- Limites de tentativas persistidos no PostgreSQL por IP e, quando aplicável, e-mail normalizado. Login do admin e do participante compartilham os mesmos limites, entre processos. Respostas de cadastro/recuperação válidos são genéricas para endereços novos e existentes; isso reduz enumeração pela resposta, sem prometer ausência de diferenças de tempo.
- Fila transacional de e-mails com tentativas, espera progressiva e recuperação de reservas expiradas. Falha do provedor não desfaz cadastro. A fila não é exibida no admin e seus códigos não são impressos em logs.

- Evento, categorias PT/EN/ES, tabela de preços com períodos, inscrição própria, revisão de categoria, cancelamento com motivo, indicadores e CSV restritos por permissão. Uma inscrição por participante/evento, inclusive sob concorrência. Valores são `Decimal` e decididos no servidor; o navegador não define dono, valor ou estado.
- Histórico de categoria/valor/moeda/revisão preservado na inscrição. PostgreSQL impede alterações dos snapshots e dos campos comerciais de preços existentes, inclusive via `QuerySet.update`. Períodos ativos não podem se sobrepor por categoria/moeda, nem em inserções simultâneas.
- Auditoria transacional de criação, revisão, cancelamento, configuração e exportação. A interface de auditoria permite somente consulta autorizada; a role de execução do banco ainda tem acesso de escrita, por isso esse histórico não equivale a um registro externo inviolável.

Pagamentos, recibos, certificados, MFA administrativo e papéis financeiro/certificados ainda não foram implementados. Revisar categoria não confirma participação; não há operação para marcar pagamento como recebido. A aplicação foi validada localmente e não está liberada como sistema final de produção.

Próxima etapa: [escolha do serviço de pagamentos](../docs/escolha-pagamentos.md), considerando a conta bancária que a organização pretende abrir no Brasil e o recebedor ainda não definido.

## Integração com o front mantido separadamente

A API de autenticação em `/api/v1/auth/` oferece sessão/CSRF, cadastro com nome e sobrenome separados, login com opção de sessão persistente, logout, confirmação e recuperação. Reutiliza os serviços e a fila existentes; não exige nova migração. As rotas de conta HTML anteriores continuam disponíveis. O front não foi modificado nem conectado automaticamente.

O [contrato para o desenvolvedor do front](../docs/integracao-autenticacao.md) especifica os campos, exemplos de JSON, métodos, erros e a sequência de CSRF. Página e API devem usar a mesma origem. A API compartilha os limites do login HTML e do admin; não abre CORS nem substitui a sessão por JWT.

Para validar essa integração isoladamente:

```bash
.venv/bin/python backend/manage.py test accounts.tests.test_auth_api --noinput
```

A API privada em `/api/v1/participant/` oferece perfil próprio (consulta e atualização completa), opções de país/idioma, evento atual, catálogo de preços elegíveis e criação/consulta da própria inscrição. Todas as rotas exigem conta ativa e e-mail confirmado. POSTs mantêm CSRF; categoria, valor, moeda e situação da inscrição são definidos pelo servidor. Repetições retornam a inscrição existente sem alterar seu histórico.

O [contrato da área do participante](../docs/integracao-participante.md) explica os campos, retornos, erros de disponibilidade e os estados que o front precisa tratar. Não oferece checkout nem confirmação de pagamento e não exige novas migrações ou dependências.

## Desenvolvimento no Windows

Siga o [passo a passo para PowerShell](../docs/desenvolvimento-windows.md), com PostgreSQL local e o arquivo `requirements-windows.txt`.

## Preparar o ambiente de nuvem

Execute a partir da raiz `Website-ishgr`:

```bash
bash backend/scripts/setup-dev.sh
.venv/bin/python backend/manage.py test accounts registrations operations config.tests --noinput
.venv/bin/python backend/manage.py runserver 127.0.0.1:8001 --noreload
```

Mantenha também um segundo processo para entregar mensagens no desenvolvimento:

```bash
.venv/bin/python backend/manage.py send_account_emails --watch --interval 2
```

O backend de desenvolvimento grava e-mails em `backend/.local/emails/`, acessíveis somente por arquivos locais privados. Não faz envio externo e não imprime links de acesso no terminal. Para conferir um fluxo, use um participante fictício e abra sua mensagem local de forma privada. Nunca copie os códigos para logs, Git ou esta conversa. Sem JavaScript, o destinatário pode copiar o código da mensagem para o formulário; com JavaScript, o fragmento do link preenche o campo e é removido do histórico sem submeter automaticamente.

Cada e-mail fica associado a um token: confirmação expira em 24 horas; recuperação, em uma hora. A fila contém temporariamente o código necessário à entrega e o apaga após envio, cancelamento ou falha final. Banco, backups, sessões e arquivos de e-mail precisam permanecer privados. Nova solicitação cancela o envio antigo. Como SMTP não oferece confirmação transacional com nosso banco, uma interrupção após o envio pode causar repetição da mensagem; o código permanece o mesmo e só pode ser consumido uma vez.

Para uma rodada manual da fila e limpeza periódica do estado técnico:

```bash
.venv/bin/python backend/manage.py send_account_emails --limit 50
.venv/bin/python backend/manage.py prune_auth_state
```

A fila tenta até cinco vezes, respeitando intervalo progressivo e validade do código. Após falha final, o participante pode solicitar novo código. O comando de limpeza remove buckets fora de duas janelas máximas e tokens expirados há mais de sete dias, com suas entregas; não remove contas, inscrições nem registros financeiros. Em produção, registre o worker num gerenciador de processos com reinício e a limpeza num agendador do CTIC.

O setup usa `uv` disponível nesta máquina, sincroniza o lockfile com verificação de hashes, inicia PostgreSQL e aplica migrações. Pode ser executado novamente sem recriar o banco nem sobrescrever os dados. Não inicia o servidor HTTP nem o worker de e-mails; ambos precisam reiniciar em cada tarefa.

O instalador PostgreSQL é específico desta máquina Debian 13: baixa pacotes de repositórios Debian assinados, verifica pelos mecanismos do APT e extrai os binários sem root em `/workspace/.chags-environment/postgres`. Não modifica o sistema operacional nem executa scripts de instalação dos pacotes. O runtime validado foi PostgreSQL 17.11. JIT fica desligado no desenvolvimento, dispensando LLVM. Esse procedimento não é a estratégia de deploy do CTIC.

O cluster fica em `backend/.local/postgres/data`, com socket em `backend/.local/postgres/socket`, porta lógica 55432 e nenhuma escuta TCP. Autenticação peer mapeia o usuário do sistema para `chags_dev`; os diretórios do socket/cluster são privados. A role de desenvolvimento possui `CREATEDB` para os testes, sem ser superusuário nem poder criar roles. Esse privilégio de teste não deve ser concedido à role de runtime em produção.

```bash
bash backend/scripts/postgres-dev.sh status
bash backend/scripts/postgres-dev.sh start
bash backend/scripts/postgres-dev.sh stop
```

Somente pare o cluster deste projeto quando não houver comandos/testes usando-o. Os dados, a chave aleatória de desenvolvimento e arquivos futuros em `.local/` são ignorados pelo Git; processos precisam reiniciar após restauração da máquina. Nunca exponha `.local/`, `.venv/`, arquivos de configuração ou o checkout inteiro em um servidor público de arquivos estáticos.

### Usar um PostgreSQL já instalado

No Linux, configure `CHAGS_PG_BIN` com o diretório que contém `postgres`, `initdb`, `pg_ctl` e `psql` do PostgreSQL 17. O setup ignora o download dos binários quando essa variável está definida. O helper local foi validado no Debian 13; outros sistemas precisam adaptar locale e inicialização conforme sua instalação.

Para usar outro banco de desenvolvimento em vez do cluster local, exporte `CHAGS_DB_NAME`, `CHAGS_DB_USER`, `CHAGS_DB_PASSWORD`, `CHAGS_DB_HOST` e `CHAGS_DB_PORT` e execute instalação de dependências, `check`, `migrate` e testes diretamente. Não execute o helper do cluster local para um banco externo. Os testes precisam de permissão para criar/remover seu banco temporário separado.

Sem `uv`, use Python 3.12 e pip no ambiente virtual:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r backend/requirements.txt
```

A sincronização com `uv` foi validada neste ambiente; a alternativa pip usa o mesmo lockfile. Atualizar dependências deve ser uma mudança explícita: editar `requirements.in`, gerar novamente o lockfile com hashes e executar os testes. O setup não reescreve o lockfile.

## Administração e comandos de desenvolvimento

Não foi criada uma conta administrativa padrão. Para criar sua conta local, execute o comando interativo, informando a senha somente no terminal:

```bash
.venv/bin/python backend/manage.py createsuperuser
```

O formulário do admin valida senhas com mínimo de 12 caracteres e as demais regras Django. Helpers internos como `create_user` fazem hash, mas não substituem a validação de senha de um formulário público.

```bash
.venv/bin/python backend/manage.py check
.venv/bin/python backend/manage.py makemigrations --check --dry-run
.venv/bin/python backend/manage.py migrate --noinput
.venv/bin/python backend/manage.py test accounts registrations operations config.tests --noinput
```

Os **115 testes** passaram no PostgreSQL real e banco temporário `test_chags_dev`; não dependem de credenciais padrão nem deixam usuários de teste no banco de desenvolvimento. Incluem contas, expiração/reuso dos códigos, revogação por mudança de senha/e-mail, concorrência, proteção CSRF, permissões, fila, snapshots, preços sobrepostos, revisão/cancelamento auditados e CSV. Não troque por SQLite para validar concorrência.

O fluxo também foi validado por HTTP real com cookie/CSRF, e-mails locais e participante fictício removido depois: cadastro, confirmação, login, atualização de perfil, logout, recuperação e login com a nova senha. CSS, JavaScript e imagem pública foram servidos; o processamento do fragmento foi verificado separadamente. Uma revisão visual em navegador e envio SMTP externo ainda não foram realizados.

O módulo de inscrições também passou por HTTP real: login do participante/comissão, criação com tentativa de adulterar valor/dono/estado, repetição idempotente, acesso negado ao participante na gestão, revisão mantendo estado pendente, indicadores, CSV, cancelamento, repetição sem reabertura, auditoria e CSS. Os dados sintéticos foram removidos e a configuração original do evento foi restaurada. O evento local segue fechado, sem categorias ou preços reais; apenas metadados e grupos operacionais foram mantidos.

## Rotas da área do participante

| Rota | Uso |
| --- | --- |
| `/conta/cadastro/` | Cadastro; POST válido redireciona para instruções genéricas. |
| `/conta/confirmar-email/` | Código da mensagem, confirmação somente por POST com CSRF. |
| `/conta/reenviar-confirmacao/` | Solicitar novo código. |
| `/conta/entrar/`, `/conta/sair/` | Login com e-mail confirmado; logout somente POST. |
| `/conta/recuperar-senha/`, `/conta/redefinir-senha/` | Solicitação genérica e redefinição com código e nova senha. |
| `/participante/`, `/participante/perfil/` | Área e perfil próprios, protegidos por sessão e confirmação. |
| `/participante/inscricao/` | Escolher preço vigente e criar/consultar a própria inscrição; POST repetido preserva a inscrição existente, inclusive cancelada. |
| `/participante/jems/` | Link HTTPS definido pela organização, com contas independentes; sem link configurado, mostra orientação. |

## Idiomas da área do participante

A seleção Português/English/Español está nas telas de conta, perfil e inscrição. A troca usa POST com CSRF e só aceita os três idiomas; redirecionamentos externos são rejeitados. Visitantes usam a seleção em cookie (ou preferência do navegador, com português como padrão). Depois de autenticar, a preferência salva no perfil tem prioridade, inclusive em outro dispositivo. Alterar o idioma no perfil também passa a valer após o redirecionamento.

Cadastro, login, confirmação, recuperação, perfil, inscrições, avisos JEMS, limites de tentativas e validações foram traduzidos. Os catálogos do projeto usam o gettext do Django; validações de senha/campo e nomes de países aproveitam as traduções do framework e da biblioteca de países. O idioma de cada e-mail fica congelado quando a mensagem entra na fila, para que uma mudança posterior de preferência ou o idioma do worker não altere aquela entrega. Mensagens antigas anteriores à migração usam português.

Categorias usam os nomes cadastrados em PT/EN/ES. Novas inscrições copiam os três nomes e o banco impede alterar esses snapshots; editar a categoria depois não reescreve sua inscrição. Inscrições anteriores e categorias sem tradução exibem o nome português preservado. Não preenchemos nomes comerciais por tradução automática. Valores continuam iguais e a moeda não muda ao trocar idioma.

O portal HTML público e as telas operacionais próprias da comissão ainda não foram traduzidos integralmente. O portal tem páginas originais PT/EN; a versão ES continua pendente. Links da área restrita levam ao portal PT em português e ao portal EN para inglês/espanhol. O JEMS e o checkout futuro possuem idiomas próprios que devem ser confirmados com os respectivos serviços.

Catálogos em `backend/locale/en/LC_MESSAGES/` e `backend/locale/es/LC_MESSAGES/`, arquivos `.po` editáveis e `.mo` compilados. Após editar traduções:

```bash
bash backend/scripts/install-gettext-runtime.sh
bash backend/scripts/compile-translations.sh
```

O setup recompila os catálogos. Na nuvem Debian 13, o instalador usa pacotes Debian assinados/verificados e os extrai em `/workspace/.chags-environment/gettext`, sem instalação no sistema. Ferramentas e bibliotecas permanecem nessa pasta; wrappers usam o loader local. Um teste com runtime vazio confirmou instalação e catálogos compilados idênticos. Se GNU gettext já estiver no sistema, ele é usado diretamente. No CTIC, instalar GNU gettext pelo gerenciador confiável caso seja necessário recompilar; a aplicação em execução só lê os catálogos `.mo`.

Verificação: 17 testes específicos de idiomas, além dos 98 anteriores; os 115 passaram no PostgreSQL. O HTTP real confirmou formulários PT/EN/ES, troca com CSRF/cookie, preferência após login, aviso de inscrições fechado e entrega local de e-mail em espanhol. O participante/mensagem fictícios foram removidos. Incluem cookie/idioma do perfil, cadastro, mensagens de erro, segurança da troca de idioma, e-mails em idioma congelado, proteção dos snapshots traduzidos e preservação dos valores.

## Configurar inscrições e a comissão

```bash
.venv/bin/python backend/manage.py initialize_chags14
.venv/bin/python backend/manage.py setup_registration_roles
```

Os comandos são idempotentes. O primeiro cria somente os dados conhecidos do CHAGS 14 (12–16/07/2027, Belém), com inscrições **fechadas**, sem categorias, preços ou URL JEMS. Preserva configurações existentes. O segundo cria grupos e adiciona permissões, sem criar usuários ou remover permissões já atribuídas. Eles foram executados no banco local; a instalação de dependências não cria dados comerciais automaticamente.

No admin técnico, cadastre as categorias e os preços aprovados, escolha a moeda do evento, preencha início/fim da janela e habilite inscrições. A moeda inicial BRL é um padrão técnico configurável, sem preço definido. O modelo aceita BRL/USD/EUR; isso não indica suporte confirmado do futuro gateway. A janela é inclusiva no início e exclusiva no fim. Preços usam a mesma regra e podem ter fim aberto. Apenas preços/categorias ativos e vigentes na moeda do evento são oferecidos.

Valor, moeda, categoria e início de validade de um preço são imutáveis: para alterá-los, desative o preço anterior (ou encerre sua validade antes do novo início) e crie outro. O fim de validade e a ativação podem ser ajustados; snapshots já registrados não mudam. A categoria não pode mudar de evento. Não há mudança de categoria da inscrição, reabertura de canceladas, isenção/desconto, upload de comprovantes ou confirmação manual de pagamento nesta etapa. Inscrição de valor zero também fica aguardando requisitos.

O superusuário técnico pode atribuir `is_staff` e grupos a contas individuais da comissão:

| Grupo | Acesso |
| --- | --- |
| CHAGS — Atendimento | Consulta inscrições, revisa categoria quando exigida e cancela com motivo. |
| CHAGS — Indicadores | Somente totais agregados, sem nome/e-mail dos participantes. |
| CHAGS — Exportação de inscrições | Consulta e CSV com nome/e-mail; sem revisão ou cancelamento por esse grupo. |

`is_staff` sozinho não concede esses acessos. Grupos podem ser acumulados; verifique permissões existentes antes de atribuí-los. Administração de contas/grupos e configuração comercial continuam técnicas. Não atribua superusuário como papel cotidiano de atendimento.

Rotas operacionais: `/gestao/inscricoes/` (busca por nome/e-mail/UUID, filtro de situação/revisão e paginação), `/gestao/inscricoes/<uuid>/` (consulta e POST autorizado com motivo), `/gestao/indicadores/` e `/gestao/exportacoes/`. O admin inclui links conforme as permissões. Alterações só ocorrem por POST com CSRF; inscrições e auditoria são somente leitura no admin, inclusive para o superusuário.

Indicadores somam os **valores das inscrições**, separados por moeda e excluindo canceladas desse total; não são receita nem pagamento recebido. CSV usa os mesmos filtros, UTF-8 com BOM e valores decimais com ponto; protege células iniciadas por fórmulas, espaços/controles e BOM. Exportação exige permissão própria e POST, tem limite de 10.000 linhas por arquivo e é auditada antes da entrega. Não registra texto da busca na auditoria. A cópia exportada deve permanecer restrita.

Migrações requerem a extensão PostgreSQL `btree_gist` para a restrição de sobreposição, além de criar funções/triggers no schema da aplicação. Isso foi validado com a role proprietária do banco local, sem superusuário. No CTIC, confirme disponibilidade/permissão com o DBA ou peça a criação prévia da extensão; não remova a restrição para contornar falta de acesso. Referências a usuários, preços, categorias e eventos de inscrições não podem ser excluídas; políticas de anonimização/retenção serão definidas antes de operar dados reais.

Links de confirmação/recuperação usam `#token=...`, evitando códigos no path/query dos logs HTTP e no Referer. Os códigos enviados por POST são marcados como sensíveis. Não adicionar rastreadores de terceiros a essas telas.

Os links legados `sistema-login.html` e `pt-br/sistema-login.html` redirecionam ao login real. `sistema-submissao.html` redireciona à área protegida: o formulário estático de artigos não é servido nessa rota. O JEMS continuará independente. Os HTML originais são servidos por uma lista explícita de páginas públicas; apenas CSS/JS públicos e o diretório de imagens entram na coleta de estáticos. GitHub Pages continua servindo somente a versão estática; esses fluxos funcionam quando o portal é servido pelo Django.

## Preparação de produção

`manage.py` usa desenvolvimento por padrão. WSGI/ASGI usam produção por padrão; um servidor WSGI de desenvolvimento deve receber explicitamente `DJANGO_SETTINGS_MODULE=config.settings.development`. Em produção, forneça as variáveis pelo gerenciador do serviço; `.env.example` documenta nomes e não é carregado automaticamente.

Obrigatórias na base de produção: `CHAGS_SECRET_KEY` aleatória de pelo menos 50 caracteres, `CHAGS_ALLOWED_HOSTS` com hosts explícitos, `CHAGS_DB_NAME`, `CHAGS_DB_USER`, `CHAGS_DB_PASSWORD` e `CHAGS_DB_HOST`. Porta padrão 5432. TLS do PostgreSQL usa `verify-full`; configure a CA confiável via `CHAGS_DB_SSLROOTCERT` conforme o CTIC. A conexão de produção e o certificado do banco ainda não foram validados.

`CHAGS_TRUST_HTTPS_PROXY=true` só é adequado quando o proxy do CTIC remove/substitui o header `X-Forwarded-Proto` do cliente. HSTS começa em 3600 segundos, sem incluir subdomínios ou preload; ampliar somente após validar domínio e HTTPS. `check --deploy` aponta essas escolhas de implantação como avisos, não prova que a hospedagem está correta.

Produção exige também `CHAGS_PUBLIC_URL` como origem HTTPS presente nos hosts permitidos, `CHAGS_EMAIL_HOST` e `CHAGS_DEFAULT_FROM_EMAIL`. As mensagens sempre usam essa origem canônica, não o header Host de uma requisição. SMTP usa STARTTLS na porta 587 por padrão; `CHAGS_EMAIL_USE_SSL=true` usa TLS desde a conexão (porta padrão 465). A verificação de certificados permanece ativa. Configure `CHAGS_EMAIL_USER` e `CHAGS_EMAIL_PASSWORD` juntos quando o relay exigir autenticação, por meio de segredos do serviço do CTIC, nunca no Git. Envio externo e credenciais não foram testados neste ambiente.

Por padrão, limites de IP usam apenas `REMOTE_ADDR` e ignoram X-Forwarded-For. Atrás de um proxy, configure `CHAGS_TRUSTED_PROXY_NETWORKS` somente com CIDRs dos proxies controlados que sobrescrevem o header, após confirmar a topologia do CTIC. A seleção percorre a cadeia pela direita até o primeiro endereço não confiável. Não confiar indiscriminadamente no header enviado pelo cliente. `CHAGS_TRUST_HTTPS_PROXY` controla HTTPS e é uma configuração distinta.

MFA administrativo, revisão visual, traduções do portal estático/gestão e validação completa no CTIC ainda são etapas anteriores à liberação pública. A liberação de certificados e os pagamentos exigem os módulos e testes definidos na arquitetura.

Com configuração e infraestrutura reais fornecidas pelo CTIC, os comandos previstos são:

```bash
.venv/bin/python backend/manage.py check --deploy --settings=config.settings.production
.venv/bin/python backend/manage.py migrate --settings=config.settings.production --noinput
.venv/bin/python backend/manage.py collectstatic --settings=config.settings.production --noinput
.venv/bin/gunicorn --chdir backend config.wsgi:application --bind 127.0.0.1:8001
.venv/bin/python backend/manage.py send_account_emails --watch --settings=config.settings.production
```

Esses comandos de deploy dependem de HTTPS, configuração de proxy e banco do CTIC; não representam publicação realizada. Não usar `runserver` em produção. O servidor web serve apenas os arquivos coletados em `STATIC_ROOT`; `MEDIA_ROOT` continuará privado.
