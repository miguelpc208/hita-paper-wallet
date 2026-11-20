"""Versioned synthetic decisions and a deterministic tactical risk check."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

from .contracts import Action, PolicyResult, Recommendation, SYNTHETIC_POLICY_VERSION, utc
from .market import BENCHMARK, CASH, EQ1, MarketData
from .risk import evaluate_tactical_risk

DEMO_INITIAL_CASH_DEC = "10000.00"
DEMO_CAPITAL_BASE_DEC = "100000.00"
DEMO_COMMISSION_DEC = "0.50"
DEMO_ENTRY_BUFFER_DEC = "10.00"
DEMO_EVALUATED_AT = datetime(2026, 3, 13, 17, tzinfo=UTC)
DEMO_MODEL_VERSION = "synthetic-price-rule-v1"
DEMO_COST_MODEL_ID = "synthetic-cost-v1"

_RISK_CONTEXT = Context(prec=60, rounding=ROUND_HALF_EVEN)
_DECISION_RISK_REASONS = {
    "SYNTHETIC_PER_TRADE_RISK_LIMIT": "TACTICAL_TRADE_RISK_LIMIT",
    "SYNTHETIC_POSITION_SIZE_LIMIT": "TACTICAL_POSITION_LIMIT",
    "SYNTHETIC_AGGREGATE_RISK_LIMIT": "TACTICAL_OPEN_RISK_LIMIT",
}


@dataclass(frozen=True, slots=True)
class PolicyCheck:
    policy_result: PolicyResult
    reason_codes: tuple[str, ...]
    planned_loss_dec: str | None = None


def assess_synthetic_add(
    reference_price: Decimal,
    quantity: Decimal,
    *,
    synthetic_capital_base: Decimal | None,
    open_tactical_risk: Decimal | None,
    commission: Decimal = Decimal(DEMO_COMMISSION_DEC),
    entry_price_adjustment: Decimal = Decimal("0"),
) -> PolicyCheck:
    """Assess a new unleveraged position using the synthetic scenario's assumptions."""
    if synthetic_capital_base is None:
        return PolicyCheck(PolicyResult.BLOCK, ("MISSING_APPROVED_DENOMINATOR",))
    if open_tactical_risk is None:
        return PolicyCheck(PolicyResult.BLOCK, ("MISSING_OPEN_TACTICAL_RISK",))
    inputs = (
        reference_price, quantity, synthetic_capital_base, open_tactical_risk,
        commission, entry_price_adjustment,
    )
    if (
        any(not isinstance(value, Decimal) or not value.is_finite() for value in inputs)
        or min(reference_price, quantity, synthetic_capital_base) <= 0
        or open_tactical_risk < 0
        or commission < 0
        or not 0 <= entry_price_adjustment < 1
    ):
        return PolicyCheck(PolicyResult.BLOCK, ("INVALID_SYNTHETIC_RISK_INPUT",))

    with localcontext(_RISK_CONTEXT):
        entry_ceiling = reference_price + Decimal(DEMO_ENTRY_BUFFER_DEC)
        position_notional = entry_ceiling * (1 + entry_price_adjustment) * quantity
        # Allow a complete loss of the unleveraged position and both fixed fees.
        planned_loss = position_notional + 2 * commission
        review = evaluate_tactical_risk(
            capital_base=synthetic_capital_base,
            planned_loss=planned_loss,
            minimum_loss=planned_loss,
            position_value=position_notional,
            position_base=synthetic_capital_base,
            aggregate_risk=open_tactical_risk + planned_loss,
            aggregate_base=synthetic_capital_base,
        )
    if review.status == "BLOCK":
        reasons = tuple(
            _DECISION_RISK_REASONS[check.reason_code]
            for check in review.checks
            if check.reason_code in _DECISION_RISK_REASONS
        )
        return PolicyCheck(
            PolicyResult.BLOCK, reasons or ("INVALID_SYNTHETIC_RISK_INPUT",), format(planned_loss, "f"),
        )
    return PolicyCheck(
        PolicyResult.ALLOW, ("SYNTHETIC_TACTICAL_LIMITS_PASSED",), format(planned_loss, "f")
    )


def demo_recommendations(
    market: MarketData, *, as_of: datetime | None = None
) -> tuple[Recommendation, ...]:
    """Freeze inputs for four covered cases using only each session's published close."""
    cutoff = utc(as_of) if as_of is not None else None
    first_decision = datetime(2026, 1, 15, 16, 30, tzinfo=UTC)
    scenarios = (
        ("demo-add", first_decision, DEMO_CAPITAL_BASE_DEC),
        ("demo-add-missing-cbr", first_decision, None),
        ("demo-abstain", datetime(2026, 1, 28, 16, 30, tzinfo=UTC), DEMO_CAPITAL_BASE_DEC),
        ("demo-avoid", datetime(2026, 2, 13, 16, 30, tzinfo=UTC), DEMO_CAPITAL_BASE_DEC),
    )
    return tuple(
        _demo_decision(market, recommendation_id, decision_at, capital_base_dec)
        for recommendation_id, decision_at, capital_base_dec in scenarios
        if cutoff is None or decision_at <= cutoff
    )


def _demo_decision(
    market: MarketData,
    recommendation_id: str,
    decision_at: datetime,
    capital_base_dec: str | None,
) -> Recommendation:
    close_at = decision_at - timedelta(minutes=30)
    observation = market.bar(EQ1, close_at, decision_at)
    reference_price_dec = observation.price_dec if observation is not None else None
    evidence_links = (market.evidence(observation),) if observation is not None else ()
    benchmark_id = BENCHMARK
    quantity_dec = None
    if reference_price_dec is None:
        action = Action.ABSTAIN
        policy = PolicyCheck(PolicyResult.REVIEW, ("MISSING_QUALIFIED_DECISION_CLOSE",))
    else:
        reference_price = Decimal(reference_price_dec)
        if reference_price < Decimal("100.00"):
            action = Action.CONSIDER_ADD
            quantity_dec = "1"
            policy = assess_synthetic_add(
                reference_price,
                Decimal(quantity_dec),
                synthetic_capital_base=Decimal(capital_base_dec) if capital_base_dec else None,
                open_tactical_risk=Decimal("0"),
            )
        elif reference_price >= Decimal("105.00"):
            action = Action.AVOID
            benchmark_id = CASH
            policy = PolicyCheck(PolicyResult.ALLOW, ("SYNTHETIC_CASH_ALTERNATIVE",))
        else:
            action = Action.ABSTAIN
            policy = PolicyCheck(PolicyResult.REVIEW, ("SYNTHETIC_NEUTRAL_PRICE_RANGE",))

    return Recommendation(
        recommendation_id=recommendation_id,
        decision_at=decision_at,
        known_as_of=decision_at,
        instrument_id=EQ1,
        action=action,
        horizon_trading_days=20,
        source_snapshot_id=market.manifest_id,
        model_version=DEMO_MODEL_VERSION,
        risk_policy_version=SYNTHETIC_POLICY_VERSION,
        policy_result=policy.policy_result,
        reason_codes=policy.reason_codes,
        benchmark_id=benchmark_id,
        cost_model_id=DEMO_COST_MODEL_ID,
        reference_price_dec=reference_price_dec,
        evidence_links=evidence_links,
        quantity_dec=quantity_dec,
        capital_base_dec=capital_base_dec if quantity_dec is not None else None,
        planned_loss_dec=policy.planned_loss_dec,
    )
