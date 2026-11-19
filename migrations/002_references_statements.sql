CREATE TABLE ref_issuers (
 issuer_id TEXT PRIMARY KEY, issuer_key TEXT NOT NULL UNIQUE, legal_name TEXT NOT NULL,
 domicile_country TEXT, issuer_status TEXT NOT NULL CHECK(issuer_status IN('ACTIVE','INACTIVE','UNKNOWN')),
 created_at TEXT NOT NULL, evidence_artifact_id TEXT REFERENCES src_source_artifacts(artifact_id) ON DELETE RESTRICT
) STRICT;
CREATE TABLE ref_instruments (
 instrument_id TEXT PRIMARY KEY, issuer_id TEXT REFERENCES ref_issuers(issuer_id) ON DELETE RESTRICT,
 instrument_key TEXT NOT NULL UNIQUE, instrument_name TEXT NOT NULL, instrument_type TEXT NOT NULL
 CHECK(instrument_type IN('EQUITY','ETF','FUND','OTHER')),
 isin TEXT, denomination_currency TEXT, synthetic_flag INTEGER NOT NULL CHECK(synthetic_flag=1),
 instrument_status TEXT NOT NULL CHECK(instrument_status IN('ACTIVE','INACTIVE','DISPUTED')), created_at TEXT NOT NULL
) STRICT;
CREATE TABLE ref_listings (
 listing_id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL REFERENCES ref_instruments(instrument_id) ON DELETE RESTRICT,
 mic TEXT NOT NULL, ticker TEXT NOT NULL, trading_currency TEXT NOT NULL,
 valid_from TEXT NOT NULL, valid_to TEXT, primary_listing_flag INTEGER NOT NULL CHECK(primary_listing_flag IN(0,1)),
 evidence_artifact_id TEXT REFERENCES src_source_artifacts(artifact_id) ON DELETE RESTRICT,
 recorded_at TEXT NOT NULL, UNIQUE(mic,ticker,valid_from), CHECK(valid_to IS NULL OR valid_to>valid_from)
) STRICT;
CREATE TABLE ref_accounts (
 account_id TEXT PRIMARY KEY, account_key TEXT NOT NULL UNIQUE, account_type TEXT NOT NULL
 CHECK(account_type IN('BANK','BROKERAGE','SYNTHETIC_OTHER')),
 reporting_currency TEXT NOT NULL, owner_reference TEXT NOT NULL, external_account_hash TEXT,
 account_status TEXT NOT NULL CHECK(account_status IN('ACTIVE','INACTIVE','BLOCKED')),
 synthetic_flag INTEGER NOT NULL CHECK(synthetic_flag=1), created_at TEXT NOT NULL
) STRICT;
CREATE TABLE ref_reporting_perimeters (
 perimeter_id TEXT PRIMARY KEY, perimeter_code TEXT NOT NULL,
 perimeter_type TEXT NOT NULL CHECK(perimeter_type IN('ACCOUNT','INVESTMENT','CONSOLIDATED_WEALTH')),
 reporting_currency TEXT NOT NULL, valid_from TEXT NOT NULL, valid_to TEXT,
 perimeter_status TEXT NOT NULL CHECK(perimeter_status IN('ACTIVE','INACTIVE','SUPERSEDED')),
 created_at TEXT NOT NULL, UNIQUE(perimeter_code,valid_from), CHECK(valid_to IS NULL OR valid_to>valid_from)
) STRICT;
CREATE TABLE ref_perimeter_memberships (
 perimeter_membership_id TEXT PRIMARY KEY,
 perimeter_id TEXT NOT NULL REFERENCES ref_reporting_perimeters(perimeter_id) ON DELETE RESTRICT,
 account_id TEXT NOT NULL REFERENCES ref_accounts(account_id) ON DELETE RESTRICT,
 effective_from TEXT NOT NULL, effective_to TEXT,
 inclusion_status TEXT NOT NULL CHECK(inclusion_status IN('INCLUDED','EXCLUDED','DISPUTED')),
 evidence_artifact_id TEXT REFERENCES src_source_artifacts(artifact_id) ON DELETE RESTRICT,
 approved_at TEXT, recorded_at TEXT NOT NULL,
 UNIQUE(perimeter_id,account_id,effective_from), CHECK(effective_to IS NULL OR effective_to>effective_from)
) STRICT;
CREATE TABLE rec_statement_headers (
 statement_id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES ref_accounts(account_id) ON DELETE RESTRICT,
 artifact_id TEXT NOT NULL REFERENCES src_source_artifacts(artifact_id) ON DELETE RESTRICT,
 statement_key TEXT NOT NULL, period_from TEXT, period_to TEXT, cutoff_at TEXT NOT NULL,
 recognition_basis TEXT NOT NULL CHECK(recognition_basis IN('TRADE_DATE','SETTLED_CUSTODY','SOURCE_DEFINED')),
 statement_status TEXT NOT NULL CHECK(statement_status IN('RECEIVED','VALIDATED','DISPUTED','SUPERSEDED')),
 recorded_at TEXT NOT NULL, UNIQUE(account_id,artifact_id,statement_key,cutoff_at),
 UNIQUE(statement_id,account_id), CHECK(period_from IS NULL OR period_to IS NULL OR period_to>=period_from)
) STRICT;
CREATE TABLE rec_statement_positions (
 statement_position_id TEXT PRIMARY KEY,
 statement_id TEXT NOT NULL REFERENCES rec_statement_headers(statement_id) ON DELETE RESTRICT,
 instrument_id TEXT NOT NULL REFERENCES ref_instruments(instrument_id) ON DELETE RESTRICT,
 quantity_dec TEXT NOT NULL, cost_basis_status TEXT NOT NULL CHECK(cost_basis_status IN('KNOWN','PARTIALLY_KNOWN','UNKNOWN','DISPUTED','NOT_APPLICABLE')),
 known_cost_dec TEXT, known_cost_currency TEXT, source_line_locator TEXT NOT NULL,
 valuation_price_dec TEXT, valuation_currency TEXT, recorded_at TEXT NOT NULL,
 UNIQUE(statement_id,source_line_locator),
 CHECK((cost_basis_status='UNKNOWN' AND known_cost_dec IS NULL) OR cost_basis_status!='UNKNOWN')
) STRICT;
CREATE TABLE rec_statement_cash_balances (
 statement_cash_id TEXT PRIMARY KEY, statement_id TEXT NOT NULL REFERENCES rec_statement_headers(statement_id) ON DELETE RESTRICT,
 currency_code TEXT NOT NULL, balance_type TEXT NOT NULL CHECK(balance_type IN('SETTLED','AVAILABLE_SOURCE','PENDING_SOURCE','OTHER_SOURCE')),
 amount_dec TEXT NOT NULL, source_line_locator TEXT NOT NULL, recorded_at TEXT NOT NULL,
 UNIQUE(statement_id,source_line_locator)
) STRICT;
