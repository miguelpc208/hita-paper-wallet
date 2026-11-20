"""Reproducible synthetic baseline experiments on the published paper engine."""

from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
import json
import sqlite3

from .contracts import content_digest, time_text, utc
from .market import DEFAULT_MARKET_VERSION, load_market
from .research_economics import evaluate_economics
from .research_features import build_feature_snapshot
from .research_forecast import (
    ForecastConfig, ForecastSample, evaluate_forecasts, forecast_rows, holdout_training_cutoff,
)
from .research_labels import build_opportunity, decision_time
from .store import PaperStore, _transaction, canonical_json
from .wallet import ENGINE_VERSION as WALLET_VERSION, WalletConfig


RESEARCH_VERSION = "hita.research.experiment.v1"
DEFAULT_EVALUATED_AT = datetime(2026, 4, 24, 17, tzinfo=UTC)
HOLDOUT_START_AT = datetime(2026, 3, 16, 16, 30, tzinfo=UTC)
LOOKBACK_SESSIONS = 5
DEFAULT_HORIZON_SESSIONS = 5
RESEARCH_RANDOM_SEED = 20261119
FORECAST_CONFIG = ForecastConfig(
    feature_names=("asset_return_dec", "relative_strength_dec", "realized_volatility_dec"),
    feature_scales=(Decimal("0.05"), Decimal("0.05"), Decimal("0.02")),
    momentum_feature_index=1,
    holdout_start_at=HOLDOUT_START_AT,
    min_train_samples=12,
    embargo_sessions=2,
    ridge_alpha=Decimal("1"),
)


def _save_experiment(db: sqlite3.Connection, identity: dict, report: dict) -> dict:
    run_id = "research-" + content_digest(identity)
    result = {"run_id": run_id, **report}
    payload = canonical_json(result)
    with _transaction(db):
        db.execute("""CREATE TABLE IF NOT EXISTS research_experiments (
            run_id TEXT PRIMARY KEY,
            source_manifest_id TEXT NOT NULL,
            output_sha256 TEXT NOT NULL,
            payload TEXT NOT NULL CHECK (json_valid(payload))
        ) STRICT""")
        for operation in ("UPDATE", "DELETE"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS research_experiments_deny_{operation.lower()}
                BEFORE {operation} ON research_experiments
                BEGIN SELECT RAISE(ABORT, 'HITA research history is append-only'); END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS research_experiments_deny_replacement
            BEFORE INSERT ON research_experiments
            WHEN EXISTS (SELECT 1 FROM research_experiments WHERE run_id = NEW.run_id)
            BEGIN SELECT RAISE(ABORT, 'HITA research history is append-only'); END""")
        existing = db.execute(
            "SELECT payload FROM research_experiments WHERE run_id = ?", (run_id,)
        ).fetchone()
        if existing is not None:
            if existing["payload"] != payload:
                raise ValueError("Research output changed for identical versioned inputs")
            return result
        db.execute("INSERT INTO research_experiments VALUES (?, ?, ?, ?)", (
            run_id, identity["source_manifest_id"], content_digest(result), payload,
        ))
    return result


def read_research_run(store: PaperStore, run_id: str) -> dict:
    table = store.db.execute(
        "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'research_experiments'"
    ).fetchone()
    if table is None:
        raise ValueError("This paper database has no saved research runs")
    row = store.db.execute(
        "SELECT source_manifest_id, output_sha256, payload FROM research_experiments WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown research run: {run_id}")
    result = json.loads(row["payload"])
    if (
        content_digest(result) != row["output_sha256"]
        or result["run_id"] != run_id
        or "research-" + content_digest(result["inputs"]) != run_id
        or result["inputs"]["source_manifest_id"] != row["source_manifest_id"]
    ):
        raise ValueError("Saved research report failed its integrity check")
    return result


def run_research(
    db_path=":memory:", *, as_of=DEFAULT_EVALUATED_AT, horizon_sessions=DEFAULT_HORIZON_SESSIONS,
    market_version=DEFAULT_MARKET_VERSION,
) -> dict:
    """Run the fixed chronological protocol and persist an append-only experiment report."""
    as_of = utc(as_of)
    market = load_market(market_version)
    if type(horizon_sessions) is not int or not 1 <= horizon_sessions < len(market.sessions):
        raise ValueError(f"Horizon must be between 1 and {len(market.sessions) - 1} trading sessions")
    wallet_config = WalletConfig()
    opportunities = []
    features = []
    samples = []
    with PaperStore(db_path) as store:
        for session in market.sessions:
            decision_at = decision_time(session)
            if decision_at > as_of:
                break
            snapshot = build_feature_snapshot(
                market, close_at=session.close_at, known_as_of=decision_at, lookback_sessions=LOOKBACK_SESSIONS,
            )
            opportunity = build_opportunity(
                market, store, session, horizon_sessions=horizon_sessions, evaluated_at=as_of,
                wallet_config=wallet_config,
            )
            features.append(snapshot)
            opportunities.append(opportunity)
            values = None
            if snapshot.status == "READY":
                values = tuple(Decimal(getattr(snapshot, name)) for name in FORECAST_CONFIG.feature_names)
            samples.append(ForecastSample(
                sample_id=opportunity.sample_id,
                decision_at=decision_at,
                feature_values=values,
                label=Decimal(opportunity.label_excess_return_dec) if opportunity.label_excess_return_dec is not None else None,
                label_start_at=opportunity.entry_at,
                label_end_at=opportunity.horizon_at,
                label_available_at=opportunity.label_available_at,
            ))

        predictions = forecast_rows(samples, FORECAST_CONFIG)
        development_cutoff = holdout_training_cutoff(
            (decision_time(session) for session in market.sessions), FORECAST_CONFIG,
        )
        predictive = evaluate_forecasts(
            samples, FORECAST_CONFIG, rows=predictions, development_label_cutoff=development_cutoff,
        )
        economics = evaluate_economics(
            market, store, opportunities, predictions, evaluated_at=as_of, wallet_config=wallet_config,
            seed=RESEARCH_RANDOM_SEED, holdout_start_at=HOLDOUT_START_AT,
        )
        identity = {
            "research_version": RESEARCH_VERSION,
            "wallet_engine_version": WALLET_VERSION,
            "economic_engine_version": economics["engine_version"],
            "source_manifest_id": market.manifest_id,
            "evaluated_at": time_text(as_of),
            "lookback_sessions": LOOKBACK_SESSIONS,
            "horizon_sessions": horizon_sessions,
            "wallet_config": asdict(wallet_config),
            "forecast_config": predictive["config"],
            "development_label_cutoff_at": predictive["metrics"]["DEVELOPMENT"]["scoring_label_cutoff_at"],
            "random_seed": RESEARCH_RANDOM_SEED,
            "evidence_digests": [
                {
                    "sample_id": opportunity.sample_id,
                    "feature_sha256": snapshot.content_sha256,
                    "label_sha256": opportunity.content_sha256,
                }
                for snapshot, opportunity in zip(features, opportunities)
            ],
        }
        report = {
            "inputs": identity,
            "protocol": {
                "mode": "SYNTHETIC_RESEARCH",
                "target": "NET_ASSET_RETURN_MINUS_MATCHED_NET_BENCHMARK_RETURN",
                "promotion_status": "RESEARCH_ONLY",
                "probability_calibration": "NOT_APPLICABLE_CONTINUOUS_TARGET",
            },
            "predictive": predictive,
            "economic": economics,
            "dataset": [
                {"sample_id": opportunity.sample_id, "features": snapshot.to_dict(), "outcome": opportunity.to_dict()}
                for snapshot, opportunity in zip(features, opportunities)
            ],
        }
        return _save_experiment(store.db, identity, report)
