from datetime import UTC, datetime, timedelta
from decimal import Decimal
import unittest

from hita_paper.contracts import Action, PolicyResult
from hita_paper.market import BENCHMARK, CASH, load_market
from hita_paper.research_labels import build_opportunity, build_research_recommendation, decision_time
from hita_paper.store import PaperStore
from hita_paper.wallet import WalletConfig, read_wallet_run


class ResearchLabelTests(unittest.TestCase):
    def setUp(self):
        self.market = load_market()
        self.sessions = {session.date.isoformat(): session for session in self.market.sessions}
        self.store = PaperStore()
        self.addCleanup(self.store.db.close)
        self.cutoff = datetime(2026, 4, 24, 17, tzinfo=UTC)

    def opportunity(self, day, *, horizon=5, cutoff=None, config=WalletConfig()):
        return build_opportunity(
            self.market, self.store, self.sessions[day], horizon_sessions=horizon,
            evaluated_at=self.cutoff if cutoff is None else cutoff, wallet_config=config,
        )

    def test_recommendation_and_label_wait_for_their_separate_clocks(self):
        session = self.sessions["2026-01-15"]
        early = self.opportunity("2026-01-15", cutoff=decision_time(session) - timedelta(seconds=1))
        self.assertIsNone(early.recommendation)
        self.assertEqual(self.store.recommendations(), ())

        publication = datetime(2026, 2, 13, 16, 5, tzinfo=UTC)
        before = self.opportunity("2026-01-15", horizon=20, cutoff=publication - timedelta(seconds=1))
        self.assertEqual(before.status, "UNMATURED")
        self.assertIsNone(before.label_excess_return_dec)
        self.assertIsNone(before.label_available_at)
        self.assertTrue(before.policy_eligible)

        label = self.opportunity("2026-01-15", horizon=20, cutoff=publication)
        self.assertEqual(label.status, "MATURED")
        self.assertEqual(label.entry_at, datetime(2026, 1, 16, 9, tzinfo=UTC))
        self.assertEqual(label.horizon_at, publication - timedelta(minutes=5))
        self.assertEqual(label.label_available_at, publication)
        self.assertEqual(label.recommendation, before.recommendation)
        self.assertEqual(label.recommendation.evidence_links[0].effective_at, session.close_at)
        self.assertTrue(all(link.available_at <= label.decision_at for link in label.recommendation.evidence_links))
        run = read_wallet_run(self.store.db, label.wallet_run_id)
        self.assertEqual(run["signals"][0]["position_net_pnl_dec"], "7.00")
        self.assertEqual(run["benchmark_ending_nav_dec"], "10009.05")
        self.assertEqual(
            Decimal(label.label_excess_return_dec).quantize(Decimal("0.000000001")),
            Decimal("-0.020398010"),
        )

    def test_late_correction_does_not_replace_original_execution_label(self):
        first = self.opportunity("2026-01-15", horizon=20, cutoff=datetime(2026, 2, 13, 16, 5, tzinfo=UTC))
        later = self.opportunity("2026-01-15", horizon=20)
        corrected = self.market.bar(first.recommendation.instrument_id, first.horizon_at, self.cutoff)
        self.assertNotIn(corrected.observation_id, first.observation_ids)
        self.assertEqual(first.observation_ids[1], "EQ1_SYNTHETIC/2026-02-13/CLOSE/1")
        self.assertEqual(first.to_dict(), later.to_dict())

    def test_future_missing_or_stale_opens_remain_in_the_population(self):
        for day, reason in (("2026-03-25", "PARTIAL_NOT_SUPPORTED"), ("2026-03-27", "MARKET_STALE")):
            with self.subTest(day=day):
                opportunity = self.opportunity(day)
                self.assertTrue(opportunity.policy_eligible)
                self.assertEqual(opportunity.status, "UNFILLED")
                self.assertEqual(opportunity.reason_code, reason)
                self.assertIsNone(opportunity.label_excess_return_dec)
        missing = self.opportunity("2026-04-10")
        self.assertEqual(missing.status, "DATA_INSUFFICIENT")
        self.assertEqual(missing.recommendation.action, Action.ABSTAIN)
        self.assertIsNone(missing.recommendation.reference_price_dec)

    def test_announced_delisting_blocks_while_surprise_delisting_censors(self):
        unknown_at_decision = self.opportunity("2026-04-02", horizon=7)
        self.assertTrue(unknown_at_decision.policy_eligible)
        self.assertEqual(unknown_at_decision.status, "CENSORED")
        self.assertEqual(unknown_at_decision.reason_code, "DELISTED_WITHOUT_PROCEEDS")
        known_at_decision = self.opportunity("2026-04-03", horizon=7)
        self.assertFalse(known_at_decision.policy_eligible)
        self.assertEqual(known_at_decision.recommendation.policy_result, PolicyResult.BLOCK)
        self.assertEqual(known_at_decision.reason_code, "KNOWN_DELISTING_WITHIN_HORIZON")
        self.assertEqual(known_at_decision.corporate_action_ids, ("EQ1/DELIST/2026-04-10",))
        self.assertIsNone(known_at_decision.wallet_run_id)

    def test_corporate_actions_and_configured_costs_bind_to_the_label(self):
        regular = self.opportunity("2026-03-16")
        self.assertEqual(regular.status, "MATURED")
        self.assertEqual(regular.corporate_action_ids, ("EQ1/SPLIT/2026-03-18", "EQ1/DIVIDEND/2026-03-23"))
        higher_cost = self.opportunity("2026-03-16", config=WalletConfig(commission_dec="1.50"))
        self.assertNotEqual(regular.sample_id, higher_cost.sample_id)
        self.assertLess(Decimal(higher_cost.asset_net_return_dec), Decimal(regular.asset_net_return_dec))
        punitive_cost = self.opportunity("2026-03-16", config=WalletConfig(commission_dec="25.00"))
        self.assertEqual(punitive_cost.status, "POLICY_BLOCKED")
        self.assertEqual(punitive_cost.reason_code, "TACTICAL_TRADE_RISK_LIMIT")

    def test_benchmark_anchor_is_a_separate_cash_matched_candidate(self):
        opportunity = self.opportunity("2026-01-15")
        benchmark = build_research_recommendation(
            self.market, self.sessions["2026-01-15"], horizon_sessions=5, instrument_id=BENCHMARK, benchmark_id=CASH,
        )
        self.assertNotEqual(opportunity.sample_id, benchmark.recommendation_id)
        self.assertEqual(benchmark.policy_result, PolicyResult.ALLOW)
        self.assertEqual(benchmark.evidence_links[0].instrument_id, BENCHMARK)


if __name__ == "__main__":
    unittest.main()
