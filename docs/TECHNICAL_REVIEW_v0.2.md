# HITA A0 v0.2 — Revisão adversarial e decisões

**Data:** 2026-11-14  
**Amostra:** protótipo A0 v0.1 de 30 tabelas e cinco migrações.  
**Limite de confiança:** revisão e testes internos realizados pelo mesmo sistema de desenvolvimento; não são sign-off independente.

## Defeito material confirmado em v0.1

Uma venda liquidada de cinco ações, sobre uma posição de dez ações com custo agregado de 800 €, podia ser revertida duas vezes por operações de correção com nomes diferentes. O segundo evento repunha outras cinco ações e 400 € de custo, elevando a posição para **15 ações e 1.200 €**. Este erro passava nos 45 testes originais.

**Tratamento em v0.2:** validação dentro da transação `BEGIN IMMEDIATE` impede segunda `REVERSE_SETTLED_SELL` aceite para a mesma venda. O caso de duplicação pelo mesmo ID mantém idempotência. A bateria de regressão passou. Um novo trigger de base de dados não foi introduzido: um escritor SQL privilegiado permanece fora da garantia do protótipo.

## Outras correções

| Área | Situação anterior | Contrato v0.2 | Evidência local |
|---|---|---|---|
| Custos por lote | Alocação STAGED podia afetar `lot_cost` | Só alocações de eventos ACCEPTED contam | Teste de alocação STAGED |
| Opening parcial | Cost known do agregado misturado com lotes UNKNOWN | Rejeição até existir PARTIALLY_KNOWN plenamente suportado | Teste de rollback de opening |
| FIFO em lotes com mesma data | Dependia de recorded_at ou UUID como desempate | Ordem estável da referência de origem e ordinal | GOLD-006 multilot |
| Rollback | Número temporal interno avançava após falha | Recupera o contador anterior | Teste de rollback temporal |
| Reabertura | Sequência temporal reiniciava em T1 | Contador recuperado da evidência de eventos aceites | Teste com ficheiro SQLite temporário |
| Cutoff | Era possível aceitar lançamento pré-abertura | Rejeição anterior a T0; igualdade exige exclusão no manifest sintético | Testes antes/exatamente T0 |
| Timestamp | Aceitava texto não-canónico ou inválido | UTC ao segundo, sintético | Testes de formatos inválidos |
| Quantum monetário | Permitia postings monetários subcêntimo | Apenas cent quantum em EUR/USD | Testes de rejeição |
| Auditoria | Apenas `inspect` estrutural | `audit` read-only com verificações de integridade cruzada | Testes de hash adulterado e lote |

## Limitações intencionais

- O writer Python e a auditoria local não são autoridades independentes; os resultados esperados são do mesmo projeto.
- A implementação não executa todas as variantes do manifesto, sobretudo bitemporalidade, accounting parcial de custos, corporate actions complexas e importação de dados reais.
- A auditoria consegue verificar hashes de payloads normalizados armazenados, **não** comprovar a autenticidade dos bytes originais dos artefactos sintéticos, que não estão materializados como fontes imutáveis externas.
- Acesso privilegiado direto à base pode contornar parte dos validadores cross-row.
- O modelo de datas económicas ao segundo e o quantum de cêntimo EUR/USD são restrições de A0 sintético; não devem ser aplicados cegamente a brokers reais.
- Não foram realizadas revisões independentes dos 21 oracles nem aprovação formal do desenho SQLite.

## Critérios para próxima fase

1. Concluir matrizes de variantes GOLD e manifestos source-byte/UUID verificáveis.
2. Obter revisão financeira independente com cálculo próprio dos valores esperados.
3. Rever as constraints físicas e caminhos de escrita, incluindo risco de SQL privilegiado.
4. Fechar G01-C05/C06/C10 e os bloqueios associados com evidência apropriada.
5. Apenas após gate explícito considerar dados reais ou módulos B/C/D para utilização operacional.

**Estado:** A0 v0.2 — protótipo sintético reforçado, não certificado.
