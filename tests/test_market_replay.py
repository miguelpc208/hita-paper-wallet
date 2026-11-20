"""Point-in-time report contracts for quotes and corporate actions."""

from datetime import datetime
import unittest

from hita_paper.contracts import ContractError, parse_time
from hita_paper.market import EQ1, load_market
from hita_paper.market_replay import replay_market_actions, replay_market_quote


class MarketReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v1 = load_market()
        cls.v2 = load_market("v2")
        cls.original_close = parse_time("2026-02-13T16:00:00Z")

    def test_published_quote_waits_for_availability(self):
        missing = replay_market_quote(
            self.v1, instrument_id=EQ1, at=self.original_close,
            known_as_of=parse_time("2026-02-13T16:01:00Z"),
        )
        self.assertEqual(missing["status"], "MISSING")
        self.assertIsNone(missing["selected"])
        self.assertEqual(missing["visible_revisions"], [])

        first = replay_market_quote(
            self.v1, instrument_id=EQ1, at=self.original_close,
            known_as_of=parse_time("2026-02-13T16:05:00Z"),
        )
        self.assertEqual(first["status"], "QUALIFIED")
        self.assertEqual(first["selected"]["price_dec"], "108.00")
        self.assertEqual(first["selected"]["available_at"], "2026-02-13T16:05:00Z")
        self.assertEqual(first["selected"]["evidence"]["source_manifest_id"], self.v1.manifest_id)

    def test_correction_visibility_depends_on_cutoff_and_version(self):
        before = replay_market_quote(
            self.v2, instrument_id=EQ1, at=self.original_close,
            known_as_of=parse_time("2026-04-03T12:09:59Z"),
        )
        at_release = parse_time("2026-04-03T12:10:00Z")
        released = replay_market_quote(
            self.v2, instrument_id=EQ1, at=self.original_close, known_as_of=at_release,
        )
        original = replay_market_quote(
            self.v1, instrument_id=EQ1, at=self.original_close, known_as_of=at_release,
        )
        self.assertEqual(before["visible_revisions"], original["visible_revisions"])
        self.assertEqual([row["price_dec"] for row in before["visible_revisions"]], ["108.00", "108.10"])
        self.assertEqual(before["selected"]["price_dec"], "108.10")
        self.assertEqual(released["selected"]["price_dec"], "108.25")
        self.assertEqual(len(released["visible_revisions"]), 3)
        self.assertEqual(released["selected"]["published_at"], "2026-04-03T12:00:00Z")
        self.assertNotEqual(original["manifest_id"], released["manifest_id"])
        self.assertEqual(released["market_version"], "v2")

    def test_stale_slot_omits_value_and_gap_is_not_carried_forward(self):
        gap_at = parse_time("2026-03-27T09:00:00Z")
        stale_at = parse_time("2026-03-30T09:00:00Z")
        missing = replay_market_quote(self.v2, instrument_id=EQ1, at=gap_at, known_as_of=stale_at)
        stale = replay_market_quote(self.v2, instrument_id=EQ1, at=stale_at, known_as_of=stale_at)
        self.assertEqual(missing["status"], "MISSING")
        self.assertEqual(missing["visible_revisions"], [])
        self.assertEqual(stale["status"], "STALE")
        self.assertIsNone(stale["selected"])
        self.assertEqual(stale["visible_revisions"][0]["condition"], "STALE")
        self.assertIsNone(stale["visible_revisions"][0]["price_dec"])
        self.assertIsNone(stale["visible_revisions"][0]["volume_dec"])

    def test_second_synthetic_equity_has_distinct_listing_and_version_scope(self):
        at = parse_time("2026-04-10T16:00:00Z")
        known_as_of = parse_time("2026-04-10T16:05:00Z")
        args = {"instrument_id": "EQ2_SYNTHETIC", "at": at, "known_as_of": known_as_of}
        qualified = replay_market_quote(self.v2, **args)
        self.assertEqual(qualified["listing_id"], "EQ2_XSYN")
        self.assertEqual(qualified["status"], "QUALIFIED")
        self.assertEqual(qualified["selected"]["evidence"]["instrument_id"], "EQ2_SYNTHETIC")
        with self.assertRaises(ContractError) as caught:
            replay_market_quote(self.v1, **args)
        self.assertEqual(caught.exception.code, "SOURCE_FIXTURE_INSTRUMENT")

    def test_actions_require_notice_by_cutoff_and_keep_terms(self):
        args = {
            "instrument_id": EQ1,
            "start_at": parse_time("2026-03-17T00:00:00Z"),
            "end_at": parse_time("2026-04-10T09:00:00Z"),
        }
        early = replay_market_actions(self.v2, **args, known_as_of=parse_time("2026-03-17T00:00:00Z"))
        before_delist = replay_market_actions(self.v2, **args, known_as_of=parse_time("2026-04-03T11:59:59Z"))
        at_delist_notice = replay_market_actions(
            self.v2, **args, known_as_of=parse_time("2026-04-03T12:00:00Z"),
        )
        self.assertEqual([action["kind"] for action in early["actions"]], ["SPLIT"])
        self.assertEqual([action["kind"] for action in before_delist["actions"]], ["SPLIT", "DIVIDEND"])
        self.assertEqual(at_delist_notice["status"], "KNOWN")
        self.assertEqual([action["kind"] for action in at_delist_notice["actions"]], ["SPLIT", "DIVIDEND", "DELIST"])
        split, dividend, delisting = at_delist_notice["actions"]
        self.assertEqual(split["ratio_dec"], "2")
        self.assertEqual(dividend["amount_dec"], "0.40")
        self.assertEqual(dividend["payable_at"], "2026-03-25T09:00:00Z")
        self.assertEqual(delisting["effective_at"], "2026-04-10T09:00:00Z")

    def test_reversed_action_windows_and_naive_quote_times_raise(self):
        with self.assertRaises(ContractError) as window:
            replay_market_actions(
                self.v1, instrument_id=EQ1, start_at=parse_time("2026-03-27T09:00:00Z"),
                end_at=self.original_close, known_as_of=self.original_close,
            )
        self.assertEqual(window.exception.code, "VALIDATION_TIME_RANGE")
        with self.assertRaises(ContractError) as naive:
            replay_market_quote(
                self.v1, instrument_id=EQ1, at=datetime(2026, 2, 13, 16, 0),
                known_as_of=self.original_close,
            )
        self.assertEqual(naive.exception.code, "VALIDATION_TIMESTAMP")


if __name__ == "__main__":
    unittest.main()
