# API de gestão das inscrições — contrato para o front

Base: `/api/v1/management/`, na mesma origem das páginas e das APIs existentes. Esta entrega contém somente backend e documentação. Nenhuma tela foi criada ou conectada automaticamente.

## Sessão e permissões

Use uma conta individual da comissão, ativa e com `is_staff`, autenticada pela sessão Django existente. O login técnico continua disponível em `/gestao/login/`; contas da comissão com e-mail confirmado também podem usar o [login da API de autenticação](integracao-autenticacao.md). Esta API não cria contas administrativas, não atribui permissões e não recebe senha, JWT nem um campo `actor` nas operações.

As regras de acesso são as mesmas da gestão existente. Sessões técnicas do admin não exigem confirmação de e-mail adicional. Isso não altera a exigência de confirmação nas APIs do participante.

| Operação | Permissões necessárias, além de conta ativa e staff |
| --- | --- |
| Sessão/capacidades | Nenhuma permissão operacional; retorna somente a própria identificação e capacidades. |
| Lista e detalhes | `registrations.view_registration` |
| Revisar categoria | `registrations.view_registration` + `registrations.review_registration` |
| Cancelar | `registrations.view_registration` + `registrations.cancel_registration` |
| Indicadores | `registrations.view_registration_indicators` |

Os grupos existentes preparados por `setup_registration_roles` continuam aplicáveis. Atendimento pode consultar/revisar/cancelar; Indicadores recebe somente agregados; Exportação mantém suas permissões anteriores. A rota de CSV existente permanece separada desta entrega. Não atribua superusuário para contornar permissões ausentes.

Depois do login, execute `GET /api/v1/management/session/`. O retorno contém `operator` (`id`, `full_name`), `permissions` e `csrf_token` mascarado. As capacidades são `view_registrations`, `review_category`, `cancel_registration` e `view_indicators`. Use-as para habilitar controles no front; o backend verifica novamente todas as permissões em cada operação. A sessão autenticada vem do cookie, não de um ID enviado pelo navegador.

Nos POSTs, envie `Content-Type: application/json`, `X-CSRFToken` com o token recebido e o cookie da sessão, seguindo o fluxo dos contratos de autenticação. Não coloque token em URL nem em logs. `application/x-www-form-urlencoded` também é aceito. `multipart/form-data` não é aceito. Todos os valores do corpo são strings. Campos desconhecidos, repetidos ou valores de tipos diferentes são recusados. O limite de corpo é 32 KiB no protocolo compartilhado.

O middleware CSRF pode devolver 403 antes da verificação da sessão em um POST sem token. Não há isenção de CSRF nem liberação de CORS. Todas as respostas controladas usam JSON e impedem cache. Métodos não previstos retornam 405 e o header `Allow`.

## Rotas

| Método | Rota relativa à base | Função |
| --- | --- | --- |
| GET | `session/` | Própria identificação, permissões e token CSRF. |
| GET | `registrations/` | Busca, filtros e paginação. |
| GET | `registrations/<uuid>/` | Detalhes da inscrição. |
| POST | `registrations/<uuid>/review/` | Aprovar ou rejeitar categoria, com motivo. |
| POST | `registrations/<uuid>/cancel/` | Cancelar inscrição, com motivo. |
| GET | `indicators/` | Totais agregados do evento atual. |

Lista, detalhes, ações e indicadores estão limitados ao evento configurado por `REGISTRATION_EVENT_CODE`. Não aceitam `event_id` para escolher outro evento. Um UUID inexistente, malformado ou pertencente a outro evento recebe o mesmo erro de inscrição não encontrada. Não há operação nesta API para criar inscrição em nome de terceiros ou alterar perfil/preços.

## Lista e detalhes

Parâmetros permitidos na lista:

| Campo | Regra |
| --- | --- |
| `q` | Até 255 caracteres; busca sem distinguir maiúsculas/minúsculas em nome/e-mail atual do participante, ou por UUID da inscrição. |
| `status` | Vazio, `pending`, `confirmed` ou `cancelled`. |
| `review` | Vazio, `not_required`, `pending`, `approved` ou `rejected`. |
| `page` | Número inteiro positivo; padrão 1. Página fora do resultado retorna 404, sem redirecionar para a última. |
| `page_size` | Entre 1 e 100; padrão 50. |

Filtros são combinados. A ordenação é criação decrescente, com UUID como desempate. Campos desconhecidos e parâmetros repetidos são recusados. As outras rotas não aceitam parâmetros de consulta.

O sucesso da lista usa `data.event_id`, `data.registrations` e `data.pagination`. A paginação contém `page`, `page_size`, `total`, `pages`, `has_next` e `has_previous`. Lista vazia tem `registrations: []`, total 0 e uma página vazia. Consultas podem mudar conforme chegam inscrições ou a comissão altera estados; a paginação não mantém um snapshot entre requisições.

Cada item reutiliza os campos do [contrato da inscrição do participante](integracao-participante.md): IDs, categoria histórica localizada, valor decimal como string (`"123.45"`), moeda, situação/revisão e seus rótulos, exigência de revisão, criação e cancelamento em ISO 8601 ou `null`. Acrescenta `participant` com `id`, `full_name`, `email` e `actions` com `can_review`/`can_cancel`. Nome/e-mail são os atuais da conta; categoria/valor/moeda continuam históricos da inscrição.

Detalhes e retornos de revisão/cancelamento usam `data.registration`. Acrescentam instituição e país ISO no objeto `participant`, `category_names` (`pt`, `en`, `es` históricos, incluindo traduções vazias), `cancellation_reason` e `cancelled_by_id` ou `null`. Não incluem senha/hash, tokens, sessões, flags de privilégios nem e-mail do operador que cancelou. Rótulos seguem o idioma da requisição/sessão; use os códigos para lógica de interface.

## Revisão e cancelamento

Corpo de revisão:

```json
{"decision": "approved", "reason": "Categoria conferida pela comissão."}
```

`decision` aceita somente `approved` ou `rejected`. A inscrição deve estar `pending` e ter `review_required: true`. É possível alterar uma decisão enquanto essas condições permanecem válidas, preservando o histórico na auditoria. Repetir a mesma decisão retorna sucesso sem criar uma segunda auditoria. Uma categoria aprovada continua com inscrição `pending`; isso não confirma pagamento nem participação.

Corpo de cancelamento:

```json
{"reason": "Cancelamento solicitado pelo participante."}
```

Ambos exigem motivo não vazio, após remover espaços nas pontas, de até 1000 caracteres. O operador é definido pela sessão. Repetir cancelamento retorna a inscrição já cancelada, preservando motivo, operador e horário originais e sem duplicar auditoria; o novo motivo ainda precisa ser válido. `can_cancel: false` serve para ocultar a ação após o cancelamento, sem alterar essa regra de repetição.

As operações reutilizam os serviços transacionais existentes e bloqueiam a inscrição durante a alteração. Antes de escrever, recarregam a conta do operador para conferir estado e permissões sem reutilizar o cache inicial da requisição. A alteração e a auditoria devem concluir juntas. Uma falha de banco desfaz a operação e devolve 503; o front não deve presumir sucesso nem tentar marcar a inscrição localmente como alterada.

Não há operação para confirmar pagamento, alterar dono/categoria/valor/moeda, reabrir inscrição, estornar valores ou emitir certificado. Cancelamento nesta etapa altera o estado da inscrição; integração financeira dependerá do serviço escolhido pela organização.

## Indicadores

`data` contém identificação do evento (`id`, `code`, `title`), `total`, `by_status`, `by_review`, `amount_basis` e `amounts`. Os dois agrupamentos incluem todos os códigos possíveis, inclusive totais zero, com `code`, `label` e `total`. O total inclui inscrições canceladas. Não há nomes, e-mails, perfis, motivos ou IDs de inscrições no retorno.

`amount_basis` vale `registration_value_excluding_cancelled`. `amounts` contém `currency`, `count`, `total` decimal como string, separado por moeda e excluindo inscrições canceladas. Não some moedas distintas. Esses valores representam inscrições, não receita nem pagamentos recebidos. Evento sem inscrições devolve total zero, agrupamentos zerados e `amounts: []`. Uma única consulta agregada calcula esses totais no banco.

## Erros e integração

Sucesso: `{"ok": true, "data": {...}}`. Erro controlado: `{"ok": false, "error": {"code": "..."}}`. Validação de campos também inclui `error.fields`, com códigos e mensagens.

| HTTP | Código | Tratamento sugerido |
| --- | --- | --- |
| 400 | `invalid_payload`, `unknown_fields` | Corrigir formato/campos; não reenviar o mesmo corpo. |
| 401 | `authentication_required` | Solicitar novo login ou informar conta inativa. |
| 403 | `permission_denied`, `csrf_failed` | Conferir permissão ou renovar o fluxo sessão/CSRF. |
| 404 | `event_not_configured`, `registration_not_found`, `page_not_found` | Informar indisponibilidade e atualizar a consulta. |
| 405 | `method_not_allowed` | Usar o método indicado em `Allow`. |
| 409 | `registration_action_unavailable` | Atualizar detalhes; o estado não permite a revisão solicitada. |
| 413 | `payload_too_large` | Reduzir o corpo enviado. |
| 415 | `unsupported_media_type` | Usar JSON ou formulário URL-encoded. |
| 422 | `validation_error` | Exibir os erros nos campos correspondentes. |
| 503 | `operation_unavailable` | Informar falha e consultar o estado antes de repetir. |

Falhas inesperadas fora desses casos continuam sujeitas ao tratamento global do Django/servidor; o front deve conferir HTTP e Content-Type antes de interpretar JSON. Use HTTPS e settings de produção na implantação. Consultas `q` podem conter dados pessoais; configure o proxy para evitar sua gravação em logs. Renderize textos de participantes como texto, sem interpretá-los como HTML.

Para testar somente a entrega:

```powershell
.\.venv\Scripts\python.exe backend\manage.py test operations.tests.test_api operations.tests.test_api_concurrency --noinput
```

No Linux, troque o executável por `.venv/bin/python`. Os testes usam PostgreSQL real e banco temporário, cobrindo permissões, CSRF, isolamento entre eventos, filtros, paginação, campos históricos, auditoria e concorrência. A conexão das telas será feita pelo responsável do front. Este contrato não exige novas migrações nem dependências.
