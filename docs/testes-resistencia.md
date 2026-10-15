# Ensaio HTTP de carga e recuperação do backend

O comando `backend/scripts/check-resilience.py` verifica o backend por HTTP real em um ambiente descartável criado pelo próprio comando. Não recebe URL, banco ou credenciais de destino. Não deve ser adaptado para apontar para produção ou para um banco com participantes reais.

## O que é exercitado

1. Cria um PostgreSQL novo com a imagem oficial fixada por digest do helper de CI, com porta aleatória acessível somente em `127.0.0.1`. A role da aplicação não é superusuária nem pode criar roles.
2. Aplica as migrações e cria cinco participantes sintéticos já confirmados, um evento e um preço fictício de R$ 9,99. Não são preços ou regras aprovadas pela organização. A carga não depende de enviar confirmações reais por e-mail.
3. Inicia Gunicorn com dois workers e quatro threads por worker, escutando somente em um socket local reservado pelo processo. Usa cookies de sessão e CSRF obtidos pela API; mantém os validadores, o custo normal de senha e os limites de tentativas.
4. Submete quatro cadastros para o mesmo endereço sintético: espera três respostas genéricas `202`, um bloqueio `429` e exatamente uma conta/perfil/token/mensagem na fila.
5. Faz doze logins simultâneos da mesma conta previamente autenticada uma vez. Espera nove sucessos e três bloqueios por limite de identidade, com `Retry-After`. Não desabilita o limitador para produzir números maiores.
6. Envia dezesseis pedidos de inscrição de quatro pessoas, incluindo repetições concorrentes. Exige quatro criações e doze reutilizações, quatro identificadores distintos e uma auditoria por inscrição.
7. Alterna consultas autenticadas de evento, preços e inscrição por uma duração limitada, primeiro com um cliente e depois com a concorrência escolhida.
8. Verifica os dados no banco, impede novos logins da role descartável e encerra suas conexões existentes. Isso simula perda real de acesso da aplicação ao banco; não desliga o serviço PostgreSQL e não simula todos os tipos de falha de rede/disco.
9. Durante essa falha, exige liveness `200` e readiness `503`, com e sem cookie de sessão. Uma tentativa de inscrição da quinta pessoa precisa falhar com `500` ou `503`, sem gravar uma inscrição.
10. Restaura o acesso, verifica prontidão, sessões anteriores e inscrições. Uma nova tentativa da quinta pessoa deve criar somente uma inscrição, preservando valores e histórico das quatro anteriores. O Gunicorn não é reiniciado para obter essa recuperação.
11. Encerra o servidor e remove contêiner, volume e arquivos privados. O relatório confirma a limpeza. Os e-mails permanecem somente na fila sintética até o banco ser descartado; não há worker de envio, cobrança ou emissão de certificado.

O perfil usa `DEBUG=False`, HTTP local e conexão persistente por até 60 segundos, com verificação de saúde da conexão ativa. Herda as demais regras dos settings de desenvolvimento, sem importar as credenciais do desenvolvedor ou da produção. HTTPS, TLS do banco, logging de produção, proxy do CTIC e serviços externos não fazem parte desta medição.

## Execução

No ambiente Linux com Docker e as dependências do backend instaladas:

```bash
.venv/bin/python backend/scripts/check-resilience.py --concurrency 8 --duration 10 --report backend/.local/resilience-report.json
```

`--concurrency` aceita 2, 4 ou 8, com padrão 4. `--duration` aceita 5 a 30 segundos por nível de leitura, com padrão 10. O tempo completo também inclui preparação, login, escrita, interrupção e limpeza. Cada chamada HTTP tem timeout de 15 segundos. Não existe parâmetro para apontar a outro servidor; o cliente ignora proxies herdados e não segue redirecionamentos.

O relatório é JSON sanitizado, mostrado no terminal e gravado no arquivo solicitado. Um arquivo já existente não é sobrescrito: escolha outro nome. Sem `--report`, um nome novo é criado em `backend/.local/resilience-reports/`. Cookies, senhas, códigos, endereços de participantes e corpos de respostas não entram no relatório. Arquivos intermediários ficam em um diretório privado por execução, independente do banco local e do estado normal do CI.

Código de saída zero exige aprovação de todos os cenários e limpeza concluída. Em falha, consulte `failed_stage`, `error_type` e `cleanup`. Se a limpeza não for possível, o estado privado de propriedade é preservado no diretório indicado por `cleanup_state_directory`, para tratar a falha do Docker sem remover contêineres de outras execuções. SIGINT/SIGTERM tentam a limpeza; encerramento forçado da máquina não oferece essa garantia.

## GitHub e Windows

O workflow **Backend Resilience** executa o mesmo ensaio em Linux nos pushes/PRs que alterem o backend ou o workflow. O job **HTTP concorrente e recuperação** usa duração de cinco segundos por nível e concorrência quatro. Também admite disparo manual por **Actions → Backend Resilience → Run workflow** após estar disponível na branch padrão. O JSON aparece no passo de execução; arquivos privados não são publicados como artefatos.

O workflow tem permissão somente de leitura, ações fixadas por SHA e não usa segredos de SMTP, pagamentos ou produção. Push e PR da mesma branch compartilham o grupo de concorrência; a execução anterior pode ser cancelada e substituída. A máquina do GitHub é descartada ao final, inclusive em cancelamentos. O workflow não faz deploy, merge ou configuração de proteção da `main`.

O runner usa recursos do Linux, como socket herdado e grupos de processos. Não precisa instalar Docker no Windows para trabalhar no backend: consulte a execução do GitHub ou use o ambiente de nuvem. Os oito testes novos de regressão/segurança também fazem parte da suíte Django comum, que pode ser executada no seu Windows com PostgreSQL local.

## Como interpretar as métricas

Cada fase informa quantidade de chamadas alvo, status HTTP, tempo decorrido, chamadas por segundo e latências p50/p95/máxima. Os percentis usam o método de posto mais próximo (`ceil(p × N)`). As latências incluem abertura da conexão HTTP local e leitura da resposta. Nas fases de cadastro/login, a obtenção prévia de CSRF fica fora da amostra de latência, mas seu tempo entra no tempo decorrido da fase.

O modelo é de clientes fechados: cada cliente aguarda uma resposta antes da próxima chamada. Não mede chegada ilimitada de pessoas nem volume de visitantes reais. Leituras não incluem login em cada requisição. Os cenários de escrita têm amostras pequenas, destinadas a provar invariantes e limites, não a estimar percentis estáveis de produção.

Os `429` dessas duas fases são esperados e precisam ocorrer nas quantidades especificadas. O erro `500/503` durante a interrupção também é esperado e separado dos resultados normais. Fora dessa interrupção, respostas inesperadas, timeouts, duplicidades ou snapshots alterados reprovam o ensaio. Não há meta de latência inventada: os tempos são registrados para comparação em condições semelhantes, sem gate de capacidade comercial.

As CPUs visíveis e versões são metadados do ambiente; não representam recursos dedicados ou uma reserva de capacidade. O hash `source_sha256` identifica o conteúdo dos arquivos Python e manifests do backend usados naquela execução, excluindo arquivos privados e caches; não equivale necessariamente ao commit da `main`.

## Correção comprovada nesta entrega

Antes da correção, uma requisição autenticada a `/health/live/` fazia duas consultas de sessão/usuário pelo middleware de idioma. Ao perder acesso ao banco, retornava erro em vez de informar que a aplicação continuava viva. Os dois testes de regressão falharam, e o ensaio HTTP também reprovou na interrupção.

O middleware agora verifica primeiro se a rota exige preferência de idioma, antes de carregar o usuário. Isso mantém a liveness independente do banco mesmo com cookie de sessão. Após a correção, o ensaio preservou `200` na liveness e `503` na readiness durante a falha e retomou o acesso às sessões após a restauração.

Resultados e limites da medição local estão em [resultados do ensaio](resultados-resistencia.md). A capacidade e os timeouts adequados da implantação precisam ser medidos na infraestrutura do CTIC com o volume esperado definido pela organização.
