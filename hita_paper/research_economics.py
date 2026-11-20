"""Synthetic holdout comparisons through the shared paper ledger."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from typing import Iterable

from .contracts import Action, PolicyResult, Recommendation, SYNTHETIC_POLICY_VERSION, content_digest, time_text, utc
from .market import BENCHMARK, CASH, EQ1, MarketData
from .research_forecast import HOLDOUT, ForecastRow
from .research_labels import Opportunity, build_research_recommendation, decision_time, seeded_opportunity_key
from .shadow import evaluate_shadow, summarize_shadow
from .store import PaperStore
from .wallet import WalletConfig, simulate_wallet


ECONOMICS_ENGINE_VERSION = "hita.research.economics.v1"
_DEFAULT_SEED = 20261119
_CONTEXT = Context(prec=60, rounding=ROUND_HALF_EVEN)
_SELECTION_STRATEGIES = ("CASH", "POLICY_ONLY", "SEEDED_RANDOM", "RELATIVE_MOMENTUM", "RIDGE")


def _anchor(market: MarketData, start: datetime, config: WalletConfig) -> Recommendation:
    # A non-trading anchor gives even cash or a late first entry the same measurement window.
    suffix = content_digest((market.manifest_id, time_text(start), config.cost_model_id))[:24]
    return Recommendation(
        recommendation_id=f"research-window-{suffix}",
        decision_at=start,
        known_as_of=start,
        instrument_id=EQ1,
        action=Action.ABSTAIN,
        horizon_trading_days=1,
        source_snapshot_id=market.manifest_id,
        model_version=ECONOMICS_ENGINE_VERSION,
        risk_policy_version=SYNTHETIC_POLICY_VERSION,
        policy_result=PolicyResult.REVIEW,
        reason_codes=("ECONOMIC_MEASUREMENT_WINDOW",),
        benchmark_id=CASH,
        cost_model_id=config.cost_model_id,
        reference_price_dec=None,
        evidence_links=(),
        strategy_sleeve="RESEARCH_ONLY",
        model_id="ECONOMIC_MEASUREMENT",
    )


def _selection(
    strategy: str, opportunity: Opportunity, forecast: ForecastRow | None, seed: int,
    *, wallet_config: WalletConfig, root_manifest_id: str,
) -> str:
    if not opportunity.policy_eligible:
        return "POLICY_INELIGIBLE"
    if strategy == "POLICY_ONLY":
        return "SELECTED"
    if strategy == "CASH":
        return "NOT_SELECTED"
    if strategy == "SEEDED_RANDOM":
        # Use the lineage root so revisions and unrelated candidates cannot shift the draw.
        key = seeded_opportunity_key(opportunity, wallet_config, root_manifest_id)
        draw = int(content_digest((seed, key))[0], 16)
        return "SELECTED" if draw < 8 else "NOT_SELECTED"
    if forecast is None:
        return "NO_PREDICTION"
    prediction = forecast.momentum_prediction if strategy == "RELATIVE_MOMENTUM" else forecast.ridge_prediction
    if prediction is None:
        return "NO_PREDICTION"
    return "SELECTED" if prediction > 0 else "NOT_SELECTED"


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _trading_metrics(fills: list[dict], opening_capital: Decimal) -> dict:
    with localcontext(_CONTEXT):
        notional = sum((abs(Decimal(fill["notional_dec"])) for fill in fills), Decimal(0))
        commission = sum((Decimal(fill["commission_dec"]) for fill in fills), Decimal(0))
        spread_slippage = sum((Decimal(fill["spread_slippage_cost_dec"]) for fill in fills), Decimal(0))
        return {
            "buy_fill_count": sum(fill["side"] == "BUY" for fill in fills),
            "sell_fill_count": sum(fill["side"] == "SELL" for fill in fills),
            "gross_traded_notional_dec": format(notional, "f"),
            "gross_turnover_on_initial_cash_dec": format(notional / opening_capital, "f"),
            "commissions_dec": format(commission, "f"),
            "spread_and_slippage_dec": format(spread_slippage, "f"),
            "total_execution_cost_dec": format(commission + spread_slippage, "f"),
        }


def _summarize_wallet(report: dict, anchor_id: str) -> dict:
    signals = [signal for signal in report["signals"] if signal["recommendation_id"] != anchor_id]
    opening_capital = Decimal(report["initial_cash_dec"])
    marks_complete = all(mark["status"] == "VALUED" for mark in report["nav"])
    matched_marks_complete = all(mark["benchmark_nav_dec"] is not None for mark in report["nav"])
    return {
        "run_id": report["run_id"],
        "valuation_start_at": report["nav"][0]["at"],
        "ending_nav_status": report["status"],
        "valuation_history_complete": marks_complete,
        "ending_nav_dec": report["ending_nav_dec"],
        "wallet_return_dec": report["wallet_return_dec"],
        "wallet_pnl_dec": report["wallet_pnl_dec"],
        "max_drawdown_dec": report["max_drawdown_dec"],
        "selected_count": len(signals),
        "disposition_counts": _counts(item["disposition"] for item in signals),
        "reason_counts": _counts(item["reason_code"] for item in signals),
        "outcome_status_counts": _counts(item["outcome_status"] for item in signals),
        "open_lot_count": sum(lot["state"] != "CLOSED" for lot in report["lots"]),
        **_trading_metrics(report["fills"], opening_capital),
        "matched_benchmark": {
            "policy": report["benchmark_policy"],
            "ending_nav_dec": report["benchmark_ending_nav_dec"],
            "wallet_return_dec": report["benchmark_return_dec"],
            "excess_wallet_return_dec": report["wallet_excess_return_dec"],
            "valuation_history_complete": matched_marks_complete,
            **_trading_metrics(report["benchmark"]["fills"], opening_capital),
        },
    }


def _passive_benchmark(
    market: MarketData, store: PaperStore, anchor: Recommendation, cutoff: datetime, config: WalletConfig
) -> dict:
    for index, session in enumerate(market.sessions):
        decision_at = decision_time(session)
        if decision_at >= anchor.decision_at:
            break
    else:
        return {"status": "NO_SCHEDULED_ENTRY"}
    horizon = len(market.sessions) - index - 1
    if not horizon or decision_at > cutoff:
        return {"status": "NO_SCHEDULED_ENTRY"}
    benchmark = build_research_recommendation(
        market, session, horizon_sessions=horizon, wallet_config=config,
        instrument_id=BENCHMARK, benchmark_id=CASH,
    )
    store.freeze((benchmark,), market)
    report = simulate_wallet(store.db, market, (anchor, benchmark), as_of=cutoff, config=config)
    summary = _summarize_wallet(report, anchor.recommendation_id)
    # Here the comparator is zero-yield cash, so the nested matched-benchmark output would be redundant.
    del summary["matched_benchmark"]
    return {
        "status": "SIMULATED",
        "allocation_policy": "ONE_BENCHMARK_UNIT_AT_NEXT_OPEN_WITH_REMAINING_CAPITAL_IN_CASH",
        "scheduled_exit_at": time_text(market.sessions[-1].close_at),
        "originating_decision_at": time_text(decision_at),
        **summary,
    }


def evaluate_economics(
    market: MarketData,
    store: PaperStore,
    opportunities: Iterable[Opportunity],
    forecasts: Iterable[ForecastRow],
    *,
    evaluated_at: datetime,
    holdout_start_at: datetime,
    wallet_config: WalletConfig = WalletConfig(),
    seed: int = _DEFAULT_SEED,
) -> dict:
    """Compare selection rules over the same declared holdout and initial wallet funding."""
    cutoff, start = utc(evaluated_at), utc(holdout_start_at)
    market.verify_fixture()
    if type(seed) is not int:
        raise ValueError("The comparison seed must be an integer")
    candidates = sorted(
        (item for item in opportunities if start <= item.decision_at <= cutoff),
        key=lambda item: (item.decision_at, item.sample_id),
    )
    candidate_times = {item.sample_id: item.decision_at for item in candidates}
    if len(candidate_times) != len(candidates):
        raise ValueError("Duplicate holdout opportunity")
    by_candidate = {}
    for row in forecasts:
        sample_id = row.sample.sample_id
        if sample_id not in candidate_times:
            continue
        if (
            row.partition != HOLDOUT
            or sample_id in by_candidate
            or row.sample.decision_at != candidate_times[sample_id]
        ):
            raise ValueError("Forecast partition or identity differs from the holdout cohort")
        by_candidate[sample_id] = row

    result = {
        "engine_version": ECONOMICS_ENGINE_VERSION,
        "status": "EVALUATED" if start <= cutoff else "HOLDOUT_NOT_STARTED",
        "holdout_start_at": time_text(start),
        "evaluated_at": time_text(cutoff),
        "market_manifest_id": market.manifest_id,
        "initial_cash_dec": wallet_config.initial_cash_dec,
        "costs": asdict(wallet_config),
        "seed": seed,
        "candidate_count": len(candidates),
        "policy_eligible_count": sum(item.policy_eligible for item in candidates),
        "opportunity_status_counts": _counts(item.status for item in candidates),
        "opportunity_reason_counts": _counts(item.reason_code for item in candidates),
        "strategy_summaries": {},
        "shadow_baseline": None,
    }
    if start > cutoff:
        return result

    anchor = _anchor(market, start, wallet_config)
    store.freeze((anchor,), market)
    root_manifest_id = market.lineage_root_manifest_id
    for strategy in _SELECTION_STRATEGIES:
        records = [anchor]
        selected_ids = []
        selections = Counter()
        for item in candidates:
            choice = _selection(
                strategy, item, by_candidate.get(item.sample_id), seed,
                wallet_config=wallet_config, root_manifest_id=root_manifest_id,
            )
            selections[choice] += 1
            if choice == "SELECTED":
                if item.recommendation is None:
                    raise ValueError("Policy eligibility requires a frozen recommendation")
                records.append(item.recommendation)
                selected_ids.append(item.sample_id)
        run = simulate_wallet(store.db, market, records, as_of=cutoff, config=wallet_config)
        result["strategy_summaries"][strategy] = {
            "selection_counts": dict(sorted(selections.items())),
            "selected_sample_ids": selected_ids,
            **_summarize_wallet(run, anchor.recommendation_id),
        }
    result["strategy_summaries"]["BENCHMARK_BUY_AND_HOLD"] = _passive_benchmark(
        market, store, anchor, cutoff, wallet_config,
    )
    shadow_outcomes = (
        evaluate_shadow(item.recommendation, market, evaluated_at=cutoff)
        for item in candidates if item.recommendation is not None
    )
    result["shadow_baseline"] = {
        "return_basis": "GROSS_COUNTERFACTUAL_NO_PORTFOLIO_CAPITAL_OR_COST_CONSTRAINT",
        **summarize_shadow(shadow_outcomes).to_dict(),
    }
    return result
