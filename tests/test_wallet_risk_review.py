"""End-to-end checks for synthetic entry risk reviews."""

from decimal import Decimal
import json
from pathlib import Path
import unittest

from hita_paper.contracts import Action, PolicyResult, Recommendation, parse_time
from hita_paper.market import BENCHMARK, CASH, EQ1, load_market
from hita_paper.store import PaperStore, canonical_json
from hita_paper.wallet import WalletConfig, read_wallet_run, simulate_wallet


def instant(day: str, clock: str = "16:30:00"):
    return parse_time(f"2026-{day}T{clock}Z")


class WalletRiskReviewTests(unittest.TestCase):
    def setUp(self):
        self.market = load_market()

    def recommendation(
        self, recommendation_id: str, day: str = "01-15", *, instrument_id: str = EQ1,
        quantity: str = "1", base: str | None = "100000", planned_loss: str | None = "150",
    ) -> Recommendation:
        decision_at = instant(day)
        observation = self.market.latest(instrument_id, decision_at, decision_at)
        return Recommendation(
            recommendation_id=recommendation_id, decision_at=decision_at, known_as_of=decision_at,
            instrument_id=instrument_id, action=Action.CONSIDER_ADD, horizon_trading_days=20,
            source_snapshot_id=self.market.manifest_id, model_version="risk-review-test-v1",
            risk_policy_version="synthetic-risk-v1",
            policy_result=PolicyResult.BLOCK if base is None else PolicyResult.ALLOW,
            reason_codes=("MISSING_APPROVED_DENOMINATOR",) if base is None else ("SYNTHETIC_TEST",),
            benchmark_id=CASH, cost_model_id="synthetic-cost-v1",
            reference_price_dec=observation.price_dec,
            evidence_links=(self.market.evidence(observation),),
            quantity_dec=quantity, capital_base_dec=base, planned_loss_dec=planned_loss,
        )

    def simulate(self, *recommendations, as_of=None, config=WalletConfig()):
        with PaperStore() as store:
            frozen = store.freeze(recommendations, self.market)
            return simulate_wallet(
                store.db, self.market, frozen, as_of=as_of or instant("01-16"), config=config,
            )

    @staticmethod
    def check(review, rule_id):
        return next(check for check in review["checks"] if check["rule_id"] == rule_id)

    def test_receipt_records_the_auction_and_the_loss_bound_that_blocked_entry(self):
        record = self.recommendation("too-little-loss", planned_loss="100")
        report = self.simulate(record)
        signal = report["signals"][0]
        review = signal["risk_review"]
        entry_at = instant("01-16", "09:00:00")
        self.assertEqual(report["fills"], [])
        self.assertEqual(signal["disposition"], "BLOCKED")
        self.assertEqual(signal["reason_code"], "PLANNED_LOSS_BELOW_ZERO_RECOVERY_BOUND")
        self.assertEqual(review["reason_code"], signal["reason_code"])
        self.assertEqual(review["status"], "BLOCK")
        self.assertEqual(review["stage"], "PAPER_ENTRY")
        self.assertEqual(review["evaluated_at"], "2026-01-16T09:00:00Z")
        self.assertEqual(review["known_as_of"], review["evaluated_at"])
        self.assertEqual(review["source_manifest_id"], self.market.manifest_id)
        self.assertEqual(review["recommendation_sha256"], record.content_sha256)
        self.assertEqual(review["evidence_observation_ids"], [self.market.bar(EQ1, entry_at, entry_at).observation_id])
        bound = self.check(review, "ZERO_RECOVERY_LOSS_BOUND")
        self.assertEqual(Decimal(bound["observed_dec"]), Decimal("100"))
        self.assertEqual(Decimal(bound["limit_dec"]), Decimal("101"))

    def test_overlap_passes_at_aggregate_limit_and_blocks_after_it(self):
        records = [self.recommendation(f"entry-{i}") for i in range(5)]
        report = self.simulate(*records)
        self.assertEqual([item["recommendation_id"] for item in report["fills"]], [f"entry-{i}" for i in range(4)])
        last_allowed, blocked = report["signals"][-2:]
        allowed_review = last_allowed["risk_review"]
        aggregate = self.check(allowed_review, "AGGREGATE_RISK_CAP")
        self.assertEqual(allowed_review["status"], "ALLOW")
        self.assertEqual(Decimal(aggregate["observed_dec"]), Decimal("600"))
        self.assertEqual(Decimal(aggregate["limit_dec"]), Decimal("600"))
        self.assertEqual(blocked["reason_code"], "SYNTHETIC_AGGREGATE_RISK_LIMIT")
        blocked_review = blocked["risk_review"]
        self.assertEqual([lot["recommendation_id"] for lot in blocked_review["open_lots"]], [
            "entry-0", "entry-1", "entry-2", "entry-3",
        ])
        self.assertEqual(Decimal(self.check(blocked_review, "AGGREGATE_RISK_CAP")["observed_dec"]), Decimal("750"))

    def test_overlapping_position_uses_the_lower_base_and_keeps_blocker_priority(self):
        first = self.recommendation("first", base="70000", planned_loss="105")
        next_entry = self.recommendation("second", base="1000000", planned_loss="1500", quantity="14")
        report = self.simulate(first, next_entry)
        self.assertEqual(len(report["fills"]), 1)
        review = report["signals"][-1]["risk_review"]
        position = self.check(review, "POSITION_CAP")
        self.assertEqual(review["reason_code"], "SYNTHETIC_POSITION_SIZE_LIMIT")
        self.assertEqual(Decimal(position["denominator_dec"]), Decimal("70000"))
        self.assertEqual(Decimal(position["observed_dec"]), Decimal("1500"))
        self.assertEqual(Decimal(position["limit_dec"]), Decimal("1400"))
        self.assertEqual(self.check(review, "AGGREGATE_RISK_CAP")["status"], "BLOCK")

    def test_stale_quote_and_stale_open_exposure_have_distinct_outcomes(self):
        stale_entry = self.recommendation("stale-entry", "03-27")
        cutoff = instant("03-30")
        report = self.simulate(stale_entry, as_of=cutoff)
        signal = report["signals"][0]
        review = signal["risk_review"]
        self.assertEqual((signal["disposition"], signal["reason_code"]), ("UNFILLED", "MARKET_STALE"))
        self.assertEqual((review["status"], review["reason_code"]), ("NOT_EVALUATED", "MARKET_STALE"))
        self.assertEqual(review["known_as_of"], "2026-03-30T09:00:00Z")
        qualification = self.check(review, "ENTRY_QUOTE_QUALIFICATION")
        self.assertEqual(qualification["status"], "UNKNOWN")
        self.assertIsNone(qualification["observed_dec"])
        [stale_observation_id] = review["evidence_observation_ids"]

        opening = self.recommendation("old-open-lot", "03-16")
        entry = self.recommendation("candidate", "03-27", instrument_id=BENCHMARK)
        report = self.simulate(opening, entry, as_of=cutoff)
        signal = report["signals"][-1]
        self.assertEqual((signal["disposition"], signal["reason_code"]), ("BLOCKED", "UNVALUED_RISK_EXPOSURE"))
        review = signal["risk_review"]
        self.assertEqual(self.check(review, "EXPOSURE_QUALIFICATION")["input_reason_code"], "MARKET_STALE")
        self.assertIn(stale_observation_id, review["evidence_observation_ids"])
        self.assertEqual(review["open_lots"][0]["observation_id"], stale_observation_id)

    def test_frozen_block_stays_blocked_and_risk_allow_does_not_override_available_cash(self):
        blocked = self.recommendation("blocked", base=None, planned_loss=None)
        valid = self.recommendation("valid")
        report = self.simulate(blocked, valid, config=WalletConfig(initial_cash_dec="100"))
        self.assertEqual(report["fills"], [])
        earlier, later = report["signals"]
        review = earlier["risk_review"]
        self.assertEqual((review["stage"], review["status"]), ("FROZEN_DECISION", "BLOCK"))
        self.assertEqual(review["reason_code"], "MISSING_APPROVED_DENOMINATOR")
        self.assertIsNone(review["capital_base_dec"])
        capital_base = self.check(review, "CAPITAL_BASE")
        self.assertEqual(capital_base["status"], "UNKNOWN")
        self.assertIsNone(capital_base["observed_dec"])
        self.assertEqual(later["risk_review"]["status"], "ALLOW")
        self.assertEqual((later["disposition"], later["reason_code"]), ("UNFILLED", "INSUFFICIENT_CASH"))

    def test_previous_wallet_engine_report_is_preserved_when_the_same_input_is_replayed(self):
        previous = json.loads(
            (Path(__file__).resolve().parents[1] / "artifacts/paper_wallet_demo.json").read_text(encoding="utf-8")
        )
        previous_report = previous["paper_wallet"]
        records = tuple(Recommendation.from_dict(item) for item in previous["recommendations"])
        with PaperStore() as store:
            frozen = store.freeze(records, self.market)
            store.db.execute(
                "INSERT INTO wallet_runs VALUES (?, ?)",
                (previous_report["run_id"], canonical_json(previous_report)),
            )
            next_report = simulate_wallet(store.db, self.market, frozen, as_of=parse_time(previous["evaluated_at"]))
            self.assertNotEqual(next_report["run_id"], previous_report["run_id"])
            self.assertEqual(next_report["engine_version"], "hita-paper-wallet-v2")
            self.assertEqual(next_report["wallet_pnl_dec"], previous_report["wallet_pnl_dec"])
            self.assertEqual(read_wallet_run(store.db, previous_report["run_id"]), previous_report)
            self.assertTrue(all("risk_review" in signal for signal in next_report["signals"]))


if __name__ == "__main__":
    unittest.main()
