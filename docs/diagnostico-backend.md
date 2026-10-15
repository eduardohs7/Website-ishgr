# Diagnóstico seguro de requisições e erros

O backend identifica as requisições que chegam ao Django pelo header de resposta **`X-Request-ID`**. Em produção, esse identificador permite relacionar a resposta com logs JSON sem registrar o conteúdo privado enviado pelo participante.

## Contrato para a integração

O identificador tem 32 caracteres hexadecimais, é gerado pelo servidor a cada requisição e também aparece em respostas de erro, rejeição de host, CSRF e redirecionamento para HTTPS. Headers `X-Request-ID` ou `X-Correlation-ID` enviados pelo cliente ou proxy não são aceitos como identificador da aplicação.

O responsável pela integração pode consultar o header da resposta na mesma origem e fornecê-lo ao suporte quando ocorrer uma falha. O identificador não autentica ninguém e não permite consultar dados ou logs por uma API pública. Os corpos dos retornos específicos de autenticação, inscrição, MFA e gestão mantêm seus contratos.

Com `DEBUG=False`, os erros gerais sob `/api` e `/api/` retornam JSON, inclusive em caminhos ou versões desconhecidos:

| HTTP | `error.code` | Situação |
| --- | --- | --- |
| 400 | `bad_request` | Requisição rejeitada pelo Django, como host inválido. |
| 403 | `permission_denied` | Negação geral de permissão, quando não houver código específico da operação. |
| 404 | `not_found` | Caminho ou recurso não encontrado pelo handler geral. |
| 500 | `internal_error` | Falha inesperada no processamento. |

Exemplo de corpo de um erro inesperado:

```json
{"ok": false, "error": {"code": "internal_error"}}
```

Essas respostas incluem `Cache-Control: no-store`. Não contêm a mensagem da exceção, o caminho solicitado ou detalhes da conexão. O identificador fica no header. Erros já tratados pelas operações continuam com os códigos anteriores, como `csrf_failed`, `authentication_required`, `rate_limited` e `operation_unavailable`; não são substituídos indiscriminadamente por `internal_error`. Não repetir operações de escrita automaticamente só porque houve erro 500.

As rotas fora de `/api` mantêm os handlers HTML padrão do Django. Em desenvolvimento, `DEBUG=True` continua permitindo as páginas técnicas de diagnóstico do Django, inclusive para erros gerais da API. Os testes verificam o comportamento de produção sem exigir credenciais reais de SMTP ou do gateway.

## Logs de produção

Os settings `config.settings.production` ativam logs JSON no stdout do processo, com uma linha por registro. O CTIC poderá coletar esse fluxo pelo gerenciador do serviço. Não há envio de alertas por e-mail ou dependência de um coletor externo nesta entrega.

O evento `http_response` contém somente:

- Data/hora UTC, nível e identificador da requisição.
- Método HTTP, status e nome estático da rota Django; caminhos não resolvidos usam `unresolved`.
- Tempo de preparação da resposta em milissegundos, medido com relógio monotônico. Não mede a transmissão completa de uma resposta em streaming.

Falhas registradas pelo Django podem produzir também um evento `server_event` com o mesmo identificador, o tipo da exceção e até oito posições da pilha: módulo, função e número da linha. Mensagens de exceção, linhas de código, variáveis locais e exceções encadeadas não são serializadas. Registros de tarefas fora de uma requisição não recebem o identificador de uma requisição anterior.

O formatter usa uma lista explícita de campos. Não formata a mensagem/argumentos originais nem inclui campos adicionais arbitrários do logger. Assim, os handlers configurados não registram corpo, formulários, query string, valores capturados na URL, IP, nome/e-mail do participante, cookies, Authorization, senhas, tokens ou parâmetros SQL. A rota é seu nome estático, como `auth_api:login`, sem os valores enviados na URL.

Os contextos de requisições simultâneas ficam separados. O logger HTTP fica silencioso nos settings de desenvolvimento; a configuração JSON é específica de produção. Logs técnicos de diagnóstico não substituem os registros transacionais de auditoria mantidos no banco.

O CTIC ainda precisa definir coleta, acesso e retenção desses logs. Logs independentes do proxy, Gunicorn, banco, ferramentas externas, `print()` e handlers próprios adicionados por bibliotecas não passam automaticamente por esse formatter. Antes da publicação real, revise também essas configurações para evitar gravação de query strings, headers e payloads. O modo de desenvolvimento não deve receber dados reais em ambiente público.

## Verificação no Windows

Com o servidor local em execução, é possível conferir o header sem alterar telas:

```powershell
$resposta = Invoke-WebRequest -Uri "http://127.0.0.1:8001/health/live/"
$resposta.Headers["X-Request-ID"]
```

Os testes específicos incluem a configuração real de produção em processo separado, erros com dados sintéticos, CSRF, rejeição de host, isolamento concorrente e proteção contra formatação de mensagens privadas:

```powershell
.\.venv\Scripts\python.exe backend\manage.py test config.tests.test_observability --noinput
```

Não precisam enviar e-mail, acessar um gateway ou criar participantes reais. A suíte completa do backend continua no workflow **Backend CI**. Esta entrega não muda dependências, modelos, migrações ou arquivos do frontend.
