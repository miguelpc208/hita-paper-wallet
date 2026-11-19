"""Targeted point-in-time and frozen-decision regressions."""

from dataclasses import FrozenInstanceError, replace
import unittest

from hita_paper.contracts import Action, ContractError, PolicyResult, Recommendation, parse_time
from hita_paper.market import BENCHMARK, EQ1, FIXTURE_SHA256, load_market


class PaperContractsTests(unittest.TestCase):
    def setUp(self):
        self.market = load_market()
        self.decision_at = parse_time("2026-01-15T16:30:00Z")
        self.decision_price = self.market.latest(EQ1, self.decision_at, self.decision_at)

    def recommendation(self):
        return Recommendation(
            recommendation_id="test:fixture:01",
            decision_at=self.decision_at,
            known_as_of=self.decision_at,
            instrument_id=EQ1,
            action=Action.CONSIDER_ADD,
            horizon_trading_days=20,
            source_snapshot_id=self.market.manifest_id,
            model_version="synthetic-price-rule-v1",
            risk_policy_version="synthetic-risk-v1",
            policy_result=PolicyResult.ALLOW,
            reason_codes=("SYNTHETIC_RISK_PASS",),
            benchmark_id=BENCHMARK,
            cost_model_id="synthetic-cost-v1",
            reference_price_dec=self.decision_price.price_dec,
            evidence_links=(self.market.evidence(self.decision_price),),
            quantity_dec="1",
            capital_base_dec="10000",
            planned_loss_dec="1.00",
        )

    def test_release_times_prevent_lookahead_while_corrections_are_versioned(self):
        close_at = parse_time("2026-02-13T16:00:00Z")
        published_at = parse_time("2026-02-13T16:01:00Z")
        released_at = parse_time("2026-02-13T16:05:00Z")
        corrected_at = parse_time("2026-04-02T12:00:00Z")
        self.assertIsNone(self.market.bar(EQ1, close_at, published_at))
        original = self.market.bar(EQ1, close_at, released_at)
        corrected = self.market.bar(EQ1, close_at, corrected_at)
        self.assertEqual(original.price_dec, "108.00")
        self.assertEqual(corrected.price_dec, "108.10")
        self.assertNotEqual(original.observation_id, corrected.observation_id)
        self.assertEqual(self.market.bar(EQ1, close_at, released_at), original)

    def test_next_open_respects_calendar_and_exact_market_observations(self):
        self.assertEqual(self.market.manifest_sha256, FIXTURE_SHA256)
        self.assertEqual(len(self.market.sessions), 79)
        open_at = parse_time("2026-01-16T09:00:00Z")
        self.assertEqual(self.market.next_open(self.decision_at).open_at, open_at)
        next_open_at = parse_time("2026-01-20T09:00:00Z")
        self.assertEqual(self.market.next_open(open_at).open_at, next_open_at)
        self.assertEqual(
            self.market.horizon(open_at, 20).close_at,
            parse_time("2026-02-13T16:00:00Z"),
        )
        gap = parse_time("2026-03-27T09:00:00Z")
        stale = parse_time("2026-03-30T09:00:00Z")
        self.assertIsNone(self.market.raw_bar(EQ1, gap, gap))
        self.assertIsNone(self.market.bar(EQ1, stale, stale))
        self.assertEqual(self.market.raw_bar(EQ1, stale, stale).condition, "STALE")

    def test_action_notices_and_currency_have_distinct_cutoffs(self):
        start = parse_time("2026-03-17T00:00:00Z")
        end = parse_time("2026-03-25T17:00:00Z")
        actions_before_dividend_notice = self.market.actions(EQ1, start, end, start)
        self.assertEqual([event.kind for event in actions_before_dividend_notice], ["SPLIT"])
        self.assertEqual(actions_before_dividend_notice[0].ratio_dec, "2")
        split, dividend = self.market.actions(EQ1, start, end, end)
        self.assertEqual(split.effective_at, parse_time("2026-03-18T09:00:00Z"))
        self.assertEqual(dividend.amount_dec, "0.40")
        self.assertEqual(dividend.payable_at, parse_time("2026-03-25T09:00:00Z"))
        self.assertEqual(self.market.fx_rate("EUR", "EUR", end, end).rate_dec, "1")
        self.assertIsNone(self.market.fx_rate("GBP", "EUR", end, end))

    def test_frozen_contract_rejects_serialized_tampering(self):
        rec = self.recommendation()
        self.market.validate_recommendation(rec)
        self.assertEqual(Recommendation.from_dict(rec.to_dict()), rec)
        with self.assertRaises(FrozenInstanceError):
            rec.model_version = "changed"
        changed = rec.to_dict()
        changed["model_version"] = "changed"
        with self.assertRaises(ContractError) as caught:
            Recommendation.from_dict(changed)
        self.assertEqual(caught.exception.code, "SOURCE_CONTENT_DIGEST_MISMATCH")
        self.assertNotEqual(replace(rec, model_version="changed").content_sha256, rec.content_sha256)

    def test_future_evidence_fails_before_freeze(self):
        rec = self.recommendation()
        future_at = parse_time("2026-01-16T09:00:00Z")
        future_price = self.market.bar(EQ1, future_at, future_at)
        with self.assertRaises(ContractError) as caught:
            replace(rec, evidence_links=(self.market.evidence(future_price),))
        self.assertEqual(caught.exception.code, "TEMPORAL_LOOKAHEAD")

    def test_fake_evidence_and_price_fail_fixture_validation(self):
        rec = self.recommendation()
        fake_evidence = replace(rec.evidence_links[0], observation_id="forged-observation")
        with self.assertRaises(ContractError) as spoof:
            self.market.validate_recommendation(replace(rec, evidence_links=(fake_evidence,)))
        self.assertEqual(spoof.exception.code, "SOURCE_EVIDENCE_UNKNOWN")
        with self.assertRaises(ContractError) as changed_price:
            self.market.validate_recommendation(replace(rec, reference_price_dec="1.00"))
        self.assertEqual(changed_price.exception.code, "SOURCE_REFERENCE_PRICE_MISMATCH")

    def test_fixture_content_cannot_reuse_another_manifest_hash(self):
        rec = self.recommendation()
        first, *remaining = self.market.observations
        changed = replace(self.market, observations=(replace(first, price_dec="1.00"), *remaining))
        self.assertEqual(changed.manifest_id, self.market.manifest_id)
        with self.assertRaises(ContractError) as caught:
            changed.validate_recommendation(rec)
        self.assertEqual(caught.exception.code, "SOURCE_FIXTURE_CHECKSUM")

    def test_missing_capital_base_requires_explicit_block(self):
        rec = self.recommendation()
        with self.assertRaises(ContractError) as caught:
            replace(rec, capital_base_dec=None)
        self.assertEqual(caught.exception.code, "MISSING_APPROVED_DENOMINATOR")
        blocked = replace(
            rec,
            capital_base_dec=None,
            policy_result=PolicyResult.BLOCK,
            reason_codes=("MISSING_APPROVED_DENOMINATOR",),
        )
        self.market.validate_recommendation(blocked)

    def test_decimal_fields_reject_floats_and_exponent_strings(self):
        rec = self.recommendation()
        for invalid in (0.2, "1e-2", "NaN", "-0.00"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ContractError) as caught:
                    replace(rec, planned_loss_dec=invalid)
                self.assertEqual(caught.exception.code, "VALIDATION_DECIMAL")
