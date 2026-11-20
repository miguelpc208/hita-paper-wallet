"""Exercise versioned observations through the CLI and immutable research store."""

from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from hita_paper.demo_inputs import demo_recommendations
from hita_paper.market import load_market
from hita_paper.research import read_research_run, run_research
from hita_paper.shadow import evaluate_shadow
from hita_paper.store import PaperStore
from hita_paper.wallet import simulate_wallet


_BEFORE_CORRECTION = datetime(2026, 4, 3, 12, 9, 59, tzinfo=UTC)
_AT_CORRECTION = datetime(2026, 4, 3, 12, 10, tzinfo=UTC)


class MarketVersionIntegrationTests(unittest.TestCase):
    def cli(self, *args):
        return json.loads(subprocess.check_output(
            [sys.executable, "-m", "hita_paper", *args], encoding="utf-8",
        ))

    def test_root_cli_catalog_and_corrected_quote(self):
        versions = self.cli("market", "catalog")
        self.assertEqual(versions["default_market_version"], "v1")
        self.assertEqual([row["version"] for row in versions["versions"]], ["v1", "v2"])
        quote = self.cli(
            "market", "quote", "--market-version", "v2", "--instrument", "EQ1_SYNTHETIC",
            "--at", "2026-02-13T16:00:00Z", "--known-as-of", "2026-04-03T12:10:00Z",
        )
        self.assertEqual(quote["selected"]["price_dec"], "108.25")
        self.assertEqual(quote["selected"]["evidence"]["source_manifest_id"], load_market("v2").manifest_id)

    def test_research_versions_coexist_and_share_random_candidate_draws(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "market versions.sqlite"
            v1 = run_research(database, as_of=_BEFORE_CORRECTION)
            v2 = run_research(database, as_of=_BEFORE_CORRECTION, market_version="v2")
            self.assertNotEqual(v1["run_id"], v2["run_id"])
            self.assertEqual(v1["predictive"]["metrics"], v2["predictive"]["metrics"])
            selected_dates = []
            for report in (v1, v2):
                dates = {row["sample_id"]: row["outcome"]["decision_at"] for row in report["dataset"]}
                random = report["economic"]["strategy_summaries"]["SEEDED_RANDOM"]
                self.assertGreater(random["selected_count"], 0)
                selected_dates.append([dates[item] for item in random["selected_sample_ids"]])
            self.assertEqual(selected_dates[0], selected_dates[1])
            self.assertEqual(
                v1["economic"]["strategy_summaries"]["SEEDED_RANDOM"]["wallet_pnl_dec"],
                v2["economic"]["strategy_summaries"]["SEEDED_RANDOM"]["wallet_pnl_dec"],
            )
            with PaperStore(database, readonly=True) as store:
                self.assertEqual(read_research_run(store, v1["run_id"]), v1)
                self.assertEqual(read_research_run(store, v2["run_id"]), v2)
            self.assertEqual(run_research(database, as_of=_BEFORE_CORRECTION), v1)
            after = self.cli("research", "--db", str(database), "--market-version", "v2")
            self.assertEqual(after["inputs"]["source_manifest_id"], load_market("v2").manifest_id)

    def test_later_correction_changes_the_shadow_outcome_not_the_auction_fill(self):
        v1, v2 = load_market(), load_market("v2")
        decision_v1 = next(record for record in demo_recommendations(v1) if record.recommendation_id == "demo-add")
        decision_v2 = replace(
            next(record for record in demo_recommendations(v2) if record.recommendation_id == "demo-add"),
            recommendation_id="demo-add-market-v2",
        )
        self.assertEqual(evaluate_shadow(decision_v1, v1, evaluated_at=_AT_CORRECTION).gross_asset_return_dec, "0.081")
        self.assertEqual(
            evaluate_shadow(decision_v2, v2, evaluated_at=_BEFORE_CORRECTION).gross_asset_return_dec, "0.081",
        )
        self.assertEqual(
            evaluate_shadow(decision_v2, v2, evaluated_at=_AT_CORRECTION).gross_asset_return_dec, "0.0825",
        )
        with PaperStore() as store:
            store.freeze((decision_v1,), v1)
            store.freeze((decision_v2,), v2)
            for market, decision in ((v1, decision_v1), (v2, decision_v2)):
                report = simulate_wallet(store.db, market, (decision,), as_of=_AT_CORRECTION)
                sell = next(fill for fill in report["fills"] if fill["side"] == "SELL")
                self.assertEqual(sell["reference_price_dec"], "108.00")


if __name__ == "__main__":
    unittest.main()
