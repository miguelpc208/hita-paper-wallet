"""Check the user-facing synthetic risk examples."""

from decimal import Decimal
import json
import subprocess
import sys
import unittest


class RiskCliTests(unittest.TestCase):
    def review(self, scenario):
        process = subprocess.run(
            [sys.executable, "-m", "hita_paper", "risk", "--scenario", scenario],
            capture_output=True, encoding="utf-8", check=True,
        )
        return json.loads(process.stdout)

    def test_exact_caps_allow_and_a_cent_over_each_cap_blocks(self):
        limits = {Decimal("150"), Decimal("2000"), Decimal("600")}
        at_limit = self.review("at-limit")
        over_limit = self.review("over-limit")
        self.assertEqual(at_limit["status"], "ALLOW")
        self.assertEqual(over_limit["status"], "BLOCK")
        self.assertEqual(over_limit["reason_code"], "SYNTHETIC_PER_TRADE_RISK_LIMIT")
        for report, delta in ((at_limit, Decimal("0")), (over_limit, Decimal("0.01"))):
            checks_by_limit = {
                Decimal(check["limit_dec"]): check
                for check in report["checks"] if check["operator"] == "<="
            }
            self.assertEqual(set(checks_by_limit), limits)
            for limit, check in checks_by_limit.items():
                self.assertEqual(Decimal(check["observed_dec"]) - limit, delta)

    def test_missing_base_and_stale_measurement_block(self):
        missing = self.review("missing-base")
        self.assertEqual(missing["reason_code"], "MISSING_APPROVED_DENOMINATOR")
        self.assertEqual(missing["status"], "BLOCK")
        self.assertIsNone(missing["capital_base_dec"])
        stale = self.review("stale-exposure")
        self.assertEqual(stale["reason_code"], "UNVALUED_RISK_EXPOSURE")
        self.assertEqual(stale["status"], "BLOCK")
        self.assertTrue(any(check.get("input_reason_code") == "MARKET_STALE" for check in stale["checks"]))

    def test_overlap_reports_current_and_proposed_components(self):
        result = self.review("overlap")
        self.assertEqual(result["status"], "BLOCK")
        self.assertEqual(result["reason_code"], "SYNTHETIC_POSITION_SIZE_LIMIT")
        self.assertEqual(result["overlap_components"]["existing_same_instrument_value_dec"], "1950.00")
        self.assertEqual(
            {check["reason_code"] for check in result["checks"] if check["status"] == "BLOCK"},
            {"SYNTHETIC_POSITION_SIZE_LIMIT", "SYNTHETIC_AGGREGATE_RISK_LIMIT"},
        )


if __name__ == "__main__":
    unittest.main()
