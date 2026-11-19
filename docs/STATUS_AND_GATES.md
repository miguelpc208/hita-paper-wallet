# HITA A0 — Estado e gates em 2026-11-14

**Última validação local:** A0 v0.2, 62/62 testes aprovados, cinco migrações, 30 tabelas.
**Auditoria local read-only:** ver artifacts/gold001_audit_result.json.
**Defeito crítico corrigido:** duplicação de reversão de venda liquidada; consulta de custo por lote não usa alocações STAGED.
**Não confundir:** testes internos ≠ revisão independente ≠ DESIGN_FROZEN ≠ certificação técnica de release.

## Registos

- Gate 01: aprovado condicionalmente.
- G01-C05/C06/C10: ABERTOS.
- VAL-B01–B06: ABERTOS.
- FCA-001–012 e CAR-001–011: ABERTOS.
- OPEN-01–26: ABERTOS.
- Oracles aprovados independentemente: 0/21.
- A0 Implementation: protótipo interno, sintético, sujeito a revisão.
- A1 real data, B/C/D operacional: não autorizados.
- DESIGN_FROZEN: NÃO concedido.
- Execução de ordens: inexistente e não autorizada.

## Próximos gates

1. Congelar fontes e os manifests completos de todas as variantes; revisão de referência independente.
2. Inspeção independente de DDL e todas as regras cross-row.
3. Testes sintéticos adicionais de temporalidade, custos parciais, recuperação e writes privilegiados.
4. Decisão explícita de Design Freeze e certificação posterior; nenhuma transição automática.

Ver TECHNICAL_REVIEW_v0.2.md para riscos e limitações.
