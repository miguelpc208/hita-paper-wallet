"""Build point-in-time synthetic opportunities and label them through S2."""

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, localcontext

from .contracts import (
    Action,
    PolicyResult,
    Recommendation,
    SYNTHETIC_POLICY_VERSION,
    content_digest,
    time_text,
    utc,
)
from .demo_inputs import DEMO_CAPITAL_BASE_DEC, assess_synthetic_add
from .market import BENCHMARK, EQ1, CorporateAction, MarketData, Session
from .store import PaperStore
from .wallet import CLOSE_PUBLICATION_DELAY, WALLET_DECIMAL_CONTEXT, WalletConfig, simulate_wallet


LABEL_VERSION = "hita.research.labels.v1"
LABEL_TARGET = "S2_NET_EXCESS_RETURN"
DECISION_DELAY = timedelta(minutes=30)
_DELISTING_BLOCK_REASON = "KNOWN_DELISTING_WITHIN_HORIZON"


@dataclass(frozen=True, slots=True)
class Opportunity:
    sample_id: str
    source_snapshot_id: str
    decision_at: datetime
    close_at: datetime
    horizon_sessions: int
    entry_at: datetime | None
    horizon_at: datetime | None
    status: str
    reason_code: str
    recommendation: Recommendation | None = None
    label_available_at: datetime | None = None
    asset_net_return_dec: str | None = None
    benchmark_net_return_dec: str | None = None
    label_excess_return_dec: str | None = None
    wallet_run_id: str | None = None
    observation_ids: tuple[str, ...] = ()
    corporate_action_ids: tuple[str, ...] = ()

    @property
    def policy_eligible(self) -> bool:
        record = self.recommendation
        return (
            record is not None
            and record.action == Action.CONSIDER_ADD
            and record.policy_result == PolicyResult.ALLOW
        )

    def _payload(self) -> dict:
        result = asdict(self)
        for name in ("decision_at", "close_at", "entry_at", "horizon_at", "label_available_at"):
            instant = getattr(self, name)
            result[name] = time_text(instant) if instant is not None else None
        result["recommendation"] = self.recommendation.to_dict() if self.recommendation else None
        result["observation_ids"] = list(self.observation_ids)
        result["corporate_action_ids"] = list(self.corporate_action_ids)
        return {
            "schema_version": LABEL_VERSION,
            "target": LABEL_TARGET,
            "policy_eligible": self.policy_eligible,
            **result,
        }

    @property
    def content_sha256(self) -> str:
        return content_digest(self._payload())

    def to_dict(self) -> dict:
        payload = self._payload()
        return {**payload, "content_sha256": content_digest(payload)}


def decision_time(session: Session) -> datetime:
    """Use one fixed decision time after each scheduled synthetic close."""
    return session.close_at + DECISION_DELAY


def _sample_id(
    market: MarketData,
    session: Session,
    horizon_sessions: int,
    wallet_config: WalletConfig,
    instrument_id: str,
    benchmark_id: str,
) -> str:
    digest = content_digest({
        "manifest_id": market.manifest_id,
        "label_version": LABEL_VERSION,
        "decision_at": time_text(decision_time(session)),
        "horizon_sessions": horizon_sessions,
        "wallet_config": asdict(wallet_config),
        "instrument_id": instrument_id,
        "benchmark_id": benchmark_id,
    })
    return f"research-{session.date.isoformat()}-h{horizon_sessions}-{digest}"


def _declared_delistings(
    market: MarketData, instrument_id: str, decision_at: datetime, horizon_sessions: int
) -> tuple[CorporateAction, ...]:
    entry = market.next_open(decision_at)
    if entry is None:
        return ()
    horizon = market.horizon(entry.open_at, horizon_sessions)
    end = horizon.close_at if horizon is not None else market.sessions[-1].close_at
    return tuple(
        action for action in market.actions(instrument_id, decision_at, end, decision_at)
        if action.kind == "DELIST"
    )


def build_research_recommendation(
    market: MarketData,
    session: Session,
    *,
    horizon_sessions: int,
    wallet_config: WalletConfig = WalletConfig(),
    instrument_id: str = EQ1,
    benchmark_id: str = BENCHMARK,
) -> Recommendation:
    """Create the fixed one-share candidate on the synthetic decision clock."""
    market.verify_fixture()
    if session not in market.sessions:
        raise ValueError("Decision session must belong to the bundled market calendar")
    at = decision_time(session)
    observation = market.bar(instrument_id, session.close_at, at)
    if observation is None:
        raw = market.raw_bar(instrument_id, session.close_at, at)
        reason = "STALE_DECISION_CLOSE" if raw is not None else "MISSING_OR_UNRELEASED_DECISION_CLOSE"
        terms = {
            "action": Action.ABSTAIN,
            "policy_result": PolicyResult.REVIEW,
            "reason_codes": (reason,),
            "reference_price_dec": None,
            "evidence_links": (),
        }
    else:
        with localcontext(WALLET_DECIMAL_CONTEXT):
            policy = assess_synthetic_add(
                Decimal(observation.price_dec),
                Decimal("1"),
                synthetic_capital_base=Decimal(DEMO_CAPITAL_BASE_DEC),
                open_tactical_risk=Decimal("0"),
                commission=Decimal(wallet_config.commission_dec),
                entry_price_adjustment=wallet_config.price_adjustment,
            )
        delistings = _declared_delistings(market, instrument_id, at, horizon_sessions)
        reasons = policy.reason_codes
        if delistings:
            risk_blocks = reasons if policy.policy_result == PolicyResult.BLOCK else ()
            reasons = (_DELISTING_BLOCK_REASON, *risk_blocks)
        terms = {
            "action": Action.CONSIDER_ADD,
            "policy_result": PolicyResult.BLOCK if delistings else policy.policy_result,
            "reason_codes": reasons,
            "reference_price_dec": observation.price_dec,
            "evidence_links": (market.evidence(observation),),
            "quantity_dec": "1",
            "capital_base_dec": DEMO_CAPITAL_BASE_DEC,
            "planned_loss_dec": policy.planned_loss_dec,
        }
    recommendation = Recommendation(
        recommendation_id=_sample_id(market, session, horizon_sessions, wallet_config, instrument_id, benchmark_id),
        decision_at=at,
        known_as_of=at,
        instrument_id=instrument_id,
        benchmark_id=benchmark_id,
        horizon_trading_days=horizon_sessions,
        source_snapshot_id=market.manifest_id,
        model_id="SYNTHETIC_RESEARCH_OPPORTUNITY",
        model_version=LABEL_VERSION,
        risk_policy_version=SYNTHETIC_POLICY_VERSION,
        cost_model_id=wallet_config.cost_model_id,
        **terms,
    )
    market.validate_recommendation(recommendation)
    return recommendation


def build_opportunity(
    market: MarketData,
    store: PaperStore,
    session: Session,
    *,
    horizon_sessions: int,
    evaluated_at: datetime,
    wallet_config: WalletConfig = WalletConfig(),
) -> Opportunity:
    """Get a frozen candidate and its net label at the predeclared release clock.

    An opportunity is an independently funded, one-candidate S2 run. The clock
    ends at the closing auction publication deadline, so a later run cannot
    rewrite labels derived from the original matched fills.
    """
    market.verify_fixture()
    if session not in market.sessions:
        raise ValueError("Decision session must belong to the bundled market calendar")
    if type(horizon_sessions) is not int or horizon_sessions < 1:
        raise ValueError("A positive integer number of horizon sessions is required")
    cutoff = utc(evaluated_at)
    decision_at = decision_time(session)
    entry = market.next_open(decision_at)
    horizon = market.horizon(entry.open_at, horizon_sessions) if entry is not None else None
    candidate = Opportunity(
        sample_id=_sample_id(market, session, horizon_sessions, wallet_config, EQ1, BENCHMARK),
        source_snapshot_id=market.manifest_id,
        decision_at=decision_at,
        close_at=session.close_at,
        horizon_sessions=horizon_sessions,
        entry_at=entry.open_at if entry is not None else None,
        horizon_at=horizon.close_at if horizon is not None else None,
        status="UNMATURED",
        reason_code="DECISION_NOT_REACHED",
    )
    if cutoff < decision_at:
        return candidate

    record = build_research_recommendation(
        market, session, horizon_sessions=horizon_sessions, wallet_config=wallet_config,
    )
    store.freeze((record,), market)
    candidate = replace(candidate, recommendation=record)
    if record.action == Action.ABSTAIN:
        return replace(candidate, status="DATA_INSUFFICIENT", reason_code=record.reason_codes[0])
    if not candidate.policy_eligible:
        known_actions = _declared_delistings(market, record.instrument_id, decision_at, horizon_sessions)
        return replace(
            candidate,
            status="POLICY_BLOCKED",
            reason_code=record.reason_codes[0],
            corporate_action_ids=tuple(action.action_id for action in known_actions),
        )
    if entry is None:
        return replace(candidate, status="DATA_INSUFFICIENT", reason_code="NO_FUTURE_SESSION_IN_FIXTURE")
    if horizon is None:
        return replace(candidate, status="CENSORED", reason_code="HORIZON_OUTSIDE_FIXTURE")

    release_at = horizon.close_at + CLOSE_PUBLICATION_DELAY
    if cutoff < entry.open_at:
        return replace(candidate, reason_code="LABEL_AUCTION_CUTOFF_NOT_REACHED")

    run = simulate_wallet(store.db, market, (record,), as_of=min(cutoff, release_at), config=wallet_config)
    signal = run["signals"][0]
    fills = run["fills"] + run["benchmark"]["fills"]
    lot_actions = run["lot_actions"] + run["benchmark"]["lot_actions"]
    label = signal["excess_return_dec"]
    status, reason = signal["outcome_status"], signal["reason_code"]
    if cutoff < release_at and status == "MATURED":
        return replace(candidate, reason_code="LABEL_AUCTION_CUTOFF_NOT_REACHED")
    if status == "MATURED" and label is None:
        reason = next((order["reason_code"] for order in run["benchmark"]["orders"] if order["reason_code"]), None)
        status, reason = "DATA_INSUFFICIENT", "BENCHMARK_" + (reason or "RETURN_UNAVAILABLE")
    return replace(
        candidate,
        status=status,
        reason_code=reason,
        label_available_at=release_at if label is not None else None,
        asset_net_return_dec=signal["net_follow_return_dec"],
        benchmark_net_return_dec=signal["benchmark_return_dec"],
        label_excess_return_dec=label,
        wallet_run_id=run["run_id"],
        observation_ids=tuple(dict.fromkeys(fill["observation_id"] for fill in fills)),
        corporate_action_ids=tuple(dict.fromkeys(action["action_id"] for action in lot_actions)),
    )
