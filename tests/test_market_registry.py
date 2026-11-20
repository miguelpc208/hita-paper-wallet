"""Check pinned synthetic fixture versions and point-in-time lineage."""

from dataclasses import replace
from unittest import TestCase

from hita_paper.contracts import ContractError, parse_time
from hita_paper.demo_inputs import demo_recommendations
from hita_paper.market import (
    BENCHMARK, DEFAULT_MARKET_VERSION, EQ1, EQ2, FIXTURE_SHA256, FIXTURE_VERSION,
    _verify_lineage, load_market, market_catalog, market_versions,
)


_CLOSE = parse_time("2026-02-13T16:00:00Z")
_PUBLISHED = parse_time("2026-04-03T12:00:00Z")
_AVAILABLE = parse_time("2026-04-03T12:10:00Z")


class MarketRegistryTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v1 = load_market("v1")
        cls.v2 = load_market("v2")

    def test_catalog_reports_pinned_manifests_and_their_lineage(self):
        self.assertEqual(DEFAULT_MARKET_VERSION, "v1")
        self.assertEqual(market_versions(), ("v1", "v2"))
        self.assertIs(load_market(), self.v1)
        self.assertEqual(self.v1.manifest_id, f"{FIXTURE_VERSION}:{FIXTURE_SHA256}")
        self.assertEqual(self.v1.lineage_root_manifest_id, self.v1.manifest_id)
        self.assertEqual(self.v2.lineage_root_manifest_id, self.v1.manifest_id)
        old, new = market_catalog()
        self.assertEqual(old["manifest_id"], self.v1.manifest_id)
        self.assertIsNone(old["parent_manifest_id"])
        self.assertEqual(new["manifest_id"], self.v2.manifest_id)
        self.assertEqual(new["parent_version"], "v1")
        self.assertEqual(new["parent_manifest_id"], self.v1.manifest_id)
        self.assertEqual(new["source_kind"], "SYNTHETIC")
        self.assertEqual(new["usage_scope"], "SYNTHETIC_RESEARCH_AND_PAPER_REPLAY")
        self.assertEqual(set(new["instrument_ids"]), {EQ1, EQ2, BENCHMARK})

    def test_revisions_require_availability_and_keep_earlier_quotes(self):
        before_original_release = parse_time("2026-02-13T16:04:59Z")
        self.assertEqual(self.v2.visible_revisions(EQ1, _CLOSE, before_original_release), ())
        before_availability = self.v2.visible_revisions(EQ1, _CLOSE, _PUBLISHED)
        self.assertEqual(before_availability, self.v1.visible_revisions(EQ1, _CLOSE, _AVAILABLE))
        self.assertEqual([row.price_dec for row in before_availability], ["108.00", "108.10"])
        revised = self.v2.visible_revisions(EQ1, _CLOSE, _AVAILABLE)
        self.assertEqual(revised[:-1], before_availability)
        self.assertEqual(revised[-1].observation_id, "EQ1_SYNTHETIC/2026-02-13/CLOSE/3")
        self.assertEqual(revised[-1].price_dec, "108.25")
        self.assertEqual(self.v2.bar(EQ1, _CLOSE, _AVAILABLE), revised[-1])
        self.assertEqual(self.v1.bar(EQ1, _CLOSE, _AVAILABLE), before_availability[-1])

    def test_second_instrument_has_its_own_listing_and_observations(self):
        instrument = next(item for item in self.v2.instruments if item.instrument_id == EQ2)
        self.assertEqual(instrument.listing_id, "EQ2_XSYN")
        self.assertEqual(instrument.currency, "EUR")
        quotation = self.v2.bar(EQ2, _CLOSE, _AVAILABLE)
        self.assertEqual(quotation.instrument_id, EQ2)
        self.assertEqual(self.v2.evidence(quotation).source_manifest_id, self.v2.manifest_id)
        self.assertIsNone(self.v1.raw_bar(EQ2, _CLOSE, _AVAILABLE))
        with self.assertRaises(ContractError) as raised:
            self.v1.visible_revisions(EQ2, _CLOSE, _AVAILABLE)
        self.assertEqual(raised.exception.code, "SOURCE_FIXTURE_INSTRUMENT")

    def test_a_pinned_dataset_cannot_be_replaced_or_used_for_another_decision(self):
        modified = replace(self.v2, observations=self.v2.observations[:-1])
        self.assertEqual(modified.manifest_id, self.v2.manifest_id)
        with self.assertRaises(ContractError) as counterfeit:
            modified.visible_revisions(EQ1, _CLOSE, _AVAILABLE)
        self.assertEqual(counterfeit.exception.code, "SOURCE_FIXTURE_CHECKSUM")
        with self.assertRaises(ContractError) as other_version:
            self.v2.validate_recommendation(demo_recommendations(self.v1)[0])
        self.assertEqual(other_version.exception.code, "SOURCE_MANIFEST_MISMATCH")
        with self.assertRaises(ContractError) as unsupported:
            load_market("synthetic_market_v2.json")
        self.assertEqual(unsupported.exception.code, "SOURCE_FIXTURE_VERSION")

    def test_lineage_blocks_removed_inherited_records_or_backdated_corrections(self):
        inherited = self.v1.observations[0]
        altered = replace(self.v2, observations=tuple(row for row in self.v2.observations if row != inherited))
        with self.assertRaises(ContractError) as removed:
            _verify_lineage(altered, self.v1)
        self.assertEqual(removed.exception.code, "SOURCE_FIXTURE_LINEAGE")

        earlier = parse_time("2026-03-01T12:00:00Z")
        correction = self.v2.bar(EQ1, _CLOSE, _AVAILABLE)
        recreated = replace(correction, published_at=earlier, available_at=earlier)
        altered = replace(self.v2, observations=tuple(
            recreated if row == correction else row for row in self.v2.observations
        ))
        with self.assertRaises(ContractError) as backdated:
            _verify_lineage(altered, self.v1)
        self.assertEqual(backdated.exception.code, "SOURCE_FIXTURE_LINEAGE")
