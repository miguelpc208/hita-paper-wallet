## v0.2.2 — Windows GOLD-001 obligation-order fix

- Ordenação estável de obrigações pela data de reconhecimento económico, em vez de `recorded_at` (que pode empatar no Windows).
- Adicionado teste que força `recorded_at` igual em duas compras e verifica a seleção das duas obrigações e respetiva liquidação.
- Preserva a correção GOLD-012 de fecho da ligação SQLite da v0.2.1, 30 tabelas, cinco migrações e dados apenas sintéticos.

## v0.2.1 — Windows concurrency-test cleanup fix (2026-11-14)

- Fix GOLD-012 test teardown on Windows: the SQLite verification connection now closes inside the TemporaryDirectory context, before `race.sqlite` is deleted.
- Replace delayed `unittest.addCleanup(verify.close)` with `try/finally: verify.close()`.
- Assert that both settlement worker threads have stopped before the verification database is opened.
- No ledger/accounting logic, SQL migration, or financial expectation changes.
- Local Linux execution of the 62 tests: PASS. Windows re-run still required on target machine.

# Changelog

## 0.2.0 — 2026-11-14

- Corrigida a segunda reversão de uma mesma venda liquidada: rejeição atómica e teste adversarial.
- Custo de lote calculado exclusivamente a partir de alocações de eventos ACEITES, não STAGED.
- Opening com mistura de custos conhecidos e desconhecidos bloqueado até existir contrato PARTIALLY_KNOWN.
- FIFO determinístico para lotes de abertura, com ordinal na referência de origem.
- Opening obrigatório e certificado antes de eventos financeiros; timestamps económicos UTC e cutoff com exclusão explícita.
- Counter de eventos sintéticos recuperado após rollback e reabertura do SQLite.
- Quantum de cêntimo e moedas EUR/USD sintéticas com rejeição fail-closed.
- CLI `audit` estritamente read-only: fingerprint, chaves, hash de payloads normalizados, quantidades/lotes, obrigações e reversões duplicadas.
- Testes locais ampliados de 45 para 62; esquema de 30 tabelas e cinco migrações preservado sem DDL novo.
- A0 continua sem aprovação independente, Design Freeze, dados financeiros reais ou execução de ordens.

# Changelog

## 0.1.0 — 2026-11-14

- Primeiro protótipo A0 de subledger exclusivamente sintético.
- 30 tabelas STRICT em cinco migrações com verificações de hash e integridade.
- Ledger contabilístico com `Decimal`, lotes, obrigações, settle/cancel, compra/venda, rendimentos, FX, split e reversões integrais.
- Guarda de NAV contra transferências internas em trânsito.
- 21 testes GOLD principais, 12 testes estruturais e 12 testes adicionais de variantes; 45 testes totais.
- Demo persistido GOLD-001 e relatório de execução.

Não representa aprovação independente nem conclusão das restantes releases HITA.
