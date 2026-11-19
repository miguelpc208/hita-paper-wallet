"""Acceptance regressions for the synthetic paper wallet."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from hita_paper.contracts import Action, PolicyResult, Recommendation, parse_time
from hita_paper.market import BENCHMARK, EQ1, load_market
from hita_paper.store import PaperStore
from hita_paper.wallet import PaperDeposit, WalletConfig, read_wallet_run, simulate_wallet


def stamp(day, clock="16:30:00"):
    return parse_time(f"2026-{day}T{clock}Z")


class PaperWalletTests(unittest.TestCase):
    def setUp(self):
        self.market = load_market()

    def recommendation(
        self, recommendation_id="add", day="01-15", *, horizon=20,
        action=Action.CONSIDER_ADD, blocked=False
    ):
        decision_at = stamp(day)
        observation = self.market.latest(EQ1, decision_at, decision_at)
        executable = action == Action.CONSIDER_ADD and not blocked
        return Recommendation(
            recommendation_id=recommendation_id,
            decision_at=decision_at,
            known_as_of=decision_at,
            instrument_id=EQ1,
            action=action,
            horizon_trading_days=horizon,
            source_snapshot_id=self.market.manifest_id,
            model_version="synthetic-test-v1",
            risk_policy_version="synthetic-risk-v1",
            policy_result=PolicyResult.BLOCK if blocked else PolicyResult.ALLOW,
            reason_codes=("MISSING_APPROVED_DENOMINATOR",) if blocked else ("SYNTHETIC_TEST",),
            benchmark_id=BENCHMARK,
            cost_model_id="synthetic-cost-v1",
            reference_price_dec=observation.price_dec,
            evidence_links=(self.market.evidence(observation),),
            quantity_dec="1" if executable else None,
            capital_base_dec="1000000.00" if executable else None,
            planned_loss_dec="1000.00" if executable else None,
        )

    def simulate(self, *recommendations, as_of=stamp("02-13"), config=WalletConfig()):
        with PaperStore(":memory:") as store:
            frozen = store.freeze(recommendations, self.market)
            return simulate_wallet(store.db, self.market, frozen, as_of=as_of, config=config)

    def test_PAPER_001_and_013_next_open_and_matched_benchmark(self):
        report = self.simulate(self.recommendation())
        self.assertEqual(len(report["fills"]), 2)
        target_window = (stamp("01-16", "09:00:00"), stamp("02-13", "16:00:00"))
        self.assertEqual(tuple(parse_time(fill["effective_at"]) for fill in report["fills"]), target_window)
        benchmark_fills = report["benchmark"]["fills"]
        self.assertEqual(tuple(parse_time(fill["effective_at"]) for fill in benchmark_fills), target_window)
        self.assertEqual(
            tuple(Decimal(fill["commission_dec"]) for fill in benchmark_fills),
            (Decimal("0.50"), Decimal("0.50")),
        )
        self.assertEqual(Decimal(benchmark_fills[0]["cash_delta_dec"]), Decimal("-100.50"))
        self.assertEqual(Decimal(report["wallet_pnl_dec"]), Decimal("7.00"))
        self.assertEqual(Decimal(report["ending_cash_dec"]), Decimal("10007.00"))
        self.assertEqual(Decimal(report["ending_nav_dec"]), Decimal("10007.00"))
        self.assertEqual(Decimal(report["wallet_return_dec"]), Decimal("0.0007"))
        cash_delta = sum(Decimal(event["amount_dec"]) for event in report["cash_events"])
        self.assertEqual(cash_delta, Decimal(report["ending_cash_dec"]))
        self.assertEqual(Decimal(report["cash_only_ending_nav_dec"]), Decimal("10000.00"))
        self.assertGreater(Decimal(report["benchmark_ending_nav_dec"]), Decimal("10007.00"))
        signal = report["signals"][0]
        self.assertEqual(Decimal(signal["gross_asset_return_dec"]), Decimal("0.08"))
        self.assertEqual(Decimal(signal["position_net_pnl_dec"]), Decimal("7.00"))
        self.assertAlmostEqual(Decimal(signal["net_follow_return_dec"]), Decimal("0.069652"), places=6)
        self.assertAlmostEqual(Decimal(signal["benchmark_return_dec"]), Decimal("0.09005"), places=5)
        self.assertLess(Decimal(signal["excess_return_dec"]), 0)
        self.assertEqual(signal["absolute_result"], "POSITIVE")
        self.assertEqual(signal["relative_result"], "UNDERPERFORMED")

    def test_PAPER_002_and_003_repeated_input_has_identical_report_and_events(self):
        with PaperStore(":memory:") as store:
            frozen = store.freeze((self.recommendation(),), self.market)
            first = simulate_wallet(store.db, self.market, frozen, as_of=stamp("02-13"))
            before = tuple(store.db.iterdump())
            second = simulate_wallet(store.db, self.market, frozen, as_of=stamp("02-13"))
            self.assertEqual(first, second)
            self.assertEqual(before, tuple(store.db.iterdump()))
            with self.assertRaises(ValueError):
                simulate_wallet(store.db, self.market, frozen + frozen, as_of=stamp("02-13"))
            self.assertEqual(before, tuple(store.db.iterdump()))

    def test_PAPER_004_underfunded_and_risk_blocked_have_no_fills(self):
        report = self.simulate(
            self.recommendation("underfunded"), self.recommendation("blocked", blocked=True),
            config=WalletConfig(initial_cash_dec="100.49"),
        )
        self.assertEqual(report["fills"], [])
        self.assertEqual(Decimal(report["ending_cash_dec"]), Decimal("100.49"))
        reasons = {signal["recommendation_id"]: signal["reason_code"] for signal in report["signals"]}
        self.assertEqual(reasons["blocked"], "MISSING_APPROVED_DENOMINATOR")
        self.assertEqual(reasons["underfunded"], "INSUFFICIENT_CASH")

    def test_PAPER_004_fill_rechecks_the_frozen_loss_limit(self):
        planned = replace(self.recommendation(), planned_loss_dec="100.00")
        report = self.simulate(planned)
        self.assertEqual(report["fills"], [])
        self.assertEqual(report["signals"][0]["disposition"], "BLOCKED")
        self.assertEqual(report["signals"][0]["reason_code"], "PLANNED_LOSS_BELOW_ZERO_RECOVERY_BOUND")

    def test_PAPER_005_a0_accounting_schema_is_rejected_untouched(self):
        fixture = Path(__file__).resolve().parents[1] / "artifacts" / "gold001_demo.sqlite"
        with TemporaryDirectory() as folder:
            copied = Path(folder) / "a0.sqlite"
            shutil.copyfile(fixture, copied)
            before = copied.read_bytes()
            with self.assertRaises((ValueError, sqlite3.DatabaseError)):
                with PaperStore(copied):
                    self.fail("A0 database must not open as a paper store")
            with closing(sqlite3.connect(copied)) as db:
                with self.assertRaises((ValueError, sqlite3.DatabaseError)):
                    simulate_wallet(db, self.market, (self.recommendation(),), as_of=stamp("02-13"))
            self.assertEqual(copied.read_bytes(), before)

    def test_PAPER_015_fills_require_unchanged_persisted_frozen_decisions(self):
        rec = self.recommendation()
        with PaperStore(":memory:") as store:
            with self.assertRaises(ValueError):
                simulate_wallet(store.db, self.market, (rec,), as_of=stamp("02-13"))
            store.freeze((rec,), self.market)
            amended = replace(rec, model_version="synthetic-test-v2")
            with self.assertRaises(ValueError):
                store.freeze((amended,), self.market)
            with self.assertRaises(ValueError):
                simulate_wallet(store.db, self.market, (amended,), as_of=stamp("02-13"))
            report = simulate_wallet(store.db, self.market, (rec,), as_of=stamp("02-13"))
            self.assertEqual(len(report["fills"]), 2)

    def test_PAPER_014_external_deposit_is_a_flow_and_time_weights_returns(self):
        deposit = PaperDeposit("contribution-1", stamp("01-16"), "1000.00")
        cutoff = stamp("02-13")
        with PaperStore() as store:
            frozen = store.freeze((self.recommendation(),), self.market)
            cash_only = simulate_wallet(store.db, self.market, (), as_of=cutoff, deposits=(deposit,))
            before = simulate_wallet(store.db, self.market, frozen, as_of=cutoff)
            after = simulate_wallet(store.db, self.market, frozen, as_of=cutoff, deposits=(deposit,))
            retry = simulate_wallet(store.db, self.market, frozen, as_of=cutoff, deposits=(deposit,))
            with self.assertRaisesRegex(ValueError, "deposit ID"):
                simulate_wallet(store.db, self.market, frozen, as_of=cutoff, deposits=(deposit, deposit))

        self.assertEqual(Decimal(cash_only["ending_nav_dec"]), Decimal("11000.00"))
        self.assertEqual(Decimal(cash_only["wallet_pnl_dec"]), 0)
        self.assertEqual(Decimal(cash_only["wallet_return_dec"]), 0)
        self.assertNotEqual(before["run_id"], after["run_id"])
        self.assertEqual(retry, after)
        self.assertEqual(Decimal(after["ending_nav_dec"]), Decimal("11007.00"))
        self.assertEqual(Decimal(after["wallet_pnl_dec"]), Decimal("7.00"))
        self.assertEqual(Decimal(after["cash_only_ending_nav_dec"]), Decimal("11000.00"))
        self.assertEqual(Decimal(after["external_cash_flows"][0]["model_pre_flow_nav_dec"]), Decimal("9999.50"))
        expected = Decimal("9999.50") / Decimal("10000") * (Decimal("11007") / Decimal("10999.50")) - 1
        self.assertAlmostEqual(Decimal(after["wallet_return_dec"]), expected, places=25)
        for ledger in (after, after["benchmark"]):
            events = [row for row in ledger["cash_events"] if row["event_type"] == "DEPOSIT"]
            self.assertEqual([row["deposit_id"] for row in events], [deposit.deposit_id])

    def test_PAPER_006_split_preserves_lot_cost_and_marked_nav(self):
        rec = self.recommendation(day="03-16", horizon=7)
        before = self.simulate(rec, as_of=stamp("03-17"))
        after = self.simulate(rec, as_of=stamp("03-18"))
        self.assertEqual(len(before["fills"]), 1)
        self.assertEqual(len(after["fills"]), 1)
        self.assertEqual(Decimal(before["ending_nav_dec"]), Decimal("9999.50"))
        self.assertEqual(Decimal(after["ending_nav_dec"]), Decimal("9999.50"))
        self.assertEqual(Decimal(before["lots"][0]["quantity_dec"]), Decimal("1"))
        self.assertEqual(Decimal(after["lots"][0]["quantity_dec"]), Decimal("2"))
        self.assertEqual(Decimal(before["lots"][0]["cost_basis_dec"]), Decimal("100.50"))
        self.assertEqual(Decimal(after["lots"][0]["cost_basis_dec"]), Decimal("100.50"))

    def test_PAPER_007_dividend_is_receivable_before_pay_date_and_credited_once(self):
        rec = self.recommendation(day="03-16", horizon=7)
        ex_date = self.simulate(rec, as_of=stamp("03-23"))
        pay_date = self.simulate(rec, as_of=stamp("03-25"))
        self.assertEqual(Decimal(ex_date["ending_cash_dec"]), Decimal("9899.50"))
        self.assertEqual(Decimal(ex_date["ending_nav_dec"]), Decimal("9999.50"))
        self.assertEqual(Decimal(ex_date["nav"][-1]["receivables_dec"]), Decimal("0.80"))
        self.assertEqual(Decimal(pay_date["ending_cash_dec"]), Decimal("9999.00"))
        self.assertEqual(Decimal(pay_date["ending_nav_dec"]), Decimal("9999.00"))
        self.assertEqual(Decimal(pay_date["nav"][-1]["receivables_dec"]), Decimal("0"))
        self.assertEqual(Decimal(pay_date["signals"][0]["position_net_pnl_dec"]), Decimal("-1.00"))

    def test_PAPER_007_sale_does_not_remove_an_earned_dividend(self):
        rec = self.recommendation(day="03-16", horizon=5)
        after_sale = self.simulate(rec, as_of=stamp("03-23"))
        after_payment = self.simulate(rec, as_of=stamp("03-25"))
        self.assertEqual(len(after_sale["fills"]), 2)
        self.assertEqual(Decimal(after_sale["ending_cash_dec"]), Decimal("9998.20"))
        self.assertEqual(Decimal(after_sale["ending_nav_dec"]), Decimal("9999.00"))
        self.assertEqual(Decimal(after_payment["ending_cash_dec"]), Decimal("9999.00"))
        self.assertEqual(Decimal(after_payment["ending_nav_dec"]), Decimal("9999.00"))

    def test_PAPER_008_costs_can_reverse_positive_gross_return(self):
        scenarios = (
            (WalletConfig(commission_dec="5.00"), Decimal("-2.00")),
            (WalletConfig(half_spread_bps_dec="250", slippage_bps_dec="200"), Decimal("-2.36")),
        )
        for config, expected_pnl in scenarios:
            with self.subTest(config=config):
                report = self.simulate(self.recommendation(), config=config)
                signal = report["signals"][0]
                self.assertEqual(Decimal(signal["gross_asset_return_dec"]), Decimal("0.08"))
                self.assertEqual(Decimal(signal["position_net_pnl_dec"]), expected_pnl)
                self.assertEqual(signal["absolute_result"], "NEGATIVE")

    def test_PAPER_009_and_010_avoid_and_hold_cannot_open_positions(self):
        report = self.simulate(
            self.recommendation("avoid", action=Action.AVOID),
            self.recommendation("hold", action=Action.HOLD),
        )
        self.assertEqual(report["fills"], [])
        self.assertEqual(report["lots"], [])
        self.assertEqual(Decimal(report["ending_nav_dec"]), Decimal("10000.00"))
        self.assertEqual(Decimal(report["wallet_pnl_dec"]), Decimal("0"))

    def test_PAPER_011_and_017_missing_stale_and_insufficient_open_cannot_fill(self):
        scenarios = (
            ("03-25", "PARTIAL_NOT_SUPPORTED"),
            ("03-26", "MARKET_OBSERVATION_UNAVAILABLE"),
            ("03-27", "MARKET_STALE"),
        )
        for day, reason in scenarios:
            with self.subTest(reason=reason):
                rec = self.recommendation(day=day, horizon=2)
                report = self.simulate(rec, as_of=stamp("04-02"))
                self.assertEqual(report["fills"], [])
                self.assertEqual(Decimal(report["ending_nav_dec"]), Decimal("10000.00"))
                self.assertEqual(report["signals"][0]["disposition"], "UNFILLED")
                self.assertEqual(report["signals"][0]["reason_code"], reason)

    def test_PAPER_012_future_horizon_remains_provisional(self):
        report = self.simulate(self.recommendation(), as_of=stamp("01-16"))
        self.assertEqual(len(report["fills"]), 1)
        signal = report["signals"][0]
        self.assertEqual(signal["disposition"], "FILLED_OPEN")
        self.assertIsNone(signal["position_net_pnl_dec"])
        self.assertIsNone(signal["net_follow_return_dec"])

    def test_PAPER_012_unpublished_horizon_close_cannot_mature(self):
        rec = self.recommendation()
        before_release = self.simulate(rec, as_of=stamp("02-13", "16:00:00"))
        after_release = self.simulate(rec, as_of=stamp("02-13", "16:05:00"))
        self.assertEqual(len(before_release["fills"]), 1)
        self.assertIsNone(before_release["signals"][0]["position_net_pnl_dec"])
        self.assertEqual(before_release["status"], "NOT_CERTIFIED")
        self.assertIsNone(before_release["ending_nav_dec"])
        self.assertEqual(len(after_release["fills"]), 2)
        self.assertEqual(after_release["signals"][0]["disposition"], "MATURED")

    def test_PAPER_016_holiday_is_not_an_eligible_open(self):
        report = self.simulate(self.recommendation(day="01-16", horizon=1), as_of=stamp("01-20"))
        self.assertEqual(len(report["fills"]), 2)
        self.assertEqual(parse_time(report["fills"][0]["effective_at"]), stamp("01-20", "09:00:00"))
        self.assertEqual(parse_time(report["fills"][1]["effective_at"]), stamp("01-20", "16:00:00"))

    def test_PAPER_018_unpriced_delisting_stays_in_population(self):
        rec = self.recommendation(day="03-30", horizon=10)
        report = self.simulate(rec, as_of=stamp("04-16"))
        self.assertEqual(len(report["fills"]), 1)
        self.assertEqual(len(report["signals"]), 1)
        self.assertEqual(report["signals"][0]["disposition"], "CENSORED")
        self.assertIsNone(report["ending_nav_dec"])

    def test_PAPER_019_late_correction_cannot_rewrite_contemporaneous_fill_or_run(self):
        close_at = stamp("02-13", "16:00:00")
        after_correction = stamp("04-02")
        self.assertEqual(self.market.bar(EQ1, close_at, after_correction).price_dec, "108.10")
        with PaperStore(":memory:") as store:
            frozen = store.freeze((self.recommendation(),), self.market)
            original = simulate_wallet(store.db, self.market, frozen, as_of=stamp("02-13"))
            replay = simulate_wallet(store.db, self.market, frozen, as_of=after_correction)
            self.assertNotEqual(original["run_id"], replay["run_id"])
            self.assertEqual(original["fills"], replay["fills"])
            self.assertEqual(original, read_wallet_run(store.db, original["run_id"]))

    def test_PAPER_020_concurrent_replays_cannot_overallocate_cash(self):
        recs = (self.recommendation("first"), self.recommendation("second"))
        config = WalletConfig(initial_cash_dec="100.50")
        with TemporaryDirectory() as folder:
            db_path = Path(folder) / "paper.sqlite"
            with PaperStore(db_path) as store:
                frozen = store.freeze(recs, self.market)
            ready = Barrier(2, timeout=5)

            def run():
                with PaperStore(db_path) as store:
                    ready.wait()
                    return simulate_wallet(store.db, self.market, frozen, as_of=stamp("02-13"), config=config)

            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(lambda _: run(), range(2)))
            self.assertEqual(results[0], results[1])
            self.assertEqual(len(results[0]["fills"]), 2)
            self.assertEqual(Decimal(results[0]["ending_cash_dec"]), Decimal("107.50"))
            reasons = [signal["reason_code"] for signal in results[0]["signals"]]
            self.assertEqual(reasons.count("INSUFFICIENT_CASH"), 1)


if __name__ == "__main__":
    unittest.main()
