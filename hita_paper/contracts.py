"""Immutable, versioned records for the synthetic recommendation register."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Any

DECISION_SCHEMA_VERSION = "hita.decision.v1"
SYNTHETIC_POLICY_VERSION = "synthetic-risk-v1"
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z", re.ASCII)
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|\+00:00)\Z",
    re.ASCII,
)


class ContractError(ValueError):
    """A validation failure with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class Action(StrEnum):
    CONSIDER_ADD = "CONSIDER_ADD"
    CONSIDER_REDUCE = "CONSIDER_REDUCE"
    HOLD = "HOLD"
    AVOID = "AVOID"
    WATCHLIST = "WATCHLIST"
    ABSTAIN = "ABSTAIN"


class PolicyResult(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    REVIEW = "REVIEW"


TRADE_ACTIONS = (Action.CONSIDER_ADD, Action.CONSIDER_REDUCE)


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ContractError("VALIDATION_TIMESTAMP", "A timezone-aware instant is required")
    return value.astimezone(timezone.utc)


def parse_time(value: str) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise ContractError("VALIDATION_TIMESTAMP", "Expected an ISO-8601 UTC timestamp")
    try:
        return utc(datetime.fromisoformat(value))
    except ValueError as exc:
        raise ContractError("VALIDATION_TIMESTAMP", "The timestamp is not a valid instant") from exc


def time_text(value: datetime) -> str:
    return utc(value).isoformat().replace("+00:00", "Z")


def decimal_value(value: str, field_name: str) -> Decimal:
    if not isinstance(value, str) or len(value) > 64 or not _DECIMAL.fullmatch(value):
        raise ContractError("VALIDATION_DECIMAL", f"{field_name} must be an exact decimal string")
    amount = Decimal(value)
    if amount.is_zero() and amount.is_signed():
        raise ContractError("VALIDATION_DECIMAL", f"{field_name} must use unsigned zero")
    return amount


def identifier(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value or not value.isascii() or any(
        ch.isspace() or not ch.isprintable() for ch in value
    ):
        raise ContractError("VALIDATION_IDENTIFIER", f"{field_name} requires a printable identifier")


def content_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EvidenceLink:
    observation_id: str
    instrument_id: str
    source_manifest_id: str
    effective_at: datetime
    published_at: datetime
    available_at: datetime
    kind: str = "MARKET_PRICE"

    def __post_init__(self) -> None:
        for name in ("observation_id", "instrument_id", "source_manifest_id"):
            identifier(getattr(self, name), name)
        for name in ("effective_at", "published_at", "available_at"):
            object.__setattr__(self, name, utc(getattr(self, name)))
        if self.kind != "MARKET_PRICE":
            raise ContractError("VALIDATION_EVIDENCE_KIND", "This fixture supplies market price evidence")
        if not self.effective_at <= self.published_at <= self.available_at:
            raise ContractError("TEMPORAL_EVIDENCE", "Evidence times are out of order")

    def to_dict(self) -> dict[str, Any]:
        result = {item.name: getattr(self, item.name) for item in fields(self)}
        for name in ("effective_at", "published_at", "available_at"):
            result[name] = time_text(result[name])
        return result

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EvidenceLink:
        if not isinstance(payload, dict) or payload.keys() != {item.name for item in fields(cls)}:
            raise ContractError("VALIDATION_EVIDENCE_SCHEMA", "Invalid evidence fields")
        values = payload.copy()
        for name in ("effective_at", "published_at", "available_at"):
            values[name] = parse_time(values[name])
        return cls(**values)


@dataclass(frozen=True, slots=True)
class Recommendation:
    recommendation_id: str
    decision_at: datetime
    known_as_of: datetime
    instrument_id: str
    action: Action
    horizon_trading_days: int
    source_snapshot_id: str
    model_version: str
    risk_policy_version: str
    policy_result: PolicyResult
    reason_codes: tuple[str, ...]
    benchmark_id: str
    cost_model_id: str
    reference_price_dec: str | None
    evidence_links: tuple[EvidenceLink, ...]
    quantity_dec: str | None = None
    capital_base_dec: str | None = None
    planned_loss_dec: str | None = None
    strategy_sleeve: str = "SATELLITE"
    model_id: str = "SYNTHETIC_PRICE_RULE"
    schema_version: str = field(init=False, default=DECISION_SCHEMA_VERSION)
    mode: str = field(init=False, default="PAPER_ONLY")
    status: str = field(init=False, default="FROZEN")
    content_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "recommendation_id", "instrument_id", "source_snapshot_id", "model_version",
            "risk_policy_version", "benchmark_id", "cost_model_id", "model_id",
        ):
            identifier(getattr(self, name), name)
        try:
            object.__setattr__(self, "action", Action(self.action))
            object.__setattr__(self, "policy_result", PolicyResult(self.policy_result))
        except ValueError as exc:
            raise ContractError("VALIDATION_ENUM", "Unknown recommendation action or policy result") from exc
        object.__setattr__(self, "decision_at", utc(self.decision_at))
        object.__setattr__(self, "known_as_of", utc(self.known_as_of))
        if self.known_as_of > self.decision_at:
            raise ContractError("TEMPORAL_LOOKAHEAD", "Decision knowledge extends past the decision time")
        if type(self.horizon_trading_days) is not int or self.horizon_trading_days < 1:
            raise ContractError("VALIDATION_HORIZON", "Horizon must be a positive integer of sessions")
        if self.strategy_sleeve not in ("CORE", "SATELLITE", "RESEARCH_ONLY"):
            raise ContractError("VALIDATION_STRATEGY_SLEEVE", "Unknown strategy sleeve")
        if not isinstance(self.reason_codes, tuple) or not self.reason_codes:
            raise ContractError("VALIDATION_POLICY_REASONS", "At least one explicit reason code is required")
        for code in self.reason_codes:
            identifier(code, "reason_code")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ContractError("VALIDATION_POLICY_REASONS", "Duplicate reason codes are not supported")
        self._validate_evidence()
        self._validate_amounts()
        object.__setattr__(self, "content_sha256", content_digest(self._body()))

    def _validate_evidence(self) -> None:
        if not isinstance(self.evidence_links, tuple) or not all(
            isinstance(link, EvidenceLink) for link in self.evidence_links
        ):
            raise ContractError("VALIDATION_EVIDENCE_SCHEMA", "Evidence links require an immutable tuple")
        seen: set[str] = set()
        for link in self.evidence_links:
            if link.available_at > self.known_as_of:
                raise ContractError("TEMPORAL_LOOKAHEAD", "Evidence is newer than the decision knowledge time")
            if link.source_manifest_id != self.source_snapshot_id:
                raise ContractError("SOURCE_MANIFEST_MISMATCH", "Evidence and decision reference different manifests")
            if link.observation_id in seen:
                raise ContractError("VALIDATION_EVIDENCE_SCHEMA", "Duplicate evidence observations are not supported")
            seen.add(link.observation_id)
        if self.reference_price_dec is None and self.action == Action.ABSTAIN:
            return
        if self.reference_price_dec is None or not any(
            link.instrument_id == self.instrument_id for link in self.evidence_links
        ):
            raise ContractError("SOURCE_EVIDENCE_REQUIRED", "The decision requires qualified instrument price evidence")

    def _validate_amounts(self) -> None:
        for name in ("reference_price_dec", "quantity_dec", "capital_base_dec", "planned_loss_dec"):
            value = getattr(self, name)
            if value is None:
                continue
            amount = decimal_value(value, name)
            if amount < 0 or (amount == 0 and name != "planned_loss_dec"):
                raise ContractError("VALIDATION_DECIMAL", f"{name} is outside the supported range")
        if self.action in TRADE_ACTIONS:
            if self.capital_base_dec is None and (
                self.policy_result != PolicyResult.BLOCK
                or "MISSING_APPROVED_DENOMINATOR" not in self.reason_codes
            ):
                raise ContractError("MISSING_APPROVED_DENOMINATOR", "Missing capital base requires a blocked result")
            if self.policy_result == PolicyResult.ALLOW:
                if self.risk_policy_version != SYNTHETIC_POLICY_VERSION:
                    raise ContractError("POLICY_UNKNOWN_VERSION", "Only the isolated synthetic policy may allow a fill")
                if self.planned_loss_dec is None or self.quantity_dec is None:
                    raise ContractError("POLICY_INPUT_MISSING", "Synthetic executable decisions require size and planned loss")

    def _body(self) -> dict[str, Any]:
        result = {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if item.name != "content_sha256"
        }
        for name in ("decision_at", "known_as_of"):
            result[name] = time_text(result[name])
        result["action"] = self.action.value
        result["policy_result"] = self.policy_result.value
        result["reason_codes"] = list(self.reason_codes)
        result["evidence_links"] = [link.to_dict() for link in self.evidence_links]
        return result

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "content_sha256": self.content_sha256}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Recommendation:
        if not isinstance(payload, dict) or payload.keys() != {item.name for item in fields(cls)}:
            raise ContractError("VALIDATION_RECOMMENDATION_SCHEMA", "Invalid frozen recommendation fields")
        if (
            payload["schema_version"] != DECISION_SCHEMA_VERSION
            or payload["mode"] != "PAPER_ONLY"
            or payload["status"] != "FROZEN"
            or not isinstance(payload["evidence_links"], list)
            or not isinstance(payload["reason_codes"], list)
        ):
            raise ContractError("VALIDATION_RECOMMENDATION_SCHEMA", "Invalid frozen recommendation version, mode or content")
        values = {item.name: payload[item.name] for item in fields(cls) if item.init}
        for name in ("decision_at", "known_as_of"):
            values[name] = parse_time(values[name])
        values["evidence_links"] = tuple(EvidenceLink.from_dict(item) for item in values["evidence_links"])
        values["reason_codes"] = tuple(values["reason_codes"])
        recommendation = cls(**values)
        if payload != recommendation.to_dict():
            raise ContractError("SOURCE_CONTENT_DIGEST_MISMATCH", "Frozen content or canonical digest changed")
        return recommendation
