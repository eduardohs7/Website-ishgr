# Resultado local do ensaio de resistência

Execução em 14/10/2026, em ambiente de nuvem com dados sintéticos e limpeza concluída. O [JSON sanitizado completo](entregas/resistencia-local-2026-10-14.json) registra as versões, métricas, falha simulada e integridade. Procedimento e definições: [guia do ensaio](testes-resistencia.md).

O Gunicorn usou dois workers e quatro threads por worker. O PostgreSQL foi 17.11, com role sem superusuário/criação de roles, conexões persistentes por 60 segundos e health checks de conexão ativos. Python 3.12.14, Django 5.2.17 e Gunicorn 23.0.0. Cada nível de leitura durou aproximadamente dez segundos, com um ou oito clientes concorrentes. O ambiente expôs cinco CPUs, sem garantia de recursos dedicados.

| Fase | Chamadas alvo | Status: quantidade | Chamadas/s | p95 (ms) |
| --- | ---: | --- | ---: | ---: |
| Cadastro repetido | 4 | 202: 3, 429: 1 | 19,21 | 196,04 |
| Limite de login | 12 | 200: 9, 429: 3 | 22,55 | 411,46 |
| Inscrições repetidas | 16 | 200: 12, 201: 4 | 138,05 | 75,09 |
| Leituras — 1 cliente | 1842 | 200: 1842 | 183,96 | 7,50 |
| Leituras — 8 clientes | 4212 | 200: 4212 | 418,42 | 30,21 |

As 6.054 consultas das duas fases de leitura retornaram `200`. Os quatro bloqueios `429` nas fases de cadastro/login foram esperados e tiveram `Retry-After`. As dezesseis tentativas de inscrição produziram somente quatro inscrições, sem auditoria duplicada. As amostras de escrita são pequenas; seus percentis não estimam desempenho financeiro ou capacidade de inscrições em produção.

Durante a perda de acesso, liveness permaneceu `200` com e sem cookie; readiness retornou `503` em ambos. A tentativa de inscrição durante a falha recebeu `500`, como erro de indisponibilidade antes de gravar dados. Isso não foi contado como sucesso normal. Após restaurar o acesso, a prontidão e a consulta com sessão foram verificadas em 26,51 ms neste ensaio; esse tempo não inclui a duração do comando administrativo que restaura o acesso e não é um SLA de recuperação.

As sessões anteriores continuaram válidas. A quinta pessoa permanecia sem inscrição depois da falha; a nova tentativa criou uma inscrição e a repetição reutilizou a mesma. A verificação final encontrou seis contas/perfis sintéticos, uma mensagem pendente na fila, cinco inscrições e cinco registros de criação na auditoria, com valores/moedas e estados preservados. Nada foi confirmado como pagamento ou participação efetiva.

O ensaio de controle, antes da correção do middleware de idioma, havia reprovado na perda de acesso: requisições de saúde com sessão autenticada dependiam do banco. Após a correção, os cenários passaram tanto no ensaio inicial com quatro clientes quanto nesta medição com oito.

A medição usa a cópia local do backend, que inclui também as entregas separadas dos PRs #10, #11 e #12. O identificador do conteúdo de código e manifests é:

```
b6ea33dd1225e364dbdd44f5f3a12b95319873ef2763a8c16b25407adaae92c1
```

Esses resultados medem apenas esta amostra em HTTP local, com carga de leitura, dados pequenos e infraestrutura compartilhada. Não estabelecem a capacidade do CTIC, o máximo de participantes simultâneos, um tempo de resposta garantido ou resistência a toda falha possível. TLS, rede externa, proxy, logging de produção, SMTP, pagamentos, usuários reais e cargas prolongadas precisam de validações próprias. O comando registra resultados e invariantes; não otimiza configurações de produção automaticamente.
