"""DDL for the SQLite store. Plain SQL so a Postgres port is mechanical."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS lines (
  plant_id TEXT NOT NULL, line_id TEXT NOT NULL, line_name TEXT, operating_window TEXT,
  PRIMARY KEY (plant_id, line_id));

CREATE TABLE IF NOT EXISTS assets (
  machine_id TEXT PRIMARY KEY, plant_id TEXT NOT NULL, line_id TEXT NOT NULL, machine_type TEXT,
  criticality TEXT, installed_date TEXT, rated_max_temp_c REAL, rated_max_vibration_mm_s REAL,
  baseline_power_kw REAL, asset_status TEXT);

CREATE TABLE IF NOT EXISTS ingestion_batches (
  batch_id TEXT PRIMARY KEY, vendor TEXT NOT NULL, schema_version TEXT NOT NULL,
  request_hash TEXT NOT NULL, received_at TEXT NOT NULL,
  total_records INTEGER NOT NULL, accepted_records INTEGER NOT NULL,
  duplicate_records INTEGER NOT NULL, revised_records INTEGER NOT NULL DEFAULT 0,
  raw_body BLOB,                       -- the request body exactly as received (byte-for-byte)
  UNIQUE (vendor, request_hash));

-- Append-only landing zone for every record exactly as received (traceability + replay).
-- status: pending | processing | processed | duplicate | rejected | dead | superseded
-- is_latest = highest revision RECEIVED; the AUTHORITATIVE revision is normalized_events.raw_record_id.
CREATE TABLE IF NOT EXISTS raw_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL, vendor TEXT NOT NULL,
  vendor_record_id TEXT NOT NULL, revision INTEGER NOT NULL, is_latest INTEGER NOT NULL DEFAULT 1,
  payload_hash TEXT NOT NULL, payload TEXT NOT NULL, envelope TEXT NOT NULL, machine_hint TEXT,
  source_version TEXT,                 -- vendor batch version (UTC ISO) used to order revisions; NULL if not sent
  status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, lease_token TEXT,
  next_attempt_at TEXT, locked_until TEXT, error_code TEXT, error_message TEXT,
  received_at TEXT NOT NULL, processed_at TEXT,
  UNIQUE (vendor, vendor_record_id, revision));
CREATE INDEX IF NOT EXISTS idx_raw_queue ON raw_records (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_raw_batch ON raw_records (batch_id);
CREATE INDEX IF NOT EXISTS idx_raw_machine ON raw_records (machine_hint, is_latest);

CREATE TABLE IF NOT EXISTS normalized_events (
  vendor TEXT NOT NULL, source_record_id TEXT NOT NULL, raw_record_id INTEGER NOT NULL,
  fingerprint TEXT NOT NULL, machine_id TEXT NOT NULL, plant_id TEXT NOT NULL, line_id TEXT NOT NULL,
  event_time TEXT NOT NULL, kind TEXT NOT NULL, event_code TEXT NOT NULL, severity INTEGER,
  vibration_mm_s REAL, vibration_g REAL, temperature_c REAL, power_kw REAL,
  machine_state TEXT, signal_quality TEXT, sensor_health REAL, confidence REAL,
  inspection_result TEXT, maintenance_status TEXT, days_since_service INTEGER, note TEXT,
  details TEXT,                        -- kind-specific structured fields (JSON), e.g. work-order type and scope
  warnings TEXT NOT NULL DEFAULT '[]', normalized_at TEXT NOT NULL,
  PRIMARY KEY (vendor, source_record_id));
CREATE INDEX IF NOT EXISTS idx_ne_machine ON normalized_events (machine_id, event_time);
CREATE INDEX IF NOT EXISTS idx_ne_fp ON normalized_events (fingerprint);

CREATE TABLE IF NOT EXISTS machine_state (
  machine_id TEXT PRIMARY KEY, plant_id TEXT NOT NULL, line_id TEXT NOT NULL,
  derived_status TEXT NOT NULL, attention_level INTEGER NOT NULL, needs_attention INTEGER NOT NULL,
  reason_codes TEXT NOT NULL, reasons TEXT NOT NULL, latest_signals TEXT NOT NULL,
  machine_state TEXT, latest_relevant_event_time TEXT, source_event_refs TEXT NOT NULL,
  state_version INTEGER NOT NULL, rules_version TEXT NOT NULL, last_processed_at TEXT NOT NULL);

-- Durable "recompute me" marker so recomputation is retried if it fails or the worker dies.
CREATE TABLE IF NOT EXISTS dirty_machines (
  machine_id TEXT PRIMARY KEY, marked_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT);
"""
