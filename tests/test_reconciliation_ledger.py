"""Focused tests for the dated synthetic ledger projection."""

from decimal import Decimal
import unittest

from hita_a0.ledger import Ledger
from hita_a0.reconciliation_ledger import project_reconciliation


class LedgerReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(gold="m4-projection")
        self.addCleanup(self.ledger.close)

    def project(self, effective_at=None, known_as_of=None):
        effective_at = effective_at or self.ledger.t(8)
        return project_reconciliation(
            self.ledger, "BRK", effective_at=effective_at, known_as_of=known_as_of or effective_at,
        )

    @staticmethod
    def by_key(report):
        return {(row["kind"], row["key"]): row for row in report["lines"]}

    def test_opening_trade_receivable_payable_and_reversal_reconstruct_with_evidence(self):
        ledger = self.ledger
        ledger.opening(
            cash={"EUR": "1000", "USD": "160"},
            positions=[{"qty": "2", "cost": "18"}],
            pending=[{"type": "RECEIVABLE", "amount": "40"}, {"type": "PAYABLE", "currency": "USD", "amount": "12"}],
        )
        buy = ledger.buy("buy", qty="3", price="10", fee="1")
        purchase = ledger.obligation()
        self.assertEqual(ledger.progress(purchase)["O"], Decimal("12"))
        purchase = ledger.obligation(index=1)
        settled = ledger.settle("pay", purchase, "31")
        reversal = ledger.reverse_buy("undo", "buy", "31")
        cancelled = ledger.cancel("cancel-opening", ledger.obligation(typ="RECEIVABLE"), "5")
        before = ledger.db.total_changes
        report = self.project()
        self.assertEqual(report["mode"], "SYNTHETIC_ONLY")
        self.assertEqual(ledger.db.total_changes, before)
        self.assertEqual(report["issues"], [])
        lines = self.by_key(report)
        self.assertEqual({key: row["amount_dec"] for key, row in lines.items()}, {
            ("CASH", "EUR"): "969", ("CASH", "USD"): "160", ("HOLDING", "EQ1"): "2",
            ("PAYABLE", "EUR"): "0", ("PAYABLE", "USD"): "12", ("RECEIVABLE", "EUR"): "66",
        })
        for row in lines.values():
            trace = row["evidence"]
            self.assertEqual(sum(Decimal(item["amount_dec"]) for item in trace), Decimal(row["amount_dec"]))
            for item in trace:
                if item["type"] == "POSTING":
                    self.assertTrue(item["source_record_ids"])
                    self.assertTrue(item["posting_id"])
                else:
                    self.assertTrue(item["opening_anchor_id"])
                    self.assertTrue(item["statement_id"])
                    self.assertTrue(item["source_record_id"])
        self.assertEqual({item.get("event_id") for item in lines["HOLDING", "EQ1"]["evidence"]}, {None, buy, reversal})
        self.assertEqual({item.get("event_id") for item in lines["PAYABLE", "EUR"]["evidence"]}, {buy, settled})
        self.assertEqual({item.get("event_id") for item in lines["RECEIVABLE", "EUR"]["evidence"]},
                         {None, reversal, cancelled})

    def test_equal_event_clocks_do_not_hide_visible_obligation_origins(self):
        ledger = self.ledger
        ledger.opening(cash={"EUR": "1000"})
        aliases = sorted(("deal", "paid"), key=lambda alias: ledger.rid("event", alias))
        early_lexical, late_lexical = aliases
        at = ledger.t(1)
        buy = ledger.buy(late_lexical, qty="1", price="10", when=at)
        paid = ledger.settle(early_lexical, ledger.obligation(), "5", when=at)
        self.assertLess(paid, buy)
        report = self.project(at, at)
        self.assertEqual(report["issues"], [])
        line = self.by_key(report)["PAYABLE", "EUR"]
        self.assertEqual(line["amount_dec"], "5")
        self.assertEqual({row["event_id"] for row in line["evidence"]}, {buy, paid})

    def test_economic_and_knowledge_cutoffs_each_exclude_future_postings(self):
        ledger = self.ledger
        ledger.opening(cash={"EUR": "1000"})
        ledger.buy("buy", qty="1", price="10")
        ledger.settle("partial", ledger.obligation(), "4")
        for economic, knowledge in ((ledger.t(1), ledger.t(2)), (ledger.t(2), ledger.t(1))):
            with self.subTest(economic=economic, knowledge=knowledge):
                report = self.project(economic, knowledge)
                self.assertEqual(report["issues"], [])
                lines = self.by_key(report)
                self.assertEqual(lines["CASH", "EUR"]["amount_dec"], "1000")
                self.assertEqual(lines["PAYABLE", "EUR"]["amount_dec"], "10")
        self.assertEqual(self.by_key(self.project())["PAYABLE", "EUR"]["amount_dec"], "6")

    def test_blocked_or_pre_anchor_cutoffs_have_no_qualified_lines(self):
        missing = self.project()
        self.assertEqual(missing["lines"], [])
        self.assertEqual(missing["issues"][0]["code"], "OPENING_ANCHOR_REQUIRED")
        self.ledger.opening(cutoff=self.ledger.t(3))
        early = self.project(known_as_of=self.ledger.t(2))
        self.assertEqual(early["lines"], [])
        self.assertEqual(early["issues"][0]["code"], "SNAPSHOT_PRECEDES_OPENING_ANCHOR")

        with_issues = Ledger(gold="m4-blocked")
        self.addCleanup(with_issues.close)
        with_issues.opening(certified=False)
        blocked = project_reconciliation(
            with_issues, "BRK", effective_at=with_issues.t(2), known_as_of=with_issues.t(2),
        )
        self.assertEqual(blocked["lines"], [])
        self.assertEqual(blocked["issues"][0]["code"], "OPENING_ANCHOR_UNQUALIFIED")

    def test_transfer_match_records_provenance_only_when_both_legs_and_match_are_known(self):
        ledger = self.ledger
        ledger.opening("BRK", cash={"EUR": "1000"})
        ledger.opening("BNK", cash={"EUR": "500"})
        outgoing = ledger.cashflow("out", "-20", "BRK", "TRANSFER_OUT")
        incoming = ledger.cashflow("in", "20", "BNK", "TRANSFER_IN")
        ledger.now = lambda: ledger.t(3)
        ledger.match_transfer("out", "in")
        early = self.project(ledger.t(2), ledger.t(2))
        self.assertEqual(early["issues"][0]["code"], "UNMATCHED_TRANSFER")
        self.assertEqual(early["issues"][0]["event_id"], outgoing)
        missing_counterpart = self.project(ledger.t(1), ledger.t(3))
        self.assertEqual(missing_counterpart["issues"][0]["code"], "UNMATCHED_TRANSFER")
        matched = self.project(ledger.t(2), ledger.t(3))
        trace = self.by_key(matched)["CASH", "EUR"]["evidence"][-1]
        self.assertEqual(matched["issues"], [])
        self.assertEqual(trace["counterparty_event_id"], incoming)
        self.assertTrue(trace["transfer_match_id"])
        self.assertTrue(trace["counterparty_source_record_ids"])

    def test_detects_mismatched_event_payload_without_writing_to_ledger(self):
        ledger = self.ledger
        ledger.opening(cash={"EUR": "1000"})
        event_id = ledger.cashflow("deposit", "20")
        ledger.db.execute("""UPDATE src_source_records SET normalized_payload_json = '{}'
            WHERE source_record_id IN (SELECT source_record_id FROM src_record_event_links WHERE event_id = ?)""",
            (event_id,))
        before = ledger.db.total_changes
        report = self.project()
        self.assertEqual(report["issues"], [{"code": "EVENT_SOURCE_UNQUALIFIED", "event_id": event_id}])
        self.assertEqual(self.by_key(report)["CASH", "EUR"]["evidence"][-1]["source_record_ids"], [])
        self.assertEqual(ledger.db.total_changes, before)


if __name__ == "__main__":
    unittest.main()
