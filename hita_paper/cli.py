"""Run and inspect HITA's local synthetic paper-wallet demonstration."""

import argparse
import json
import sqlite3

from .contracts import parse_time, time_text, utc
from .demo_inputs import (
    DEMO_CAPITAL_BASE_DEC,
    DEMO_COMMISSION_DEC,
    DEMO_ENTRY_BUFFER_DEC,
    DEMO_EVALUATED_AT,
    DEMO_INITIAL_CASH_DEC,
    demo_recommendations,
)
from .market import load_market
from .recovery import copy_paper_database
from .research import DEFAULT_EVALUATED_AT, DEFAULT_HORIZON_SESSIONS, read_research_run, run_research
from .shadow import SHADOW_ENGINE_VERSION, evaluate_shadow, summarize_shadow
from .store import PaperStore
from .wallet import WalletConfig, read_wallet_run, simulate_wallet


def _utc(value: str):
    try:
        return parse_time(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def run_demo(db_path=":memory:", *, as_of=DEMO_EVALUATED_AT, wallet_config=None) -> dict:
    """Freeze and replay only the synthetic signals published by the requested clock."""
    as_of = utc(as_of)
    market = load_market()
    records = sorted(
        demo_recommendations(market, as_of=as_of),
        key=lambda record: record.recommendation_id,
    )
    if not records:
        raise ValueError("The evaluation time precedes all demo recommendations")
    if wallet_config is None:
        wallet_config = WalletConfig(initial_cash_dec=DEMO_INITIAL_CASH_DEC)
    with PaperStore(db_path) as paper:
        frozen = paper.freeze(records, market)
        outcomes = tuple(evaluate_shadow(record, market, evaluated_at=as_of) for record in frozen)
        shadow = paper.save_shadow_run({
            "engine_version": SHADOW_ENGINE_VERSION,
            "market_manifest_id": market.manifest_id,
            "evaluated_at": time_text(as_of),
            "recommendation_hashes": {record.recommendation_id: record.content_sha256 for record in frozen},
        }, [outcome.to_dict() for outcome in outcomes], summarize_shadow(outcomes).to_dict())
        wallet = simulate_wallet(paper.db, market, frozen, as_of=as_of, config=wallet_config)
    return {
        "schema_version": "hita.paper.demo.v1",
        "mode": "PAPER_ONLY",
        "data_class": "SYNTHETIC",
        "evaluated_at": time_text(as_of),
        "market_manifest_id": market.manifest_id,
        "decision_policy_assumptions": {
            "fictional_risk_reference_eur_dec": DEMO_CAPITAL_BASE_DEC,
            "decision_price_buffer_eur_dec": DEMO_ENTRY_BUFFER_DEC,
            "reference_commission_per_side_eur_dec": DEMO_COMMISSION_DEC,
            "loss_basis": "FULL_UNLEVERAGED_POSITION_PLUS_TWO_FEES",
            "cash_and_risk_reference_are_separate": True,
            "approved_capital_base_rule_status": "UNRESOLVED",
        },
        "recommendations": [record.to_dict() for record in frozen],
        "shadow_signal_book": shadow,
        "paper_wallet": wallet,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="HITA synthetic research and paper portfolio evaluation")
    commands = parser.add_subparsers(dest="command", required=True)

    demo = commands.add_parser("demo", help="run the end-to-end synthetic demonstration")
    demo.add_argument("--db", default=":memory:", help="paper SQLite file, or in-memory by default")
    demo.add_argument("--as-of", default=DEMO_EVALUATED_AT, type=_utc)

    decisions = commands.add_parser("decisions", help="read frozen recommendations")
    decisions.add_argument("--db", required=True)
    research = commands.add_parser("research", help="compare synthetic research baselines out of sample")
    research.add_argument("--db", default=":memory:", help="paper SQLite file, or in-memory by default")
    research.add_argument("--as-of", default=DEFAULT_EVALUATED_AT, type=_utc)
    research.add_argument("--horizon-sessions", default=DEFAULT_HORIZON_SESSIONS, type=int)
    research.add_argument("--full", action="store_true", help="include dated features, predictions and outcome provenance")

    report = commands.add_parser("report", help="read a saved evaluation by run ID")
    report.add_argument("kind", choices=("shadow", "wallet", "research"))
    report.add_argument("run_id")
    report.add_argument("--db", required=True)

    for name, help_text in (
        ("backup", "save a verified copy of a paper database"),
        ("restore", "restore a verified paper database to a new file"),
    ):
        operation = commands.add_parser(name, help=help_text)
        operation.add_argument("--db", required=True, help="source paper SQLite file")
        operation.add_argument("--to", required=True, help="new destination SQLite file")

    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            result = run_demo(args.db, as_of=args.as_of)
        elif args.command == "research":
            run = run_research(args.db, as_of=args.as_of, horizon_sessions=args.horizon_sessions)
            result = run if args.full else {
                "run_id": run["run_id"],
                "protocol": run["protocol"],
                "inputs": {key: value for key, value in run["inputs"].items() if key != "evidence_digests"},
                "predictive_metrics": run["predictive"]["metrics"],
                "economic": run["economic"],
            }
        elif args.command in ("backup", "restore"):
            result = copy_paper_database(args.db, args.to)
        else:
            with PaperStore(args.db, readonly=True) as paper:
                if args.command == "decisions":
                    result = {"mode": "PAPER_ONLY", "recommendations": [
                        record.to_dict() for record in paper.recommendations()
                    ]}
                elif args.kind == "shadow":
                    result = paper.shadow_run(args.run_id)
                elif args.kind == "research":
                    result = read_research_run(paper, args.run_id)
                else:
                    result = read_wallet_run(paper.db, args.run_id)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(2, f"hita-paper: {error}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
