"""End-to-end checks for the point-in-time experiment and its saved report."""

from datetime import UTC, datetime
from decimal import Decimal, ROUND_UP, localcontext
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from hita_paper.research import read_research_run, run_research
from hita_paper.store import PaperStore


class ResearchIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.experiment = run_research()

    def test_full_calendar_keeps_blocked_and_missing_outcomes_in_holdout(self):
        rows = self.experiment["predictive"]["rows"]
        dataset = {sample["sample_id"]: sample for sample in self.experiment["dataset"]}
        holdout = [row for row in rows if row["partition"] == "HOLDOUT"]
        self.assertEqual(len(rows), 79)
        self.assertEqual(len(holdout), 30)
        self.assertEqual(holdout[0]["decision_at"], "2026-03-16T16:30:00Z")
        self.assertEqual(holdout[-1]["decision_at"], "2026-04-24T16:30:00Z")
        self.assertEqual(set(dataset), {row["sample_id"] for row in rows})
        self.assertIsNone(holdout[-1]["ridge_prediction_dec"])
        self.assertGreater(sum(row["label_dec"] is None for row in holdout), 0)

    def test_holdout_predictions_fit_once_and_use_earlier_released_labels(self):
        rows = self.experiment["predictive"]["rows"]
        by_id = {row["sample_id"]: row for row in rows}
        holdout = [row for row in rows if row["partition"] == "HOLDOUT"]
        modeled = [row for row in holdout if row["ridge_prediction_dec"] is not None]
        self.assertGreater(len(modeled), 0)
        development = self.experiment["predictive"]["metrics"]["DEVELOPMENT"]
        self.assertEqual(development["scoring_label_cutoff_at"], "2026-03-12T00:00:00Z")
        self.assertEqual(development["labels_excluded_by_scoring_cutoff"], 7)
        self.assertEqual(development["matched_count"], 18)
        self.assertEqual(len({row["model_id"] for row in modeled}), 1)
        for row in modeled:
            self.assertTrue(row["training_sample_ids"])
            for sample_id in row["training_sample_ids"]:
                training = by_id[sample_id]
                self.assertEqual(training["partition"], "DEVELOPMENT")
                self.assertLess(training["label_available_at"], row["training_cutoff_at"])
                self.assertLess(training["label_available_at"], "2026-03-12T00:00:00Z")

    def test_two_independent_runs_match_under_different_decimal_contexts(self):
        with localcontext() as context:
            context.prec = 3
            context.rounding = ROUND_UP
            self.assertEqual(run_research(), self.experiment)

    def test_registry_preserves_an_earlier_cutoff_and_cli_reads_both(self):
        with TemporaryDirectory() as folder:
            path = Path(folder, "research.sqlite")
            earlier = run_research(path, as_of=datetime(2026, 3, 18, 17, tzinfo=UTC))
            final = run_research(path)
            self.assertNotEqual(earlier["run_id"], final["run_id"])
            early_rows = earlier["predictive"]["rows"]
            final_rows = {row["sample_id"]: row for row in final["predictive"]["rows"]}
            for row in early_rows:
                self.assertEqual(row["ridge_prediction_dec"], final_rows[row["sample_id"]]["ridge_prediction_dec"])
                self.assertEqual(row["model_id"], final_rows[row["sample_id"]]["model_id"])
            with PaperStore(path) as store:
                self.assertEqual(read_research_run(store, earlier["run_id"]), earlier)
                self.assertEqual(read_research_run(store, final["run_id"]), final)
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    store.db.execute("DELETE FROM research_experiments")
            result = subprocess.run(
                [sys.executable, "-m", "hita_paper", "report", "research", final["run_id"], "--db", str(path)],
                check=True, capture_output=True, encoding="utf-8",
            )
            self.assertEqual(json.loads(result.stdout), final)

    def test_independent_split_and_dividend_net_label_oracle(self):
        row = next(
            item for item in self.experiment["dataset"]
            if item["outcome"]["decision_at"] == "2026-03-16T16:30:00Z"
        )
        outcome = row["outcome"]
        self.assertEqual(outcome["status"], "MATURED")
        self.assertEqual(outcome["horizon_at"], "2026-03-23T16:00:00Z")
        self.assertAlmostEqual(
            Decimal(outcome["label_excess_return_dec"]), Decimal("-0.00149253731343"), places=12,
        )


if __name__ == "__main__":
    unittest.main()
