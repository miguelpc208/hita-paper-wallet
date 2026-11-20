"""Bundled examples of tactical paper risk decisions."""

from decimal import Decimal

from .risk import evaluate_tactical_risk


RISK_SCENARIOS = ("at-limit", "over-limit", "missing-base", "stale-exposure", "overlap")
_SCENARIO_DESCRIPTIONS = {
    "at-limit": "The three measured exposures are exactly at their limits.",
    "over-limit": "Each of the three measured exposures exceeds its limit by one cent.",
    "missing-base": "The denominator for the synthetic calculation is unavailable.",
    "stale-exposure": "A required open position mark is stale.",
    "overlap": "A new position adds to exposure already held in the same instrument.",
}


def run_risk_demo(scenario: str) -> dict:
    """Review hand-calculated scenarios with one hypothetical EUR capital base."""
    if scenario not in RISK_SCENARIOS:
        raise ValueError(f"Unknown synthetic risk scenario: {scenario}")
    base = Decimal("100000.00")
    minimum_loss = Decimal("101.00")
    planned_loss = Decimal("150.00")
    position_value = Decimal("2000.00")
    aggregate_risk = Decimal("600.00")
    exposure_reason = None
    overlap = None
    if scenario == "over-limit":
        planned_loss = Decimal("150.01")
        position_value = Decimal("2000.01")
        aggregate_risk = Decimal("600.01")
    elif scenario == "missing-base":
        base = None
    elif scenario == "stale-exposure":
        exposure_reason = "MARKET_STALE"
        position_value = None
        aggregate_risk = None
    elif scenario == "overlap":
        position_value = Decimal("2050.00")
        aggregate_risk = Decimal("601.00")
        overlap = {
            "proposed_position_value_dec": "100.00",
            "existing_same_instrument_value_dec": "1950.00",
            "proposed_planned_loss_dec": "150.00",
            "existing_portfolio_risk_dec": "451.00",
        }

    review = evaluate_tactical_risk(
        capital_base=base,
        planned_loss=planned_loss,
        minimum_loss=minimum_loss,
        position_value=position_value,
        position_base=base,
        aggregate_risk=aggregate_risk,
        aggregate_base=base,
        exposure_reason=exposure_reason,
    )
    return {
        "mode": "PAPER_ONLY",
        "data_class": "SYNTHETIC",
        "scenario": scenario,
        "description": _SCENARIO_DESCRIPTIONS[scenario],
        "measurement_source": f"BUILT_IN_SYNTHETIC_RISK_FIXTURE:{scenario}",
        "currency": "EUR",
        "capital_base_dec": format(base, "f") if base is not None else None,
        "overlap_components": overlap,
        **review.to_dict(),
    }
