# Comparação de pagamentos para o CHAGS 14

Pesquisa e atualização com captura fornecida pelo usuário: **06/10/2026**. O evento será em 12–16/07/2027. As condições abaixo foram consultadas em fontes oficiais e precisam ser reconfirmadas antes de contratar e abrir as cobranças.

## Recomendação

**Avaliar primeiro o Asaas, com uma condição decisiva: confirmar e testar o pagamento de participantes estrangeiros no checkout hospedado.** Seu cartão nacional tem custo padrão anunciado menor que o da Stripe, e o checkout documentado oferece PIX e cartão. Porém, cadastrar pagadores estrangeiros depende de liberação da conta em produção; isso não comprova, sozinho, que qualquer cartão estrangeiro funcionará ou que o checkout dispensará CPF brasileiro.

**Manter Stripe como alternativa**, especialmente pela documentação explícita de cartões internacionais e pelos mecanismos de integração. Seu preço brasileiro anuncia adicional para cartão internacional e PIX **somente por convite**. Confirmar que a organização conseguirá habilitar PIX antes de escolher Stripe como solução única.

**Mercado Pago passa a ser candidato prioritário junto ao Asaas**, com as taxas da captura fornecida pelo usuário: PIX de 0,99% e crédito de 4,98% na hora, 4,49% em 14 dias ou 3,98% em 30 dias. No exemplo de R$ 100, seu PIX tem o menor custo apresentado. O SDK oficial para Python está confirmado. A captura não informa endereço da página nem confirma aplicação ao checkout escolhido, parcelamento ou condições de cartões internacionais; esses pontos permanecem pendentes.

Nenhum serviço foi contratado, nenhuma conta foi criada e nenhuma integração de pagamento foi ativada. A organização pretende abrir uma conta bancária no Brasil; recebedor e provedor ainda estão indefinidos.

## O que estamos escolhendo

O gateway processa PIX/cartões, informa ao nosso backend o resultado e faz o repasse ao recebedor cadastrado. No fluxo proposto, o participante escolhe sua inscrição no site e paga em uma página hospedada pelo provedor. Dados de cartão ficam com esse serviço. Nosso Django guarda a inscrição, o valor, a situação e os documentos do evento.

A organização define o titular da conta do serviço e a conta bancária de repasse. Uma conta bancária no Brasil, sozinha, não informa se o cadastro será de pessoa física, entidade brasileira ou outro tipo de recebedor aceito pelo serviço.

## Comparação prática

| Critério | Asaas | Stripe | Mercado Pago |
| --- | --- | --- | --- |
| Checkout hospedado | Documentado com PIX e cartão de crédito. | Stripe Checkout documentado; métodos dependem da conta. | Checkout Pro é o candidato; documentação brasileira não pôde ser consultada nesta pesquisa. |
| PIX | Oferecido no checkout documentado. | Página brasileira de preços anuncia “somente por convite”; confirmar habilitação. | Captura anuncia 0,99%, na hora. Confirmar aplicação à integração e à conta. |
| Público estrangeiro | API tem `foreignCustomer`, com liberação necessária em produção. Confirmar cartões internacionais e fluxo sem CPF no checkout. | Preço publicado contempla cartões internacionais. Testar países/bandeiras do evento; disponibilidade não garante aprovação de toda transação. | Confirmar cartões internacionais, documentos exigidos e requisitos do recebedor. |
| Python/Django | API HTTP com documentação; podemos integrar em Python. | API HTTP e documentação de Checkout, eventos e chaves para evitar repetições. | SDK oficial exige Python 3.9+, compatível com a versão mínima do nosso Python 3.12; instalação e integração ainda não testadas. |
| Confirmação de pagamento | Documenta notificações ao backend e eventos de Checkout; retorno do navegador não confirma pagamento. | Documenta notificações com assinatura e requisições idempotentes. | Contrato de notificações e recuperação após falha ainda precisa de validação na documentação da integração escolhida. |
| Recebimento do cartão | Simulador anuncia parcela a cada 32 dias; antecipação tem condições e custo próprios. | Repasses no Brasil são automáticos e diários, **depois** de o saldo ficar disponível. Prazo de disponibilidade precisa ser confirmado por método/conta. | Captura anuncia crédito: 4,98% na hora; 4,49% em 14 dias; 3,98% em 30 dias. Confirmar prazo posterior de transferência bancária. |
| Principal condição para escolher | Liberação e teste do público estrangeiro. | PIX habilitado e aceitação do custo adicional internacional. | Verificar preços, público internacional e contrato técnico. |

Todas as opções exigem uma conta recebedora aprovada e condições comerciais confirmadas. Não há impedimento para continuar com Python/Django. A linguagem não é o critério principal desta decisão.

## Taxas publicadas e captura fornecida

Asaas/Stripe: valores pesquisados para checkout online/pagamento avulso. Mercado Pago: transcrição da captura do usuário; confirmar que são aplicáveis ao checkout escolhido. Não usar taxas de maquininha ou Asaas Tap como taxas deste site.

| Método | Asaas: padrão anunciado | Stripe: padrão anunciado no Brasil | Mercado Pago |
| --- | --- | --- | --- |
| PIX | **R$ 1,99** por transação recebida. | **1,19%** por PIX pago, somente por convite. | **0,99%**, na hora (captura). |
| Cartão nacional à vista | **2,99% + R$ 0,49**. | **3,99% + R$ 0,39**. | **4,98%** na hora; **4,49%** em 14 dias; **3,98%** em 30 dias (crédito na captura; confirmar condições). |
| Cartão internacional | Custo específico e elegibilidade não confirmados. Não aplicar automaticamente a taxa nacional. | Adicional de **2 pontos percentuais**; para o exemplo de cartão abaixo, **5,99% + R$ 0,39**. | Não verificado. |

Fontes de taxas: [Asaas — preços e taxas](https://www.asaas.com/precos-e-taxas) e [Stripe — preços no Brasil](https://stripe.com/br/pricing).

Mercado Pago: captura fornecida pelo usuário em 06/10/2026, sem endereço da página informado. A imagem também mostra boleto a **R$ 3,49**, em até 3 dias; saldo no Mercado Pago a **4,99%**, na hora; e cartão de débito virtual CAIXA a **3,99%**, na hora. Esses meios não são automaticamente incluídos no projeto. A indicação “na hora” não comprova transferência imediata para a futura conta bancária externa.


O Asaas também anuncia, por três meses, PIX de R$ 0,99 e cartão à vista de 1,99% + R$ 0,49. **Não usei essa promoção no planejamento:** o período pode terminar antes da venda de inscrições. As condições da conta podem diferir das taxas publicadas.

Antecipação, parcelamento, conversão cambial, notificações extras, contestações e serviços adicionais não estão incluídos na tabela. Para Asaas, a página anuncia antecipação de cartão a 1,25% ao mês, sujeita à análise de crédito; esse custo não está embutido no cartão padrão acima. Na Stripe, a página informa que as taxas de processamento da transação original não são devolvidas em reembolsos. Confirmar os termos aplicáveis ao evento em cada serviço.

### Exemplo de custo por inscrição

Valores **hipotéticos**, sem definir os preços do CHAGS 14. Cálculo por transação, com taxas padrão acima e arredondamento para centavos; exclui os custos adicionais mencionados. Na prática, o provedor pode aplicar suas próprias regras de arredondamento.

| Inscrição hipotética | Asaas PIX | Stripe PIX, se habilitado | Asaas cartão nacional à vista | Stripe cartão nacional | Stripe cartão internacional |
| --- | --- | --- | --- | --- | --- |
| R$ 100,00 | R$ 1,99 | R$ 1,19 | R$ 3,48 | R$ 4,38 | R$ 6,38 |
| R$ 250,00 | R$ 1,99 | R$ 2,98 | R$ 7,97 | R$ 10,37 | R$ 15,37 |
| R$ 500,00 | R$ 1,99 | R$ 5,95 | R$ 15,44 | R$ 20,34 | R$ 30,34 |

Com essas taxas, o PIX percentual da Stripe tem custo menor abaixo de aproximadamente **R$ 167,23**; acima disso, o PIX fixo do Asaas tem custo menor. Isso só é útil se o PIX estiver liberado e sem outras condições na conta. Cartão internacional Asaas/Mercado Pago segue sem simulação específica: a captura não estabelece taxa ou elegibilidade para cartões estrangeiros.

### Exemplos com as taxas da captura do Mercado Pago

| Inscrição hipotética | PIX (0,99%) | Crédito na hora (4,98%) | Crédito em 14 dias (4,49%) | Crédito em 30 dias (3,98%) |
| --- | --- | --- | --- | --- |
| R$ 100,00 | R$ 0,99 | R$ 4,98 | R$ 4,49 | R$ 3,98 |
| R$ 250,00 | R$ 2,48 | R$ 12,45 | R$ 11,23 | R$ 9,95 |
| R$ 500,00 | R$ 4,95 | R$ 24,90 | R$ 22,45 | R$ 19,90 |

Com as taxas apresentadas, o PIX Mercado Pago tem menor custo que o PIX Stripe (0,99% versus 1,19%, se habilitado). Comparado ao PIX fixo de R$ 1,99 do Asaas, o ponto de igualdade é aproximadamente **R$ 201,01**: abaixo disso Mercado Pago tem menor custo; acima disso Asaas tem menor custo, antes de arredondamentos e outras condições. Os exemplos de crédito não incluem parcelamento, acréscimo internacional ou conversão cambial.

A organização deve decidir se absorve as taxas ou estabelece outra política aprovada. O backend não acrescentará taxas ao preço do participante por suposição.

## O que confirmar antes da escolha

A organização pode usar estas perguntas na conversa com os provedores:

1. **Recebedor:** qual titular será cadastrado, quais documentos e tipo de entidade são aceitos e qual conta bancária pode receber os repasses? Quem administrará a conta?
2. **Estrangeiros:** vocês aceitam os cartões e países dos nossos participantes? É possível pagar sem CPF brasileiro? Há liberação especial, coleta de outros documentos ou taxa adicional? O checkout pode ser usado em inglês/espanhol?
3. **PIX:** estará habilitado nessa conta desde a abertura? Há convite, análise, mínimo de cobrança ou outro requisito?
4. **Moeda:** pretendemos cobrar em reais e receber no Brasil; como funciona o cartão emitido no exterior? Quais custos de câmbio são do organizador e quais dependem do banco do participante?
5. **Custos reais:** qual tabela valerá durante as inscrições de 2027, incluindo antecipação, parcelamento, contestações, reembolso e repasse? Qual prazo até o dinheiro ficar disponível e chegar à conta bancária?

A configuração deve respeitar preços e regras aprovados pela comissão. A existência de BRL/USD/EUR no nosso modelo não comprova que o provedor aceitará esses métodos em todas essas moedas. O PIX documentado da Stripe usa BRL; a escolha inicial mais simples a avaliar é cobrar em reais, caso a organização aprove.

## Validação técnica depois da escolha

Usar primeiro o ambiente de testes, com credenciais privadas, e demonstrar:

- Uma inscrição paga por PIX e outra por cartão, com confirmação autenticada recebida pelo backend.
- Fluxo de participante estrangeiro sem CPF brasileiro, nos métodos e idiomas necessários. Confirmar por escrito a habilitação de produção; testes simulados não comprovam aprovação de cartões reais.
- Consulta do estado depois de uma interrupção, repetição da requisição sem duplicar cobrança e eventos duplicados sem gerar dois recibos.
- Política de cancelamento/reembolso aprovada e prazos de repasse conhecidos.

No Asaas, a documentação orienta consultar o resultado anterior antes de repetir uma criação. Não tratar `externalReference` como garantia automática de unicidade. Na Stripe, a API documenta chaves de idempotência para evitar repetir a mesma operação. O contrato exato será verificado para o endpoint escolhido.

Essas verificações são critérios de implementação; ainda não foram executadas contra contas dos provedores. Não fornecer credenciais em chat/Git e não marcar inscrição como paga por parâmetro de retorno do navegador. Não é necessário aguardar a conta bancária definitiva para estudar ou testar a API, mas aprovação do recebedor e métodos em produção são necessárias antes de cobrar participantes reais.

## Fontes e limites da pesquisa

Fontes oficiais lidas em 06/10/2026:

- [Asaas — preços e taxas](https://www.asaas.com/precos-e-taxas).
- [Asaas — Checkout](https://docs.asaas.com/docs/checkout-asaas).
- [Asaas — criar cliente, incluindo pagador estrangeiro](https://docs.asaas.com/reference/criar-novo-cliente).
- [Asaas — dados do cliente no Checkout](https://docs.asaas.com/docs/como-informar-os-dados-do-cliente).
- [Asaas — FAQ do Checkout](https://docs.asaas.com/docs/faq-do-asaas-checkout).
- [Asaas — retries e idempotência](https://docs.asaas.com/docs/retries-e-idempot%C3%AAncia).
- [Stripe — preços no Brasil](https://stripe.com/br/pricing).
- [Stripe — Checkout](https://docs.stripe.com/payments/checkout).
- [Stripe — PIX](https://docs.stripe.com/payments/pix).
- [Stripe — repasses](https://docs.stripe.com/payouts).
- [Stripe — idempotência](https://docs.stripe.com/api/idempotent_requests).
- [Stripe — notificações ao backend](https://docs.stripe.com/webhooks).
- [Mercado Pago — SDK oficial para Python](https://github.com/mercadopago/sdk-python).

Fonte complementar: captura de taxas Mercado Pago enviada pelo usuário em 06/10/2026, sem endereço da página. A captura foi transcrita; condições específicas da conta e do checkout permanecem a confirmar.

Páginas oficiais que retornaram HTTP 403 na pesquisa automatizada:

- [Mercado Pago — taxas do Checkout](https://www.mercadopago.com.br/ajuda/Quanto-custa-receber-pagamentos-com-Checkout_220).
- [Mercado Pago — Checkout Pro](https://www.mercadopago.com.br/developers/pt/docs/checkout-pro/overview).

A documentação de cadastro de estrangeiros Asaas não estabelece, por si só, compatibilidade completa do checkout escolhido. A página de preços da Stripe informa PIX por convite mesmo havendo documentação pública do método. Por isso, essas condições aparecem como pendências concretas da escolha.

**Esforço:** médio para comparação e requisitos; alto para implementar/revisar pagamentos e conciliação; leve para ajustes simples de texto depois das decisões.
