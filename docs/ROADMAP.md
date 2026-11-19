# HITA — Roadmap por releases

## A0 — Synthetic Foundation

**Nesta entrega:** protótipo local executável com esquema de 30 tabelas, cinco migrações e 62 testes. Próximo gate: revisão independente dos 21 oracles, cobertura exaustiva das variantes, aceitação do desenho físico, imutabilidade e adversarial/backup tests; `DESIGN_FROZEN` e release certification ainda pendentes.

## A1 — Certified Portfolio Ingestion

Após autorização e revisão apropriadas: contrato de acesso a extratos, importadores segregados, deduplicação real, reconciliador de holdings/cash/pending, observações de mercado e FX com proveniência e confidencialidade. Sem ordens.

## B — Portfolio Risk

Construir denominadores de capital e risco, alocação Core/Satellite, cash floor separado, drawdowns, limites de posição e trade risk, estado de bloqueio e explicações de decisões; testes com carteiras sintéticas antes de dados reais.

## C — Market & Quant Research

Definir PIT dataset, corporate-action adjusted prices, experiment registry, backtests com custos/fricções, walk-forward, leakage tests, modelos quant e análise de robustez. Modelos de recomendação nunca controlam a carteira sem aprovação humana.

## D — Paper Trading & Decision Agent

Simulação de decisões, diário e comparação de sinais, limites, resultados hipotéticos e monitorização. **Nenhuma execução real de ordens.**

## Segregação de gates

Documentary acceptance ≠ independent sign-off ≠ executable test pass ≠ design freeze ≠ production clearance. Cada gate exige evidência separada.
