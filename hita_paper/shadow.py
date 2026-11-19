"""Versioned, observation-only scoring of frozen synthetic recommendations."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from enum import StrEnum

from .contracts import Action, ContractError, PolicyResult, Recommendation, time_text, utc
from .market import CASH, MarketData

SHADOW_ENGINE_VERSION = "hita.shadow.v1"
_RETURN_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)
_CASH_RETURN = Decimal("0")


class ShadowStatus(StrEnum):
    UNMATURED = "UNMATURED"
    MATURED = "MATURED"
    CENSORED = "CENSORED"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


class RelativeResult(StrEnum):
    OUTPERFORMED = "OUTPERFORMED"
    UNDERPERFORMED = "UNDERPERFORMED"
    TIED = "TIED"
    NOT_COMPARABLE = "NOT_COMPARABLE"


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value == 0:
        return "0"
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


@dataclass(frozen=True, slots=True)
class ShadowOutcome:
    recommendation_id: str
    recommendation_content_sha256: str
    action: Action
    policy_result: PolicyResult
    recommendation_reason_codes: tuple[str, ...]
    state: ShadowStatus
    quality_gate: str
    reason_codes: tuple[str, ...]
    source_snapshot_id: str
    model_version: str
    risk_policy_version: str
    decision_at: datetime
    known_as_of: datetime
    evaluated_at: datetime
    benchmark_id: str
    entry_at: datetime | None = None
    horizon_at: datetime | None = None
    holding_at_decision: bool | None = None
    asset_entry_price_dec: str | None = None
    asset_horizon_price_dec: str | None = None
    benchmark_entry_price_dec: str | None = None
    benchmark_horizon_price_dec: str | None = None
    gross_asset_return_dec: str | None = None
    gross_benchmark_return_dec: str | None = None
    asset_excess_return_dec: str | None = None
    action_counterfactual: str | None = None
    action_excess_return_dec: str | None = None
    observed_opportunity_dec: str | None = None
    absolute_result: str = "NOT_APPLICABLE"
    relative_result: RelativeResult = RelativeResult.NOT_COMPARABLE
    observation_ids: tuple[str, ...] = ()
    corporate_action_ids: tuple[str, ...] = ()
    engine_version: str = SHADOW_ENGINE_VERSION
    mode: str = "PAPER_ONLY"

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["action"] = self.action.value
        result["policy_result"] = self.policy_result.value
        result["state"] = self.state.value
        result["relative_result"] = self.relative_result.value
        for key in ("decision_at", "known_as_of", "evaluated_at", "entry_at", "horizon_at"):
            value = result[key]
            if value is not None:
                result[key] = time_text(value)
        for key in ("recommendation_reason_codes", "reason_codes", "observation_ids", "corporate_action_ids"):
            result[key] = list(result[key])
        return result


@dataclass(frozen=True, slots=True)
class _Leg:
    result: Decimal | None = None
    entry_price_dec: str | None = None
    horizon_price_dec: str | None = None
    observation_ids: tuple[str, ...] = ()
    corporate_action_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    censored: bool = False


def _total_return(
    market: MarketData,
    instrument_id: str,
    entry_at: datetime,
    horizon_at: datetime,
    evaluated_at: datetime,
    role: str,
) -> _Leg:
    if instrument_id == CASH:
        return _Leg(result=_CASH_RETURN)

    entry = market.bar(instrument_id, entry_at, entry_at)
    exit_mark = market.bar(instrument_id, horizon_at, evaluated_at)
    actions = market.actions(instrument_id, entry_at, horizon_at, evaluated_at)
    leg = _Leg(
        entry_price_dec=entry.price_dec if entry else None,
        horizon_price_dec=exit_mark.price_dec if exit_mark else None,
        observation_ids=tuple(item.observation_id for item in (entry, exit_mark) if item),
        corporate_action_ids=tuple(action.action_id for action in actions),
    )
    if any(action.kind == "DELIST" for action in actions):
        return replace(leg, censored=True, reason_codes=(f"{role}_DELISTING_TERMS_UNAVAILABLE",))

    if entry is None or exit_mark is None:
        reasons = []
        if entry is None:
            reasons.append(f"{role}_ENTRY_PRICE_UNAVAILABLE_AT_OPEN")
        if exit_mark is None:
            reasons.append(f"{role}_HORIZON_PRICE_UNAVAILABLE")
        return replace(leg, reason_codes=tuple(reasons))

    quantity = Decimal("1")
    dividends = Decimal("0")
    # Dividends accrue at the ex-date, including when payment follows the horizon.
    for action in sorted(actions, key=lambda item: (item.effective_at, item.kind != "SPLIT", item.action_id)):
        if action.kind == "SPLIT":
            quantity *= Decimal(action.ratio_dec)
        elif action.kind == "DIVIDEND":
            dividends += quantity * Decimal(action.amount_dec)
        else:
            return replace(leg, censored=True, reason_codes=(f"{role}_ACTION_TERMS_UNAVAILABLE",))

    price_at_entry = Decimal(entry.price_dec)
    horizon_value = quantity * Decimal(exit_mark.price_dec) + dividends
    return replace(leg, result=(horizon_value - price_at_entry) / price_at_entry)


def _relative(value: Decimal | None) -> RelativeResult:
    if value is None:
        return RelativeResult.NOT_COMPARABLE
    if value > 0:
        return RelativeResult.OUTPERFORMED
    if value < 0:
        return RelativeResult.UNDERPERFORMED
    return RelativeResult.TIED


def _absolute(value: Decimal | None) -> str:
    if value is None:
        return "NOT_APPLICABLE"
    if value > 0:
        return "POSITIVE"
    if value < 0:
        return "NEGATIVE"
    return "FLAT"


def evaluate_shadow(
    recommendation: Recommendation,
    market: MarketData,
    *,
    evaluated_at: datetime,
    holding_at_decision: bool | None = None,
) -> ShadowOutcome:
    """Score qualified gross observations from the next open to the Nth session close.

    Cash earns a zero return. The equity and benchmark legs include split-adjusted
    shares and unreinvested gross dividend entitlements. There are no orders or
    costs in the shadow book. The open is pinned to opening-time knowledge and
    horizon observations use the specified evaluation cutoff.
    """
    evaluated_at = utc(evaluated_at)
    if evaluated_at < recommendation.decision_at:
        raise ContractError("TEMPORAL_EVALUATION_BEFORE_DECISION", "evaluated_at precedes decision_at")
    if holding_at_decision is not None and type(holding_at_decision) is not bool:
        raise ContractError("VALIDATION_INVALID_HOLDING", "holding_at_decision must be a boolean or null")
    market.validate_recommendation(recommendation)

    source_quality = "POLICY_BLOCKED" if recommendation.policy_result == PolicyResult.BLOCK else "VALID"
    outcome = ShadowOutcome(
        recommendation_id=recommendation.recommendation_id,
        recommendation_content_sha256=recommendation.content_sha256,
        action=recommendation.action,
        policy_result=recommendation.policy_result,
        recommendation_reason_codes=recommendation.reason_codes,
        state=ShadowStatus.UNMATURED,
        quality_gate=source_quality,
        reason_codes=(),
        source_snapshot_id=market.manifest_id,
        model_version=recommendation.model_version,
        risk_policy_version=recommendation.risk_policy_version,
        decision_at=recommendation.decision_at,
        known_as_of=recommendation.known_as_of,
        evaluated_at=evaluated_at,
        benchmark_id=recommendation.benchmark_id,
        holding_at_decision=holding_at_decision,
    )
    entry = market.next_open(recommendation.decision_at)
    if entry is None:
        return replace(
            outcome,
            state=ShadowStatus.DATA_INSUFFICIENT,
            quality_gate="INVALID_DATA",
            reason_codes=("ENTRY_SESSION_OUTSIDE_FIXTURE",),
        )
    outcome = replace(outcome, entry_at=entry.open_at)
    horizon = market.horizon(entry.open_at, recommendation.horizon_trading_days)
    if horizon is None:
        return replace(
            outcome,
            state=ShadowStatus.DATA_INSUFFICIENT,
            quality_gate="INVALID_DATA",
            reason_codes=("HORIZON_SESSION_OUTSIDE_FIXTURE",),
        )
    outcome = replace(outcome, horizon_at=horizon.close_at)
    if evaluated_at < horizon.close_at:
        return replace(outcome, reason_codes=("HORIZON_NOT_REACHED",))

    with localcontext(_RETURN_CONTEXT):
        asset = _total_return(
            market, recommendation.instrument_id, entry.open_at, horizon.close_at, evaluated_at, "ASSET"
        )
        benchmark = _total_return(
            market, recommendation.benchmark_id, entry.open_at, horizon.close_at, evaluated_at, "BENCHMARK"
        )
        excess = None
        if asset.result is not None and benchmark.result is not None:
            excess = asset.result - benchmark.result
        outcome = replace(
            outcome,
            asset_entry_price_dec=asset.entry_price_dec,
            asset_horizon_price_dec=asset.horizon_price_dec,
            benchmark_entry_price_dec=benchmark.entry_price_dec,
            benchmark_horizon_price_dec=benchmark.horizon_price_dec,
            gross_asset_return_dec=_decimal_text(asset.result),
            gross_benchmark_return_dec=_decimal_text(benchmark.result),
            asset_excess_return_dec=_decimal_text(excess),
            observation_ids=tuple(dict.fromkeys(asset.observation_ids + benchmark.observation_ids)),
            corporate_action_ids=tuple(dict.fromkeys(asset.corporate_action_ids + benchmark.corporate_action_ids)),
        )
        if asset.result is None or benchmark.result is None:
            return replace(
                outcome,
                state=ShadowStatus.CENSORED if asset.censored or benchmark.censored else ShadowStatus.DATA_INSUFFICIENT,
                quality_gate="INVALID_DATA",
                reason_codes=asset.reason_codes + benchmark.reason_codes,
            )
        return _classify_matured(outcome, asset.result, benchmark.result)


def _classify_matured(outcome: ShadowOutcome, asset_return: Decimal, benchmark_return: Decimal) -> ShadowOutcome:
    absolute = None
    action_excess = None
    counterfactual = None
    opportunity = None
    reasons = ()
    if outcome.action == Action.CONSIDER_ADD:
        absolute = asset_return
        action_excess = asset_return - benchmark_return
        counterfactual = "ENTER_ASSET_VS_BENCHMARK"
    elif outcome.action == Action.AVOID:
        action_excess = benchmark_return - asset_return
        counterfactual = "BENCHMARK_VS_ENTER_ASSET"
    elif outcome.action in (Action.HOLD, Action.CONSIDER_REDUCE):
        if outcome.holding_at_decision is not True:
            reasons = ("HOLDING_UNKNOWN" if outcome.holding_at_decision is None else "NO_EXISTING_HOLDING",)
        elif outcome.action == Action.HOLD:
            absolute = asset_return
            action_excess = asset_return - _CASH_RETURN
            counterfactual = "HOLD_ASSET_VS_EXIT_TO_CASH"
        else:
            action_excess = _CASH_RETURN - asset_return
            counterfactual = "REDUCE_TO_CASH_VS_HOLD_ASSET"
    else:
        opportunity = asset_return - benchmark_return
        reasons = ("NO_DIRECTIONAL_CLAIM",)

    return replace(
        outcome,
        state=ShadowStatus.MATURED,
        reason_codes=reasons,
        action_counterfactual=counterfactual,
        action_excess_return_dec=_decimal_text(action_excess),
        observed_opportunity_dec=_decimal_text(opportunity),
        absolute_result=_absolute(absolute),
        relative_result=_relative(action_excess),
    )


@dataclass(frozen=True, slots=True)
class ShadowScorecard:
    candidate_count: int
    matured_count: int
    directional_count: int
    directional_wins: int
    directional_losses: int
    directional_ties: int
    blocked_directional_count: int
    abstention_count: int
    decision_coverage_dec: str | None
    directional_coverage_dec: str | None
    state_counts: tuple[tuple[str, int], ...]
    action_counts: tuple[tuple[str, int], ...]
    policy_counts: tuple[tuple[str, int], ...]
    engine_version: str = SHADOW_ENGINE_VERSION
    mode: str = "PAPER_ONLY"

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        for key in ("state_counts", "action_counts", "policy_counts"):
            result[key] = dict(result[key])
        return result


def summarize_shadow(outcomes: Iterable[ShadowOutcome]) -> ShadowScorecard:
    """Summarize a single evaluation population containing one outcome per candidate."""
    population = tuple(outcomes)
    candidate_count = len(population)
    if len({item.recommendation_id for item in population}) != candidate_count:
        raise ContractError("VALIDATION_DUPLICATE_SHADOW_CANDIDATE", "scorecard has multiple evaluations of a candidate")

    states = Counter(item.state for item in population)
    actions = Counter(item.action for item in population)
    policies = Counter(item.policy_result for item in population)
    directional = [item for item in population if item.relative_result != RelativeResult.NOT_COMPARABLE]
    results = Counter(item.relative_result for item in directional)
    matured_count = states[ShadowStatus.MATURED]
    with localcontext(_RETURN_CONTEXT):
        decision_coverage = Decimal(matured_count) / candidate_count if candidate_count else None
        directional_coverage = Decimal(len(directional)) / candidate_count if candidate_count else None

    return ShadowScorecard(
        candidate_count=candidate_count,
        matured_count=matured_count,
        directional_count=len(directional),
        directional_wins=results[RelativeResult.OUTPERFORMED],
        directional_losses=results[RelativeResult.UNDERPERFORMED],
        directional_ties=results[RelativeResult.TIED],
        blocked_directional_count=sum(item.policy_result == PolicyResult.BLOCK for item in directional),
        abstention_count=actions[Action.ABSTAIN],
        decision_coverage_dec=_decimal_text(decision_coverage),
        directional_coverage_dec=_decimal_text(directional_coverage),
        state_counts=tuple((state.value, states[state]) for state in ShadowStatus),
        action_counts=tuple((action.value, actions[action]) for action in Action),
        policy_counts=tuple((policy.value, policies[policy]) for policy in PolicyResult),
    )
