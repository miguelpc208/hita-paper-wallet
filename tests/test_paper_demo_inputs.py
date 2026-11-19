import unittest
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

from hita_paper.contracts import Action, PolicyResult
from hita_paper.demo_inputs import assess_synthetic_add, demo_recommendations
from hita_paper.market import CASH, load_market


class SyntheticDecisionTests(unittest.TestCase):
    def test_demo_cases_use_only_published_evidence(self):
        market = load_market()
        allowed, blocked, abstention, avoidance = demo_recommendations(market)
        self.assertEqual((allowed.action, allowed.policy_result), (Action.CONSIDER_ADD, PolicyResult.ALLOW))
        self.assertEqual(Decimal(allowed.planned_loss_dec), Decimal("110.00"))
        self.assertEqual((blocked.action, blocked.policy_result), (Action.CONSIDER_ADD, PolicyResult.BLOCK))
        self.assertIsNone(blocked.capital_base_dec)
        self.assertEqual(blocked.reason_codes, ("MISSING_APPROVED_DENOMINATOR",))
        self.assertEqual(abstention.action, Action.ABSTAIN)
        self.assertEqual((avoidance.action, avoidance.benchmark_id), (Action.AVOID, CASH))
        for decision in (allowed, blocked, abstention, avoidance):
            with self.subTest(decision=decision.recommendation_id):
                self.assertEqual(decision.source_snapshot_id, market.manifest_id)
                self.assertEqual(len(decision.evidence_links), 1)
                self.assertLessEqual(decision.evidence_links[0].available_at, decision.known_as_of)

    def test_early_cutoff_does_not_request_future_scenario_evidence(self):
        fixture = load_market()
        market = Mock(wraps=fixture, manifest_id=fixture.manifest_id)
        cutoff = datetime(2026, 1, 20, tzinfo=UTC)
        decisions = demo_recommendations(market, as_of=cutoff)
        self.assertEqual(tuple(decision.recommendation_id for decision in decisions), (
            "demo-add", "demo-add-missing-cbr",
        ))
        self.assertEqual(market.bar.call_count, 2)
        for read in market.bar.call_args_list:
            self.assertLessEqual(read.args[2], cutoff)

    def test_unavailable_close_yields_abstain_without_a_price_or_evidence(self):
        fixture = load_market()
        market = Mock(wraps=fixture, manifest_id=fixture.manifest_id)
        market.bar.return_value = None
        decision = demo_recommendations(market)[0]
        self.assertEqual((decision.action, decision.policy_result), (Action.ABSTAIN, PolicyResult.REVIEW))
        self.assertEqual(decision.reason_codes, ("MISSING_QUALIFIED_DECISION_CLOSE",))
        self.assertIsNone(decision.reference_price_dec)
        self.assertEqual(decision.evidence_links, ())

    def test_plausible_trade_risk_still_blocks_without_certified_open_risk(self):
        policy = assess_synthetic_add(
            Decimal("99"), Decimal("1"),
            synthetic_capital_base=Decimal("100000"), open_tactical_risk=None,
        )
        self.assertEqual(policy.policy_result, PolicyResult.BLOCK)
        self.assertEqual(policy.reason_codes, ("MISSING_OPEN_TACTICAL_RISK",))

    def test_tactical_caps_reject_excess_added_and_aggregate_risk(self):
        oversized = assess_synthetic_add(
            Decimal("99"), Decimal("20"),
            synthetic_capital_base=Decimal("100000"), open_tactical_risk=Decimal("0"),
        )
        self.assertEqual(oversized.policy_result, PolicyResult.BLOCK)
        self.assertEqual(oversized.reason_codes, (
            "TACTICAL_TRADE_RISK_LIMIT", "TACTICAL_POSITION_LIMIT", "TACTICAL_OPEN_RISK_LIMIT",
        ))
        excess_open_risk = assess_synthetic_add(
            Decimal("99"), Decimal("1"),
            synthetic_capital_base=Decimal("100000"), open_tactical_risk=Decimal("550"),
        )
        self.assertEqual(excess_open_risk.policy_result, PolicyResult.BLOCK)
        self.assertEqual(excess_open_risk.reason_codes, ("TACTICAL_OPEN_RISK_LIMIT",))
