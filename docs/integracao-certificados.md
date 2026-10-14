# Estrutura de certificados — integração do backend

Esta entrega prepara **elegibilidade e rascunhos internos de participação**. Não
emite certificados, não gera PDF e não oferece download ou validação pública.
Critérios, modelo, assinatura e carga horária continuam aguardando a organização.
O front permanece com o outro desenvolvedor; nenhuma tela foi criada ou alterada.

## Dados e limites desta fase

`CertificateEligibility` possui uma relação única com a inscrição. Começa em
`pending`, sem critério, decisão, responsável ou data de avaliação. Os estados
`eligible`/`ineligible` e os campos de versão do critério, motivo e revisor estão
reservados no modelo para a próxima fase: **não há operação de aprovação ou
rejeição de elegibilidade nesta entrega**. O banco exige esses campos de
proveniência para decisões diferentes de pendente.

`CertificateDraft` possui uma relação única com a elegibilidade. Guarda UUID,
tipo `participation`, nome do participante, título/datas do evento copiados,
motivo da preparação, conta responsável e data. Não contém número de certificado,
arquivo, assinatura, carga horária ou data de emissão. Documentos efetivamente
emitidos e suas versões terão um modelo e fluxo próprios na próxima etapa.

Preparar rascunho:

- Não confirma inscrição, pagamento, presença ou elegibilidade.
- Exige inscrição não cancelada; uma inscrição ainda pendente pode ter rascunho.
- Reutiliza o rascunho existente sem alterar seus dados, motivo, autor ou auditoria.
- Não modifica uma eventual avaliação de elegibilidade já registrada.
- Registra `certificate.draft_prepared` com motivo, responsável, inscrição e tipo.
- Conclui criação de elegibilidade/rascunho e auditoria na mesma transação.

O snapshot é preservado pelas operações desta API. Alterar o perfil ou título/datas
do evento posteriormente não atualiza o rascunho. A futura emissão deverá permitir
conferência explícita dos dados e tratar revisões/versionamento; não deve aprovar
automaticamente os snapshots antigos. Não há operação de edição/exclusão nesta
fase. A role técnica do banco permanece capaz de escrever e deve ser protegida.

As relações usam `PROTECT` para impedir a remoção acidental de inscrição,
elegibilidade e responsável referenciados. A preparação é serializada por conta,
evento e inscrição; solicitações concorrentes criam um rascunho e uma auditoria.
Cancelamento concorrente pode impedir a preparação ou ocorrer depois dela, mas
nunca reabre a inscrição nem transforma o rascunho em documento emitido.

## Acesso da comissão

Base: `/api/v1/management/certificates/`. Mesma origem, sessão Django e política de
[MFA da gestão](integracao-mfa.md). Exige conta ativa e staff. As permissões são:

| Operação | Permissões necessárias |
| --- | --- |
| Consultar rascunhos | `registrations.view_registration` e `certificates.view_certificatedraft`. |
| Preparar rascunho | As anteriores e `certificates.prepare_certificatedraft`. |

As permissões são criadas pela migração, mas **nenhuma conta ou grupo existente
as recebe automaticamente**. O responsável técnico pode atribuí-las a um grupo
ou conta pelo admin de usuários. `certificates.view_certificateeligibility` é uma
permissão de modelo reservada e, sozinha, não permite usar estas APIs.

`GET /api/v1/management/session/` inclui `view_certificate_drafts` e
`prepare_certificate_drafts`. Essas capacidades não autorizam emitir documentos.
Os grupos existentes de inscrições continuam com suas atribuições anteriores.

| Método | Rota relativa | Operação |
| --- | --- | --- |
| GET | `` (base) | Listar rascunhos do evento configurado. |
| GET | `<uuid>/` | Consultar um rascunho do evento configurado. |
| POST | `prepare/` | Preparar ou reutilizar o rascunho de uma inscrição. |

Listagem aceita `registration_id` (UUID exato), `page` (padrão 1) e `page_size`
(padrão 50, de 1 a 100). Retorna `data.drafts`, `data.pagination`,
`issuance_enabled: false` e `issuance_block_reason: certificate_rules_not_configured`.
A paginação usa `page`, `page_size`, `total`, `pages`, `has_next` e `has_previous`,
como a gestão existente; ordena por criação/UUID decrescentes. Novas criações podem
deslocar páginas. Filtros sem resultados retornam lista vazia na primeira página.

Cada rascunho retorna `id`, `registration_id`, `kind`, `state: draft`,
`participant_name`, `event_title`, `created_at`, `prepared_by_id`,
`eligibility_status`, `registration_status` e os dois campos de bloqueio de emissão.
O detalhe retorna `data.draft` com os mesmos campos e mais `event_starts_on`,
`event_ends_on` e `preparation_reason`. Não retorna e-mail ou credenciais da conta.
Nomes/motivos são texto livre e devem ser exibidos como texto, sem interpretar HTML.

POST de preparação (JSON, valores em texto):

```json
{
  "registration_id": "26a02ccd-22e6-4d31-a1b1-721a324c24db",
  "reason": "Preparação para conferência interna pela comissão."
}
```

Envie cookies e `X-CSRFToken` obtido no fluxo de sessão existente. JSON ou formulário
URL-encoded são aceitos, com limite de corpo de 32 KiB. O motivo é obrigatório,
sem espaços vazios e com até 1000 caracteres. Campos extras, valores que não sejam
texto e chaves repetidas são rejeitados. A API não aceita nome, dono, aprovação,
carga horária, arquivo, assinatura ou liberação enviados pelo navegador.

Retorna `data.draft` e `data.created`: HTTP 201 na primeira preparação e 200 ao
reutilizar. Repetir a solicitação não cria outra auditoria nem substitui o motivo.
A disponibilidade da emissão permanece falsa nos dois casos.

## Consulta do participante

`GET /api/v1/participant/certificates/` exige sessão própria ativa e e-mail
confirmado, como as demais APIs pessoais. Não aceita parâmetros de consulta nem
identificadores de outros participantes. Não exige MFA de comissão.

Exemplo com inscrição existente:

```json
{
  "ok": true,
  "data": {
    "registration_id": "26a02ccd-22e6-4d31-a1b1-721a324c24db",
    "eligibility_status": "pending",
    "issuance_enabled": false,
    "issuance_block_reason": "certificate_rules_not_configured",
    "certificates": []
  }
}
```

Sem inscrição, `registration_id` é nulo e o bloqueio é `registration_required`.
Para inscrição cancelada, o bloqueio é `registration_cancelled`. A consulta não
cria elegibilidade nem rascunho. Rascunhos internos, motivos, responsáveis e seus
identificadores não aparecem no retorno pessoal. `certificates` permanece vazio:
um rascunho não deve aparecer como certificado disponível na tela.

## Erros e segurança

Respostas seguem `ok/data` ou `ok/error`, em JSON e com `Cache-Control: no-store`.
Não há CORS público. As rotas da comissão sempre ficam no prefixo protegido por
MFA; preservar essa proteção em futuras operações de emissão/download.

| HTTP | Código | Situação |
| --- | --- | --- |
| 400 | `unknown_fields`, `invalid_payload` | Parâmetros/campos extras, repetidos ou tipos de corpo incorretos. |
| 401 | `authentication_required` | Sessão ausente ou conta inativa. |
| 403 | `permission_denied`, `email_not_verified`, `csrf_failed` | Permissões, confirmação ou CSRF ausentes. |
| 403 | `mfa_enrollment_required`, `mfa_required` | Concluir cadastro/desafio da comissão. |
| 404 | `event_not_configured`, `registration_not_found`, `certificate_draft_not_found`, `page_not_found` | Objeto ausente, fora do evento ou página inexistente. |
| 405 | `method_not_allowed` | Método não suportado; rascunhos existentes aceitam somente GET. |
| 409 | `registration_cancelled`, `certificate_data_incomplete` | Preparação indisponível pela inscrição ou por nome/título vazios. |
| 413 / 415 | `payload_too_large`, `unsupported_media_type` | Corpo excedido ou formato não aceito. |
| 422 | `validation_error` | Consultar `error.fields`. |
| 503 | `operation_unavailable` | Falha no banco/auditoria; não presumir que a preparação concluiu. |

UUID de rascunho inválido ou de outro evento recebe o mesmo 404. UUID de inscrição
com formato inválido no formulário recebe 422; ausente ou de outro evento, 404.
Não existem rotas de emissão, aprovação, arquivo, validação pública ou download.
Nenhuma alteração de configuração ou campo do POST habilita emissão nesta fase.

## Migração e testes

Não há dependência nova. Após integrar esta branch, aplique a migração:

```powershell
.\.venv\Scripts\python.exe backend\manage.py migrate
.\.venv\Scripts\python.exe backend\manage.py test certificates --noinput
```

No Linux, use `.venv/bin/python`. O CI inclui o app `certificates` na suíte completa.
Os testes usam PostgreSQL real e cobrem permissões/MFA/CSRF, escopo por evento e
participante, ausência de emissão, snapshots, limites do protocolo, restrições de
banco, rollback de auditoria e concorrência entre preparação/cancelamento. Windows
nativo e a integração das telas ainda precisam ser validados nesses ambientes.

Para a próxima etapa, a organização precisa fornecer os critérios de participação
e comprovação, responsável pela aprovação, modelo/textos e idiomas, assinaturas,
carga horária quando aplicável, regras de correção/reemissão e revogação. Pagamento
isolado não comprova presença. O projeto não fará submissão/avaliação científica;
o JEMS continua independente.
