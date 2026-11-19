"""Point-in-time feature acceptance tests against the shipped synthetic fixture."""

from dataclasses import replace
from decimal import Decimal, ROUND_UP, localcontext
from statistics import stdev
import unittest

from hita_paper.contracts import ContractError, content_digest, parse_time
from hita_paper.market import BENCHMARK, EQ1, load_market
from hita_paper.research_features import build_feature_snapshot


class ResearchFeatureTests(unittest.TestCase):
    def setUp(self):
        self.market = load_market()

    def snapshot(self, date, cutoff=None, **kwargs):
        return build_feature_snapshot(
            self.market,
            close_at=parse_time(f"2026-{date}T16:00:00Z"),
            known_as_of=parse_time(cutoff or f"2026-{date}T16:30:00Z"),
            **kwargs,
        )

    def test_matches_arithmetic_oracle_on_six_ordinary_closes(self):
        snapshot = self.snapshot("01-15")
        self.assertEqual(snapshot.status, "READY")
        self.assertEqual(snapshot.window_start_at, parse_time("2026-01-08T16:00:00Z"))
        asset_start = Decimal("100.41")
        asset_final = Decimal("99.00")
        benchmark_start = Decimal("99.36")
        benchmark_final = Decimal("99.95")
        asset_return = asset_final / asset_start - 1
        benchmark_return = benchmark_final / benchmark_start - 1
        prices = [100.41, 100.12, 99.84, 99.56, 99.28, 99.00]
        returns = [second / first - 1 for first, second in zip(prices, prices[1:])]
        self.assertAlmostEqual(Decimal(snapshot.asset_return_dec), asset_return, places=24)
        self.assertAlmostEqual(Decimal(snapshot.benchmark_return_dec), benchmark_return, places=24)
        self.assertAlmostEqual(Decimal(snapshot.relative_strength_dec), asset_return - benchmark_return, places=24)
        self.assertAlmostEqual(float(snapshot.realized_volatility_dec), stdev(returns), places=15)
        self.assertEqual(len(snapshot.evidence_links), 12)

    def test_unreleased_close_creates_a_gap_without_filling_from_prior_day(self):
        snapshot = self.snapshot("01-15", "2026-01-15T16:04:59Z")
        at_close = snapshot.close_at
        self.assertEqual(snapshot.status, "DATA_INSUFFICIENT")
        self.assertIsNone(snapshot.asset_close_price_dec)
        self.assertIsNone(snapshot.asset_return_dec)
        self.assertEqual({gap.instrument_id for gap in snapshot.gaps}, {EQ1, BENCHMARK})
        self.assertTrue(all(gap.close_at == at_close for gap in snapshot.gaps))
        self.assertTrue(all(link.effective_at < at_close for link in snapshot.evidence_links))
        self.assertLessEqual(snapshot.feature_available_at_max, snapshot.known_as_of)

    def test_weekend_and_calendar_holiday_are_skipped(self):
        snapshot = self.snapshot("01-20", lookback_sessions=2)
        dates = {link.effective_at.date().isoformat() for link in snapshot.evidence_links}
        self.assertEqual(snapshot.status, "READY")
        self.assertEqual(dates, {"2026-01-15", "2026-01-16", "2026-01-20"})

    def test_short_history_retains_anchor_evidence_and_unscheduled_close_fails_closed(self):
        short = self.snapshot("01-08")
        self.assertEqual(short.reason_codes, ("INSUFFICIENT_HISTORY",))
        self.assertEqual(short.asset_close_price_dec, "100.41")
        self.assertIsNone(short.asset_return_dec)
        holiday = self.snapshot("01-19")
        self.assertEqual(holiday.reason_codes, ("UNSCHEDULED_CLOSE",))
        self.assertEqual(holiday.evidence_links, ())

    def test_split_and_ex_date_entitlement_preserve_wealth_in_fixture(self):
        snapshot = self.snapshot("03-23")
        self.assertEqual(snapshot.asset_close_price_dec, "49.60")
        self.assertEqual(snapshot.asset_return_dec, "0")
        self.assertEqual(snapshot.realized_volatility_dec, "0")
        self.assertEqual(snapshot.corporate_action_ids, ("EQ1/SPLIT/2026-03-18", "EQ1/DIVIDEND/2026-03-23"))
        self.assertEqual(snapshot.corporate_actions[-1].payable_at, parse_time("2026-03-25T09:00:00Z"))
        before = self.snapshot("03-20")
        self.assertNotIn("EQ1/DIVIDEND/2026-03-23", before.corporate_action_ids)

    def test_revision_is_visible_at_its_availability_without_rewriting_first_snapshot(self):
        original = self.snapshot("02-13")
        just_before = self.snapshot("02-13", "2026-04-02T11:59:59Z")
        before_payload = original.to_dict()
        revised = self.snapshot("02-13", "2026-04-02T12:00:00Z")
        self.assertEqual(original.asset_return_dec, just_before.asset_return_dec)
        self.assertEqual(original.asset_close_price_dec, "108.00")
        self.assertEqual(revised.asset_close_price_dec, "108.10")
        self.assertGreater(Decimal(revised.asset_return_dec), Decimal(original.asset_return_dec))
        self.assertEqual(revised.feature_available_at_max, revised.known_as_of)
        self.assertNotEqual(original.content_sha256, revised.content_sha256)
        self.assertEqual(original.to_dict(), before_payload)
        self.assertIn("EQ1_SYNTHETIC/2026-02-13/CLOSE/2",
                      {link.observation_id for link in revised.evidence_links})

    def test_delisted_listing_remains_visible_after_event_leaves_window(self):
        snapshot = self.snapshot("04-23")
        self.assertEqual(snapshot.status, "DATA_INSUFFICIENT")
        self.assertIn("DELISTING_TERMS_UNAVAILABLE", snapshot.reason_codes)
        self.assertIn("EQ1/DELIST/2026-04-10", snapshot.corporate_action_ids)
        self.assertIsNone(snapshot.asset_close_price_dec)
        self.assertIsNone(snapshot.benchmark_return_dec)

    def test_snapshot_digest_is_deterministic_and_market_fixture_is_bound(self):
        usual = self.snapshot("01-15").to_dict()
        with localcontext() as context:
            context.prec = 5
            context.rounding = ROUND_UP
            self.assertEqual(self.snapshot("01-15").to_dict(), usual)
        digest = usual.pop("content_sha256")
        self.assertEqual(digest, content_digest(usual))
        with self.assertRaises(ContractError) as error:
            build_feature_snapshot(replace(self.market, provenance="changed"), close_at=self.market.sessions[5].close_at,
                                   known_as_of=self.market.sessions[6].open_at)
        self.assertEqual(error.exception.code, "SOURCE_FIXTURE_CHECKSUM")

    def test_invalid_lookback_and_unknown_instrument_have_stable_errors(self):
        with self.assertRaises(ContractError) as lookback:
            self.snapshot("01-15", lookback_sessions=1)
        self.assertEqual(lookback.exception.code, "VALIDATION_LOOKBACK")
        with self.assertRaises(ContractError) as instrument:
            self.snapshot("01-15", benchmark_id="UNKNOWN")
        self.assertEqual(instrument.exception.code, "SOURCE_FIXTURE_INSTRUMENT")


if __name__ == "__main__":
    unittest.main()
