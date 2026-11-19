"""Point-in-time close-based features from the bundled synthetic market."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

from hita_a0.money import canonical

from .contracts import ContractError, EvidenceLink, content_digest, time_text, utc
from .market import BENCHMARK, EQ1, CorporateAction, MarketData, Observation


FEATURE_VERSION = "hita.research.close_features.v1"
DEFAULT_LOOKBACK_SESSIONS = 5
_FEATURE_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True, slots=True)
class FeatureGap:
    instrument_id: str
    close_at: datetime
    reason_code: str

    def to_dict(self) -> dict:
        return {
            "instrument_id": self.instrument_id,
            "close_at": time_text(self.close_at),
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    source_manifest_id: str
    calendar_id: str
    currency: str
    instrument_id: str
    benchmark_id: str
    close_at: datetime
    known_as_of: datetime
    lookback_sessions: int
    window_start_at: datetime | None
    feature_available_at_max: datetime | None
    status: str
    reason_codes: tuple[str, ...]
    asset_close_price_dec: str | None = None
    benchmark_close_price_dec: str | None = None
    asset_return_dec: str | None = None
    benchmark_return_dec: str | None = None
    relative_strength_dec: str | None = None
    realized_volatility_dec: str | None = None
    evidence_links: tuple[EvidenceLink, ...] = ()
    corporate_actions: tuple[CorporateAction, ...] = ()
    gaps: tuple[FeatureGap, ...] = ()
    feature_version: str = field(init=False, default=FEATURE_VERSION)
    content_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "content_sha256", content_digest(self._body()))

    @property
    def corporate_action_ids(self) -> tuple[str, ...]:
        return tuple(action.action_id for action in self.corporate_actions)

    def _body(self) -> dict:
        return {
            "feature_version": self.feature_version,
            "data_class": "SYNTHETIC",
            "source_manifest_id": self.source_manifest_id,
            "calendar_id": self.calendar_id,
            "currency": self.currency,
            "instrument_id": self.instrument_id,
            "benchmark_id": self.benchmark_id,
            "close_at": time_text(self.close_at),
            "known_as_of": time_text(self.known_as_of),
            "lookback_sessions": self.lookback_sessions,
            "window_start_at": time_text(self.window_start_at) if self.window_start_at is not None else None,
            "feature_available_at_max": (
                time_text(self.feature_available_at_max) if self.feature_available_at_max is not None else None
            ),
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "asset_close_price_dec": self.asset_close_price_dec,
            "benchmark_close_price_dec": self.benchmark_close_price_dec,
            "asset_return_dec": self.asset_return_dec,
            "benchmark_return_dec": self.benchmark_return_dec,
            "relative_strength_dec": self.relative_strength_dec,
            "realized_volatility_dec": self.realized_volatility_dec,
            "evidence_links": [link.to_dict() for link in self.evidence_links],
            "corporate_action_ids": list(self.corporate_action_ids),
            "corporate_actions": [
                {
                    "action_id": action.action_id,
                    "instrument_id": action.instrument_id,
                    "kind": action.kind,
                    "available_at": time_text(action.available_at),
                    "effective_at": time_text(action.effective_at),
                    "ratio_dec": action.ratio_dec,
                    "amount_dec": action.amount_dec,
                    "payable_at": time_text(action.payable_at) if action.payable_at is not None else None,
                }
                for action in self.corporate_actions
            ],
            "gaps": [gap.to_dict() for gap in self.gaps],
        }

    def to_dict(self) -> dict:
        return {**self._body(), "content_sha256": self.content_sha256}


def _closes(
    market: MarketData, instrument: str, window: tuple[datetime, ...], known_as_of: datetime
) -> tuple[tuple[Observation, ...], tuple[FeatureGap, ...]]:
    observations = []
    gaps = []
    for close_at in window:
        row = market.raw_bar(instrument, close_at, known_as_of)
        if row is None:
            gaps.append(FeatureGap(instrument, close_at, "CLOSE_UNAVAILABLE_AT_CUTOFF"))
        elif row.condition != "VALID":
            gaps.append(FeatureGap(instrument, close_at, "STALE_CLOSE"))
        else:
            observations.append(row)
    return tuple(observations), tuple(gaps)


def _wealth_series(
    observations: tuple[Observation, ...], actions: tuple[CorporateAction, ...]
) -> tuple[Decimal, ...]:
    """Value one starting share and the accumulated, unreinvested cash entitlements."""
    shares = Decimal(1)
    dividends = Decimal(0)
    remaining = iter(actions)
    action = next(remaining, None)
    wealth = []
    for observation in observations:
        while action is not None and action.effective_at <= observation.at:
            if action.kind == "SPLIT":
                shares *= Decimal(action.ratio_dec)
            else:
                dividends += shares * Decimal(action.amount_dec)
            action = next(remaining, None)
        wealth.append(shares * Decimal(observation.price_dec) + dividends)
    return tuple(wealth)


def _window_values(
    asset: tuple[Observation, ...],
    benchmark: tuple[Observation, ...],
    actions: tuple[CorporateAction, ...],
) -> dict[str, str]:
    with localcontext(_FEATURE_CONTEXT):
        asset_wealth = _wealth_series(asset, tuple(action for action in actions if action.instrument_id == asset[0].instrument_id))
        benchmark_wealth = _wealth_series(
            benchmark, tuple(action for action in actions if action.instrument_id == benchmark[0].instrument_id)
        )
        asset_return = (asset_wealth[-1] - asset_wealth[0]) / asset_wealth[0]
        benchmark_return = (benchmark_wealth[-1] - benchmark_wealth[0]) / benchmark_wealth[0]
        returns = tuple((after - before) / before for before, after in zip(asset_wealth, asset_wealth[1:]))
        average = sum(returns) / len(returns)
        variance = sum((value - average) ** 2 for value in returns) / (len(returns) - 1)
        return {
            "asset_return_dec": canonical(asset_return),
            "benchmark_return_dec": canonical(benchmark_return),
            "relative_strength_dec": canonical(asset_return - benchmark_return),
            "realized_volatility_dec": canonical(variance.sqrt()),
        }


def build_feature_snapshot(
    market: MarketData,
    *,
    close_at: datetime,
    known_as_of: datetime,
    lookback_sessions: int = DEFAULT_LOOKBACK_SESSIONS,
    instrument_id: str = EQ1,
    benchmark_id: str = BENCHMARK,
) -> FeatureSnapshot:
    """Compute matched close-to-close features using only releases known at the cutoff.

    A lookback of five sessions contains five returns measured from six scheduled
    closes. Volatility is the non-annualized sample deviation of the asset's simple
    daily returns. Prices are raw. Dividends enter wealth as entitlements on the
    effective date and stay in EUR cash.
    """
    market.verify_fixture()
    close_at = utc(close_at)
    known_as_of = utc(known_as_of)
    if type(lookback_sessions) is not int or lookback_sessions < 2:
        raise ContractError("VALIDATION_LOOKBACK", "Feature lookback requires at least two sessions")
    instruments = {instrument.instrument_id for instrument in market.instruments}
    if instrument_id not in instruments or benchmark_id not in instruments or instrument_id == benchmark_id:
        raise ContractError("SOURCE_FIXTURE_INSTRUMENT", "Distinct fixture instruments are required")

    closes = tuple(session.close_at for session in market.sessions if session.close_at <= close_at)
    base = {
        "source_manifest_id": market.manifest_id,
        "calendar_id": market.calendar_id,
        "currency": market.currency,
        "instrument_id": instrument_id,
        "benchmark_id": benchmark_id,
        "close_at": close_at,
        "known_as_of": known_as_of,
        "lookback_sessions": lookback_sessions,
    }
    if not closes or closes[-1] != close_at:
        return FeatureSnapshot(**base, window_start_at=None, feature_available_at_max=None,
                               status="DATA_INSUFFICIENT", reason_codes=("UNSCHEDULED_CLOSE",))

    window = closes[-(lookback_sessions + 1):]
    asset, asset_gaps = _closes(market, instrument_id, window, known_as_of)
    benchmark, benchmark_gaps = _closes(market, benchmark_id, window, known_as_of)
    evidence = tuple(market.evidence(observation) for observation in (*asset, *benchmark))
    actions = []
    for instrument in (instrument_id, benchmark_id):
        # A previously delisted listing remains unavailable after its event leaves the window.
        history = market.actions(instrument, market.sessions[0].open_at - timedelta(microseconds=1), close_at, known_as_of)
        actions.extend(action for action in history if action.kind == "DELIST" or action.effective_at > window[0])
    ordered_actions = tuple(sorted(
        actions, key=lambda action: (action.effective_at, action.kind != "SPLIT", action.action_id)
    ))
    gaps = asset_gaps + benchmark_gaps
    reasons = list(dict.fromkeys(gap.reason_code for gap in gaps))
    if len(window) <= lookback_sessions:
        reasons.append("INSUFFICIENT_HISTORY")
    if any(action.kind == "DELIST" for action in ordered_actions):
        reasons.append("DELISTING_TERMS_UNAVAILABLE")

    availability = [link.available_at for link in evidence] + [action.available_at for action in ordered_actions]
    return FeatureSnapshot(
        **base,
        window_start_at=window[0],
        feature_available_at_max=max(availability, default=None),
        status="READY" if not reasons else "DATA_INSUFFICIENT",
        reason_codes=tuple(reasons),
        asset_close_price_dec=asset[-1].price_dec if asset and asset[-1].at == close_at else None,
        benchmark_close_price_dec=benchmark[-1].price_dec if benchmark and benchmark[-1].at == close_at else None,
        evidence_links=evidence,
        corporate_actions=ordered_actions,
        gaps=gaps,
        **(_window_values(asset, benchmark, ordered_actions) if not reasons else {}),
    )
