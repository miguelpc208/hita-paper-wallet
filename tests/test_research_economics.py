"""Exercise the matched holdout portfolios through the paper store."""

from dataclasses import replace
from decimal import Decimal
import unittest

from hita_paper.contracts import parse_time
from hita_paper.market import load_market
from hita_paper.research_economics import evaluate_economics
from hita_paper.research_forecast import ForecastRow, ForecastSample
from hita_paper.research_labels import build_opportunity
from hita_paper.store import PaperStore
from hita_paper.wallet import read_wallet_run


class ResearchEconomicsTests(unittest.TestCase):
    start = parse_time("2026-03-16T16:30:00Z")
    cutoff = parse_time("2026-04-24T17:00:00Z")

    def setUp(self):
        self.market = load_market()
        self.store = PaperStore()
        self.addCleanup(self.store.db.close)

    def opportunity(self, day, *, horizon=5):
        session = next(session for session in self.market.sessions if session.date.isoformat() == f"2026-{day}")
        return build_opportunity(self.market, self.store, session, horizon_sessions=horizon, evaluated_at=self.cutoff)

    def forecast(self, item, *, ridge, momentum, partition="HOLDOUT"):
        return ForecastRow(
            sample=ForecastSample(
                sample_id=item.sample_id, decision_at=item.decision_at, feature_values=None,
                label=None, label_start_at=None, label_end_at=None, label_available_at=None,
            ),
            partition=partition,
            state="FORECAST_AVAILABLE" if ridge is not None else "FEATURES_UNAVAILABLE",
            model=None,
            training_cutoff_at=None,
            training_sample_ids=(),
            latest_training_label_available_at=None,
            ridge_prediction=Decimal(ridge) if ridge is not None else None,
            momentum_prediction=Decimal(momentum) if momentum is not None else None,
        )

    def evaluate(self, opportunities, forecasts=(), **kwargs):
        return evaluate_economics(
            self.market, self.store, opportunities, forecasts,
            holdout_start_at=self.start, evaluated_at=kwargs.pop("evaluated_at", self.cutoff), **kwargs,
        )

    def test_strategies_share_window_and_real_wallet_accounting(self):
        first, second = self.opportunity("03-16"), self.opportunity("03-17")
        before_start, missing = self.opportunity("03-13"), self.opportunity("04-20")
        forecasts = [
            self.forecast(first, ridge="0.02", momentum="-0.01"),
            self.forecast(second, ridge="-0.01", momentum="0.02"),
        ]
        result = self.evaluate((first, missing, before_start, second), forecasts)
        self.assertEqual(result["candidate_count"], 3)
        self.assertEqual(result["policy_eligible_count"], 2)
        strategies = result["strategy_summaries"]
        self.assertEqual(strategies["CASH"]["selected_count"], 0)
        self.assertEqual(Decimal(strategies["CASH"]["ending_nav_dec"]), Decimal("10000.00"))
        self.assertEqual(strategies["POLICY_ONLY"]["selected_count"], 2)
        self.assertEqual(strategies["RIDGE"]["selected_sample_ids"], [first.sample_id])
        self.assertEqual(strategies["RELATIVE_MOMENTUM"]["selected_sample_ids"], [second.sample_id])
        self.assertEqual(strategies["RIDGE"]["selection_counts"]["POLICY_INELIGIBLE"], 1)
        for name in ("CASH", "POLICY_ONLY", "SEEDED_RANDOM", "RIDGE", "BENCHMARK_BUY_AND_HOLD"):
            summary = strategies[name]
            stored = read_wallet_run(self.store.db, summary["run_id"])
            self.assertEqual(summary["valuation_start_at"], "2026-03-16T16:30:00Z")
            self.assertEqual(summary["ending_nav_dec"], stored["ending_nav_dec"])
            self.assertEqual(
                Decimal(summary["commissions_dec"]),
                sum(Decimal(fill["commission_dec"]) for fill in stored["fills"]),
            )
            self.assertEqual(stored["initial_cash_dec"], "10000.00")
        self.assertEqual(strategies["BENCHMARK_BUY_AND_HOLD"]["buy_fill_count"], 1)
        self.assertEqual(strategies["BENCHMARK_BUY_AND_HOLD"]["sell_fill_count"], 1)
        self.assertEqual(result["shadow_baseline"]["candidate_count"], 3)

    def test_selection_does_not_depend_on_outcome_availability(self):
        item = self.opportunity("03-16")
        forecast = self.forecast(item, ridge="0.01", momentum=None)
        changed = replace(item, status="DATA_INSUFFICIENT", reason_code="TEST_NO_LABEL", label_excess_return_dec=None)
        original = self.evaluate((item,), (forecast,))
        unavailable = self.evaluate((changed,), (forecast,))
        self.assertEqual(unavailable["strategy_summaries"], original["strategy_summaries"])
        self.assertNotEqual(unavailable["opportunity_status_counts"], original["opportunity_status_counts"])
        self.assertEqual(original["strategy_summaries"]["RELATIVE_MOMENTUM"]["selection_counts"], {"NO_PREDICTION": 1})

    def test_missing_marks_and_development_forecasts_are_disclosed(self):
        item = self.opportunity("04-02", horizon=8)
        with self.assertRaisesRegex(ValueError, "Forecast partition"):
            self.evaluate((item,), (self.forecast(item, ridge="1", momentum="1", partition="DEVELOPMENT"),))
        result = self.evaluate((item,), (self.forecast(item, ridge="1", momentum="1"),))
        policy = result["strategy_summaries"]["POLICY_ONLY"]
        self.assertEqual(policy["outcome_status_counts"], {"CENSORED": 1})
        self.assertEqual(policy["ending_nav_status"], "NOT_CERTIFIED")
        self.assertIsNone(policy["ending_nav_dec"])
        self.assertIsNone(policy["wallet_return_dec"])

    def test_holdout_has_no_runs_before_its_declared_start(self):
        result = self.evaluate((), evaluated_at=parse_time("2026-03-10T17:00:00Z"))
        self.assertEqual(result["status"], "HOLDOUT_NOT_STARTED")
        self.assertEqual(result["strategy_summaries"], {})
        self.assertIsNone(result["shadow_baseline"])


if __name__ == "__main__":
    unittest.main()
