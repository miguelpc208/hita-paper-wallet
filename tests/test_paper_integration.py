"""End-to-end replay, decision immutability and A0 isolation checks."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import ROUND_UP, localcontext
from pathlib import Path
import json
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from hita_paper.cli import run_demo
from hita_paper.demo_inputs import demo_recommendations
from hita_paper.market import load_market
from hita_paper.store import PaperStore


class PaperIntegrationTests(unittest.TestCase):
    def test_frozen_decision_replays_and_model_promotion_does_not_replace_it(self):
        market = load_market()
        recommendations = demo_recommendations(market)
        record = recommendations[0]
        with PaperStore() as paper:
            paper.freeze((record,), market)
            paper.freeze((record,), market)
            self.assertEqual(paper.recommendations(), (record,))
            with self.assertRaisesRegex(ValueError, "different content"):
                paper.freeze((recommendations[1], replace(record, model_version="new-model-v2")), market)
            self.assertEqual(paper.recommendations(), (record,))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                paper.db.execute("UPDATE frozen_recommendations SET content_sha256 = 'changed'")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                paper.db.execute("INSERT OR REPLACE INTO frozen_recommendations SELECT * FROM frozen_recommendations")

    def test_a0_database_is_rejected_without_changing_any_bytes(self):
        a0_fixture = Path(__file__).resolve().parent.parent / "artifacts" / "gold001_demo.sqlite"
        with TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "a0.sqlite"
            shutil.copyfile(a0_fixture, target)
            before = target.read_bytes()
            for readonly in (True, False):
                with self.assertRaisesRegex(ValueError, "non-paper"):
                    PaperStore(target, readonly=readonly)
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(list(Path(tempdir).iterdir()), [target])

    def test_demo_is_deterministic_with_a_retained_earlier_evaluation(self):
        early = datetime(2026, 1, 16, 12, tzinfo=timezone.utc)
        with TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "paper.sqlite"
            early_report = run_demo(target, as_of=early)
            report = run_demo(target)
            self.assertEqual(run_demo(target), report)
            self.assertEqual(len(report["recommendations"]), 4)
            self.assertEqual(len(early_report["recommendations"]), 2)
            early_shadow = early_report["shadow_signal_book"]
            final_shadow = report["shadow_signal_book"]
            self.assertNotEqual(early_shadow["run_id"], final_shadow["run_id"])
            self.assertNotEqual(early_report["paper_wallet"]["run_id"], report["paper_wallet"]["run_id"])
            with PaperStore(target, readonly=True) as paper:
                self.assertEqual(paper.shadow_run(early_shadow["run_id"]), early_shadow)
                self.assertEqual(paper.shadow_run(final_shadow["run_id"]), final_shadow)
                self.assertEqual(paper.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(paper.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_replay_is_independent_of_callers_decimal_rounding_context(self):
        expected = run_demo()
        with localcontext() as context:
            context.prec = 3
            context.rounding = ROUND_UP
            self.assertEqual(run_demo(), expected)

    def test_rejects_shadow_outcome_with_unrelated_frozen_digest(self):
        with TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "paper.sqlite"
            result = run_demo(target)["shadow_signal_book"]
            invalid = [{**result["outcomes"][0], "recommendation_content_sha256": "0" * 64}, *result["outcomes"][1:]]
            with PaperStore(target) as paper:
                with self.assertRaisesRegex(ValueError, "provenance"):
                    paper.save_shadow_run(result["inputs"], invalid, result["scorecard"])
                self.assertEqual(paper.shadow_run(result["run_id"]), result)

    def test_persisted_demo_is_readable_through_cli(self):
        with TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "paper with spaces.sqlite"
            report = run_demo(target)
            before = target.read_bytes()
            cases = (
                (("decisions",), {"mode": "PAPER_ONLY", "recommendations": report["recommendations"]}),
                (("report", "shadow", report["shadow_signal_book"]["run_id"]), report["shadow_signal_book"]),
                (("report", "wallet", report["paper_wallet"]["run_id"]), report["paper_wallet"]),
            )
            for args, expected in cases:
                with self.subTest(command=args):
                    completed = subprocess.run([sys.executable, "-m", "hita_paper", *args, "--db", str(target)],
                                               capture_output=True, encoding="utf-8", check=True)
                    self.assertEqual(json.loads(completed.stdout), expected)
            self.assertEqual(before, target.read_bytes())
            self.assertEqual(list(Path(tempdir).iterdir()), [target])


if __name__ == "__main__":
    unittest.main()
