# Integração do histórico de auditoria

Esta API consulta ações já registradas pelo backend: criação, revisão e cancelamento
de inscrições, geração de CSV e operações de segundo fator. Não cria telas.
Inclui a preparação de rascunhos internos de certificados. Pagamentos e emissão de certificados terão suas próprias ações quando forem implementados.

## Acesso

As rotas ficam sob `/api/v1/management/`, na mesma origem do portal, e usam a sessão
Django existente. Exigem conta ativa, `is_staff` e a permissão
`operations.view_auditentry`. O middleware de MFA também se aplica; consulte
[o contrato de segundo fator](integracao-mfa.md) para cadastrar e verificar o fator.

Essa permissão permite consultar o histórico **global do backend**, inclusive ações
de segurança e registros de outros eventos. Deve ser concedida somente a responsáveis
autorizados a consultar esse histórico. Os grupos de Atendimento, Indicadores e
Exportação continuam sem recebê-la automaticamente. O administrador técnico pode
atribuí-la a uma conta ou grupo pelo Django Admin; não é preciso torná-los superusuários.
Esta entrega não altera permissões nem grupos de contas existentes.

`GET /api/v1/management/session/` passa a incluir `permissions.view_audit`, independente
das permissões de inscrições. O front pode usar a capacidade para disponibilizar o
acesso; todas as requisições continuam sendo autorizadas no servidor.

## Rotas

| Método | Rota | Retorno |
| --- | --- | --- |
| GET | `/api/v1/management/audit/` | Registros paginados e filtros opcionais. |
| GET | `/api/v1/management/audit/<uuid>/` | Registro com motivo e detalhes permitidos. |

Não existem operações de criação, edição, exclusão ou exportação de auditoria por
esta API. Leituras não alteram os registros nem geram novos registros de auditoria.
Todas as respostas usam JSON, o envelope existente `ok/data` ou `ok/error` e
`Cache-Control: no-store`. Não há redirecionamento para uma tela de login.

### Listagem

Filtros opcionais:

| Parâmetro | Regra |
| --- | --- |
| `action` | Código exato de ação, até 64 caracteres. |
| `actor_id` | UUID da conta que realizou a ação. |
| `target_type` | Tipo exato, como `registrations.registration` ou `accounts.user`, até 64 caracteres. |
| `target_id` | Identificador exato do objeto, até 64 caracteres. Usar junto de `target_type` para evitar ambiguidade. |
| `created_from` | Data/hora ISO 8601 com fuso; início inclusivo. |
| `created_until` | Data/hora ISO 8601 com fuso; fim exclusivo e posterior ao início, se ambos forem informados. |
| `page` | Inteiro a partir de 1; padrão 1. |
| `page_size` | Inteiro de 1 a 100; padrão 50. |

Os filtros se combinam com AND. Sem filtros, a consulta abrange todo o histórico
permitido. Campos desconhecidos ou repetidos são rejeitados. Não há busca por
e-mail, nome ou texto do motivo.

Para consultar um dia no horário de Belém, envie, por exemplo,
`created_from=2026-10-13T00:00:00-03:00` e
`created_until=2026-10-14T00:00:00-03:00`. Construa a query com `URLSearchParams`
no front, especialmente quando o fuso contiver `+`. Datas sem fuso são inválidas.
O backend converte os limites para UTC; os horários retornados também estão em UTC.

Exemplo de resposta:

```json
{
  "ok": true,
  "data": {
    "entries": [{
      "id": "17c84b82-21a5-4f03-9612-a0a73a7cb421",
      "action": "registration.category_reviewed",
      "actor": {"id": "fe5bef54-55da-477d-a780-094fd27ad89d", "full_name": "Pessoa de exemplo"},
      "target": {"type": "registrations.registration", "id": "26a02ccd-22e6-4d31-a1b1-721a324c24db"},
      "created_at": "2026-10-13T15:00:00+00:00"
    }],
    "pagination": {"page": 1, "page_size": 50, "total": 1, "pages": 1, "has_next": false, "has_previous": false}
  }
}
```

A ordem é decrescente por horário e UUID, inclusive quando há horários iguais.
Novos registros podem deslocar páginas entre consultas; não se trata de um snapshot.
Uma busca vazia retorna `entries: []`, `total: 0` e `pages: 1` na primeira página.
Uma página inexistente retorna 404, sem escolher outra automaticamente.

`actor` contém somente o UUID e o nome atual da conta, sem e-mail ou credenciais.
Pode ser `null` para ações do sistema ou quando a conta do autor foi removida; o
registro e a referência ao objeto permanecem. O nome não é um snapshot histórico.
O objeto-alvo não é expandido em dados do participante. Motivo e detalhes ficam
fora da listagem e só são enviados na consulta individual autorizada.

### Detalhe

Retorna `data.entry` com os campos da listagem e mais `reason` e `details`.
Motivos são texto livre restrito à equipe autorizada; o front deve exibi-los como
texto, sem interpretar HTML. Não inclua senhas, códigos, tokens ou documentos em
motivos administrativos. Esta regra também vale para nomes exibidos.

Os detalhes publicados são selecionados por ação **e tipo do objeto**:

| Ação / alvo | Detalhes permitidos |
| --- | --- |
| `registration.created` / `registrations.registration` | `category_id`, `amount` como decimal em texto, `currency`. São dados registrados na criação. |
| `registration.category_reviewed` / `registrations.registration` | `previous`, `decision`. |
| `registration.exported` / `registrations.event` | `rows`, `status`, `review`, `search_applied` como booleano. O texto da busca não é publicado. |
| `security.mfa_verified` / `accounts.user` | `method`: `totp` ou `recovery`. |
| `certificate.draft_prepared` / `certificates.certificatedraft` | `registration_id`, `kind` (`participation`). Não inclui nome copiado nem caminho de arquivos. |
| Demais ações / alvos | Objeto vazio `{}`. O motivo e os metadados continuam disponíveis. |

Campos internos adicionais, valores inválidos e JSON com tipos inesperados são
omitidos. A API nunca serializa o payload interno completo. Incluir uma nova ação
ou campo no retorno exige atualizar explicitamente esse contrato e seus testes.

## Erros

| HTTP | Código | Tratamento |
| --- | --- | --- |
| 400 | `unknown_fields`, `invalid_payload` | Corrigir parâmetros desconhecidos/repetidos. |
| 401 | `authentication_required` | Fazer novo login. |
| 403 | `permission_denied` | Conta sem autorização para auditoria. |
| 403 | `mfa_enrollment_required`, `mfa_required` | Completar o fluxo de segundo fator. |
| 404 | `audit_entry_not_found`, `page_not_found` | Registro/identificador ou página inexistente. |
| 405 | `method_not_allowed` | Rotas aceitam somente GET. POST sem CSRF válido pode ser rejeitado antes por `csrf_failed`. |
| 422 | `validation_error` | Conferir `error.fields`, inclusive `__all__` para intervalo invertido. |
| 503 | `operation_unavailable` | Backend/banco indisponível; tentar novamente posteriormente. |

## Verificação

```powershell
.\.venv\Scripts\python.exe backend\manage.py test operations.tests.test_audit_api operations.tests.test_api --noinput
```

No Linux, use `.venv/bin/python`. Os testes usam PostgreSQL real e dados fictícios,
incluindo ações dos serviços de revisão/cancelamento, permissões/grupos/revogação,
MFA, filtros por fuso horário, paginação, leituras sem alteração de histórico,
campos privados omitidos e indisponibilidade do banco. Não há nova migração nem
dependência nesta entrega. A validação nativa do Windows e a integração das telas
ficam para os respectivos ambientes/responsáveis.
