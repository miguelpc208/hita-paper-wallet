"""Exercise the bundled reconciliation stories through the A0 entry point."""

from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


class ReconciliationCliTests(unittest.TestCase):
    def run_review(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "hita_a0.cli", "reconcile", *args],
            capture_output=True, encoding="utf-8",
        )

    def test_matched_fixture_replays_the_four_hand_calculated_balances(self):
        command = self.run_review()
        self.assertEqual(command.returncode, 0, command.stderr)
        self.assertEqual(command.stdout, self.run_review("--scenario", "matched").stdout)
        report = json.loads(command.stdout)
        self.assertEqual(report["status"], "RECONCILED")
        self.assertEqual(
            {(line["kind"], line["key"]): Decimal(line["expected_dec"]) for line in report["lines"]},
            {("CASH", "EUR"): Decimal("910"), ("HOLDING", "EQ1"): Decimal("9"),
             ("PAYABLE", "EUR"): Decimal("226"), ("RECEIVABLE", "EUR"): Decimal("248")},
        )
        for line in report["lines"]:
            self.assertTrue(line["ledger_evidence"])
            self.assertTrue(line["statement_source_record_id"])

    def test_break_fixture_returns_readable_unqualified_report(self):
        command = self.run_review("--scenario", "breaks")
        self.assertEqual(command.returncode, 2, command.stderr)
        report = json.loads(command.stdout)
        self.assertEqual(report["status"], "UNQUALIFIED")
        rows = {(line["kind"], line["key"]): line for line in report["lines"]}
        self.assertEqual(Decimal(rows["CASH", "EUR"]["difference_dec"]), Decimal("-10"))
        self.assertEqual(Decimal(rows["HOLDING", "EQ1"]["difference_dec"]), Decimal("-1"))
        self.assertEqual(rows["PAYABLE", "EUR"]["status"], "MISSING_STATEMENT_LINE")
        self.assertIsNone(rows["PAYABLE", "EUR"]["observed_dec"])
        self.assertEqual(rows["CASH", "USD"]["status"], "UNEXPECTED_STATEMENT_LINE")
        self.assertIsNone(rows["CASH", "USD"]["expected_dec"])

    def test_bundled_review_rejects_a_database_argument_without_creating_it(self):
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "unused.sqlite"
            command = self.run_review("--db", str(destination))
            self.assertEqual(command.returncode, 2)
            self.assertIn("bundled in-memory fixture", command.stderr)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
