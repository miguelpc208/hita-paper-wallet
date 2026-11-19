CREATE TABLE led_event_accounting_templates (
 accounting_template_id TEXT PRIMARY KEY, template_code TEXT NOT NULL, template_version TEXT NOT NULL,
 event_class TEXT NOT NULL, definition_sha256 TEXT NOT NULL, required_postings_json TEXT NOT NULL,
 template_status TEXT NOT NULL CHECK(template_status IN('DRAFT','APPROVED','SUSPENDED','SUPERSEDED')),
 effective_from TEXT NOT NULL, approved_at TEXT, recorded_at TEXT NOT NULL,
 UNIQUE(template_code,template_version), CHECK(template_status!='APPROVED' OR approved_at IS NOT NULL)
) STRICT;
CREATE TABLE led_opening_anchors (
 opening_anchor_id TEXT PRIMARY KEY,
 account_id TEXT NOT NULL REFERENCES ref_accounts(account_id) ON DELETE RESTRICT,
 statement_id TEXT NOT NULL, cutoff_at TEXT NOT NULL,
 recognition_basis TEXT NOT NULL CHECK(recognition_basis IN('TRADE_DATE','SETTLED_CUSTODY','SOURCE_DEFINED')),
 anchor_version INTEGER NOT NULL CHECK(anchor_version>0), included_effects_manifest_json TEXT NOT NULL,
 included_effects_sha256 TEXT NOT NULL, anchor_status TEXT NOT NULL
 CHECK(anchor_status IN('DRAFT','VALIDATED','RECONCILIATION_PENDING','CERTIFIED','BLOCKED','SUPERSEDED')),
 recorded_at TEXT NOT NULL,
 supersedes_anchor_id TEXT REFERENCES led_opening_anchors(opening_anchor_id) ON DELETE RESTRICT,
 certification_audit_id TEXT REFERENCES aud_events(audit_event_id) ON DELETE RESTRICT,
 UNIQUE(account_id,cutoff_at,recognition_basis,anchor_version), UNIQUE(opening_anchor_id,account_id),
 FOREIGN KEY(statement_id,account_id) REFERENCES rec_statement_headers(statement_id,account_id) ON DELETE RESTRICT,
 CHECK(anchor_status!='CERTIFIED' OR certification_audit_id IS NOT NULL)
) STRICT;
CREATE TABLE led_opening_cash (
 opening_cash_id TEXT PRIMARY KEY, opening_anchor_id TEXT NOT NULL REFERENCES led_opening_anchors(opening_anchor_id) ON DELETE RESTRICT,
 currency_code TEXT NOT NULL, balance_type TEXT NOT NULL CHECK(balance_type IN('SETTLED','AVAILABLE_SOURCE','PENDING_SOURCE')),
 amount_dec TEXT NOT NULL, source_line_locator TEXT NOT NULL, recorded_at TEXT NOT NULL,
 UNIQUE(opening_anchor_id,currency_code,balance_type)
) STRICT;
CREATE TABLE led_opening_positions (
 opening_position_id TEXT PRIMARY KEY, opening_anchor_id TEXT NOT NULL REFERENCES led_opening_anchors(opening_anchor_id) ON DELETE RESTRICT,
 instrument_id TEXT NOT NULL REFERENCES ref_instruments(instrument_id) ON DELETE RESTRICT,
 quantity_dec TEXT NOT NULL, cost_basis_status TEXT NOT NULL CHECK(cost_basis_status IN('KNOWN','PARTIALLY_KNOWN','UNKNOWN','DISPUTED','NOT_APPLICABLE')),
 known_cost_dec TEXT, known_cost_quantity_dec TEXT, cost_currency TEXT, source_line_locator TEXT NOT NULL,
 recorded_at TEXT NOT NULL, UNIQUE(opening_anchor_id,source_line_locator),
 UNIQUE(opening_position_id,opening_anchor_id,instrument_id),
 CHECK((cost_basis_status='UNKNOWN' AND known_cost_dec IS NULL) OR cost_basis_status!='UNKNOWN')
) STRICT;
CREATE TABLE led_opening_pending_items (
 opening_pending_id TEXT PRIMARY KEY,
 opening_anchor_id TEXT NOT NULL REFERENCES led_opening_anchors(opening_anchor_id) ON DELETE RESTRICT,
 source_line_locator TEXT NOT NULL, obligation_type TEXT NOT NULL CHECK(obligation_type IN('RECEIVABLE','PAYABLE')),
 currency_code TEXT NOT NULL, original_amount_dec TEXT NOT NULL, prior_execution_reference TEXT,
 expected_settlement_date TEXT, evidence_status TEXT NOT NULL
 CHECK(evidence_status IN('PENDING_VERIFICATION','VERIFIED','DISPUTED')),
 recorded_at TEXT NOT NULL, UNIQUE(opening_anchor_id,source_line_locator),
 UNIQUE(opening_pending_id,opening_anchor_id,currency_code,obligation_type)
) STRICT;
