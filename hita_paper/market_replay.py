"""Inspectable point-in-time views of the bundled market data."""

from datetime import datetime

from .contracts import ContractError, time_text, utc
from .market import MarketData, Observation


def _market_context(market: MarketData, instrument_id: str) -> dict:
    market.verify_fixture()
    instrument = next(
        (instrument for instrument in market.instruments if instrument.instrument_id == instrument_id), None,
    )
    if instrument is None:
        raise ContractError("SOURCE_FIXTURE_INSTRUMENT", f"Instrument is unavailable in {market.version}: {instrument_id}")
    return {
        "data_class": market.data_mode,
        "market_version": market.version,
        "manifest_id": market.manifest_id,
        "instrument_id": instrument.instrument_id,
        "listing_id": instrument.listing_id,
        "currency": instrument.currency,
    }


def _quote_record(observation: Observation) -> dict:
    qualified = observation.condition == "VALID"
    return {
        "observation_id": observation.observation_id,
        "field": observation.field,
        "at": time_text(observation.at),
        "published_at": time_text(observation.published_at),
        "available_at": time_text(observation.available_at),
        "condition": observation.condition,
        "price_dec": observation.price_dec if qualified else None,
        "volume_dec": observation.volume_dec if qualified else None,
    }


def replay_market_quote(
    market: MarketData,
    *,
    instrument_id: str,
    at: datetime,
    known_as_of: datetime,
) -> dict:
    """Show the qualified quote and the released revision history for an exact slot."""
    context = _market_context(market, instrument_id)
    revisions = market.visible_revisions(instrument_id, at, known_as_of)
    selected = market.bar(instrument_id, at, known_as_of)
    quote = None
    status = "STALE" if revisions else "MISSING"
    if selected is not None:
        status = "QUALIFIED"
        quote = {**_quote_record(selected), "evidence": market.evidence(selected).to_dict()}
    return {
        "kind": "QUOTE",
        **context,
        "at": time_text(at),
        "known_as_of": time_text(known_as_of),
        "status": status,
        "selected": quote,
        "visible_revisions": [_quote_record(revision) for revision in revisions],
    }


def replay_market_actions(
    market: MarketData,
    *,
    instrument_id: str,
    start_at: datetime,
    end_at: datetime,
    known_as_of: datetime,
) -> dict:
    """Show known action notices with effective times in (start_at, end_at]."""
    start, end = utc(start_at), utc(end_at)
    if start > end:
        raise ContractError("VALIDATION_TIME_RANGE", "start_at must be at or before end_at")
    context = _market_context(market, instrument_id)
    actions = market.actions(instrument_id, start, end, known_as_of)
    return {
        "kind": "CORPORATE_ACTIONS",
        **context,
        "start_at": time_text(start),
        "end_at": time_text(end),
        "known_as_of": time_text(known_as_of),
        "status": "KNOWN" if actions else "NO_ACTIONS",
        "actions": [
            {
                "action_id": action.action_id,
                "kind": action.kind,
                "effective_at": time_text(action.effective_at),
                "available_at": time_text(action.available_at),
                "ratio_dec": action.ratio_dec,
                "amount_dec": action.amount_dec,
                "payable_at": time_text(action.payable_at) if action.payable_at is not None else None,
            }
            for action in sorted(actions, key=lambda action: (action.effective_at, action.action_id))
        ],
    }
