# API da área do participante — CHAGS 14

Contrato v1, preparado em 10/10/2026. Complementa o [contrato de autenticação](integracao-autenticacao.md). O backend oferece perfil próprio, opções de país/idioma, evento atual, preços disponíveis e inscrição própria. O responsável pelo front implementa as telas e sua conexão; esta entrega não modifica HTML, CSS ou JavaScript.

## Sessão, permissões e formato

Página e API devem ter a mesma origem. Use a sessão criada por `/api/v1/auth/login/`. Obtenha CSRF em `/api/v1/auth/session/`, preserve os cookies e envie o header `X-CSRFToken` em todo POST. Use o token renovado após login/logout. O cookie de sessão é `HttpOnly`; não há JWT nem autenticação via parâmetro de URL.

Todas as rotas abaixo exigem participante ativo com e-mail confirmado. Requisição sem sessão válida recebe `401 authentication_required`; sessão ainda não confirmada recebe `403 email_not_verified`. Para POST, CSRF é validado antes da operação: sua ausência pode produzir `403 csrf_failed` antes da verificação da sessão. Não há redirecionamento HTML em lugar dessas respostas.

Use `application/json`, com valores string, ou `application/x-www-form-urlencoded`, conforme o contrato de autenticação. Não use multipart/FormData. Os limites de corpo (32 KiB), rejeição de chaves repetidas, campos desconhecidos e envelope `ok/data/error` são os mesmos. As URLs exigem barra final. Não envie identificador de usuário: o dono é sempre a conta da sessão. Não existem rotas de participante para consultar outras contas ou escolher outro evento.

As respostas possuem `Cache-Control: no-store`. Dados de perfil são privados e devem ser apresentados como texto, sem inserir conteúdo do participante como HTML. A preferência salva de idioma tem prioridade sobre `Accept-Language`; rótulos e nomes localizados acompanham `Content-Language`.

## Rotas disponíveis

| Método | Rota | Finalidade |
| --- | --- | --- |
| GET | `/api/v1/participant/profile/` | Consultar o próprio perfil |
| POST | `/api/v1/participant/profile/` | Atualizar o próprio perfil |
| GET | `/api/v1/participant/profile/options/` | Obter países e idiomas aceitos |
| GET | `/api/v1/participant/event/` | Consultar o evento configurado |
| GET | `/api/v1/participant/prices/` | Consultar categorias/preços disponíveis |
| GET | `/api/v1/participant/registration/` | Consultar a própria inscrição |
| POST | `/api/v1/participant/registration/` | Criar a própria inscrição ou recuperar o resultado de uma repetição |

PATCH, PUT e DELETE não são aceitos. A API do participante não oferece aprovação de categoria, cancelamento, confirmação de participação ou marcação de pagamento. Essas operações não podem ser habilitadas enviando campos adicionais.

## Perfil

GET retorna:

```json
{
  "ok": true,
  "data": {
    "profile": {
      "id": "UUID-DA-CONTA",
      "full_name": "Maria da Silva",
      "email": "participante@example.org",
      "institution": "UFPA",
      "country": "BR",
      "preferred_language": "pt-br"
    }
  }
}
```

POST recebe:

```json
{
  "full_name": "Maria da Silva",
  "institution": "UFPA",
  "country": "BR",
  "preferred_language": "pt-br"
}
```

| Campo enviado | Regra |
| --- | --- |
| `full_name` | Obrigatório, até 255 caracteres, sem espaços vazios nas pontas |
| `institution` | Opcional, até 255 caracteres |
| `country` | Opcional, código ISO de duas letras em maiúsculas ou string vazia |
| `preferred_language` | Obrigatório: `pt-br`, `en` ou `es` |

POST atualiza o formulário completo: envie os valores atuais dos campos que pretende manter. Omitir instituição/país os limpa; não é uma atualização parcial. Não é possível alterar e-mail, senha, confirmação, dono ou privilégios nessa rota. Enviar esses campos retorna `400 unknown_fields`, sem salvar alterações.

Sucesso retorna `200` com o perfil atualizado no mesmo formato do GET. Erros retornam `422 validation_error`, com `error.fields`. Validação inválida não salva parte dos dados. O backend verifica novamente a elegibilidade da conta sob bloqueio transacional antes de escrever.

Após alteração válida de idioma, a resposta e as próximas requisições usam a nova preferência. Novas mensagens de conta também seguem esse idioma; mensagens já enfileiradas mantêm o idioma que tinham quando foram solicitadas.

`GET profile/options/` retorna `data.languages` e `data.countries`, cada um com objetos `{ "code": "...", "name": "..." }`. Use `code` como valor enviado e `name` como texto do seletor. Os países vêm da lista validada pelo backend, com nomes localizados. Não presuma francês como idioma disponível.

## Evento e catálogo

`GET event/` retorna `data.event`:

| Campo | Formato |
| --- | --- |
| `id`, `code`, `title` | Identificador, código e título do evento atual |
| `starts_on`, `ends_on` | Datas `YYYY-MM-DD` |
| `timezone` | Nome IANA, por exemplo `America/Belem` |
| `registration_currency` | Moeda configurada: BRL, USD ou EUR |
| `registration_opens_at`, `registration_closes_at` | Data/hora ISO 8601 com offset, ou `null` |
| `registrations_open` | Booleano calculado com configuração e horário atual |

O evento é escolhido pelo servidor usando `REGISTRATION_EVENT_CODE`. Sua conta não pode habilitar inscrições, definir moeda ou mudar o prazo por essa API. Se o evento não estiver configurado, as rotas de evento, preços e inscrição retornam `404 event_not_configured`.

`GET prices/` retorna `data.event_id`, `data.registrations_open` e `data.prices`. Cada item contém:

```json
{
  "price_id": "UUID-DO-PRECO",
  "category_id": "UUID-DA-CATEGORIA",
  "category_code": "exemplo",
  "category_name": "Categoria fictícia",
  "requires_review": true,
  "amount": "123.45",
  "currency": "BRL",
  "valid_from": "2026-10-01T00:00:00+00:00",
  "valid_until": null
}
```

O valor acima é **fictício**, usado para explicar o formato; não define preço comercial do evento. O catálogo retorna apenas preços ativos de categorias ativas do evento atual, na moeda configurada e dentro do período válido. Início é inclusivo; fim é exclusivo. Não é feita conversão cambial.

Com inscrições fechadas, `registrations_open` é `false` e `prices` fica vazio. Com inscrições abertas e nenhum preço elegível, o booleano permanece `true`, mas a lista também fica vazia. O front deve tratar ambas as situações e não inventar preço para permitir inscrição.

`amount` é string decimal com duas casas e ponto; nunca é float. Use o valor para apresentação. O valor cobrado no futuro será definido pelo backend, não por cálculos ou campos ocultos do navegador. Datas incluem o offset; apresente-as considerando o fuso informado pelo evento.

Nome da categoria segue PT/EN/ES e usa português quando a tradução não estiver disponível. O catálogo reflete a configuração atual; a inscrição preserva o nome e o valor existentes no momento da criação.

## Inscrição própria

`GET registration/` retorna `200` com `data.registration: null` quando essa conta ainda não tem inscrição no evento atual. Uma inscrição existente retorna somente seus dados:

```json
{
  "ok": true,
  "data": {
    "registration": {
      "id": "UUID-DA-INSCRICAO",
      "event_id": "UUID-DO-EVENTO",
      "category_id": "UUID-DA-CATEGORIA",
      "category_name": "Categoria fictícia",
      "source_price_id": "UUID-DO-PRECO",
      "amount": "123.45",
      "currency": "BRL",
      "status": "pending",
      "status_label": "Aguardando requisitos",
      "review_required": true,
      "category_review": "pending",
      "category_review_label": "Aguardando revisão",
      "created_at": "2026-10-10T12:00:00+00:00",
      "cancelled_at": null
    }
  }
}
```

IDs e dados deste exemplo são ilustrativos. Não são inscrições criadas no banco real. A resposta não inclui identificação do operador ou o motivo interno do cancelamento.

Para criar, envie **somente** o `price_id` escolhido no catálogo:

```json
{"price_id": "UUID-VALIDO-RETORNADO-PELO-CATALOGO"}
```

É preciso enviar um UUID real; os textos ilustrativos acima não são UUIDs válidos. Dono, evento, categoria, valor, moeda, revisão e situação são decididos pelo servidor. UUID malformado ou ausente retorna `422 validation_error`, no campo `price_id`. Campos adicionais, como `amount`, `user`, `status` ou `event_id`, retornam `400 unknown_fields`.

Primeira criação retorna **201**, com `data.registration` e `data.created: true`. A operação e seu registro de auditoria são gravados na mesma transação; falha na auditoria desfaz a criação. Duas requisições simultâneas para a mesma conta/evento criam uma única inscrição e um único registro de criação.

Uma repetição válida retorna **200**, com a inscrição existente e `data.created: false`. Não muda categoria, valor, revisão nem situação. Isso vale mesmo se o prazo tiver fechado ou o preço anterior tiver saído do catálogo. É necessário continuar enviando um UUID válido no campo `price_id`; não envie dados incompletos como mecanismo de consulta, use GET.

Uma inscrição cancelada continua sendo retornada e **não é reativada** por um novo POST. Categoria e valor da inscrição preservam seus snapshots; editar o catálogo depois não modifica o histórico. Trocar o idioma muda o nome apresentado a partir das traduções preservadas, sem substituir o nome por uma versão comercial nova.

O backend revalida a janela do evento e a disponibilidade do preço no momento de criar. Se a seleção ficou desatualizada, a criação retorna **409**:

| `error.code` | Ação do front |
| --- | --- |
| `registrations_closed` | Informar que o período de inscrições não está aberto; consultar o evento novamente |
| `price_unavailable` | Atualizar o catálogo e pedir nova escolha; não reenviar em laço |
| `registration_unavailable` | Tratar impedimento de inscrição sem presumir pagamento ou confirmação |

Em uma repetição que recupera inscrição já existente, essa inscrição é devolvida antes da revalidação do catálogo, conforme a regra de unicidade acima.

## Situação e revisão

| Campo | Códigos |
| --- | --- |
| `status` | `pending`, `confirmed`, `cancelled` |
| `category_review` | `not_required`, `pending`, `approved`, `rejected` |

Use os códigos para lógica e os campos `*_label` para apresentação localizada. Toda criação nesta etapa começa em `pending`, inclusive com valor zero. Categoria sem revisão começa em `not_required`; categoria com revisão, em `pending`. Aprovar categoria não confirma pagamento ou participação.

Pagamentos ainda aguardam a escolha do serviço. A resposta não contém URL de checkout, Pix, recibo ou comprovante, e não promete matrícula confirmada. Cancelamento e revisão continuam na gestão autorizada já implementada; não são operações livres do participante. Artigos e submissões continuam no JEMS, com conta independente.

## Verificação e entrega

No PowerShell com a conexão local configurada:

```powershell
.\.venv\Scripts\python.exe backend\manage.py test accounts.tests.test_participant_api registrations.tests.test_participant_api --noinput
```

Os testes criam seu próprio banco temporário. Não é preciso abrir inscrições reais, cadastrar categorias comerciais nem alterar o evento existente para executar a suíte.

Para integração das telas, conferir:

- Participante anônimo, não confirmado e inativo recebem os erros de acesso apropriados.
- Perfil consultado e atualizado pertence apenas à conta da sessão.
- País/idioma inválidos não salvam mudanças parciais.
- Evento fechado e catálogo vazio têm estados distintos e tratados na tela.
- Categoria/preço desativado após a consulta resulta em erro de disponibilidade.
- Criação seguida de repetição devolve a mesma inscrição, sem cobrar ou confirmar automaticamente.
- Logout impede novas consultas privadas e CSRF continua obrigatório em todos os POSTs.

Não há novas dependências ou migrações nesta etapa. O guia assume que o backend anterior já foi instalado e suas migrações aplicadas. A execução nativa no Windows, a conexão do front e a configuração de produção continuam sendo verificações separadas da suíte Linux/PostgreSQL.
