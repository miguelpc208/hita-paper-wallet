CREATE TABLE gov_schema_migrations (
 migration_id TEXT PRIMARY KEY, migration_version TEXT NOT NULL UNIQUE,
 parent_version TEXT, definition_sha256 TEXT NOT NULL CHECK(length(definition_sha256)=64),
 target_schema_sha256 TEXT, applied_at TEXT NOT NULL, migration_status TEXT NOT NULL CHECK(migration_status='APPLIED'),
 applied_by_role TEXT NOT NULL
) STRICT;
CREATE TABLE aud_events (
 audit_event_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
 actor_role TEXT NOT NULL, action_code TEXT NOT NULL, entity_type TEXT NOT NULL,
 entity_reference TEXT, correlation_id TEXT, severity TEXT NOT NULL CHECK(severity IN('INFO','WARNING','ERROR','CRITICAL')),
 action_outcome TEXT NOT NULL CHECK(action_outcome IN('ACCEPTED','REJECTED','BLOCKED','INFORMATION')),
 detail_json TEXT, detail_sha256 TEXT
) STRICT;
CREATE TABLE src_data_sources (
 source_id TEXT PRIMARY KEY, source_code TEXT NOT NULL UNIQUE, source_name TEXT NOT NULL,
 source_kind TEXT NOT NULL CHECK(source_kind IN('SYNTHETIC','BROKER','BANK','MARKET','MACRO','OTHER')),
 privacy_class TEXT NOT NULL CHECK(privacy_class IN('PUBLIC','INTERNAL','CONFIDENTIAL','RESTRICTED')),
 active_flag INTEGER NOT NULL CHECK(active_flag IN(0,1)), registered_at TEXT NOT NULL, source_description TEXT
) STRICT;
CREATE TABLE src_source_contracts (
 contract_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES src_data_sources(source_id) ON DELETE RESTRICT,
 contract_code TEXT NOT NULL, contract_version TEXT NOT NULL, schema_sha256 TEXT NOT NULL,
 effective_from TEXT NOT NULL, effective_to TEXT, qualification_status TEXT NOT NULL
 CHECK(qualification_status IN('DRAFT','APPROVED','SUSPENDED','SUPERSEDED')),
 approved_at TEXT, contract_description TEXT,
 UNIQUE(source_id,contract_code,contract_version), CHECK(effective_to IS NULL OR effective_to>effective_from),
 CHECK(qualification_status!='APPROVED' OR approved_at IS NOT NULL)
) STRICT;
CREATE TABLE src_source_artifacts (
 artifact_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES src_data_sources(source_id) ON DELETE RESTRICT,
 provider_artifact_key TEXT, artifact_locator TEXT NOT NULL, content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
 content_bytes INTEGER NOT NULL CHECK(content_bytes>=0), acquired_at TEXT NOT NULL, source_published_at TEXT,
 artifact_class TEXT NOT NULL, privacy_class TEXT NOT NULL, artifact_status TEXT NOT NULL
 CHECK(artifact_status IN('RECEIVED','VERIFIED','QUARANTINED','SUPERSEDED'))
) STRICT;
CREATE TABLE src_ingestion_batches (
 batch_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES src_source_artifacts(artifact_id) ON DELETE RESTRICT,
 contract_id TEXT NOT NULL REFERENCES src_source_contracts(contract_id) ON DELETE RESTRICT,
 attempt_no INTEGER NOT NULL CHECK(attempt_no>0), started_at TEXT NOT NULL, finished_at TEXT,
 batch_status TEXT NOT NULL CHECK(batch_status IN('STARTED','ACCEPTED','REJECTED','PARTIAL','FAILED')),
 total_records INTEGER NOT NULL DEFAULT 0 CHECK(total_records>=0),
 accepted_records INTEGER NOT NULL DEFAULT 0 CHECK(accepted_records>=0),
 duplicate_records INTEGER NOT NULL DEFAULT 0 CHECK(duplicate_records>=0),
 rejected_records INTEGER NOT NULL DEFAULT 0 CHECK(rejected_records>=0),
 quarantined_records INTEGER NOT NULL DEFAULT 0 CHECK(quarantined_records>=0),
 pending_records INTEGER NOT NULL DEFAULT 0 CHECK(pending_records>=0), error_reference TEXT,
 UNIQUE(artifact_id,contract_id,attempt_no),
 CHECK(batch_status IN('STARTED','PARTIAL') OR finished_at IS NOT NULL)
) STRICT;
CREATE TABLE src_source_records (
 source_record_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES src_ingestion_batches(batch_id) ON DELETE RESTRICT,
 record_locator TEXT NOT NULL, record_content_sha256 TEXT NOT NULL CHECK(length(record_content_sha256)=64),
 normalized_sha256 TEXT, provider_operation_key TEXT, provider_operation_scope TEXT,
 source_effective_at TEXT, source_record_type TEXT,
 classification_status TEXT NOT NULL CHECK(classification_status IN('PENDING','ACCEPTED','DUPLICATE','REJECTED','QUARANTINED')),
 validation_error_code TEXT, normalized_payload_json TEXT,
 UNIQUE(batch_id,record_locator), CHECK(provider_operation_key IS NULL OR provider_operation_scope IS NOT NULL)
) STRICT;
