# Segundo fator para comissão e administração

O backend oferece TOTP de aplicativo autenticador para contas ativas com `is_staff`. O primeiro fator continua sendo a senha/sessão Django. Nenhuma tela ou QR foi criado: o responsável do front deve conectar os fluxos abaixo. Não há envio de códigos por e-mail/SMS nem dependência do gateway.

## Proteção e implantação

O segundo fator protege todos os acessos em `/gestao/` (admin, gestão tradicional e CSV) e `/api/v1/management/`, incluindo exportação. Login/logout do admin permanecem disponíveis para estabelecer/encerrar o primeiro fator. As APIs pessoais do participante seguem suas regras anteriores; esta entrega é uma verificação adicional dos acessos da comissão, não uma substituição da autenticação de participantes.

Uma conta que habilita MFA passa a exigi-lo nessas rotas imediatamente. `CHAGS_REQUIRE_STAFF_MFA=true` também bloqueia contas staff ainda não cadastradas até concluírem o cadastro do fator. Produção exige por padrão; desenvolvimento começa com `false` para permitir preparar as telas. `false` não contorna um fator já habilitado. Contas técnicas com e-mail não confirmado usam o login existente em `/gestao/login/`; contas confirmadas também podem usar a API de login.

Antes da implantação real, o responsável do front deve concluir as telas de cadastro/desafio/recuperação. Sem elas, o admin retorna JSON 403 informando o desafio necessário após o login com senha. Não houve ativação ou publicação de produção nesta entrega. O primeiro cadastro deve ser feito pela própria pessoa em uma sessão confiável, conhecendo sua senha atual.

O dispositivo usa SHA-1/TOTP padrão de 6 dígitos e período de 30 segundos. O servidor aceita um período anterior/posterior para pequena diferença de relógio. Uma vez consumido, o mesmo período não pode autenticar outra sessão; aguarde o próximo código se acabou de usá-lo. Mantenha relógios automáticos sincronizados.

## Preparar o backend

Há uma migração nova (`accounts.0004`) e duas dependências fixadas/verificadas por hashes: PyOTP e cryptography. Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes -r backend\requirements-windows.txt
.\.venv\Scripts\python.exe backend\manage.py migrate --noinput
```

Na nuvem, o setup existente sincroniza o lockfile Linux e aplica migrações. Sem ele, use o pip da `.venv` com `backend/requirements.txt` e os mesmos comandos Django.

Produção exige uma `CHAGS_MFA_ENCRYPTION_KEY` Fernet dedicada, além da chave Django. O comando abaixo **cria um arquivo**, não imprime a chave e não sobrescreve arquivos existentes. Rode com settings de desenvolvimento para gerar antes de preencher a configuração de produção:

```powershell
.\.venv\Scripts\python.exe backend\manage.py create_mfa_key backend\.local\private-keys\production-mfa.key --settings=config.settings.development
```

Transfira seu conteúdo ao gerenciador de segredos do CTIC de forma privada e configure `CHAGS_MFA_ENCRYPTION_KEY`. Não envie o conteúdo nesta conversa, por URL, em logs ou para o Git. No Linux, arquivos novos recebem `0600` e pastas novas `0700`; no Windows, confira ACLs em Propriedades → Segurança. A execução nativa no Windows ainda precisa ser validada.

Em desenvolvimento, quando essa variável não existe, uma chave aleatória é criada uma vez em `backend/.local/mfa-encryption-key` e reutilizada. Produção nunca usa esse fallback. Valores inválidos ou ausência de chave em produção impedem carregar os settings. A chave precisa ser a mesma entre processos e reinicializações.

Os segredos TOTP ativo/pendente são criptografados; códigos de recuperação de 128 bits ficam somente como SHA-256. As tabelas não são registradas no admin nem recebem permissões de modelo. A chave de criptografia deve permanecer separada do banco e ser protegida/recuperável. Trocar/perder a chave invalida provas já emitidas e impede validar os dispositivos antigos; não gere outra como tentativa de consertar uma configuração perdida. Rotação com recriptografia exige procedimento técnico próprio, não fornecido por esta API.

## Rotas e sessão

Base: `/api/v1/auth/mfa/`. Mesma origem, sessão/cookie Django e CSRF em todos os POSTs. JSON e formulário URL-encoded são aceitos; os valores são strings. Não aceita outros usuários, parâmetros na URL ou campos desconhecidos. Respostas usam `{"ok": true, "data": {...}}` ou `{"ok": false, "error": {"code": "..."}}`, sem cache. Segredos/códigos não devem ser registrados em ferramentas de análise ou logs do front.

| Método | Rota | Corpo / retorno principal |
| --- | --- | --- |
| GET | `status/` | Estado próprio e `csrf_token`, sem segredo/códigos. |
| POST | `enroll/` | `password`; retorna segredo/URI de cadastro somente nessa resposta. |
| POST | `confirm/` | `code` de 6 dígitos do dispositivo pendente; habilita e retorna dez códigos de recuperação. |
| POST | `verify/` | `code`, `method` opcional (`totp`, padrão, ou `recovery`); verifica a sessão atual. |
| POST | `recovery-codes/` | `password`; substitui todos os códigos e retorna os dez novos uma vez. |
| POST | `disable/` | `password`; remove o fator somente quando a política permite MFA opcional. |

`status/` retorna `enabled`, `required`, `verified`, `recently_verified`, `pending_enrollment`, `recovery_codes_remaining` e `csrf_token`. Não cria dispositivo nem retorna material secreto. Está disponível após o primeiro fator, mesmo quando a gestão está bloqueada; consulte-o antes de tentar `/api/v1/management/session/`.

Envie `X-CSRFToken` e cookies nas requisições. Habilitar/verificar MFA e gerenciar recuperação rotaciona a sessão/CSRF; substitua o token usado pelo front pelo `csrf_token` retornado. Login com senha sozinho não marca a sessão como verificada. Logout remove a prova junto com a sessão.

## Cadastro e troca de dispositivo

1. Depois do login com senha, consulte `status/`.
2. Para cadastrar, envie `{"password": "<senha atual>"}` para `enroll/`.
3. A resposta contém `secret`, `provisioning_uri` no formato `otpauth://` e `expires_at`. Use a URI para gerar QR localmente na tela, ou ofereça inserção manual no aplicativo. Nunca envie a URI a um serviço externo de QR: ela contém o segredo.
4. Envie `{"code": "<6 dígitos>"}` para `confirm/`, no mesmo navegador/sessão que começou o cadastro. O cadastro pendente dura dez minutos e fica vinculado à senha atual e a um desafio privado daquela sessão. Um novo `enroll/` substitui o anterior.
5. Após confirmar, salve os dez `recovery_codes` em lugar privado e substitua o CSRF pelo retornado. Os códigos não podem ser consultados novamente; o backend guarda somente seus hashes. A sessão atual já estará verificada.

Segredo ativo nunca é devolvido pela API. Não use GET para confirmar cadastro. Confirmar um cadastro expirado, de outra sessão ou anterior à troca de senha recebe `mfa_enrollment_expired`.

Para trocar dispositivo já habilitado, primeiro verifique TOTP ou um código de recuperação e informe a senha atual em `enroll/`. A prova deve ter no máximo cinco minutos. O dispositivo antigo continua ativo até a confirmação do novo; confirmar troca substitui segredo/códigos e invalida provas de outras sessões.

## Desafio e recuperação

Dispositivo cadastrado: envie `{"code": "<6 dígitos>", "method": "totp"}` para `verify/`. Sem celular, envie `{"code": "<código de recuperação>", "method": "recovery"}`. Cada código de recuperação funciona uma vez, somente para sua conta. Duas requisições simultâneas não podem consumir o mesmo TOTP/período ou código de recuperação.

A prova dura uma hora e não é prolongada pelas consultas. Fica vinculada ao usuário, versão do fator, senha e chave de criptografia; não pode ser transportada entre contas/sessões pelo front. Alterar senha não remove o fator e exige verificar a nova sessão. A prova de outra sessão não libera esta.

Para renovar códigos ou remover/trocar dispositivo, também é necessário informar a senha atual e ter uma prova com menos de cinco minutos. Se `recently_verified` for falso, faça novo `verify/`, mesmo que `verified` ainda permita consultas comuns. `recovery-codes/` invalida os códigos anteriores e as provas de outras sessões. `disable/` é recusado com `mfa_mandatory` quando a exigência global está ativa.

Limites persistidos no banco são compartilhados entre processos/sessões e usam identidade definida pelo servidor. Confirmação/verificação compartilham cinco requisições por conta a cada cinco minutos e trinta por IP; cadastro/gestão sensível compartilham cinco por conta e vinte por IP no mesmo período. Requisições válidas no protocolo contam mesmo quando senha/código falham; sucesso não zera contador. Excesso retorna 429 e `Retry-After`. O IP segue a configuração de proxies confiáveis existente.

## Recuperação técnica e auditoria

Perdeu celular e todos os códigos: um operador autorizado do servidor deve confirmar a identidade por procedimento da organização antes do reset. Não existe endpoint de reset de terceiros nem recuperação automática por e-mail. Exemplo técnico (substitua o UUID; sem senhas/códigos no motivo):

```powershell
.\.venv\Scripts\python.exe backend\manage.py reset_staff_mfa UUID-DA-CONTA --reason "Identidade confirmada conforme procedimento da organização" --confirm
```

O comando exige conta staff, motivo e confirmação explícita; apaga material ativo/pendente e códigos, muda a versão e audita o reset com ator técnico nulo. Proteja o acesso ao servidor e associe o motivo a um registro operacional que identifique o responsável. Com exigência ativa, a conta continua bloqueada até novo cadastro. O comando não foi executado em contas reais nesta entrega.

Cadastro iniciado/habilitado, verificação bem-sucedida, renovação, desativação e reset são auditados sem segredo, senha, URI ou códigos. Verificação guarda somente `method`. Alteração/consumo, auditoria e gravação da prova da sessão concluem na mesma transação; falha de banco/auditoria não libera acesso nem consome definitivamente o código. Contadores de tentativas permanecem independentes dessa transação.

Backups passam a incluir segredos TOTP criptografados, hashes de recuperação e sessões. Também proteja a chave MFA fora do dump. Ao restaurar uma cópia antiga em produção, interrompa serviços, invalide sessões antigas e planeje renovação de recuperação/recadastro controlado: o dump pode trazer estado anterior de consumo de códigos. O ensaio de backup não inicia serviços nem concede sessão automaticamente.

## Erros e validação

| HTTP | Código | Significado |
| --- | --- | --- |
| 401 | `authentication_required`, `invalid_credentials`, `invalid_mfa_code` | Sessão/conta inválida, senha incorreta ou código inválido/já usado. |
| 403 | `permission_denied`, `csrf_failed` | Exige staff ativo/CSRF válido. |
| 403 | `mfa_required`, `mfa_enrollment_required` | Verificar sessão ou concluir cadastro antes da gestão. |
| 409 | `mfa_enrollment_expired`, `mfa_mandatory` | Reiniciar cadastro ou manter exigência obrigatória. |
| 429 | `rate_limited` | Aguardar `Retry-After`. |
| 503 | `mfa_unavailable`, `operation_unavailable` | Chave/material inacessível ou falha de banco; não presumir sucesso. |

Formato/método/campos inválidos mantêm os códigos 400/405/413/415/422 da API existente. Erros de gestão podem ocorrer antes da validação do corpo/CSRF quando falta a prova. O front deve tratar o desafio MFA e depois retomar a operação pretendida, sem alterar localmente o estado da inscrição.

Verificação específica (Linux):

```bash
.venv/bin/python backend/manage.py test accounts.tests.test_mfa accounts.tests.test_mfa_concurrency accounts.tests.test_mfa_crypto config.tests.test_settings --noinput
```

Windows usa `.\.venv\Scripts\python.exe` e o PostgreSQL local. Foram usados PostgreSQL real, vetores públicos RFC 6238, testes de recuperação/replay, concorrência, criptografia, provas de sessão, permissões, CSRF e rollback. HTTPS, relógio/segredos do CTIC e integração das telas ainda exigem validação na implantação.

## Operações de segurança da conta

O [contrato de segurança da conta](integracao-seguranca-conta.md) oferece troca de
senha autenticada e logout global. A troca para staff com MFA exige prova recente;
preserva o fator e seus códigos de recuperação, invalida provas anteriores e
descarta cadastros pendentes. Logout global confirma a senha atual e encerra todos
os acessos, inclusive o atual, sem exigir desafio MFA e sem desativar o fator.
