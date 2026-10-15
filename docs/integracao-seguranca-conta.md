# Segurança da conta: troca de senha e logout global

Estas operações funcionam no backend, sem serviço de e-mail e sem gateway. Usam a
sessão Django existente, a senha atual e CSRF. Nenhuma tela foi alterada; o front
deve conectar os endpoints e tratar os retornos descritos abaixo.

## Rotas

Base: `/api/v1/auth/`.

| Método | Rota | Operação |
| --- | --- | --- |
| POST | `change-password/` | Trocar a senha e manter somente a sessão atual autenticada. |
| POST | `logout-all/` | Invalidar todas as sessões da própria conta, incluindo a atual. |

Exigem conta ativa já autenticada. Contas técnicas staff podem usá-las após login
no admin, mesmo sem confirmação de e-mail. O usuário-alvo vem da sessão; IDs,
e-mail, flags de MFA ou escolhas de outras sessões não são aceitos no corpo.

Envie cookies e `X-CSRFToken` obtido em `GET /api/v1/auth/session/` ou no fluxo de
MFA existente. As respostas usam JSON, envelope `ok/data` ou `ok/error`, e
`Cache-Control: no-store`. Todos os POSTs exigem CSRF, inclusive logout global.
As rotas não oferecem CORS público nem redirecionam para uma tela de login.

### Trocar senha

```json
{
  "current_password": "<senha atual>",
  "new_password1": "<nova senha>",
  "new_password2": "<repetição da nova senha>"
}
```

As três entradas são obrigatórias, em texto, sem remover espaços da senha, e têm
limite de 1024 caracteres cada. JSON e formulário URL-encoded são aceitos; o corpo
total segue o limite de 32 KiB. Campos extras, chaves repetidas, query parameters
e valores que não sejam texto são rejeitados.

A senha atual é conferida no servidor. A nova senha usa os validadores existentes
do Django: mínimo de 12 caracteres, comparação com nome/e-mail, rejeição de senha
comum ou exclusivamente numérica. As duas entradas precisam coincidir, e a nova
senha precisa ser diferente da atual.

Para staff com MFA cadastrado, a prova deve ter menos de cinco minutos. Se o
ambiente exige cadastro obrigatório e ele ainda não existe, retorna
`mfa_enrollment_required`. Se a prova estiver ausente/antiga, retorna `mfa_required`:
verifique o fator e tente novamente com o CSRF mais recente.

Resposta de sucesso, HTTP 200:

```json
{
  "ok": true,
  "data": {
    "code": "password_changed",
    "authenticated": true,
    "csrf_token": "<novo token mascarado>",
    "mfa_verification_required": false
  }
}
```

O backend troca o identificador da sessão e o CSRF, preservando a política de
lembrar-me/fechamento do navegador. Substitua o token usado pelo front pelo valor
retornado. As outras sessões deixam de autenticar na próxima validação pelo
servidor. O usuário poderá fazer novos logins usando a nova senha.

O dispositivo MFA, seu contador contra replay e os códigos de recuperação são
preservados. As provas de MFA das sessões anteriores são invalidadas, incluindo
a prova da sessão atual. Para staff protegido, `mfa_verification_required` será
`true`; consulte o [fluxo de MFA](integracao-mfa.md) antes de retomar a gestão.
Trocar senha não permite reutilizar um TOTP ou código de recuperação já consumido.

### Sair de todos os dispositivos

```json
{"current_password": "<senha atual>"}
```

Retorna HTTP 200 com `code: all_sessions_logged_out`, `authenticated: false` e um
novo `csrf_token`. Remova o estado autenticado da interface e ofereça novo login.
O dispositivo atual também sai da conta; repetir a operação sem login recebe 401.

Essa operação confirma a senha atual, mas não exige desafio MFA: permite reduzir
o acesso mesmo quando a pessoa está sem o autenticador. Não desativa o fator nem
concede prova ou acesso novo. A senha permanece a mesma; o próximo login continua
exigindo MFA nas áreas protegidas. O logout comum continua encerrando somente a
sessão atual e não exige senha.

## Revogação, tokens e auditoria

O usuário possui uma versão interna de sessão, inicialmente nula. Uma operação
bem-sucedida gera outra versão, usada na assinatura de autenticação do Django.
Não é preciso percorrer ou expor a tabela de sessões, listar dispositivos ou
devolver identificadores de outras sessões ao navegador. Linhas antigas podem
continuar no banco até a próxima validação/limpeza, mas deixam de autenticar.

As duas operações revogam links antigos de confirmação/recuperação da conta,
cancelam mensagens ainda não enviadas e apagam os códigos de entrega pendentes.
Também descartam cadastros pendentes de MFA. Uma mensagem já em trânsito pode
chegar, mas seu link anterior não será aceito. Novos links emitidos posteriormente
continuam funcionando. Nenhum e-mail é enviado por estas operações.

As ações `security.password_changed` e `security.sessions_revoked` são auditadas
com conta responsável e alvo. Não registram senha, link, token, versão interna,
identificador de sessão, IP em claro ou conteúdo do formulário.

Senha/versão, cancelamento de tokens, auditoria e persistência/remoção da sessão
concluem na mesma transação. Falhas nessas etapas retornam 503 sem sucesso
confirmado. Não presuma conclusão diante de erro ou perda da resposta; consulte
a sessão e, se necessário, faça novo login com a senha correspondente à ação.

Os serviços bloqueiam a conta e conferem novamente a assinatura da sessão. Duas
alterações concorrentes não sobrescrevem a senha uma da outra. Uma requisição
antiga não pode recuperar autenticação pela troca de senha ou pelos serviços de
MFA depois de aguardar uma revogação. Outras requisições que já passaram pela
autenticação podem terminar; conteúdo já exibido no navegador não é apagado pelo
servidor. O front deve tratar 401 nas próximas consultas.

Troca/logout global compartilham limite de **cinco tentativas por conta e trinta
por IP a cada cinco minutos**, entre sessões e processos. A identidade vem do
UUID da sessão, sem aceitar um e-mail escolhido pelo cliente. Sucessos e falhas
contam; rollback de auditoria não zera o limite. Excesso retorna 429 e `Retry-After`.
Se o contador no banco falhar, a operação é rejeitada com 503.

## Erros

| HTTP | Código | Tratamento |
| --- | --- | --- |
| 400 | `unknown_fields`, `invalid_payload` | Corrigir campos, tipos, parâmetros ou chaves repetidas. |
| 401 | `authentication_required` | Fazer novo login; sessão ausente, inativa ou revogada. |
| 401 | `invalid_credentials` | Senha atual incorreta ou conta sem senha utilizável. |
| 403 | `csrf_failed` | Renovar o CSRF pela sessão e conferir origem/cookies. |
| 403 | `mfa_enrollment_required`, `mfa_required` | Completar cadastro/desafio recente antes da troca de senha staff. |
| 405 | `method_not_allowed` | Usar POST. |
| 409 | `password_unchanged` | Escolher uma senha diferente da atual. |
| 413 / 415 | `payload_too_large`, `unsupported_media_type` | Respeitar tamanho/formato do corpo. |
| 422 | `validation_error` | Conferir `error.fields`, incluindo `new_password1`/`new_password2`. |
| 429 | `rate_limited` | Respeitar o prazo de `Retry-After`. |
| 503 | `operation_unavailable` | Banco/sessão/auditoria indisponível; não presumir conclusão. |

## Migração e operação

Não há dependência nova. Aplique `accounts.0005_user_session_version` e reinicie
todos os processos da aplicação com a mesma versão do código. Enquanto a versão
da conta for nula, a assinatura mantém o comportamento anterior do Django:
aplicar a migração não encerra sessões ou invalida links existentes em massa.
Rotação só ocorre quando a própria conta conclui uma destas operações.

Gravações comuns do usuário preservam a versão interna de sessão e não restauram
um hash de senha antigo carregado em outra operação. Serviços que alteram esses
campos usam `set_password()` e/ou `update_fields` explícitos. Preserve essa regra
ao acrescentar novos serviços ou fluxos administrativos.

Depois da primeira rotação, todos os processos que autenticam essas contas devem
usar esta implementação. Como outros estados de autenticação, restaurar um banco
antigo pode restaurar sessões/tokens anteriores à revogação: a recuperação de
produção exige invalidá-los e validar o procedimento, conforme o
[guia de backup](backup-recuperacao.md). `clearsessions` elimina sessões expiradas;
não equivale a logout global de sessões válidas.

```powershell
.\.venv\Scripts\python.exe backend\manage.py migrate
.\.venv\Scripts\python.exe backend\manage.py test accounts.tests.test_security_api accounts.tests.test_security_concurrency --noinput
```

No Linux, use `.venv/bin/python`. Os testes usam PostgreSQL real, contas fictícias,
operações simultâneas, CSRF, MFA, recuperação, auditoria, falhas na sessão/banco,
compatibilidade das assinaturas anteriores e rotação de chave Django com fallback.
Windows nativo, telas e infraestrutura do CTIC ainda precisam ser validados.
