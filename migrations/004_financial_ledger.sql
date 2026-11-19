CREATE TABLE led_financial_events (
 event_id TEXT PRIMARY KEY,
 account_id TEXT NOT NULL REFERENCES ref_accounts(account_id) ON DELETE RESTRICT,
 source_id TEXT NOT NULL REFERENCES src_data_sources(source_id) ON DELETE RESTRICT,
 instrument_id TEXT REFERENCES ref_instruments(instrument_id) ON DELETE RESTRICT,
 accounting_template_id TEXT NOT NULL REFERENCES led_event_accounting_templates(accounting_template_id) ON DELETE RESTRICT,
 event_class TEXT NOT NULL, event_subtype TEXT NOT NULL,
 provider_operation_key TEXT NOT NULL, provider_operation_scope TEXT NOT NULL,
 parent_order_reference TEXT, economic_effective_at TEXT NOT NULL, available_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
 event_status TEXT NOT NULL CHECK(event_status IN('STAGED','ACCEPTED','REJECTED','QUARANTINED')),
 correction_of_event_id TEXT REFERENCES led_financial_events(event_id) ON DELETE RESTRICT,
 event_content_sha256 TEXT NOT NULL CHECK(length(event_content_sha256)=64),
 event_terms_schema_code TEXT NOT NULL, event_terms_json TEXT NOT NULL,
 executed_quantity_dec TEXT, executed_unit_price_dec TEXT, trade_currency_code TEXT,
 gross_amount_dec TEXT, fee_amount_dec TEXT, tax_amount_dec TEXT, net_amount_dec TEXT,
 UNIQUE(event_id,account_id), UNIQUE(event_id,accounting_template_id),
 UNIQUE(event_id,account_id,instrument_id),
 UNIQUE(source_id,account_id,provider_operation_scope,provider_operation_key,event_subtype)
) STRICT;
CREATE TABLE led_event_relations (
 relation_id TEXT PRIMARY KEY,
 from_event_id TEXT NOT NULL REFERENCES led_financial_events(event_id) ON DELETE RESTRICT,
 to_event_id TEXT NOT NULL REFERENCES led_financial_events(event_id) ON DELETE RESTRICT,
 relation_type TEXT NOT NULL CHECK(relation_type IN('TRANSFER_MATCH','CORRECTS','REVERSES','SETTLES','ASSOCIATED_FILL')),
 matching_rule_version TEXT, evidence_artifact_id TEXT REFERENCES src_source_artifacts(artifact_id) ON DELETE RESTRICT,
 relation_status TEXT NOT NULL CHECK(relation_status IN('PROPOSED','ACCEPTED','REJECTED','SUPERSEDED')),
 effective_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
 UNIQUE(from_event_id,to_event_id,relation_type), CHECK(from_event_id!=to_event_id)
) STRICT;
CREATE TABLE led_settlement_obligations (
 settlement_obligation_id TEXT PRIMARY KEY,
 obligation_origin_type TEXT NOT NULL CHECK(obligation_origin_type IN('EVENT_ORIGIN','OPENING_ORIGIN')),
 originating_event_id TEXT REFERENCES led_financial_events(event_id) ON DELETE RESTRICT,
 opening_pending_id TEXT REFERENCES led_opening_pending_items(opening_pending_id) ON DELETE RESTRICT,
 account_id TEXT NOT NULL REFERENCES ref_accounts(account_id) ON DELETE RESTRICT,
 currency_code TEXT NOT NULL, obligation_type TEXT NOT NULL CHECK(obligation_type IN('RECEIVABLE','PAYABLE')),
 original_amount_dec TEXT NOT NULL, recognized_at TEXT NOT NULL, expected_settlement_date TEXT,
 evidence_status TEXT NOT NULL CHECK(evidence_status IN('PENDING_VERIFICATION','VERIFIED','DISPUTED')),
 record_version_status TEXT NOT NULL CHECK(record_version_status IN('CURRENT','SUPERSEDED')),
 supersedes_obligation_id TEXT REFERENCES led_settlement_obligations(settlement_obligation_id) ON DELETE RESTRICT,
 source_evidence_reference TEXT NOT NULL, recorded_at TEXT NOT NULL,
 UNIQUE(settlement_obligation_id,account_id,currency_code,obligation_type),
 CHECK((obligation_origin_type='EVENT_ORIGIN' AND originating_event_id IS NOT NULL AND opening_pending_id IS NULL)
 OR (obligation_origin_type='OPENING_ORIGIN' AND opening_pending_id IS NOT NULL AND originating_event_id IS NULL))
) STRICT;
CREATE UNIQUE INDEX uq_current_open_pending ON led_settlement_obligations(opening_pending_id)
 WHERE opening_pending_id IS NOT NULL AND record_version_status='CURRENT';
CREATE TABLE led_position_lots (
 lot_id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES ref_accounts(account_id) ON DELETE RESTRICT,
 instrument_id TEXT NOT NULL REFERENCES ref_instruments(instrument_id) ON DELETE RESTRICT,
 lot_origin_type TEXT NOT NULL CHECK(lot_origin_type IN('ACQUISITION_EVENT','OPENING_POSITION')),
 acquisition_event_id TEXT REFERENCES led_financial_events(event_id) ON DELETE RESTRICT,
 opening_position_id TEXT REFERENCES led_opening_positions(opening_position_id) ON DELETE RESTRICT,
 initial_quantity_dec TEXT NOT NULL, cost_basis_status TEXT NOT NULL
 CHECK(cost_basis_status IN('KNOWN','PARTIALLY_KNOWN','UNKNOWN','DISPUTED','NOT_APPLICABLE')),
 verified_initial_cost_dec TEXT, verified_cost_quantity_dec TEXT, cost_currency TEXT,
 origin_effective_at TEXT NOT NULL, origin_evidence_reference TEXT NOT NULL, recorded_at TEXT NOT NULL,
 UNIQUE(lot_id,account_id,instrument_id),
 CHECK((lot_origin_type='ACQUISITION_EVENT' AND acquisition_event_id IS NOT NULL AND opening_position_id IS NULL)
 OR (lot_origin_type='OPENING_POSITION' AND opening_position_id IS NOT NULL AND acquisition_event_id IS NULL)),
 CHECK(cost_basis_status!='UNKNOWN' OR verified_initial_cost_dec IS NULL)
) STRICT;
CREATE TABLE led_event_postings (
 posting_id TEXT PRIMARY KEY,
 event_id TEXT NOT NULL, account_id TEXT NOT NULL, accounting_template_id TEXT NOT NULL,
 posting_sequence INTEGER NOT NULL CHECK(posting_sequence>0), posting_class TEXT NOT NULL
 CHECK(posting_class IN('SECURITY_QTY','SETTLED_CASH','RECEIVABLE','PAYABLE','FEE_MEMO','TAX_MEMO')),
 instrument_id TEXT REFERENCES ref_instruments(instrument_id) ON DELETE RESTRICT,
 lot_id TEXT, settlement_obligation_id TEXT, obligation_type TEXT,
 currency_code TEXT, amount_dec TEXT, quantity_dec TEXT,
 effective_at TEXT NOT NULL, recorded_at TEXT NOT NULL, posting_evidence_reference TEXT NOT NULL,
 UNIQUE(event_id,posting_sequence),
 FOREIGN KEY(event_id,account_id) REFERENCES led_financial_events(event_id,account_id) ON DELETE RESTRICT,
 FOREIGN KEY(event_id,accounting_template_id) REFERENCES led_financial_events(event_id,accounting_template_id) ON DELETE RESTRICT,
 FOREIGN KEY(lot_id,account_id,instrument_id) REFERENCES led_position_lots(lot_id,account_id,instrument_id) ON DELETE RESTRICT,
 FOREIGN KEY(settlement_obligation_id,account_id,currency_code,obligation_type)
 REFERENCES led_settlement_obligations(settlement_obligation_id,account_id,currency_code,obligation_type) ON DELETE RESTRICT,
 CHECK((posting_class='SECURITY_QTY' AND quantity_dec IS NOT NULL AND instrument_id IS NOT NULL AND lot_id IS NOT NULL AND amount_dec IS NULL AND currency_code IS NULL AND settlement_obligation_id IS NULL AND obligation_type IS NULL)
 OR (posting_class!='SECURITY_QTY' AND amount_dec IS NOT NULL AND currency_code IS NOT NULL AND quantity_dec IS NULL AND lot_id IS NULL)),
 CHECK((posting_class='PAYABLE' AND settlement_obligation_id IS NOT NULL AND obligation_type='PAYABLE')
 OR (posting_class='RECEIVABLE' AND settlement_obligation_id IS NOT NULL AND obligation_type='RECEIVABLE')
 OR (posting_class NOT IN('PAYABLE','RECEIVABLE') AND settlement_obligation_id IS NULL AND obligation_type IS NULL))
) STRICT;
CREATE TABLE led_lot_allocations (
 lot_allocation_id TEXT PRIMARY KEY,
 lot_id TEXT NOT NULL REFERENCES led_position_lots(lot_id) ON DELETE RESTRICT,
 movement_event_id TEXT NOT NULL REFERENCES led_financial_events(event_id) ON DELETE RESTRICT,
 allocation_action TEXT NOT NULL CHECK(allocation_action IN('DISPOSE','RESTORE','VOID_ACQUISITION')),
 allocated_quantity_dec TEXT NOT NULL, allocated_cost_dec TEXT,
 cost_basis_status TEXT NOT NULL CHECK(cost_basis_status IN('KNOWN','PARTIALLY_KNOWN','UNKNOWN','DISPUTED')),
 reverses_allocation_id TEXT REFERENCES led_lot_allocations(lot_allocation_id) ON DELETE RESTRICT,
 allocation_method TEXT CHECK(allocation_method IN('FIFO','SPECIFIC_IDENTIFICATION','APPROVED_OTHER')),
 evidence_reference TEXT NOT NULL, recorded_at TEXT NOT NULL,
 UNIQUE(lot_id,movement_event_id,allocation_action),
 CHECK((allocation_action='DISPOSE' AND allocation_method IS NOT NULL AND reverses_allocation_id IS NULL)
 OR (allocation_action='RESTORE' AND allocation_method IS NULL AND reverses_allocation_id IS NOT NULL)
 OR (allocation_action='VOID_ACQUISITION' AND allocation_method IS NULL AND reverses_allocation_id IS NULL)),
 CHECK(cost_basis_status!='UNKNOWN' OR allocated_cost_dec IS NULL)
) STRICT;
CREATE TABLE src_record_event_links (
 record_event_link_id TEXT PRIMARY KEY,
 source_record_id TEXT NOT NULL REFERENCES src_source_records(source_record_id) ON DELETE RESTRICT,
 event_id TEXT NOT NULL REFERENCES led_financial_events(event_id) ON DELETE RESTRICT,
 link_role TEXT NOT NULL CHECK(link_role IN('EVIDENCES','CONFIRMS','CORRECTS_SOURCE','SUPPORTS_SETTLEMENT')),
 evidence_status TEXT NOT NULL CHECK(evidence_status IN('VERIFIED','PENDING_VERIFICATION','DISPUTED')),
 recorded_at TEXT NOT NULL, UNIQUE(source_record_id,event_id,link_role)
) STRICT;
CREATE INDEX ix_postings_event ON led_event_postings(event_id);
CREATE INDEX ix_postings_account ON led_event_postings(account_id,posting_class,effective_at);
CREATE INDEX ix_lots_account ON led_position_lots(account_id,instrument_id);
CREATE INDEX ix_obligations_account ON led_settlement_obligations(account_id,obligation_type);
CREATE INDEX ix_events_effective ON led_financial_events(account_id,economic_effective_at);
-- Prevent routine mutation of sealed financial state. No DELETE of accepted financial effects.
CREATE TRIGGER trg_event_immutable_update BEFORE UPDATE ON led_financial_events
 WHEN OLD.event_status='ACCEPTED' BEGIN SELECT RAISE(ABORT,'accepted financial event immutable'); END;
CREATE TRIGGER trg_event_immutable_delete BEFORE DELETE ON led_financial_events
 WHEN OLD.event_status='ACCEPTED' BEGIN SELECT RAISE(ABORT,'accepted financial event immutable'); END;
CREATE TRIGGER trg_event_accept_minimum BEFORE UPDATE OF event_status ON led_financial_events
 WHEN NEW.event_status='ACCEPTED' BEGIN
 SELECT CASE WHEN (SELECT COUNT(*) FROM led_event_postings WHERE event_id=NEW.event_id)=0
   THEN RAISE(ABORT,'financial event without posting') END;
 SELECT CASE WHEN (SELECT COUNT(*) FROM src_record_event_links WHERE event_id=NEW.event_id AND evidence_status='VERIFIED')=0
   THEN RAISE(ABORT,'financial event without verified source') END;
END;
CREATE TRIGGER trg_posting_immutable_update BEFORE UPDATE ON led_event_postings
 WHEN EXISTS(SELECT 1 FROM led_financial_events WHERE event_id=OLD.event_id AND event_status='ACCEPTED')
 BEGIN SELECT RAISE(ABORT,'accepted posting immutable'); END;
CREATE TRIGGER trg_posting_immutable_delete BEFORE DELETE ON led_event_postings
 WHEN EXISTS(SELECT 1 FROM led_financial_events WHERE event_id=OLD.event_id AND event_status='ACCEPTED')
 BEGIN SELECT RAISE(ABORT,'accepted posting immutable'); END;
CREATE TRIGGER trg_posting_sealed_insert BEFORE INSERT ON led_event_postings
 WHEN EXISTS(SELECT 1 FROM led_financial_events WHERE event_id=NEW.event_id AND event_status='ACCEPTED')
 BEGIN SELECT RAISE(ABORT,'cannot add postings to accepted event'); END;
CREATE TRIGGER trg_allocation_immutable_update BEFORE UPDATE ON led_lot_allocations
 WHEN EXISTS(SELECT 1 FROM led_financial_events WHERE event_id=OLD.movement_event_id AND event_status='ACCEPTED')
 BEGIN SELECT RAISE(ABORT,'accepted allocation immutable'); END;
CREATE TRIGGER trg_allocation_immutable_delete BEFORE DELETE ON led_lot_allocations
 WHEN EXISTS(SELECT 1 FROM led_financial_events WHERE event_id=OLD.movement_event_id AND event_status='ACCEPTED')
 BEGIN SELECT RAISE(ABORT,'accepted allocation immutable'); END;
CREATE TRIGGER trg_obligation_immutable_update BEFORE UPDATE ON led_settlement_obligations
 BEGIN SELECT RAISE(ABORT,'obligation record immutable; use linked version'); END;
CREATE TRIGGER trg_obligation_immutable_delete BEFORE DELETE ON led_settlement_obligations
 BEGIN SELECT RAISE(ABORT,'obligation record immutable; use linked version'); END;
-- Shape guards supplement (but cannot replace) exact-decimal application-level validation.
CREATE TRIGGER trg_event_accept_min_shapes BEFORE UPDATE OF event_status ON led_financial_events
 WHEN NEW.event_status='ACCEPTED' BEGIN
 SELECT CASE WHEN NEW.event_subtype IN('SETTLE_PAYABLE','SETTLE_RECEIVABLE') AND (
     (SELECT COUNT(*) FROM led_event_postings WHERE event_id=NEW.event_id)!=2 OR
     (SELECT COUNT(*) FROM led_event_postings WHERE event_id=NEW.event_id AND posting_class='SETTLED_CASH')!=1 OR
     (SELECT COUNT(*) FROM led_event_postings WHERE event_id=NEW.event_id AND posting_class=CASE WHEN NEW.event_subtype='SETTLE_PAYABLE' THEN 'PAYABLE' ELSE 'RECEIVABLE' END)!=1)
 THEN RAISE(ABORT,'incomplete paired settlement') END;
 SELECT CASE WHEN NEW.event_subtype IN('CANCEL_UNSETTLED_PAYABLE','CANCEL_UNSETTLED_RECEIVABLE') AND (
     (SELECT COUNT(*) FROM led_event_postings WHERE event_id=NEW.event_id)!=1 OR
     (SELECT COUNT(*) FROM led_event_postings WHERE event_id=NEW.event_id AND posting_class=CASE WHEN NEW.event_subtype='CANCEL_UNSETTLED_PAYABLE' THEN 'PAYABLE' ELSE 'RECEIVABLE' END)!=1)
 THEN RAISE(ABORT,'invalid obligation cancellation posting set') END;
 SELECT CASE WHEN NEW.event_subtype='BUY' AND (
     (SELECT COUNT(*) FROM led_event_postings WHERE event_id=NEW.event_id AND posting_class='SECURITY_QTY')!=1 OR
     (SELECT COUNT(*) FROM led_event_postings WHERE event_id=NEW.event_id AND posting_class='PAYABLE')!=1)
 THEN RAISE(ABORT,'incomplete buy posting set') END;
 SELECT CASE WHEN NEW.event_subtype='SELL' AND (
     (SELECT COUNT(*) FROM led_event_postings WHERE event_id=NEW.event_id AND posting_class='SECURITY_QTY')<1 OR
     (SELECT COUNT(*) FROM led_event_postings WHERE event_id=NEW.event_id AND posting_class='RECEIVABLE')!=1)
 THEN RAISE(ABORT,'incomplete sell posting set') END;
END;
