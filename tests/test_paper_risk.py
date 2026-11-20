"""Verify exact synthetic risk decisions and stable evidence explanations."""

from decimal import Decimal, localcontext
import unittest

from hita_paper.risk import evaluate_tactical_risk


class PaperRiskTests(unittest.TestCase):
    def evaluate(self, **changes):
        values = {
            "capital_base": Decimal("100000"),
            "planned_loss": Decimal("150"),
            "minimum_loss": Decimal("120"),
            "position_value": Decimal("2000"),
            "position_base": Decimal("100000"),
            "aggregate_risk": Decimal("600"),
            "aggregate_base": Decimal("100000"),
        }
        return evaluate_tactical_risk(**(values | changes))

    def test_equal_caps_and_recovery_bound_are_inclusive(self):
        result = self.evaluate(minimum_loss=Decimal("150")).to_dict()
        checks = {check["rule_id"]: check for check in result["checks"]}
        self.assertEqual(result["status"], "ALLOW")
        self.assertEqual(result["policy_version"], "synthetic-risk-v1")
        self.assertIsNone(result["reason_code"])
        for rule, boundary, operator in (
            ("ZERO_RECOVERY_LOSS_BOUND", "150", ">="),
            ("TRADE_RISK_CAP", "150", "<="),
            ("POSITION_CAP", "2000", "<="),
            ("AGGREGATE_RISK_CAP", "600", "<="),
        ):
            with self.subTest(rule=rule):
                self.assertEqual(checks[rule]["status"], "PASS")
                self.assertEqual(checks[rule]["observed_dec"], boundary)
                self.assertEqual(checks[rule]["limit_dec"], boundary)
                self.assertEqual(checks[rule]["operator"], operator)

    def test_each_cap_blocks_a_one_cent_breach(self):
        for field, value, reason in (
            ("planned_loss", "150.01", "SYNTHETIC_PER_TRADE_RISK_LIMIT"),
            ("position_value", "2000.01", "SYNTHETIC_POSITION_SIZE_LIMIT"),
            ("aggregate_risk", "600.01", "SYNTHETIC_AGGREGATE_RISK_LIMIT"),
        ):
            with self.subTest(field=field):
                review = self.evaluate(**{field: Decimal(value)})
                self.assertEqual(review.status, "BLOCK")
                self.assertEqual(review.reason_code, reason)
                check = next(check for check in review.to_dict()["checks"] if check["reason_code"] == reason)
                self.assertEqual(check["status"], "BLOCK")
                self.assertEqual(check["observed_dec"], value)

    def test_first_failure_keeps_wallet_priority(self):
        review = self.evaluate(planned_loss=Decimal("150.01"), minimum_loss=Decimal("151"),
                               position_value=Decimal("2000.01"), aggregate_risk=Decimal("600.01"))
        self.assertEqual(review.reason_code, "PLANNED_LOSS_BELOW_ZERO_RECOVERY_BOUND")
        self.assertEqual([check.reason_code for check in review.checks if check.status == "BLOCK"], [
            "PLANNED_LOSS_BELOW_ZERO_RECOVERY_BOUND", "SYNTHETIC_PER_TRADE_RISK_LIMIT",
            "SYNTHETIC_POSITION_SIZE_LIMIT", "SYNTHETIC_AGGREGATE_RISK_LIMIT",
        ])

    def test_invalid_or_missing_inputs_block(self):
        for field, value, reason in (
            ("capital_base", None, "MISSING_APPROVED_DENOMINATOR"),
            ("capital_base", Decimal("0"), "MISSING_APPROVED_DENOMINATOR"),
            ("capital_base", Decimal("NaN"), "MISSING_APPROVED_DENOMINATOR"),
            ("planned_loss", Decimal("-1"), "MISSING_PLANNED_LOSS"),
            ("minimum_loss", None, "MISSING_ZERO_RECOVERY_LOSS_BOUND"),
            ("position_base", Decimal("-100000"), "MISSING_APPROVED_DENOMINATOR"),
            ("aggregate_base", None, "MISSING_APPROVED_DENOMINATOR"),
            ("aggregate_risk", Decimal("Infinity"), "UNVALUED_RISK_EXPOSURE"),
            ("position_value", 100.0, "UNVALUED_RISK_EXPOSURE"),
        ):
            with self.subTest(field=field, value=value):
                review = self.evaluate(**{field: value})
                self.assertEqual(review.status, "BLOCK")
                self.assertEqual(review.reason_code, reason)

    def test_stale_price_blocks_even_with_precomputed_exposure_values(self):
        report = self.evaluate(exposure_reason="MARKET_STALE").to_dict()
        checks = {check["rule_id"]: check for check in report["checks"]}
        self.assertEqual(report["reason_code"], "UNVALUED_RISK_EXPOSURE")
        self.assertEqual(checks["EXPOSURE_QUALIFICATION"]["input_reason_code"], "MARKET_STALE")
        self.assertEqual(checks["POSITION_CAP"]["status"], "UNKNOWN")
        self.assertEqual(checks["AGGREGATE_RISK_CAP"]["status"], "UNKNOWN")

    def test_limits_are_independent_of_ambient_decimal_precision(self):
        arguments = {
            "capital_base": Decimal("99999.99"),
            "planned_loss": Decimal("149.999985"),
            "position_base": Decimal("99999.99"),
            "position_value": Decimal("1999.9998"),
            "aggregate_base": Decimal("99999.99"),
            "aggregate_risk": Decimal("599.99994"),
        }
        expected = self.evaluate(**arguments).to_dict()
        with localcontext() as context:
            context.prec = 3
            actual = self.evaluate(**arguments).to_dict()
        self.assertEqual(actual, expected)
        self.assertEqual(actual["status"], "ALLOW")


if __name__ == "__main__":
    unittest.main()
