"""Acceptance coverage for S1 on the bundled synthetic fixture."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import unittest

from hita_paper.contracts import Action, ContractError, PolicyResult, parse_time
from hita_paper.demo_inputs import DEMO_EVALUATED_AT, demo_recommendations
from hita_paper.market import load_market
from hita_paper.shadow import RelativeResult, ShadowStatus, evaluate_shadow, summarize_shadow


class ShadowBookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = load_market()
        cls.recommendations = {
            item.recommendation_id: item for item in demo_recommendations(cls.market)
        }

    def outcome(self, recommendation_id, evaluated_at=DEMO_EVALUATED_AT):
        return evaluate_shadow(self.recommendations[recommendation_id], self.market, evaluated_at=evaluated_at)

    def decision_at_close(self, day, horizon):
        recommendation = self.recommendations["demo-add"]
        decision_at = parse_time(f"{day}T16:30:00Z")
        observation = self.market.bar(recommendation.instrument_id, parse_time(f"{day}T16:00:00Z"), decision_at)
        return replace(
            recommendation,
            recommendation_id=day,
            decision_at=decision_at,
            known_as_of=decision_at,
            horizon_trading_days=horizon,
            reference_price_dec=observation.price_dec,
            evidence_links=(self.market.evidence(observation),),
        )

    def test_paper_012_horizon_and_publication_are_distinct_cutoffs(self):
        horizon = parse_time("2026-02-13T16:00:00Z")
        observing = self.outcome("demo-add", horizon - timedelta(microseconds=1))
        pending_release = self.outcome("demo-add", horizon + timedelta(minutes=4))
        scored = self.outcome("demo-add", horizon + timedelta(minutes=5))
        self.assertEqual(observing.state, ShadowStatus.UNMATURED)
        self.assertEqual(observing.relative_result, RelativeResult.NOT_COMPARABLE)
        self.assertEqual(pending_release.state, ShadowStatus.DATA_INSUFFICIENT)
        self.assertIn("ASSET_HORIZON_PRICE_UNAVAILABLE", pending_release.reason_codes)
        self.assertEqual(scored.state, ShadowStatus.MATURED)
        self.assertEqual(scored.gross_asset_return_dec, "0.08")
        self.assertEqual(scored.absolute_result, "POSITIVE")
        self.assertEqual(scored.relative_result, RelativeResult.UNDERPERFORMED)
        self.assertLess(scored.decision_at, scored.entry_at)
        self.assertEqual(scored.horizon_at, horizon)
        self.assertEqual(scored.to_dict()["horizon_at"], "2026-02-13T16:00:00Z")

    def test_paper_004_and_009_policy_block_and_avoid_remain_observable(self):
        blocked = self.outcome("demo-add-missing-cbr")
        avoid = self.outcome("demo-avoid")
        abstain = self.outcome("demo-abstain")
        immature = self.outcome("demo-add", parse_time("2026-01-30T17:00:00Z"))
        self.assertEqual(blocked.state, ShadowStatus.MATURED)
        self.assertEqual(blocked.policy_result, PolicyResult.BLOCK)
        self.assertEqual(blocked.quality_gate, "POLICY_BLOCKED")
        self.assertIn("MISSING_APPROVED_DENOMINATOR", blocked.recommendation_reason_codes)
        self.assertEqual(avoid.gross_asset_return_dec, "-0.1")
        self.assertEqual(avoid.gross_benchmark_return_dec, "0")
        self.assertEqual(avoid.absolute_result, "NOT_APPLICABLE")
        self.assertEqual(avoid.action_excess_return_dec, "0.1")
        self.assertEqual(avoid.relative_result, RelativeResult.OUTPERFORMED)
        self.assertEqual(abstain.state, ShadowStatus.MATURED)
        self.assertEqual(abstain.relative_result, RelativeResult.NOT_COMPARABLE)
        self.assertIsNotNone(abstain.observed_opportunity_dec)
        scorecard = summarize_shadow((blocked, avoid, abstain, immature)).to_dict()
        self.assertEqual(scorecard["candidate_count"], 4)
        self.assertEqual(scorecard["state_counts"]["UNMATURED"], 1)
        self.assertEqual(scorecard["directional_wins"], 1)
        self.assertEqual(scorecard["directional_losses"], 1)
        self.assertEqual(scorecard["blocked_directional_count"], 1)
        self.assertEqual(scorecard["abstention_count"], 1)
        self.assertEqual(scorecard["decision_coverage_dec"], "0.75")
        self.assertEqual(scorecard["directional_coverage_dec"], "0.5")

    def test_paper_010_hold_requires_existing_holding_for_directional_score(self):
        original = self.recommendations["demo-avoid"]
        hold = replace(original, action=Action.HOLD)
        no_holding = evaluate_shadow(hold, self.market, evaluated_at=DEMO_EVALUATED_AT, holding_at_decision=False)
        unknown = evaluate_shadow(hold, self.market, evaluated_at=DEMO_EVALUATED_AT)
        existing = evaluate_shadow(hold, self.market, evaluated_at=DEMO_EVALUATED_AT, holding_at_decision=True)
        self.assertEqual(no_holding.reason_codes, ("NO_EXISTING_HOLDING",))
        self.assertEqual(unknown.reason_codes, ("HOLDING_UNKNOWN",))
        self.assertEqual(no_holding.relative_result, RelativeResult.NOT_COMPARABLE)
        self.assertEqual(existing.relative_result, RelativeResult.UNDERPERFORMED)
        self.assertEqual(existing.action_excess_return_dec, "-0.1")

    def test_paper_006_and_007_split_and_dividend_preserve_value(self):
        # Opening one share at EUR 100 yields two shares after the split.
        # On ex-date the EUR 49.60 mark and EUR 0.40 dividend each total EUR 100.
        split = self.decision_at_close("2026-03-16", 2)
        dividend = self.decision_at_close("2026-03-16", 5)
        split_outcome = evaluate_shadow(split, self.market, evaluated_at=parse_time("2026-03-18T17:00:00Z"))
        dividend_outcome = evaluate_shadow(dividend, self.market, evaluated_at=parse_time("2026-03-23T17:00:00Z"))
        self.assertEqual(split_outcome.asset_horizon_price_dec, "50.00")
        self.assertEqual(split_outcome.gross_asset_return_dec, "0")
        self.assertEqual(dividend_outcome.asset_horizon_price_dec, "49.60")
        self.assertEqual(dividend_outcome.gross_asset_return_dec, "0")
        self.assertEqual(dividend_outcome.corporate_action_ids, ("EQ1/SPLIT/2026-03-18", "EQ1/DIVIDEND/2026-03-23"))

    def test_paper_011_stale_open_is_never_replaced_by_later_price(self):
        recommendation = self.decision_at_close("2026-03-27", 2)
        outcome = evaluate_shadow(recommendation, self.market, evaluated_at=parse_time("2026-04-01T17:00:00Z"))
        self.assertEqual(outcome.entry_at, parse_time("2026-03-30T09:00:00Z"))
        self.assertEqual(outcome.state, ShadowStatus.DATA_INSUFFICIENT)
        self.assertIn("ASSET_ENTRY_PRICE_UNAVAILABLE_AT_OPEN", outcome.reason_codes)
        self.assertIsNone(outcome.gross_asset_return_dec)

    def test_paper_016_horizon_skips_weekends_and_fixture_holidays(self):
        recommendation = self.decision_at_close("2026-01-15", 2)
        outcome = evaluate_shadow(recommendation, self.market, evaluated_at=DEMO_EVALUATED_AT)
        self.assertEqual(outcome.entry_at, parse_time("2026-01-16T09:00:00Z"))
        self.assertEqual(outcome.horizon_at, parse_time("2026-01-20T16:00:00Z"))

    def test_paper_018_delisted_candidate_remains_in_scorecard_population(self):
        recommendation = self.decision_at_close("2026-04-08", 2)
        outcome = evaluate_shadow(recommendation, self.market, evaluated_at=parse_time("2026-04-10T17:00:00Z"))
        self.assertEqual(outcome.state, ShadowStatus.CENSORED)
        self.assertIn("ASSET_DELISTING_TERMS_UNAVAILABLE", outcome.reason_codes)
        self.assertIn("EQ1/DELIST/2026-04-10", outcome.corporate_action_ids)
        scorecard = summarize_shadow((outcome,)).to_dict()
        self.assertEqual(scorecard["candidate_count"], 1)
        self.assertEqual(scorecard["state_counts"]["CENSORED"], 1)
        self.assertEqual(scorecard["decision_coverage_dec"], "0")

    def test_paper_019_correction_changes_only_the_later_evaluation(self):
        original = self.outcome("demo-add")
        corrected = self.outcome("demo-add", parse_time("2026-04-02T12:00:00Z"))
        self.assertEqual(original.source_snapshot_id, corrected.source_snapshot_id)
        self.assertEqual(original.recommendation_content_sha256, corrected.recommendation_content_sha256)
        self.assertEqual(original.asset_entry_price_dec, "100.00")
        self.assertEqual(corrected.asset_entry_price_dec, original.asset_entry_price_dec)
        self.assertEqual(original.asset_horizon_price_dec, "108.00")
        self.assertEqual(corrected.asset_horizon_price_dec, "108.10")
        self.assertEqual(corrected.gross_asset_return_dec, "0.081")
        self.assertNotEqual(original.observation_ids, corrected.observation_ids)
        self.assertEqual(original, self.outcome("demo-add"))
        with self.assertRaises(ContractError) as duplicate:
            summarize_shadow((original, corrected))
        self.assertEqual(duplicate.exception.code, "VALIDATION_DUPLICATE_SHADOW_CANDIDATE")

    def test_evaluation_cannot_precede_decision(self):
        with self.assertRaises(ContractError) as caught:
            self.outcome("demo-add", parse_time("2026-01-15T16:29:59Z"))
        self.assertEqual(caught.exception.code, "TEMPORAL_EVALUATION_BEFORE_DECISION")


if __name__ == "__main__":
    unittest.main()
