"""Read-only internal integrity audit for synthetic HITA A0 SQLite files.

This is same-project verification, not independent assurance or release certification.
No network, account, brokerage, order, or external-service interactions.
"""
from __future__ import annotations
from pathlib import Path
from decimal import Decimal
from hashlib import sha256
import sqlite3
from .schema import schema_fingerprint
from .money import dec


def audit_database(path):
    file=Path(path).expanduser().resolve(strict=True)
    db=sqlite3.connect(file.as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('PRAGMA foreign_keys=ON')
    issues=[]
    try:
        rows=lambda sql,args=(): db.execute(sql,args).fetchall()
        one=lambda sql,args=(): db.execute(sql,args).fetchone()
        tables=one("SELECT count(*) n FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")['n']
        if tables!=30:issues.append('unexpected physical table count')
        migration_count=one('SELECT count(*) n FROM gov_schema_migrations')['n']
        if migration_count!=5:issues.append('unexpected migration count')
        fp=schema_fingerprint(db)
        recorded=one("SELECT target_schema_sha256 FROM gov_schema_migrations WHERE migration_version='005_reconciliation'")
        if not recorded or recorded['target_schema_sha256']!=fp:issues.append('schema fingerprint drift')
        integrity=one('PRAGMA integrity_check')[0]
        if integrity!='ok':issues.append('SQLite integrity_check failed')
        foreign_key_issues=len(rows('PRAGMA foreign_key_check'))
        if foreign_key_issues:issues.append('foreign key violation(s)')
        source_hash_mismatches=[]
        for r in rows('SELECT source_record_id,record_content_sha256,normalized_payload_json FROM src_source_records'):
            payload=r['normalized_payload_json']
            if payload is None or sha256(payload.encode('utf-8')).hexdigest()!=r['record_content_sha256']:
                source_hash_mismatches.append(r['source_record_id'])
        if source_hash_mismatches:issues.append('normalized source payload hash mismatch')
        no_sources=rows("SELECT e.event_id FROM led_financial_events e WHERE e.event_status='ACCEPTED' AND NOT EXISTS (SELECT 1 FROM src_record_event_links l WHERE l.event_id=e.event_id AND l.evidence_status='VERIFIED')")
        if no_sources:issues.append('accepted event without verified source link')
        count_non_synthetic=one("SELECT count(*) n FROM src_data_sources WHERE source_kind<>'SYNTHETIC'")['n']
        count_real_accounts=one('SELECT count(*) n FROM ref_accounts WHERE synthetic_flag<>1')['n']
        if count_non_synthetic or count_real_accounts:issues.append('non-synthetic reference detected in A0')
        quantity_mismatches=[]
        for account in rows('SELECT account_id,account_key FROM ref_accounts'):
            aid=account['account_id']
            for inst in rows('SELECT instrument_id,instrument_key FROM ref_instruments'):
                iid=inst['instrument_id']
                opening=sum((dec(r['quantity_dec'],scale=12) for r in rows('SELECT o.quantity_dec FROM led_opening_positions o JOIN led_opening_anchors a ON a.opening_anchor_id=o.opening_anchor_id WHERE a.account_id=? AND o.instrument_id=?',(aid,iid))),Decimal(0))
                posting=sum((dec(r['quantity_dec'],scale=12) for r in rows("SELECT p.quantity_dec FROM led_event_postings p JOIN led_financial_events e ON e.event_id=p.event_id WHERE p.account_id=? AND p.instrument_id=? AND p.posting_class='SECURITY_QTY' AND e.event_status='ACCEPTED'",(aid,iid))),Decimal(0))
                lots=Decimal(0)
                for lot in rows('SELECT lot_id,lot_origin_type,initial_quantity_dec FROM led_position_lots WHERE account_id=? AND instrument_id=?',(aid,iid)):
                    lots+=dec(lot['initial_quantity_dec'],scale=12) if lot['lot_origin_type']=='OPENING_POSITION' else Decimal(0)
                    lots+=sum((dec(r['quantity_dec'],scale=12) for r in rows("SELECT p.quantity_dec FROM led_event_postings p JOIN led_financial_events e ON e.event_id=p.event_id WHERE p.lot_id=? AND p.posting_class='SECURITY_QTY' AND e.event_status='ACCEPTED'",(lot['lot_id'],))),Decimal(0))
                if opening+posting!=lots or lots<0:
                    quantity_mismatches.append(f"{account['account_key']}/{inst['instrument_key']}")
        if quantity_mismatches:issues.append('holdings/lot quantity mismatch')
        bad_lot_cost=[]
        for lot in rows('SELECT lot_id,cost_basis_status,verified_initial_cost_dec FROM led_position_lots'):
            if lot['cost_basis_status']!='KNOWN':continue
            cost=dec(lot['verified_initial_cost_dec'])
            for a in rows("SELECT a.allocation_action,a.allocated_cost_dec FROM led_lot_allocations a JOIN led_financial_events e ON a.movement_event_id=e.event_id WHERE a.lot_id=? AND e.event_status='ACCEPTED'",(lot['lot_id'],)):
                if a['allocated_cost_dec'] is None:
                    bad_lot_cost.append(lot['lot_id']);break
                value=dec(a['allocated_cost_dec'])
                cost+=value if a['allocation_action']=='RESTORE' else -value
            if cost<0:bad_lot_cost.append(lot['lot_id'])
        if bad_lot_cost:issues.append('negative or unknown derived known lot cost')
        bad_obligations=[]
        for obligation in rows('SELECT settlement_obligation_id,original_amount_dec FROM led_settlement_obligations'):
            oid=obligation['settlement_obligation_id'];o=dec(obligation['original_amount_dec']);s=Decimal(0);c=Decimal(0)
            for posting in rows("SELECT p.amount_dec,e.event_subtype FROM led_event_postings p JOIN led_financial_events e ON e.event_id=p.event_id WHERE p.settlement_obligation_id=? AND e.event_status='ACCEPTED'",(oid,)):
                v=dec(posting['amount_dec']);t=posting['event_subtype']
                if v<0 and t.startswith('SETTLE_'):s-=v
                elif v<0 and t.startswith('CANCEL_'):c-=v
            if s<0 or c<0 or o-s-c<0:bad_obligations.append(oid)
        if bad_obligations:issues.append('obligation settlement/cancellation exceeds original')
        double_reversals=rows("SELECT correction_of_event_id,count(*) n FROM led_financial_events WHERE event_subtype='REVERSE_SETTLED_SELL' AND event_status='ACCEPTED' GROUP BY correction_of_event_id HAVING count(*)>1")
        if double_reversals:issues.append('sale reversed more than once')
        accepted=one("SELECT count(*) n FROM led_financial_events WHERE event_status='ACCEPTED'")['n']
        postings=one("SELECT count(*) n FROM led_event_postings p JOIN led_financial_events e ON e.event_id=p.event_id WHERE e.event_status='ACCEPTED'")['n']
        return {
            'classification':'SYNTHETIC_ONLY',
            'assurance':'INTERNAL_READ_ONLY_NOT_INDEPENDENT',
            'status':'PASS_LOCAL_CHECKS' if not issues else 'FAIL_LOCAL_CHECKS',
            'tables':tables,'migrations':migration_count,
            'accepted_events':accepted,'accepted_postings':postings,
            'source_record_hash_mismatches':len(source_hash_mismatches),
            'accepted_events_missing_verified_sources':len(no_sources),
            'lot_quantity_mismatches':quantity_mismatches,
            'invalid_obligation_count':len(bad_obligations),
            'double_sale_reversal_count':len(double_reversals),
            'sqlite_integrity_check':integrity,'foreign_key_issues':foreign_key_issues,
            'schema_sha256':fp,
            'original_source_artifact_bytes_verified':False,
            'independent_oracle_approvals':0,
            'issues':issues,
        }
    finally:
        db.close()
