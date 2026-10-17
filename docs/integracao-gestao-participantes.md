# Gestão de participantes — contrato para o painel

API na mesma origem, com base `/api/v1/management/`. Permite à comissão consultar contas de participantes, inclusive sem inscrição, e corrigir nome, instituição, país e idioma com motivo e auditoria. Esta entrega contém backend; a conexão das telas cabe ao responsável pelo front.

## Acesso e preparação

Todas as rotas exigem sessão Django de uma conta individual ativa, `is_staff`, permissões próprias e o [segundo fator da comissão](integracao-mfa.md) conforme a política do ambiente. POST exige cookie de sessão e `X-CSRFToken`. Obtenha o token mascarado em `GET /api/v1/management/session/`. Não há acesso por ID de operador informado no corpo, JWT ou CORS.

| Operação | Permissões |
| --- | --- |
| Listar, consultar detalhes e histórico cadastral | `accounts.view_participant` |
| Corrigir cadastro | `accounts.view_participant` + `accounts.correct_participant` |

As capacidades `view_participants` e `correct_participants` foram acrescentadas a `data.permissions` da sessão de gestão. Servem para habilitar controles; o servidor verifica as permissões novamente em cada operação. `is_staff` sozinho e os grupos de inscrições existentes não concedem estas permissões.

A consulta abrange **todas as contas sem `is_staff` e sem `is_superuser`**, ativas ou inativas, confirmadas ou não, com ou sem perfil e inscrição. Portanto, esta permissão autoriza consulta cadastral global, inclusive de participantes que só tenham inscrição em outro evento. Conceda-a apenas à equipe responsável por esse atendimento. Contas técnicas/da comissão ficam excluídas. Inscrições exibidas e o filtro de inscrição se referem somente ao evento configurado por `REGISTRATION_EVENT_CODE`.

Depois de integrar o código ao backend, execute na raiz do projeto, no PowerShell:

```powershell
.\.venv\Scripts\python.exe backend\manage.py migrate --noinput
.\.venv\Scripts\python.exe backend\manage.py setup_participant_roles
```

No Linux, use `.venv/bin/python backend/manage.py ...`. A migração `accounts.0006_participant_management_permissions` adiciona metadados de permissões, sem alterar colunas ou dados de participantes. O comando prepara de forma aditiva os grupos `CHAGS — Consulta de participantes` e `CHAGS — Correção de participantes`; não cria usuários, não atribui contas aos grupos e não remove permissões existentes. Um superusuário técnico deve atribuir o grupo apropriado à conta staff autorizada. Não conceda superusuário ao atendimento. Não há novas dependências.

## Rotas e retornos

| Método | Rota relativa à base | Função |
| --- | --- | --- |
| GET | `participants/` | Busca, filtros e paginação. |
| GET | `participants/<uuid>/` | Detalhes e revisão atual do cadastro. |
| POST | `participants/<uuid>/` | Correção completa dos quatro campos permitidos. |
| GET | `participants/<uuid>/history/` | Histórico paginado de correções desse participante. |

Sucesso: `{"ok": true, "data": {...}}`. Erro controlado: `{"ok": false, "error": {"code": "..."}}`. Todos os retornos controlados são JSON com cache desabilitado. Consultas não alteram dados. Métodos não previstos retornam 405 com `Allow`. UUID inválido, inexistente ou de conta privilegiada recebe o mesmo `participant_not_found` com HTTP 404.

A lista retorna `data.event_id`, `data.participants` e `data.pagination`. Cada participante contém `id`, `full_name`, `email`, `is_active`, `email_verified`, `joined_at` em ISO 8601 e `has_registration` para o evento atual. Não contém valores financeiros, hash de senha, tokens, sessões ou permissões.

Filtros combinados da lista:

| Parâmetro | Valores/regra |
| --- | --- |
| `q` | Até 255 caracteres; busca sem distinguir maiúsculas/minúsculas por nome/e-mail atual ou pelo UUID da conta. |
| `email_status` | Vazio, `verified` ou `unverified`. |
| `account_status` | Vazio, `active` ou `inactive`. |
| `registration` | Vazio, `registered` ou `not_registered`, no evento atual. |
| `page` | Inteiro positivo, padrão 1. |
| `page_size` | Inteiro de 1 a 100, padrão 50. |

Ordenação por criação decrescente e UUID como desempate. `pagination` contém `page`, `page_size`, `total`, `pages`, `has_next`, `has_previous`. Resultado vazio tem uma página vazia. Página fora do resultado retorna 404. Campos desconhecidos e parâmetros repetidos são recusados; `event_id` não permite selecionar outro evento. A paginação não mantém um snapshot entre requisições.

Detalhes e correção retornam `data.event_id` e `data.participant`, com os campos da lista e mais:

| Campo | Conteúdo |
| --- | --- |
| `institution` | Instituição atual ou string vazia. |
| `country` | Código ISO de país ou string vazia. |
| `preferred_language` | `pt-br`, `en` ou `es`; padrão `pt-br` em contas sem perfil. |
| `revision` | Identificador opaco de 64 caracteres hexadecimais do estado consultado. |
| `registration` | `{"id": "<uuid>", "status": "pending"}` ou `null`; somente a inscrição no evento atual. Estados possíveis: `pending`, `confirmed`, `cancelled`. |
| `actions.can_correct` | Se o operador possui as duas permissões de correção. |
| `actions.can_view_registration` | Se há inscrição atual e o operador também possui `registrations.view_registration`. |

Para abrir detalhes financeiros/de categoria da inscrição, use a [API de inscrições](integracao-gestao.md) com as permissões próprias. Cadastro de outro evento não expõe UUID nem situação dessa inscrição.

## Correção e alterações simultâneas

Consulte os detalhes e envie os quatro campos, a revisão recebida e o motivo. Use `Content-Type: application/json`; todos os valores devem ser strings. Exemplo fictício, substituindo a revisão pelo valor real retornado no GET:

```json
{
  "full_name": "Maria de Souza",
  "institution": "Universidade de exemplo",
  "country": "BR",
  "preferred_language": "pt-br",
  "revision": "<revision recebida nos detalhes>",
  "reason": "Correção conferida com a participante."
}
```

Nome é obrigatório e aceita até 255 caracteres. Instituição aceita até 255 e pode ser vazia. País aceita um código válido ou vazio. O idioma deve ser uma das três opções acima. Motivo é obrigatório, com até 1000 caracteres; espaços nas pontas são removidos. Instituição e país ausentes são tratados como vazios: envie sempre o conjunto completo para preservar os valores atuais. `application/x-www-form-urlencoded` também é aceito; `multipart/form-data` não. Campos desconhecidos, repetidos, arrays e valores que não sejam strings são recusados. Limite de corpo: 32 KiB.

O retorno acrescenta `data.changed`: `true` quando houve alteração, `false` quando os valores já eram iguais. Repita uma requisição idêntica somente para reconciliar uma falha de comunicação; se os dados já correspondem ao pedido, não haverá auditoria duplicada, inclusive com a revisão anterior.

`revision` é uma impressão do estado, e não uma sequência numérica nem uma credencial. A correção bloqueia a conta no PostgreSQL, lê o perfil depois do bloqueio e compara a revisão. Se outro operador ou o próprio participante tiver alterado o cadastro e os valores enviados diferirem do estado atual, retorna **409 `participant_changed`** sem sobrescrever os dados. Recarregue os detalhes, confira as mudanças e solicite novo salvamento; não substitua a revisão automaticamente e reenvie valores antigos.

Estado equivalente produz a mesma revisão; uma alteração seguida de reversão não é distinguida. Isso não representa autorização para editar: sessão, permissões e MFA são conferidos independentemente. Mudanças de nome/instituição/país/idioma, e-mail, ativação, confirmação de e-mail e criação do perfil alteram a revisão.

Alteração e auditoria concluem na mesma transação. Falha na auditoria desfaz também nome e perfil. As permissões e a sessão do operador são recarregadas antes de escrever. Uma correção não ativa contas nem confirma e-mail, e não permite trocar endereço, senha, privilégios, grupos ou situação financeira. Não reescreve valores/categoria da inscrição nem snapshots de rascunhos de certificados existentes. Campos de perfil ausentes são criados somente quando necessário para gravar uma alteração nesses campos.

## Histórico cadastral

`GET participants/<uuid>/history/` aceita somente `page` e `page_size`, nas regras da lista. Retorna `data.participant_id`, `data.history` e `data.pagination`. A ordem é criação decrescente, com UUID como desempate.

Cada item tem `id`, `action: "participant.profile_updated"`, `actor` com `id` e `full_name` (ou `null` se a conta foi removida), `target` com `type: "accounts.user"` e `id`, `created_at` em UTC, `reason` e `details.fields`: lista dos nomes dos campos alterados. Valores anteriores/novos e e-mail do operador não são publicados. Detalhes desconhecidos ou malformados não são repassados.

Esse histórico mostra apenas correções feitas por este serviço para a conta consultada. Não é um histórico completo de alterações próprias ou intervenções técnicas, e não permite consultar eventos de segurança ou correções de outras pessoas pelo ID informado. A auditoria global continua exigindo a [permissão separada](integracao-auditoria.md). O histórico cadastral independe da configuração de evento; lista, detalhes e correção exigem o evento atual configurado.

Informe no motivo apenas o necessário para justificar a correção. Textos de participantes e motivos devem ser renderizados como texto, sem interpretar HTML. Pesquisas podem conter dados pessoais; o proxy deve evitar gravar valores de `q` em logs.

## Erros e validação

| HTTP | Código | Tratamento |
| --- | --- | --- |
| 400 | `invalid_payload`, `unknown_fields` | Corrigir formato ou campos. |
| 401 | `authentication_required` | Refazer login ou verificar conta inativa. |
| 403 | `permission_denied`, `csrf_failed` | Conferir acesso ou renovar sessão/CSRF. |
| 403 | `mfa_required`, `mfa_enrollment_required` | Concluir o fluxo de MFA. |
| 404 | `participant_not_found`, `event_not_configured`, `page_not_found` | Atualizar consulta ou verificar configuração. |
| 405 | `method_not_allowed` | Usar o método indicado em `Allow`. |
| 409 | `participant_changed` | Recarregar cadastro e conferir antes de salvar novamente. |
| 409 | `participant_update_unavailable` | Recarregar e conferir dados/regras antes de repetir. |
| 413 | `payload_too_large` | Reduzir o corpo. |
| 415 | `unsupported_media_type` | Usar JSON ou formulário URL-encoded. |
| 422 | `validation_error` | Exibir `error.fields` nos campos. |
| 503 | `operation_unavailable`, `mfa_unavailable` | Informar indisponibilidade e consultar o estado antes de repetir. |

Confira HTTP e Content-Type antes de interpretar JSON, pois falhas inesperadas podem seguir o tratamento global do servidor. Use HTTPS e settings de produção na implantação.

Testes focados no PowerShell, com PostgreSQL de desenvolvimento e permissão para criar/remover o banco temporário de testes:

```powershell
.\.venv\Scripts\python.exe backend\manage.py test operations.tests.test_participant_management --noinput
```

Cobrem acesso, revogação, MFA, CSRF, busca, filtros, contas sem inscrição/perfil, campos protegidos, concorrência entre operadores distintos, repetição sem auditoria duplicada, histórico limitado e rollback em falha da auditoria.
