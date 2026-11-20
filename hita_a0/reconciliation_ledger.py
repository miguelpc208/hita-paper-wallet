"""Point-in-time ledger evidence for synthetic account reconciliation."""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from hashlib import sha256
import json

from .ledger import Ledger, validate_utc_seconds
from .money import FinancialValidationError, canonical, dec


_CONTEXT = Context(prec=60, rounding=ROUND_HALF_EVEN)
_POSTING_KINDS = {
    "SETTLED_CASH": "CASH", "SECURITY_QTY": "HOLDING",
    "PAYABLE": "PAYABLE", "RECEIVABLE": "RECEIVABLE",
}


@contextmanager
def _read_snapshot(ledger: Ledger):
    own_transaction = not ledger.db.in_transaction
    if own_transaction:
        ledger.db.execute("BEGIN")
    try:
        yield
    finally:
        if own_transaction:
            ledger.db.rollback()


def _hash_matches(content: str | None, expected: str) -> bool:
    return content is not None and sha256(content.encode("utf-8")).hexdigest() == expected


def _opening(ledger: Ledger, account_id: str, effective_at: str, known_as_of: str, issues: list[dict]):
    anchors = ledger.all("""SELECT a.opening_anchor_id, a.account_id, a.anchor_status, a.cutoff_at,
        a.recognition_basis, a.statement_id, a.included_effects_manifest_json, a.included_effects_sha256,
        s.statement_status, s.cutoff_at AS statement_cutoff, s.recognition_basis AS statement_basis,
        f.artifact_id, f.artifact_status, d.source_kind
        FROM led_opening_anchors a
        JOIN rec_statement_headers s ON s.statement_id = a.statement_id AND s.account_id = a.account_id
        JOIN src_source_artifacts f USING(artifact_id)
        JOIN src_data_sources d USING(source_id)
        WHERE a.account_id = ? ORDER BY a.opening_anchor_id""", (account_id,))
    if len(anchors) != 1:
        issues.append({"code": "OPENING_ANCHOR_REQUIRED", "anchor_count": len(anchors)})
        return None, None
    anchor = anchors[0]
    aid = anchor["opening_anchor_id"]
    if (
        anchor["anchor_status"] != "CERTIFIED" or anchor["recognition_basis"] != "TRADE_DATE"
        or anchor["statement_status"] != "VALIDATED" or anchor["statement_basis"] != "TRADE_DATE"
        or anchor["statement_cutoff"] != anchor["cutoff_at"] or anchor["source_kind"] != "SYNTHETIC"
        or anchor["artifact_status"] != "VERIFIED"
    ):
        issues.append({"code": "OPENING_ANCHOR_UNQUALIFIED", "opening_anchor_id": aid})
        return None, None
    if min(effective_at, known_as_of) < anchor["cutoff_at"]:
        issues.append({
            "code": "SNAPSHOT_PRECEDES_OPENING_ANCHOR", "opening_anchor_id": aid, "cutoff_at": anchor["cutoff_at"],
        })
        return None, None
    manifest = anchor["included_effects_manifest_json"]
    try:
        effects = json.loads(manifest)
    except ValueError:
        effects = None
    if (
        not isinstance(effects, dict) or effects.get("included") != [] or effects.get("unresolved") != []
        or not isinstance(effects.get("excluded"), list)
        or not _hash_matches(manifest, anchor["included_effects_sha256"])
    ):
        issues.append({"code": "OPENING_EFFECTS_UNQUALIFIED", "opening_anchor_id": aid})
        return None, None
    source = ledger.one("""SELECT r.source_record_id, r.record_content_sha256, r.normalized_payload_json
        FROM rec_reconciliation_runs run
        JOIN src_ingestion_batches b ON b.artifact_id = ?
        JOIN src_source_records r ON r.batch_id = b.batch_id
            AND r.record_content_sha256 = run.input_manifest_sha256
        WHERE run.opening_anchor_id = ? AND run.statement_id = ? AND run.reconciliation_scope = 'OPENING'
            AND run.run_status = 'PASSED' AND r.classification_status = 'ACCEPTED'
        ORDER BY r.source_record_id LIMIT 1""", (anchor["artifact_id"], aid, anchor["statement_id"]))
    if source is None or not _hash_matches(source["normalized_payload_json"], source["record_content_sha256"]):
        issues.append({"code": "OPENING_SOURCE_UNQUALIFIED", "opening_anchor_id": aid})
        return None, None
    return anchor, source["source_record_id"]


def _add(lines, issues, kind: str, key: str, amount: str, evidence: dict) -> Decimal | None:
    try:
        value = dec(amount, scale=12 if kind == "HOLDING" else 8)
    except FinancialValidationError:
        issues.append({"code": "INVALID_LEDGER_AMOUNT", "kind": kind, "key": key, **evidence})
        return None
    lines[kind, key].append((value, {**evidence, "amount_dec": canonical(value)}))
    return value


def _opening_lines(ledger, anchor, source_id, lines, issues):
    aid = anchor["opening_anchor_id"]
    origin = {
        "type": "OPENING",
        "opening_anchor_id": aid,
        "statement_id": anchor["statement_id"],
        "artifact_id": anchor["artifact_id"],
        "source_record_id": source_id,
    }
    for row in ledger.all("""SELECT c.*, s.statement_cash_id FROM led_opening_cash c
        LEFT JOIN rec_statement_cash_balances s ON s.statement_id = ? AND s.source_line_locator = c.source_line_locator
            AND s.currency_code = c.currency_code AND s.balance_type = c.balance_type AND s.amount_dec = c.amount_dec
        WHERE opening_anchor_id = ? AND c.balance_type = 'SETTLED' ORDER BY c.opening_cash_id""",
        (anchor["statement_id"], aid)):
        evidence = {**origin, "opening_cash_id": row["opening_cash_id"],
                    "statement_line_id": row["statement_cash_id"], "source_line_locator": row["source_line_locator"]}
        if row["statement_cash_id"] is None:
            issues.append({"code": "OPENING_STATEMENT_LINE_UNQUALIFIED", **evidence})
        _add(lines, issues, "CASH", row["currency_code"], row["amount_dec"], evidence)
    for row in ledger.all("""SELECT p.*, i.instrument_key, s.statement_position_id
        FROM led_opening_positions p JOIN ref_instruments i USING(instrument_id)
        LEFT JOIN rec_statement_positions s ON s.statement_id = ? AND s.source_line_locator = p.source_line_locator
            AND s.instrument_id = p.instrument_id AND s.quantity_dec = p.quantity_dec
        WHERE opening_anchor_id = ? ORDER BY p.opening_position_id""", (anchor["statement_id"], aid)):
        lots = ledger.all("SELECT lot_id FROM led_position_lots WHERE opening_position_id = ? ORDER BY lot_id",
                          (row["opening_position_id"],))
        evidence = {**origin, "opening_position_id": row["opening_position_id"],
                    "statement_line_id": row["statement_position_id"], "source_line_locator": row["source_line_locator"],
                    "lot_ids": [lot["lot_id"] for lot in lots]}
        if row["statement_position_id"] is None or not lots:
            issues.append({"code": "OPENING_POSITION_UNQUALIFIED", **evidence})
        _add(lines, issues, "HOLDING", row["instrument_key"], row["quantity_dec"], evidence)
    outstanding = {}
    for row in ledger.all("""SELECT p.*, o.settlement_obligation_id, o.evidence_status AS obligation_evidence,
        o.original_amount_dec AS obligation_amount, o.account_id AS obligation_account,
        o.currency_code AS obligation_currency, o.obligation_type AS current_obligation_type
        FROM led_opening_pending_items p LEFT JOIN led_settlement_obligations o
            ON o.opening_pending_id = p.opening_pending_id AND o.record_version_status = 'CURRENT'
        WHERE p.opening_anchor_id = ? ORDER BY p.opening_pending_id""", (aid,)):
        oid = row["settlement_obligation_id"]
        evidence = {**origin, "opening_pending_id": row["opening_pending_id"],
                    "settlement_obligation_id": oid, "source_line_locator": row["source_line_locator"]}
        if (
            oid is None or row["evidence_status"] != "VERIFIED" or row["obligation_evidence"] != "VERIFIED"
            or row["obligation_amount"] != row["original_amount_dec"] or row["obligation_account"] != anchor["account_id"]
            or row["obligation_currency"] != row["currency_code"] or row["current_obligation_type"] != row["obligation_type"]
        ):
            issues.append({"code": "OPENING_OBLIGATION_UNQUALIFIED", **evidence})
        amount = _add(lines, issues, row["obligation_type"], row["currency_code"], row["original_amount_dec"], evidence)
        if amount is not None and amount <= 0:
            issues.append({"code": "INVALID_OPENING_OBLIGATION_AMOUNT", **evidence})
        if oid is not None and amount is not None:
            outstanding[oid] = amount
    return outstanding


def _verified_event_sources(ledger, event):
    rows = ledger.all("""SELECT l.source_record_id, r.record_content_sha256, r.normalized_payload_json
        FROM src_record_event_links l JOIN src_source_records r USING(source_record_id)
        JOIN src_ingestion_batches b USING(batch_id) JOIN src_source_artifacts a USING(artifact_id)
        JOIN src_data_sources s ON s.source_id = a.source_id
        WHERE l.event_id = ? AND l.evidence_status = 'VERIFIED' AND l.link_role = 'EVIDENCES'
            AND r.classification_status = 'ACCEPTED' AND a.artifact_status = 'VERIFIED'
            AND s.source_kind = 'SYNTHETIC' AND s.source_id = ?
        ORDER BY l.source_record_id""", (event["event_id"], event["source_id"]))
    return [row["source_record_id"] for row in rows
            if row["record_content_sha256"] == event["event_content_sha256"]
            and _hash_matches(row["normalized_payload_json"], row["record_content_sha256"])]


def _visible_transfer_match(ledger, event, effective_at, known_as_of) -> dict | None:
    other_subtype = "TRANSFER_IN" if event["event_subtype"] == "TRANSFER_OUT" else "TRANSFER_OUT"
    eid = event["event_id"]
    matches = ledger.all("""SELECT e.*, r.relation_id, r.effective_at AS match_effective_at,
        r.recorded_at AS match_recorded_at FROM led_event_relations r
        JOIN led_financial_events e ON e.event_id = CASE WHEN r.from_event_id = ? THEN r.to_event_id ELSE r.from_event_id END
        JOIN src_data_sources s USING(source_id)
        WHERE r.relation_type = 'TRANSFER_MATCH' AND r.relation_status = 'ACCEPTED'
            AND (r.from_event_id = ? OR r.to_event_id = ?) AND e.event_status = 'ACCEPTED'
            AND e.event_subtype = ? AND s.source_kind = 'SYNTHETIC' AND e.account_id != ?
            AND e.economic_effective_at <= ? AND e.available_at <= ?
        ORDER BY r.relation_id""", (eid, eid, eid, other_subtype, event["account_id"], effective_at, known_as_of))
    if len(matches) != 1:
        return None
    match = matches[0]
    # A0 generates match timestamps on the system clock. Treat an accepted match as
    # known only when both relation timestamps precede the synthetic knowledge cutoff.
    try:
        relation_times = tuple(datetime.fromisoformat(match[key]) for key in ("match_effective_at", "match_recorded_at"))
    except ValueError:
        return None
    cutoff = datetime.fromisoformat(known_as_of)
    if any(value.utcoffset() != timezone.utc.utcoffset(value) or value > cutoff for value in relation_times):
        return None
    other_sources = _verified_event_sources(ledger, match)
    if not other_sources:
        return None
    return {
        "transfer_match_id": match["relation_id"],
        "counterparty_event_id": match["event_id"],
        "counterparty_source_record_ids": other_sources,
    }


def _event_lines(ledger, account_id, anchor, effective_at, known_as_of, lines, issues, outstanding):
    exclusions = json.loads(anchor["included_effects_manifest_json"])["excluded"]
    visible_origins = set(outstanding)
    movements = defaultdict(list)
    for event in ledger.all("""SELECT e.*, s.source_kind FROM led_financial_events e
        JOIN src_data_sources s USING(source_id)
        WHERE e.account_id = ? AND e.event_status = 'ACCEPTED'
            AND e.economic_effective_at <= ? AND e.available_at <= ?
        ORDER BY e.economic_effective_at, e.available_at, e.event_id""", (account_id, effective_at, known_as_of)):
        eid = event["event_id"]
        if event["source_kind"] != "SYNTHETIC":
            issues.append({"code": "NON_SYNTHETIC_EVENT", "event_id": eid})
            continue
        if (
            event["economic_effective_at"] < anchor["cutoff_at"]
            or event["economic_effective_at"] == anchor["cutoff_at"] and event["provider_operation_key"] not in exclusions
        ):
            issues.append({"code": "EVENT_OUTSIDE_OPENING_BOUNDARY", "event_id": eid})
            continue
        source_ids = _verified_event_sources(ledger, event)
        if not source_ids:
            issues.append({"code": "EVENT_SOURCE_UNQUALIFIED", "event_id": eid})
        match = None
        if event["event_subtype"] in ("TRANSFER_OUT", "TRANSFER_IN"):
            match = _visible_transfer_match(ledger, event, effective_at, known_as_of)
            if match is None:
                issues.append({"code": "UNMATCHED_TRANSFER", "event_id": eid, "source_record_ids": source_ids})
        postings = ledger.all("""SELECT p.*, i.instrument_key, o.originating_event_id,
            o.evidence_status, o.record_version_status, o.original_amount_dec,
            origin.economic_effective_at AS origin_effective_at
            FROM led_event_postings p LEFT JOIN ref_instruments i USING(instrument_id)
            LEFT JOIN led_settlement_obligations o USING(settlement_obligation_id)
            LEFT JOIN led_financial_events origin ON origin.event_id = o.originating_event_id
            WHERE p.event_id = ? AND posting_class IN ('SECURITY_QTY', 'SETTLED_CASH', 'PAYABLE', 'RECEIVABLE')
            ORDER BY p.posting_sequence""", (eid,))
        for posting in postings:
            kind = _POSTING_KINDS[posting["posting_class"]]
            key = posting["instrument_key"] if kind == "HOLDING" else posting["currency_code"]
            amount = posting["quantity_dec"] if kind == "HOLDING" else posting["amount_dec"]
            oid = posting["settlement_obligation_id"]
            evidence = {
                "type": "POSTING", "posting_id": posting["posting_id"], "event_id": eid,
                "event_subtype": event["event_subtype"], "operation_key": event["provider_operation_key"],
                "economic_effective_at": event["economic_effective_at"], "available_at": event["available_at"],
                "source_record_ids": source_ids,
            }
            if match is not None:
                evidence.update(match)
            if posting["effective_at"] != event["economic_effective_at"]:
                issues.append({"code": "POSTING_EFFECTIVE_TIME_MISMATCH", **evidence})
            if posting["lot_id"] is not None:
                evidence["lot_id"] = posting["lot_id"]
            if oid is not None:
                evidence["settlement_obligation_id"] = oid
            value = _add(lines, issues, kind, key, amount, evidence)
            if oid is not None and value is not None:
                if posting["record_version_status"] != "CURRENT" or posting["evidence_status"] != "VERIFIED":
                    issues.append({"code": "OBLIGATION_UNQUALIFIED", **evidence})
                if posting["originating_event_id"] == eid:
                    if value <= 0 or canonical(value) != posting["original_amount_dec"] or oid in visible_origins:
                        issues.append({"code": "OBLIGATION_ORIGIN_UNQUALIFIED", **evidence})
                    visible_origins.add(oid)
                else:
                    movements[oid].append(evidence)
                    origin_at = posting["origin_effective_at"] or anchor["cutoff_at"]
                    if (
                        value >= 0 or event["economic_effective_at"] < origin_at
                        or event["event_subtype"] not in (f"SETTLE_{kind}", f"CANCEL_UNSETTLED_{kind}")
                    ):
                        issues.append({"code": "OBLIGATION_MOVEMENT_UNQUALIFIED", **evidence})
                outstanding[oid] = outstanding.get(oid, Decimal(0)) + value
    for oid, effects in movements.items():
        if oid not in visible_origins:
            issues.extend({"code": "OBLIGATION_ORIGIN_NOT_VISIBLE", **effect} for effect in effects)
    for oid, amount in sorted(outstanding.items()):
        if amount < 0:
            issues.append({"code": "NEGATIVE_OUTSTANDING_OBLIGATION", "settlement_obligation_id": oid})


def project_reconciliation(ledger: Ledger, account: str, *, effective_at: str, known_as_of: str) -> dict:
    """Return dated synthetic A0 amounts and additive provenance from one read snapshot.

    A certified opening is available at its declared cutoff. Events must pass
    both their economic and their availability cutoffs. A stored transfer
    confirmation also has to be known by the requested knowledge cutoff.
    """
    effective_at = validate_utc_seconds(effective_at)
    known_as_of = validate_utc_seconds(known_as_of)
    result = {
        "mode": "SYNTHETIC_ONLY", "account": account, "basis": "TRADE_DATE",
        "effective_at": effective_at, "known_as_of": known_as_of,
        "lines": [], "issues": [],
    }
    issues = result["issues"]
    lines = defaultdict(list)
    with _read_snapshot(ledger), localcontext(_CONTEXT):
        account_id = ledger.aid(account)
        anchor, source_id = _opening(ledger, account_id, effective_at, known_as_of, issues)
        if anchor is None:
            return result
        outstanding = _opening_lines(ledger, anchor, source_id, lines, issues)
        _event_lines(ledger, account_id, anchor, effective_at, known_as_of, lines, issues, outstanding)
        for (kind, key), entries in sorted(lines.items()):
            result["lines"].append({
                "kind": kind, "key": key,
                "amount_dec": canonical(sum((value for value, _ in entries), Decimal(0))),
                "evidence": [trace for _, trace in entries],
            })
    return result
