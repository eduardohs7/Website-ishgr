# Integração das telas de autenticação com o backend CHAGS 14

Contrato v1, preparado em 10/10/2026. Este documento é a entrega para quem mantém o front. O backend oferece rotas de sessão, cadastro, login, logout, confirmação e recuperação. Nenhum HTML, CSS ou JavaScript foi alterado nesta etapa. As telas no GitHub ainda precisam ser conectadas por seu responsável.

## Origem e autenticação

Use **a mesma origem** para as páginas e a API: mesmo protocolo, host e porta. No desenvolvimento, o backend roda em `http://127.0.0.1:8001`. Uma página no Live Server (porta 5500), aberta por `file://` ou publicada no GitHub Pages não é a mesma origem. A publicação deve colocar páginas e API atrás da mesma origem HTTPS, por exemplo com o proxy do CTIC.

O contrato usa sessão Django em cookie `HttpOnly`. Não usa JWT, token de acesso em `localStorage` ou CORS aberto. A sessão também funciona nas rotas existentes do participante; o login pela API não cria um sistema de autenticação separado.

As páginas novas do amigo foram consultadas em `origin/main` (`fa9afd9`): `login.html`, `register.html`, `pt-br/login.html`, `pt-br/cadastro.html`. Elas não foram copiadas sobre os arquivos locais nem registradas como novas páginas públicas nesta etapa. O responsável pelo front precisa organizar a entrega das páginas na mesma origem com o backend. Os campos abaixo descrevem essa ligação; o responsável pelo front fará as alterações de formulário e apresentação de mensagens.

## Sequência para CSRF

1. Antes de qualquer operação, chame `GET /api/v1/auth/session/`. A resposta contém `data.csrf_token`; mantenha esse valor em memória. O navegador recebe o cookie CSRF.
2. Envie o token no header **`X-CSRFToken`** em cada POST. As requisições precisam preservar os cookies da mesma origem.
3. Após login ou logout, substitua o token em memória pelo novo `data.csrf_token`, pois ele pode mudar. Também é possível consultar `session/` novamente.
4. Não envie tokens de confirmação ou recuperação na URL da API. Eles pertencem ao corpo do POST. Abrir um link de e-mail não deve confirmar a conta nem alterar a senha automaticamente.

Os POSTs sem CSRF válido ou com origem não autorizada recebem `403 csrf_failed`. Nenhuma rota de autenticação desativa CSRF. Nunca envie cookie ou token para outra origem.

## Formato das requisições e respostas

Envie `Content-Type: application/json`. Os valores dos campos são **strings**, incluindo `remember_me` (`"true"` ou `"false"`). Também é aceito `application/x-www-form-urlencoded` com os mesmos nomes; nesse caso o token pode ir no campo `csrfmiddlewaretoken`. Multipart/FormData não faz parte deste contrato. Envie `{}` para logout em JSON.

O corpo pode ter até 32 KiB. JSON deve ser um objeto sem chaves repetidas. Campos desconhecidos são rejeitados; não envie `is_staff`, `is_superuser`, `user`, `email_verified_at` ou qualquer campo de privilégio. Use a barra final nas URLs: um redirecionamento para acrescentá-la pode perder o POST.

Sucesso:

```json
{"ok": true, "data": {"code": "signup_received"}}
```

Erro de validação (mensagens acompanham o idioma vigente):

```json
{
  "ok": false,
  "error": {
    "code": "validation_error",
    "fields": {
      "password2": [
        {"code": "", "message": "As senhas não coincidem."}
      ]
    }
  }
}
```

O código do erro principal é estável. Mensagens de campos vêm dos validadores Django; algumas validações legadas têm `code` vazio. Cada campo contém uma lista de erros. `__all__`, quando presente, indica erro geral do formulário. Não apresente a resposta como HTML: mensagens e dados do participante devem ser tratados como texto.

| HTTP | Significado / `error.code` |
| --- | --- |
| 200 | Operação concluída ou consulta de sessão |
| 202 | Solicitação recebida; não garante envio ou existência de conta |
| 400 | `invalid_payload`, `unknown_fields` ou `invalid_token` |
| 401 | `invalid_credentials` no login |
| 403 | `csrf_failed` |
| 405 | `method_not_allowed`; header `Allow` informa o método |
| 413 | `payload_too_large` |
| 415 | `unsupported_media_type` |
| 422 | `validation_error`, com `error.fields` |
| 429 | `rate_limited`; respeite o header `Retry-After`, em segundos |

As rotas listadas retornam JSON, sem cache. Rotas inexistentes, falhas de infraestrutura ou erros internos não fazem parte desse envelope; o front também deve tratar indisponibilidade sem tentar ler qualquer resposta como JSON obrigatoriamente.

## Rotas

| Método | Rota | Entrada | Resultado |
| --- | --- | --- | --- |
| GET | `/api/v1/auth/session/` | Nenhuma | Sessão atual e token CSRF |
| POST | `/api/v1/auth/signup/` | Cadastro abaixo | `202 signup_received` |
| POST | `/api/v1/auth/login/` | Login abaixo | Participante, destino seguro e novo CSRF |
| POST | `/api/v1/auth/logout/` | Objeto vazio | `200 logged_out` e CSRF |
| POST | `/api/v1/auth/confirm-email/` | `token` | `200 email_confirmed` |
| POST | `/api/v1/auth/resend-confirmation/` | `email` | `202 email_request_received` |
| POST | `/api/v1/auth/request-password-reset/` | `email` | `202 email_request_received` |
| POST | `/api/v1/auth/reset-password/` | `token`, `password1`, `password2` | `200 password_updated` |

### Cadastro e correspondência com a tela

```json
{
  "first_name": "Maria",
  "last_name": "da Silva",
  "email": "participante@example.org",
  "institution": "UFPA",
  "country": "BR",
  "language": "pt-br",
  "password1": "EXEMPLO-APENAS-NAO-UTILIZAR-73!",
  "password2": "EXEMPLO-APENAS-NAO-UTILIZAR-73!"
}
```

Esse endereço e essa senha são exemplos de contrato, não credenciais criadas.

| Campo atual da tela (`id`) | Campo enviado à API | Regra |
| --- | --- | --- |
| `firstname` | `first_name` | Obrigatório, até 127 caracteres, após remoção de espaços nas pontas |
| `lastname` | `last_name` | Obrigatório, até 127 caracteres |
| `institution` | `institution` | Opcional para o backend, até 255 caracteres |
| `email` | `email` | Obrigatório, e-mail válido, até 254 caracteres |
| `password` | `password1` | Obrigatório, pelo menos 12 caracteres e demais validadores de senha |
| `confirm-password` | `password2` | Obrigatório e igual a `password1` |
| Não presente nas telas consultadas | `country` | Opcional, código ISO de duas letras em maiúsculas; por exemplo `BR`, `PT`, `US` |
| Idioma da página | `language` | Opcional: `pt-br`, `en` ou `es` |

O backend junta nome e sobrenome, preserva sobrenomes compostos e armazena `full_name`. Não é preciso criar duas colunas novas no banco. Senhas não são aparadas nem normalizadas. Um e-mail é aparado e convertido para minúsculas, sem retirar pontos ou o sufixo `+`.

Se país não estiver disponível na tela, pode omiti-lo e completá-lo depois no perfil. Se idioma for omitido, usa-se o idioma atual da requisição (cookie/`Accept-Language`, com fallback português). O idioma explícito do cadastro define o perfil e a mensagem de confirmação. Francês não faz parte dos idiomas previstos no backend.

O cadastro não autentica e não confirma o e-mail. Endereço novo e já existente retornam a mesma resposta; cadastro repetido não substitui nome, senha ou perfil. A mensagem deve dizer que **se os dados permitirem o cadastro, as instruções serão enviadas**, sem afirmar que a conta foi criada ou que o e-mail existe.

### Login

```json
{
  "email": "participante@example.org",
  "password": "SENHA-ESCOLHIDA-PELO-PARTICIPANTE",
  "remember_me": "false",
  "next": "/participante/"
}
```

`remember_me` e `next` são opcionais. O identificador enviado é `email`; o campo `username` é exclusivo do formulário HTML legado, não da API.

Sucesso:

```json
{
  "ok": true,
  "data": {
    "participant": {
      "id": "UUID-DO-PARTICIPANTE",
      "full_name": "Maria da Silva",
      "email": "participante@example.org"
    },
    "next": "/participante/",
    "csrf_token": "TOKEN-CSRF-RENOVADO"
  }
}
```

Conta inexistente, senha incorreta, conta inativa e e-mail não confirmado retornam **o mesmo `401 invalid_credentials`**. Sugestão de mensagem: “E-mail ou senha inválidos, ou conta ainda não confirmada.” A tela pode oferecer recuperação e reenvio de confirmação sem dizer qual condição ocorreu.

O login renova sessão e CSRF. Sem `remember_me`, ou com `"false"`, o cookie de sessão não tem validade persistente e a sessão é configurada para fechar com o navegador; restauração de sessões pelo navegador pode preservar cookies. Com `"true"`, aplica-se `SESSION_COOKIE_AGE`, atualmente 14 dias. Isso não guarda a senha e não substitui reautenticação quando necessário.

`next` só aceita destino considerado seguro pelo Django, no mesmo host e sem rebaixar HTTPS para HTTP em produção. Destinos externos são substituídos por `/participante/`. Use o destino devolvido, não uma URL arbitrária recebida do visitante.

### Sessão e logout

Sessão anônima retorna `authenticated: false`, `participant: null` e um `csrf_token`. Sessão de participante confirmado retorna `authenticated: true` e os três campos públicos de participante acima. Uma sessão técnica sem e-mail confirmado não é apresentada como participante autenticado. Não há exposição de permissões, hash de senha ou dados de outras contas.

Logout é POST com CSRF, inclusive para encerrar uma sessão já anônima. Não use links GET para modificar a sessão. Como o Django usa uma sessão compartilhada, sair também encerra o login administrativo dessa sessão de navegador.

### Confirmação e recuperação

Os tokens são de uso único e ligados à finalidade, conta e estado da senha/e-mail. Confirmação expira em 24 horas; recuperação, em uma hora. Reenvio revoga o token anterior. Token inválido, expirado, revogado ou já usado retorna `400 invalid_token`.

Os e-mails existentes apontam para `/conta/confirmar-email/#token=...` e `/conta/redefinir-senha/#token=...`. A API não mudou esses destinos. As telas atuais do backend continuam atendendo esses links como compatibilidade. Para assumir o visual dessas etapas, o responsável pelo front deverá preparar as páginas correspondentes e coordenar as rotas com o backend. O token deve permanecer no fragmento, ser retirado do histórico depois de lido e só ser enviado após ação explícita do usuário.

Recuperação e reenvio recebem só `email`. A resposta não informa se existe conta e não devolve token. Recuperação só enfileira mensagem para conta ativa com e-mail confirmado. E-mail de conta não confirmada deve usar reenvio de confirmação.

Redefinição recebe `token`, `password1` e `password2`. Os nomes dos erros são esses mesmos, ainda que o validador interno Django use `new_password1/2`. Senha fraca ou confirmação divergente não consome o token. Sucesso não faz login automático; a alteração invalida as sessões anteriores e o participante deve entrar com a nova senha.

## Proteções e divisão de trabalho

API, formulários existentes e login do admin compartilham os limites persistidos por IP/e-mail no PostgreSQL; alternar endpoints não reinicia o limite. Cadastro, recuperação e confirmação possuem seus próprios limites. `Retry-After` orienta quando permitir nova tentativa; não faça reenvio automático em laço. Nenhum cabeçalho de proxy enviado pelo cliente é confiado por padrão.

O backend valida todos os dados mesmo que a tela já tenha validação. Senha, cookies e tokens não devem aparecer em console, ferramentas de analytics, logs ou URLs de requisição. A proteção em produção depende também de HTTPS, segredo, SMTP e proxy corretamente configurados.

| Responsável | Entrega |
| --- | --- |
| Backend | API, validação, sessão, CSRF, fila de mensagens, limites, testes e contrato |
| Front | HTML/CSS/JavaScript, envio dos campos, preservação dos cookies, CSRF, mensagens, estados de carregamento e navegação |
| Organização/CTIC | Hospedagem, HTTPS, domínio, serviço de envio e configuração de produção |

As telas consultadas citam submissão e acompanhamento de trabalhos. O escopo aprovado mantém artigos no **JEMS**, separado da conta CHAGS; fazer login aqui não autentica automaticamente no JEMS. O responsável pelo front deve alinhar esses textos com a coordenação.

## Verificação local e entrega

Teste primeiro o backend, sem depender das telas. No PowerShell já configurado para o PostgreSQL conforme o guia Windows:

```powershell
.\.venv\Scripts\python.exe backend\manage.py test accounts.tests.test_auth_api --noinput
```

Consulta manual, com o servidor ligado (não precisa de senha ou criar usuário):

```powershell
$sessaoWeb = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$resposta = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/api/v1/auth/session/' -WebSession $sessaoWeb
$resposta.data.authenticated
```

A consulta deve retornar `False` para uma sessão nova. Para as operações de escrita, use essa mesma sessão de cookies e o token CSRF retornado, sem imprimir o token.

Checklist para o responsável pelo front:

- Servir página e API na mesma origem e incluir a barra final nas rotas.
- Obter CSRF antes de POST e atualizar o valor após login/logout.
- Enviar os nomes indicados na tabela, com valores string.
- Exibir os erros de cada campo e tratar `401`, `403`, `429` e falha de rede.
- Não afirmar a existência da conta ao receber cadastro/recuperação/reenvio.
- Validar cadastro → e-mail → confirmação → login → sessão → logout.
- Validar recuperação → nova senha → login; conferir que a senha anterior falha.

Em desenvolvimento, o worker `send_account_emails` grava mensagens em `backend/.local/emails`, sem enviar e-mail externo. Execute-o separadamente conforme o [guia Windows](desenvolvimento-windows.md). Credenciais SMTP reais continuam pendentes para produção.

Esta entrega não exige novas dependências nem migrações além das já existentes no backend. Ela exige levar os arquivos novos e modificados de **backend** para a cópia que executará a API. Atualizar o código na nuvem não atualiza automaticamente o GitHub ou o computador do desenvolvedor; use a branch publicada para obter a versão correspondente.
