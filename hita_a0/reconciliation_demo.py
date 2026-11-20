"""Deterministic fixtures for reviewing synthetic reconciliation results."""

from .ledger import Ledger
from .reconciliation import compare_reconciliation
from .reconciliation_ledger import project_reconciliation


EFFECTIVE_AT = "2026-01-12T00:00:00Z"
STATEMENT_AVAILABLE_AT = "2026-01-12T02:00:00Z"
KNOWN_AS_OF = "2026-01-12T03:00:00Z"

# Statement observations are declared independently of the ledger projection.
# EUR cash = 1000 - 100 + 10, quantity = 8 + 3 - 2,
# payables = 40 - 15 + (300 + 1) - 100, receivables = 20 - 10 + (240 - 2).
_STATEMENT_ROWS = {
    "matched": (
        ("CASH", "EUR", "910.00"), ("HOLDING", "EQ1", "9"),
        ("PAYABLE", "EUR", "226.00"), ("RECEIVABLE", "EUR", "248.00"),
    ),
    "breaks": (
        ("CASH", "EUR", "900.00"), ("CASH", "USD", "20.00"),
        ("HOLDING", "EQ1", "8"), ("RECEIVABLE", "EUR", "248.00"),
    ),
}


def build_reconciliation_demo(scenario: str = "matched") -> dict:
    """Compare the same in-memory ledger to either synthetic closing statement."""
    if scenario not in _STATEMENT_ROWS:
        raise ValueError(f"Unknown synthetic reconciliation scenario: {scenario}")
    statement_id = f"M4-01-{scenario.upper()}"
    statement = {
        "mode": "SYNTHETIC_ONLY",
        "statement_id": statement_id,
        "account": "BRK",
        "effective_at": EFFECTIVE_AT,
        "available_at": STATEMENT_AVAILABLE_AT,
        "basis": "TRADE_DATE",
        "lines": [
            {"kind": kind, "key": key, "amount_dec": amount, "source_record_id": f"{statement_id}:{kind}:{key}"}
            for kind, key, amount in _STATEMENT_ROWS[scenario]
        ],
    }

    ledger = Ledger(gold="901", variant="reconciliation")
    try:
        ledger.opening(
            cash={"EUR": "1000"},
            positions=[{"instrument": "EQ1", "qty": "8", "cost": "400"}],
            pending=[{"type": "PAYABLE", "amount": "40"}, {"type": "RECEIVABLE", "amount": "20"}],
        )
        ledger.buy("review-buy", qty="3", price="100", fee="1")
        ledger.settle("review-buy-partial", ledger.rid("obligation", "review-buy"), "100")
        ledger.sell("review-sell", qty="2", price="120", fee="2")
        ledger.settle("review-receivable-partial", ledger.rid("obligation", "brk.opening.1"), "10")
        ledger.cancel("review-payable-cancel", ledger.rid("obligation", "brk.opening.0"), "15")
        projection = project_reconciliation(
            ledger, "BRK", effective_at=EFFECTIVE_AT, known_as_of=KNOWN_AS_OF,
        )
        return {"scenario": scenario, **compare_reconciliation(projection, statement)}
    finally:
        ledger.close()
