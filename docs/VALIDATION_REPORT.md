# HITA A0 v0.2 — Relatório de validação local

**Data:** 2026-11-14. **Modo:** exclusivamente sintético, local, sem ordens.

## Observado na execução

| Controlo | Resultado |
|---|---|
| Testes Python unittest | **62/62 PASS** |
| Cobertura base GOLD | 21 cenários principais (não é aprovação independente) |
| Testes adicionais de variantes | 12 |
| Testes estruturais originais | 12 |
| Testes adversariais novos | 17 |
| Tabelas SQLite STRICT | 30 |
| Migrações iniciais | 5 |
| Demo GOLD-001 | 12 eventos, 23 postings, 35 ações, NAV EUR 18.811 |
| `PRAGMA integrity_check` demo | ok |
| `PRAGMA foreign_key_check` demo | 0 violações |
| `audit` read-only | PASS_LOCAL_CHECKS |
| Teste de reversão dupla | Segunda reversão rejeitada; quantidade/custo intactos |
| Revisão independente | 0/21 aprovados |

Ver `artifacts/test_report.txt`, `artifacts/gold001_demo_result.json`, `artifacts/gold001_audit_result.json` e `docs/TECHNICAL_REVIEW_v0.2.md`.

## O que isto não demonstra

Os 62 testes foram preparados e executados pela mesma equipa lógica que desenvolveu o código; não são um oracle independente. Não foi verificada a totalidade de variantes, segurança perante modificações SQL privilegiadas, importação real, bitemporalidade completa, segurança de credenciais ou estabilidade de modelos de investimento. Os SHA-256 de payloads normalizados não garantem a autenticidade dos bytes de origem.

O projeto conserva abertos **G01-C05/C06/C10**, **VAL-B01–B06**, **FCA-001–012**, **CAR-001–011** e **OPEN-01–26**. `DESIGN_FROZEN` permanece não concedido.

**Decisão:** protótipo sintético local testado; A0 não certificado, A1/B/C/D não liberados para dados reais.
