"""Bundled synthetic market fixtures with separate observation and release clocks."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
import json
from types import MappingProxyType
from typing import Mapping

from .contracts import ContractError, EvidenceLink, Recommendation, decimal_value, parse_time, utc

EQ1 = "EQ1_SYNTHETIC"
EQ2 = "EQ2_SYNTHETIC"
BENCHMARK = "BENCHMARK_SYNTHETIC"
CASH = "CASH_EUR"
DEFAULT_MARKET_VERSION = "v1"
FIXTURE_VERSION = "hita.synthetic.market.v1"
FIXTURE_SHA256 = "8f0689e10b27e7386a27124c452b1458d8681d80b9aaa0d848ed03a3e8aba64b"


@dataclass(frozen=True, slots=True)
class _FixtureManifest:
    fixture_version: str
    filename: str
    sha256: str
    instrument_ids: frozenset[str]
    parent_version: str | None = None


_MANIFESTS: Mapping[str, _FixtureManifest] = MappingProxyType({
    "v1": _FixtureManifest(
        FIXTURE_VERSION, "synthetic_market_v1.json", FIXTURE_SHA256, frozenset({EQ1, BENCHMARK}),
    ),
    "v2": _FixtureManifest(
        "hita.synthetic.market.v2", "synthetic_market_v2.json",
        "6c0863fb7c60ca120ef13fc1e99402ad04f00de0ef0a1c7ccea1d6ae1772a0c2",
        frozenset({EQ1, EQ2, BENCHMARK}), parent_version="v1",
    ),
})


def market_versions() -> tuple[str, ...]:
    """Return the selectors for the approved bundled synthetic datasets."""
    return tuple(_MANIFESTS)


def _version_selector(fixture_version: str) -> str:
    for version, manifest in _MANIFESTS.items():
        if manifest.fixture_version == fixture_version:
            return version
    raise ContractError("SOURCE_FIXTURE_VERSION", "Unknown bundled synthetic market fixture")


@dataclass(frozen=True, slots=True)
class Instrument:
    instrument_id: str
    listing_id: str
    label: str
    currency: str
    asset_type: str


@dataclass(frozen=True, slots=True)
class Session:
    open_at: datetime
    close_at: datetime

    @property
    def date(self) -> date:
        return self.open_at.date()


@dataclass(frozen=True, slots=True)
class Observation:
    observation_id: str
    instrument_id: str
    field: str
    at: datetime
    published_at: datetime
    available_at: datetime
    price_dec: str
    volume_dec: str
    condition: str


@dataclass(frozen=True, slots=True)
class CorporateAction:
    action_id: str
    instrument_id: str
    kind: str
    effective_at: datetime
    available_at: datetime
    ratio_dec: str | None
    amount_dec: str | None
    payable_at: datetime | None


@dataclass(frozen=True, slots=True)
class FXRate:
    observation_id: str
    base_currency: str
    quote_currency: str
    rate_dec: str
    effective_at: datetime
    available_at: datetime


@dataclass(frozen=True, slots=True)
class MarketData:
    fixture_version: str
    manifest_sha256: str
    calendar_id: str
    currency: str
    cash_yield_dec: str
    price_basis: str
    provenance: str
    holidays: tuple[date, ...]
    instruments: tuple[Instrument, ...]
    sessions: tuple[Session, ...]
    observations: tuple[Observation, ...]
    corporate_actions: tuple[CorporateAction, ...]
    fx_rates: tuple[FXRate, ...]
    data_mode: str = field(init=False, default="SYNTHETIC")
    _opens: tuple[datetime, ...] = field(init=False, repr=False, compare=False)
    _opening_indexes: Mapping[datetime, int] = field(init=False, repr=False, compare=False)
    _by_observation_id: Mapping[str, Observation] = field(init=False, repr=False, compare=False)
    _by_time: Mapping[tuple[str, datetime], tuple[Observation, ...]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        manifest = _MANIFESTS[self.version]
        if (
            self.manifest_sha256 != manifest.sha256
            or self.currency != "EUR" or self.price_basis != "UNADJUSTED"
            or decimal_value(self.cash_yield_dec, "cash_yield_dec") != 0
        ):
            raise ContractError("SOURCE_FIXTURE_VERSION", "Invalid synthetic market fixture metadata")
        if any(
            not isinstance(value, tuple)
            for value in (
                self.holidays, self.instruments, self.sessions, self.observations,
                self.corporate_actions, self.fx_rates,
            )
        ):
            raise ContractError("SOURCE_FIXTURE_SCHEMA", "Market collections require immutable tuples")
        opens = tuple(session.open_at for session in self.sessions)
        if (
            not opens or opens != tuple(sorted(set(opens)))
            or any(s.open_at >= s.close_at or s.open_at.date() != s.close_at.date() for s in self.sessions)
            or any(s.date in self.holidays for s in self.sessions)
        ):
            raise ContractError("SOURCE_FIXTURE_CALENDAR", "Invalid synthetic trading calendar")
        open_index = {opens_at: i for i, opens_at in enumerate(opens)}
        expected_fields = {s.open_at: "OPEN" for s in self.sessions}
        expected_fields.update({s.close_at: "CLOSE" for s in self.sessions})
        ids = {instrument.instrument_id for instrument in self.instruments}
        if (
            ids != manifest.instrument_ids or len(ids) != len(self.instruments)
            or len({instrument.listing_id for instrument in self.instruments}) != len(self.instruments)
            or any(inst.currency != self.currency for inst in self.instruments)
        ):
            raise ContractError("SOURCE_FIXTURE_INSTRUMENT", "Invalid fixture instrument or currency")

        by_time: dict[tuple[str, datetime], list[Observation]] = {}
        by_id: dict[str, Observation] = {}
        for observation in self.observations:
            if (
                observation.instrument_id not in ids
                or observation.observation_id in by_id
                or expected_fields.get(observation.at) != observation.field
                or not observation.at <= observation.published_at <= observation.available_at
                or observation.condition not in {"VALID", "STALE"}
                or decimal_value(observation.price_dec, "price_dec") <= 0
                or decimal_value(observation.volume_dec, "volume_dec") < 0
            ):
                raise ContractError("SOURCE_FIXTURE_PRICE", "Invalid synthetic market observation")
            by_time.setdefault((observation.instrument_id, observation.at), []).append(observation)
            by_id[observation.observation_id] = observation

        revisions: dict[tuple[str, datetime], tuple[Observation, ...]] = {}
        for key, observations in by_time.items():
            if len({observation.available_at for observation in observations}) != len(observations):
                raise ContractError("SOURCE_FIXTURE_PRICE", "Ambiguous price revision release time")
            revisions[key] = tuple(sorted(observations, key=lambda record: record.available_at))

        action_ids: set[str] = set()
        for action in self.corporate_actions:
            if (
                action.action_id in action_ids or action.instrument_id not in ids
                or action.effective_at not in open_index
                or action.available_at > action.effective_at
                or not self._valid_action_terms(action, open_index)
            ):
                raise ContractError("SOURCE_FIXTURE_ACTION", "Invalid synthetic corporate action")
            action_ids.add(action.action_id)

        for rate in self.fx_rates:
            if (
                rate.base_currency != self.currency or rate.quote_currency != self.currency
                or rate.available_at < rate.effective_at
                or decimal_value(rate.rate_dec, "rate_dec") != 1
            ):
                raise ContractError("SOURCE_FIXTURE_FX", "The fixture supports only same-currency EUR accounting")

        object.__setattr__(self, "_opens", opens)
        object.__setattr__(self, "_opening_indexes", MappingProxyType(open_index))
        object.__setattr__(self, "_by_observation_id", MappingProxyType(by_id))
        object.__setattr__(self, "_by_time", MappingProxyType(revisions))

    @staticmethod
    def _valid_action_terms(action: CorporateAction, open_index: Mapping[datetime, int]) -> bool:
        if action.kind == "SPLIT":
            return (
                action.ratio_dec is not None and decimal_value(action.ratio_dec, "ratio_dec") > 0
                and action.amount_dec is None and action.payable_at is None
            )
        if action.kind == "DIVIDEND":
            return (
                action.amount_dec is not None and decimal_value(action.amount_dec, "amount_dec") > 0
                and action.ratio_dec is None and action.payable_at is not None
                and action.payable_at >= action.effective_at and action.payable_at in open_index
            )
        return action.kind == "DELIST" and all(
            term is None for term in (action.ratio_dec, action.amount_dec, action.payable_at)
        )

    @property
    def version(self) -> str:
        return _version_selector(self.fixture_version)

    @property
    def manifest_id(self) -> str:
        return f"{self.fixture_version}:{self.manifest_sha256}"

    @property
    def lineage_root_manifest_id(self) -> str:
        """Identify the original manifest shared by all versions in this lineage."""
        manifest = _MANIFESTS[self.version]
        while manifest.parent_version is not None:
            manifest = _MANIFESTS[manifest.parent_version]
        return f"{manifest.fixture_version}:{manifest.sha256}"

    def verify_fixture(self) -> None:
        """Require the exact contents of the bundled manifest at write boundaries."""
        official = load_market(self.version)
        if self is not official and self != official:
            raise ContractError("SOURCE_FIXTURE_CHECKSUM", "Market records differ from the bundled manifest")

    def visible_revisions(self, instrument_id: str, at: datetime, known_as_of: datetime) -> tuple[Observation, ...]:
        """Return the exact observation's released revisions from oldest to newest."""
        self.verify_fixture()
        if instrument_id not in _MANIFESTS[self.version].instrument_ids:
            raise ContractError("SOURCE_FIXTURE_INSTRUMENT", "Unknown instrument in the selected market fixture")
        revisions = self._by_time.get((instrument_id, utc(at)), ())
        known = utc(known_as_of)
        return tuple(row for row in revisions if row.available_at <= known)

    def raw_bar(self, instrument_id: str, at: datetime, known_as_of: datetime) -> Observation | None:
        """Return the revision released by the cutoff, including explicit stale observations."""
        revisions = self._by_time.get((instrument_id, utc(at)), ())
        known = utc(known_as_of)
        return next((row for row in reversed(revisions) if row.available_at <= known), None)

    def bar(self, instrument_id: str, at: datetime, known_as_of: datetime) -> Observation | None:
        """Return a qualified observation for this exact economic and knowledge time."""
        price = self.raw_bar(instrument_id, at, known_as_of)
        return price if price is not None and price.condition == "VALID" else None

    def latest(self, instrument_id: str, economic_as_of: datetime, known_as_of: datetime) -> Observation | None:
        """Return the latest released observation before the economic cutoff if qualified."""
        as_of = utc(economic_as_of)
        known = utc(known_as_of)
        latest: Observation | None = None
        for inst, at in self._by_time:
            if inst != instrument_id or at > as_of or (latest is not None and at <= latest.at):
                continue
            row = self.raw_bar(inst, at, known)
            if row is not None:
                latest = row
        return latest if latest is not None and latest.condition == "VALID" else None

    def next_open(self, decision_at: datetime) -> Session | None:
        i = bisect_right(self._opens, utc(decision_at))
        return self.sessions[i] if i < len(self.sessions) else None

    def horizon(self, open_at: datetime, trading_days: int) -> Session | None:
        if type(trading_days) is not int or trading_days < 1:
            raise ContractError("VALIDATION_HORIZON", "Horizon must be a positive integer of sessions")
        start = self._opening_indexes.get(utc(open_at))
        if start is None or start + trading_days > len(self.sessions):
            return None
        return self.sessions[start + trading_days - 1]

    def actions(
        self, instrument_id: str, start_at: datetime, end_at: datetime, known_as_of: datetime
    ) -> tuple[CorporateAction, ...]:
        start, end, known = utc(start_at), utc(end_at), utc(known_as_of)
        return tuple(
            action for action in self.corporate_actions
            if action.instrument_id == instrument_id
            and start < action.effective_at <= end and action.available_at <= known
        )

    def fx_rate(
        self, base_currency: str, quote_currency: str, at: datetime, known_as_of: datetime
    ) -> FXRate | None:
        at, known = utc(at), utc(known_as_of)
        eligible = (
            rate for rate in self.fx_rates
            if (rate.base_currency, rate.quote_currency) == (base_currency, quote_currency)
            and rate.effective_at <= at and rate.available_at <= known
        )
        return max(eligible, key=lambda rate: (rate.effective_at, rate.available_at), default=None)

    def evidence(self, observation: Observation) -> EvidenceLink:
        if (
            observation.condition != "VALID"
            or self._by_observation_id.get(observation.observation_id) != observation
        ):
            raise ContractError("SOURCE_EVIDENCE_UNKNOWN", "Evidence requires a qualified fixture observation")
        return EvidenceLink(
            observation_id=observation.observation_id,
            instrument_id=observation.instrument_id,
            source_manifest_id=self.manifest_id,
            effective_at=observation.at,
            published_at=observation.published_at,
            available_at=observation.available_at,
        )

    def validate_recommendation(self, recommendation: Recommendation) -> None:
        self.verify_fixture()
        if recommendation.source_snapshot_id != self.manifest_id:
            raise ContractError("SOURCE_MANIFEST_MISMATCH", "The recommendation references another fixture")
        if (
            recommendation.instrument_id not in {inst.instrument_id for inst in self.instruments}
            or recommendation.benchmark_id not in (BENCHMARK, CASH)
            or recommendation.benchmark_id == recommendation.instrument_id
        ):
            raise ContractError("SOURCE_FIXTURE_INSTRUMENT", "Invalid instrument or benchmark for the synthetic fixture")
        for link in recommendation.evidence_links:
            observation = self._by_observation_id.get(link.observation_id)
            if (
                observation is None
                or self.bar(observation.instrument_id, observation.at, recommendation.known_as_of) != observation
                or self.evidence(observation) != link
            ):
                raise ContractError("SOURCE_EVIDENCE_UNKNOWN", "Evidence does not match its qualified fixture observation")
        if recommendation.reference_price_dec is None:
            return
        latest_price = self.latest(
            recommendation.instrument_id, recommendation.decision_at, recommendation.known_as_of
        )
        if (
            latest_price is None
            or self.evidence(latest_price) not in recommendation.evidence_links
            or decimal_value(recommendation.reference_price_dec, "reference_price_dec")
            != decimal_value(latest_price.price_dec, "price_dec")
        ):
            raise ContractError("SOURCE_REFERENCE_PRICE_MISMATCH", "The reference price does not match the evidence")


def _times(payload: dict, *keys: str) -> dict:
    return {**payload, **{key: parse_time(payload[key]) for key in keys if payload[key] is not None}}


def _verify_lineage(market: MarketData, parent: MarketData) -> None:
    inherited = ("holidays", "instruments", "sessions", "observations", "corporate_actions", "fx_rates")
    if market.calendar_id != parent.calendar_id or any(
        not set(getattr(parent, name)).issubset(getattr(market, name)) for name in inherited
    ):
        raise ContractError("SOURCE_FIXTURE_LINEAGE", "A newer market version removed or changed inherited records")
    for bar_key, revisions in parent._by_time.items():
        if market._by_time[bar_key][:len(revisions)] != revisions:
            raise ContractError("SOURCE_FIXTURE_LINEAGE", "A market revision precedes an inherited release")


@lru_cache(maxsize=len(_MANIFESTS))
def _load_market(version: str) -> MarketData:
    manifest = _MANIFESTS[version]
    data = files(__package__).joinpath(manifest.filename).read_bytes()
    digest = sha256(data).hexdigest()
    if digest != manifest.sha256:
        raise ContractError("SOURCE_FIXTURE_CHECKSUM", "Synthetic fixture bytes failed checksum verification")
    payload = json.loads(data)
    if payload["data_mode"] != "SYNTHETIC":
        raise ContractError("SOURCE_FIXTURE_MODE", "Synthetic market data is required")
    if payload["fixture_version"] != manifest.fixture_version:
        raise ContractError("SOURCE_FIXTURE_VERSION", "Market file version does not match the registry")
    market = MarketData(
        fixture_version=payload["fixture_version"],
        manifest_sha256=digest,
        calendar_id=payload["calendar_id"],
        currency=payload["currency"],
        cash_yield_dec=payload["cash_yield_dec"],
        price_basis=payload["price_basis"],
        provenance=payload["provenance"],
        holidays=tuple(map(date.fromisoformat, payload["holidays"])),
        instruments=tuple(Instrument(**item) for item in payload["instruments"]),
        sessions=tuple(Session(**_times(item, "open_at", "close_at")) for item in payload["sessions"]),
        observations=tuple(
            Observation(**_times(item, "at", "published_at", "available_at"))
            for item in payload["observations"]
        ),
        corporate_actions=tuple(
            CorporateAction(**_times(item, "effective_at", "available_at", "payable_at"))
            for item in payload["corporate_actions"]
        ),
        fx_rates=tuple(
            FXRate(**_times(item, "effective_at", "available_at")) for item in payload["fx_rates"]
        ),
    )
    if manifest.parent_version is not None:
        _verify_lineage(market, _load_market(manifest.parent_version))
    return market


def load_market(version: str = DEFAULT_MARKET_VERSION) -> MarketData:
    """Load a pinned bundled synthetic dataset by its registry selector."""
    if not isinstance(version, str) or version not in _MANIFESTS:
        raise ContractError("SOURCE_FIXTURE_VERSION", "Unknown bundled synthetic market version")
    return _load_market(version)


def market_catalog() -> list[dict]:
    """Describe the verified dataset versions and their synthetic source contract."""
    catalog = []
    for version, manifest in _MANIFESTS.items():
        market = load_market(version)
        parent_id = load_market(manifest.parent_version).manifest_id if manifest.parent_version is not None else None
        catalog.append({
            "version": version,
            "fixture_version": market.fixture_version,
            "manifest_id": market.manifest_id,
            "manifest_sha256": market.manifest_sha256,
            "parent_version": manifest.parent_version,
            "parent_manifest_id": parent_id,
            "source_id": "HITA_PROJECT_SYNTHETIC",
            "source_contract_version": "hita.synthetic.dataset.v1",
            "source_kind": market.data_mode,
            "provenance": market.provenance,
            "usage_scope": "SYNTHETIC_RESEARCH_AND_PAPER_REPLAY",
            "instrument_ids": sorted(manifest.instrument_ids),
            "calendar_id": market.calendar_id,
            "currency": market.currency,
        })
    return catalog
