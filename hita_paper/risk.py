"""Explain the tactical controls used for synthetic paper-wallet entries."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from typing import Literal

from .contracts import SYNTHETIC_POLICY_VERSION


TRADE_RISK_LIMIT = Decimal("0.0015")
POSITION_LIMIT = Decimal("0.02")
OPEN_RISK_LIMIT = Decimal("0.006")

_ZERO = Decimal("0")
_MISSING_DENOMINATOR = "MISSING_APPROVED_DENOMINATOR"
_UNVALUED_EXPOSURE = "UNVALUED_RISK_EXPOSURE"
CheckStatus = Literal["PASS", "BLOCK", "UNKNOWN"]


def _amount_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value.is_zero():
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


@dataclass(frozen=True, slots=True)
class RiskCheck:
    rule_id: str
    status: CheckStatus
    observed: Decimal | None = None
    limit: Decimal | None = None
    denominator: Decimal | None = None
    operator: Literal[">", ">=", "<="] | None = None
    reason_code: str | None = None
    input_reason_code: str | None = None

    def to_dict(self) -> dict:
        check = {
            "rule_id": self.rule_id,
            "status": self.status,
            "observed_dec": _amount_text(self.observed),
            "limit_dec": _amount_text(self.limit),
            "denominator_dec": _amount_text(self.denominator),
            "operator": self.operator,
            "reason_code": self.reason_code,
        }
        if self.input_reason_code is not None:
            check["input_reason_code"] = self.input_reason_code
        return check


@dataclass(frozen=True, slots=True)
class RiskReview:
    checks: tuple[RiskCheck, ...]

    @property
    def reason_code(self) -> str | None:
        if not self.checks:
            return "MISSING_RISK_CHECKS"
        return next(
            (check.reason_code or "UNKNOWN_RISK_CHECK" for check in self.checks if check.status != "PASS"),
            None,
        )

    @property
    def status(self) -> Literal["ALLOW", "BLOCK"]:
        return "ALLOW" if self.reason_code is None else "BLOCK"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "policy_version": SYNTHETIC_POLICY_VERSION,
            "checks": [check.to_dict() for check in self.checks],
        }


def _finite(value: Decimal | None) -> Decimal | None:
    return value if isinstance(value, Decimal) and value.is_finite() else None


def _missing(value: Decimal | None) -> bool:
    return value is None or value < _ZERO


def _invalid_denominator(value: Decimal | None) -> bool:
    return value is None or value <= _ZERO


def check_capital_base(capital_base: Decimal | None) -> RiskCheck:
    """Explain whether the supplied synthetic denominator is usable."""
    observed = _finite(capital_base)
    missing = _invalid_denominator(observed)
    return RiskCheck(
        "CAPITAL_BASE", "UNKNOWN" if missing else "PASS", observed=observed,
        limit=_ZERO, operator=">", reason_code=_MISSING_DENOMINATOR if missing else None,
    )


def _minimum_check(planned_loss: Decimal | None, minimum_loss: Decimal | None) -> RiskCheck:
    if planned_loss is None or planned_loss <= _ZERO:
        status, reason = "UNKNOWN", "MISSING_PLANNED_LOSS"
    elif _missing(minimum_loss):
        status, reason = "UNKNOWN", "MISSING_ZERO_RECOVERY_LOSS_BOUND"
    elif planned_loss < minimum_loss:
        status, reason = "BLOCK", "PLANNED_LOSS_BELOW_ZERO_RECOVERY_BOUND"
    else:
        status, reason = "PASS", None
    return RiskCheck(
        "ZERO_RECOVERY_LOSS_BOUND", status, observed=planned_loss, limit=minimum_loss,
        operator=">=", reason_code=reason,
    )


def _cap(
    rule_id: str,
    observed: Decimal | None,
    denominator: Decimal | None,
    fraction: Decimal,
    *,
    breach: str,
    unavailable: str | None = None,
) -> RiskCheck:
    limit = None
    if not _invalid_denominator(denominator):
        # The sum of the coefficient sizes is sufficient for an exact product.
        precision = len(denominator.as_tuple().digits) + len(fraction.as_tuple().digits)
        with localcontext(Context(prec=precision, rounding=ROUND_HALF_EVEN)):
            limit = denominator * fraction
    if unavailable:
        status, reason = "UNKNOWN", unavailable
    elif limit is None:
        status, reason = "UNKNOWN", _MISSING_DENOMINATOR
    elif _missing(observed):
        status, reason = "UNKNOWN", _UNVALUED_EXPOSURE
    elif observed > limit:
        status, reason = "BLOCK", breach
    else:
        status, reason = "PASS", None
    return RiskCheck(rule_id, status, observed, limit, denominator, "<=", reason)


def evaluate_tactical_risk(
    *,
    capital_base: Decimal | None,
    planned_loss: Decimal | None,
    minimum_loss: Decimal | None,
    position_value: Decimal | None,
    position_base: Decimal | None,
    aggregate_risk: Decimal | None,
    aggregate_base: Decimal | None,
    exposure_reason: str | None = None,
) -> RiskReview:
    """Evaluate the existing caps on caller-supplied synthetic measurements.

    Position and total risk include the candidate. Each denominator is an input
    from the synthetic scenario. Both a breached limit and unavailable evidence
    block execution. Checks are ordered to retain the wallet's primary reasons.
    """
    capital_check = check_capital_base(capital_base)
    planned_loss = _finite(planned_loss)
    minimum_loss = _finite(minimum_loss)
    position_value = _finite(position_value)
    position_base = _finite(position_base)
    aggregate_risk = _finite(aggregate_risk)
    aggregate_base = _finite(aggregate_base)

    exposure_unknown = exposure_reason is not None or _missing(position_value) or _missing(aggregate_risk)
    unavailable_exposure = _UNVALUED_EXPOSURE if exposure_unknown else None
    return RiskReview((
        capital_check,
        _minimum_check(planned_loss, minimum_loss),
        _cap(
            "TRADE_RISK_CAP", planned_loss, capital_check.observed, TRADE_RISK_LIMIT,
            breach="SYNTHETIC_PER_TRADE_RISK_LIMIT",
            unavailable="MISSING_PLANNED_LOSS" if planned_loss is None or planned_loss <= _ZERO else None,
        ),
        RiskCheck(
            "EXPOSURE_QUALIFICATION", "UNKNOWN" if exposure_unknown else "PASS",
            reason_code=unavailable_exposure, input_reason_code=exposure_reason,
        ),
        _cap(
            "POSITION_CAP", position_value, position_base, POSITION_LIMIT,
            breach="SYNTHETIC_POSITION_SIZE_LIMIT", unavailable=unavailable_exposure,
        ),
        _cap(
            "AGGREGATE_RISK_CAP", aggregate_risk, aggregate_base, OPEN_RISK_LIMIT,
            breach="SYNTHETIC_AGGREGATE_RISK_LIMIT", unavailable=unavailable_exposure,
        ),
    ))
