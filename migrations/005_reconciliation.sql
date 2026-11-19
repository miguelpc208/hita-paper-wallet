CREATE TABLE rec_reconciliation_runs (
 reconciliation_id TEXT PRIMARY KEY,
 account_id TEXT NOT NULL REFERENCES ref_accounts(account_id) ON DELETE RESTRICT,
 statement_id TEXT NOT NULL REFERENCES rec_statement_headers(statement_id) ON DELETE RESTRICT,
 opening_anchor_id TEXT REFERENCES led_opening_anchors(opening_anchor_id) ON DELETE RESTRICT,
 reconciliation_scope TEXT NOT NULL CHECK(reconciliation_scope IN('OPENING','CASH','POSITIONS','SETTLEMENT','FULL_ACCOUNT')),
 cutoff_at TEXT NOT NULL, recognition_basis TEXT NOT NULL
 CHECK(recognition_basis IN('TRADE_DATE','SETTLED_CUSTODY','SOURCE_DEFINED')),
 ruleset_version TEXT NOT NULL, input_manifest_sha256 TEXT NOT NULL, run_status TEXT NOT NULL
 CHECK(run_status IN('STARTED','PASSED','FAILED','BLOCKED')),
 started_at TEXT NOT NULL, completed_at TEXT, accepted_audit_id TEXT REFERENCES aud_events(audit_event_id) ON DELETE RESTRICT,
 explanation_reference TEXT, CHECK(run_status='STARTED' OR completed_at IS NOT NULL)
) STRICT;
CREATE TABLE rec_reconciliation_breaks (
 reconciliation_break_id TEXT PRIMARY KEY, reconciliation_id TEXT NOT NULL REFERENCES rec_reconciliation_runs(reconciliation_id) ON DELETE RESTRICT,
 break_sequence INTEGER NOT NULL CHECK(break_sequence>0), break_code TEXT NOT NULL,
 affected_entity_type TEXT NOT NULL, affected_entity_reference TEXT,
 value_kind TEXT NOT NULL CHECK(value_kind IN('MONEY','QUANTITY','IDENTITY','TIMING','OTHER')),
 expected_value_dec TEXT, observed_value_dec TEXT, difference_dec TEXT, currency_code TEXT,
 instrument_id TEXT REFERENCES ref_instruments(instrument_id) ON DELETE RESTRICT,
 severity TEXT NOT NULL CHECK(severity IN('CRITICAL','HIGH','MODERATE','LOW')),
 break_status TEXT NOT NULL CHECK(break_status IN('OPEN','RESOLVED','ACCEPTED_LIMITATION')),
 resolution_evidence_reference TEXT, created_at TEXT NOT NULL, resolved_at TEXT,
 UNIQUE(reconciliation_id,break_sequence),
 CHECK(break_status!='RESOLVED' OR (resolved_at IS NOT NULL AND resolution_evidence_reference IS NOT NULL))
) STRICT;
