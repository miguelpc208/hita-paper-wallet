"""Verify exact reconciliation decisions independently of the SQLite projection."""

from copy import deepcopy
from decimal import Context, ROUND_UP, localcontext
import unittest

from hita_a0.reconciliation import compare_reconciliation


EFFECTIVE = "2026-01-12T00:00:00Z"
AVAILABLE = "2026-01-12T10:00:00Z"


def ledger_line(kind, key, amount):
    return {
        "kind": kind, "key": key, "amount_dec": amount,
        "evidence": [{"posting_id": f"ledger-{kind}-{key}"}],
    }


def statement_line(kind, key, amount):
    return {
        "kind": kind, "key": key, "amount_dec": amount,
        "source_record_id": f"statement-{kind}-{key}",
    }


def group_result(report, kind, key):
    return next(row for row in report["lines"] if (row["kind"], row["key"]) == (kind, key))


class ReconciliationComparisonTests(unittest.TestCase):
    def setUp(self):
        self.projection = {
            "account": "BRK", "effective_at": EFFECTIVE, "known_as_of": AVAILABLE,
            "basis": "TRADE_DATE",
            "lines": [ledger_line("HOLDING", "EQ1", "9.125"), ledger_line("CASH", "EUR", "100.50")],
            "issues": [],
        }
        self.statement = {
            "mode": "SYNTHETIC_ONLY", "statement_id": "fixture-1",
            "account": "BRK", "effective_at": EFFECTIVE, "available_at": AVAILABLE,
            "basis": "TRADE_DATE",
            "lines": [statement_line("CASH", "EUR", "100.500"), statement_line("HOLDING", "EQ1", "9.125000")],
        }

    def test_numeric_tie_preserves_both_sources_and_stable_hashes(self):
        before = deepcopy((self.projection, self.statement))
        report = compare_reconciliation(self.projection, self.statement)
        self.assertEqual((self.projection, self.statement), before)
        self.assertEqual(report["status"], "RECONCILED")
        self.assertEqual(report["certification"], "INTERNAL_SYNTHETIC_MATCH")
        self.assertEqual(report["issues"], [])
        cash = group_result(report, "CASH", "EUR")
        self.assertEqual(cash, {
            "kind": "CASH", "key": "EUR", "status": "MATCHED",
            "expected_dec": "100.50", "observed_dec": "100.500", "difference_dec": "0",
            "statement_source_record_id": "statement-CASH-EUR",
            "ledger_evidence": [{"posting_id": "ledger-CASH-EUR"}],
        })
        self.projection["lines"].reverse()
        self.statement["lines"].reverse()
        self.assertEqual(compare_reconciliation(self.projection, self.statement), report)

    def test_breaks_keep_missing_values_unknown_and_show_exact_difference(self):
        self.statement["lines"] = [
            statement_line("CASH", "EUR", "100.49999999"),
            statement_line("RECEIVABLE", "USD", "0"),
        ]
        with localcontext(Context(prec=3, rounding=ROUND_UP)):
            report = compare_reconciliation(self.projection, self.statement)
        self.assertEqual(report["status"], "UNQUALIFIED")
        self.assertEqual(report["certification"], "UNQUALIFIED")
        self.assertEqual({issue["code"] for issue in report["issues"]}, {
            "AMOUNT_MISMATCH", "MISSING_STATEMENT_LINE", "UNEXPECTED_STATEMENT_LINE",
        })
        self.assertEqual(group_result(report, "CASH", "EUR")["difference_dec"], "-0.00000001")
        holding = group_result(report, "HOLDING", "EQ1")
        self.assertIsNone(holding["observed_dec"])
        self.assertIsNone(holding["difference_dec"])
        self.assertTrue(holding["ledger_evidence"])
        extra = group_result(report, "RECEIVABLE", "USD")
        self.assertIsNone(extra["expected_dec"])
        self.assertIsNone(extra["difference_dec"])
        self.assertEqual(extra["observed_dec"], "0")

    def test_incompatible_snapshots_are_not_compared(self):
        cases = [
            ("account", "BNK", "ACCOUNT_MISMATCH"),
            ("effective_at", "2026-01-11T00:00:00Z", "EFFECTIVE_TIME_MISMATCH"),
            ("basis", "SETTLED_CUSTODY", "UNSUPPORTED_RECOGNITION_BASIS"),
            ("available_at", "2026-01-12T10:00:01Z", "STATEMENT_NOT_AVAILABLE"),
            ("available_at", "2026-01-11T10:00:00Z", "STATEMENT_AVAILABLE_BEFORE_EFFECTIVE_TIME"),
        ]
        for name, value, code in cases:
            with self.subTest(code=code):
                report = compare_reconciliation(self.projection, {**self.statement, name: value})
                self.assertEqual(report["status"], "UNQUALIFIED")
                self.assertEqual(report["issues"], [{"code": code}])
                self.assertEqual(report["lines"], [])

    def test_upstream_issues_block_qualification_even_when_values_match(self):
        self.projection["issues"] = [{"code": "IN_FLIGHT_TRANSFER", "event_id": "transfer-1"}]
        report = compare_reconciliation(self.projection, self.statement)
        self.assertEqual(report["status"], "UNQUALIFIED")
        self.assertEqual(report["issues"], self.projection["issues"])
        self.assertTrue(all(row["status"] == "MATCHED" for row in report["lines"]))

    def test_empty_inputs_do_not_get_qualified(self):
        self.projection["lines"] = []
        self.statement["lines"] = []
        report = compare_reconciliation(self.projection, self.statement)
        self.assertEqual(report["status"], "UNQUALIFIED")
        self.assertEqual(report["issues"], [{"code": "NO_RECONCILIATION_LINES"}])

    def test_rejects_duplicate_keys_bad_amounts_and_non_synthetic_inputs(self):
        for amount in (100, 100.5, None, "NaN", "1E2", "100.000000001"):
            with self.subTest(amount=amount):
                first_line = {**self.statement["lines"][0], "amount_dec": amount}
                with self.assertRaises(ValueError):
                    compare_reconciliation(self.projection, {**self.statement, "lines": [first_line]})
        with self.assertRaisesRegex(ValueError, "Duplicate statement line"):
            compare_reconciliation(self.projection, {**self.statement, "lines": self.statement["lines"] * 2})
        with self.assertRaisesRegex(ValueError, "Synthetic statement mode"):
            compare_reconciliation(self.projection, {**self.statement, "mode": "REAL"})
        self.projection["lines"][0]["evidence"] = []
        with self.assertRaisesRegex(ValueError, "needs evidence"):
            compare_reconciliation(self.projection, self.statement)


if __name__ == "__main__":
    unittest.main()
