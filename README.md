> **Windows v0.2.2:** A ordem de `obligation(index=...)` usa `recognized_at` em vez de `recorded_at`; elimina flutuações quando o Windows atribui o mesmo timestamp a compras sucessivas. O teste de regressão força timestamps iguais. `test_gold.py` continua a conter a correção WinError 32 da v0.2.1.

> **Windows users (v0.2.1):** A prior GOLD-012 test used `unittest.addCleanup` for a SQLite connection inside `TemporaryDirectory`. On Windows this can produce `WinError 32` during temporary directory removal despite successful financial assertions. v0.2.1 closes that SQLite connection before temporary-file cleanup. Run tests with `python -m unittest discover -s tests -v` from the project root in the `hita-a0` Conda environment. This patch does not modify financial logic or schema.

# HITA — A0 Synthetic Financial Foundation

For the synthetic recommendation and paper-wallet module, see [HITA synthetic paper wallet](docs/PAPER_WALLET.md). For the walk-forward research command and its results, see [Synthetic research baseline](docs/RESEARCH_BASELINE.md). See the [development roadmap](docs/ROADMAP.md) for upcoming milestones and acceptance criteria. Windows users can follow the [setup and verification guide](docs/WINDOWS_SETUP.md). The [synthetic reconciliation guide](docs/SYNTHETIC_RECONCILIATION.md) explains how to trace exceptions. The sections below describe the A0 baseline.

The [versioned market data guide](docs/VERSIONED_MARKET_DATA.md) shows how to replay quotes, corporate actions, and corrections at a knowledge cutoff.

**Estado:** protótipo técnico local A0, verificável com dados exclusivamente sintéticos. **Não é uma aplicação de trading operacional, não está certificada independentemente e não permite ordens financeiras.**

## Objetivo

Reconstruir posições, caixa liquidada, receivables/payables, lotes FIFO, custo de aquisição, reversões e NAV sintético a partir de operações financeiras rastreáveis, sem contar duas vezes transferências, lotes ou comissões.

### Implementado

- Cinco migrações versionadas; exatamente **30 tabelas SQLite STRICT** (das 55 candidatas previstas no desenho global R3).
- `PRAGMA foreign_keys=ON`, PK/FK, chaves compostas para evento/conta/template e lote/conta/instrumento, checks XOR para origens, constraints de estado e triggers de imutabilidade de eventos aceites.
- Migrações transacionais com checksums do SQL e hash do esquema SQLite observado.
- Contabilidade com `Decimal` e formatos canónicos; cálculo monetário EUR/USD sintético com arredondamento `ROUND_HALF_EVEN` no limite de booking.
- Abertura com holdings, custos conhecidos ou desconhecidos, obrigações pendentes e reconciliação sintética.
- Compras, vendas FIFO, liquidação parcial, cancelamento de obrigações externas, split/reverse split, dividendos, juros, comissões, impostos, fluxos externos, FX, transferências entre contas e reversão integral de operações liquidadas.
- Identificação de transações sintéticas com UUIDv5, proveniência da fonte, aceitação atómica com `BEGIN IMMEDIATE`, reimportação idempotente e bloqueio de conflitos.
- Estado de transferência interna incompleta que impede certificação do NAV consolidado.
- **63 testes locais:** um cenário principal para cada GOLD-001 a GOLD-021, variantes e testes de integridade/concorrência/rollback.

### Não implementado / não certificado

- Os 25 modelos físicos das releases posteriores, fontes reais, contratos de mercado ou importadores de contas/brokers.
- Dashboard, gestão de risco Core/Satellite, modelos de research, recomendações, agentes IA, backtesting ou paper trading.
- Conclusão independente dos 21 oracles, dos manifests byte-a-byte, da revisão física ou de `DESIGN_FROZEN`.
- Todas as variantes documentais e todos os casos de origem/correção exóticos. Os testes automatizados cobrem os cenários-base e um subconjunto explícito das variantes.
- Garantia de imutabilidade face a administradores de sistema/ficheiros ou conexão SQLite não autorizada. A proteção implementada é um controlo de aplicação/DDL para um protótipo local.
- Ficheiros de fonte com todos os manifests imutáveis assinados por terceiros; os hashes locais não são assinatura independente.

## Executar no Windows (PowerShell)

Requer **Python 3.11+** com a biblioteca SQLite compilada na versão **3.37+**. Não há dependências de execução de terceiros nem rede.

```powershell
# Extrair o ZIP e entrar na pasta HITA-A0
cd HITA-A0

# Confirmar o runtime
py -3.11 -c "import sqlite3; print(sqlite3.sqlite_version)"

# Executar todos os testes, localmente
py -3.11 -m unittest discover -s tests -v

# Criar um exemplo persistido APENAS com dados sintéticos
py -3.11 -m hita_a0.cli demo --db artifacts\demo_local.sqlite

# Inspecionar a integridade física do exemplo
py -3.11 -m hita_a0.cli inspect --db artifacts\demo_local.sqlite
```

Comandos alternativos se `py` não estiver disponível: `python -m ...`.

O comando `schema` cria apenas as 30 tabelas A0, sem operações financeiras:

```powershell
py -3.11 -m hita_a0.cli schema --db artifacts\schema_vazia.sqlite
```

Se usar `demo` num ficheiro onde já existam os eventos da demonstração, a abertura inicial será rejeitada; crie um ficheiro novo para um novo teste. Não use ficheiros de contas reais.

## Resultado GOLD-001 validado localmente

| Indicador | Resultado |
|---|---:|
| Caixa BRK | 13.111 € |
| Caixa BNK | 1.500 € |
| Ações EQ1 | 35 |
| Valor da posição ao preço sintético 120 € | 4.200 € |
| Custo remanescente | 1.751,75 € |
| Ganho realizado | 347,75 € |
| NAV consolidado sintético | **18.811 €** |
| Eventos aceites | 12 |
| Postings aceites | 23 |

O exemplo parte de 16.000 € de património inicial e tem valorização líquida sintética de 2.811 €, sem adicionar as transferências próprias aos rendimentos.

## Estrutura

```text
HITA-A0/
├── hita_a0/
│   ├── money.py          # Decimal e quantização
│   ├── schema.py         # Migrações transacionais e fingerprint
│   ├── ledger.py         # Subledger, lotes, obrigações e reconstrução
│   └── cli.py            # schema / demo / inspect
├── migrations/           # 001 ... 005 SQL (30 tabelas)
├── tests/
│   ├── test_gold.py      # GOLD-001 a GOLD-021
│   ├── test_variants.py  # Variantes negativas e temporais
│   └── test_hardening.py # Reversões duplicadas, cutoff, proveniência e auditoria read-only
│   └── test_integrity.py # SQLite/FK/immutabilidade/rollback
├── docs/
│   ├── STATUS_AND_GATES.md
│   └── ROADMAP.md
├── artifacts/
│   ├── gold001_demo.sqlite (sintético)
│   ├── gold001_demo_result.json
│   └── test_report.txt
└── pyproject.toml
```

## Invariantes centrais

**Cash** = settled opening cash + accepted `SETTLED_CASH` postings.

**Holdings** = opening quantity + accepted `SECURITY_QTY` postings.

**Lot quantity** = lot opening quantity (para opening lots) + accepted lot-specific quantity postings; o lote de compra é explicado pelo posting da execução, não somado duas vezes.

**Obligation** = original amount - settlements - cancellations, com estados `OPEN`, `OPEN_PARTIAL`, `SETTLED_FULL`, `CANCELLED_FULL` e `CLOSED_MIXED`.

**NAV sintético** = cash + securities × preço sintético + receivables - payables, com conversão explícita de moeda. Um preço ausente ou transferências internas em trânsito bloqueiam o resultado certificável.

## Limites de segurança

Sem `requests`, sem conexões externas, sem credenciais e sem funções de emissão de ordens. O CLI opera exclusivamente sobre o caminho SQLite local fornecido e apenas com o conjunto de dados sintéticos.

**Nenhuma utilização financeira real está autorizada** por esta entrega. A primeira utilização de dados reais e qualquer integração externa exigem revisão, consentimento e fase própria.

## Governação

O facto de os testes executados localmente terem passado **não constitui aprovação independente**, `DESIGN_FROZEN` ou certificação técnica de release. G01-C05/C06/C10, VAL-B01–B06, FCA-001–012, CAR-001–011 e OPEN-01–26 mantêm o estatuto pendente até decisões formais e evidência de revisão apropriada. Ver `docs/STATUS_AND_GATES.md`.

## Atualização de qualidade A0 v0.2 (2026-11-14)

Esta revisão substitui o código v0.1 para experiências **exclusivamente sintéticas**, sem mudar as cinco migrações nem o catálogo de 30 tabelas. Corrige a possibilidade de reverter uma venda liquidada duas vezes (anteriormente gerava 15 ações e custo 1.200 € a partir de uma abertura de dez ações e custo 800 €), impede que alocações STAGED alterem o custo reconhecido, evita misturar custos de abertura conhecidos/desconhecidos sem contrato parcial aprovado, estabiliza o FIFO de lotes de abertura e melhora o comportamento após rollback e reabertura.

No A0 v0.2, eventos precisam de um opening anchor sintético certificado e timestamps económicos em UTC ao segundo. Eventos anteriores ao cutoff são rejeitados; eventos exatamente no cutoff exigem uma exclusão explícita no manifest sintético (`excluded_ops`). Lançamentos monetários EUR/USD têm quantum de cêntimo; não se altera a precisão de preços/quantidades. Estes são limites conservadores do protótipo, **não** um motor temporal/monetário completo para dados reais.

Ferramenta adicional de inspeção **só de leitura** (não certificação independente):

```powershell
py -3.11 -m hita_a0.cli audit --db artifacts\gold001_demo.sqlite
```

O resultado deve indicar `PASS_LOCAL_CHECKS`, 30 tabelas, cinco migrações, 12 eventos e 23 postings para esta demo. O comando falha (código 2) se identificar uma violação de integridade auditada. Ver `docs/TECHNICAL_REVIEW_v0.2.md` e `docs/VALIDATION_REPORT.md`.

**Limites mantidos:** não há assinatura de revisor independente, não existe uma garantia física contra escritores SQLite privilegiados, nem certificação completa das variantes/manifests. `DESIGN_FROZEN` não foi concedido.
