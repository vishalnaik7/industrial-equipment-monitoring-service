"""SQLite access. WAL mode lets the API process and a separate worker process share one file."""
from __future__ import annotations

import csv
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import get_settings

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
  UNIQUE (vendor, request_hash));

-- Append-only landing zone for every record exactly as received (traceability + replay).
-- status: pending | processing | processed | duplicate | rejected | dead | superseded
CREATE TABLE IF NOT EXISTS raw_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL, vendor TEXT NOT NULL,
  vendor_record_id TEXT NOT NULL, revision INTEGER NOT NULL, is_latest INTEGER NOT NULL DEFAULT 1,
  payload_hash TEXT NOT NULL, payload TEXT NOT NULL, envelope TEXT NOT NULL, machine_hint TEXT,
  status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
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


def connect(path: str | None = None) -> sqlite3.Connection:
    p = path or get_settings().db_path
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection):
    """Write transaction (BEGIN IMMEDIATE avoids upgrade deadlocks between api and worker)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    load_reference(conn)


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_reference(conn: sqlite3.Connection) -> None:
    """Upsert asset/line reference data from CSV (idempotent; runs at startup)."""
    ref = Path(get_settings().reference_dir)
    with tx(conn):
        for r in _read_csv(ref / "line_reference.csv"):
            conn.execute(
                "INSERT INTO lines VALUES (:plant_id,:line_id,:line_name,:operating_window) "
                "ON CONFLICT(plant_id,line_id) DO UPDATE SET line_name=excluded.line_name, "
                "operating_window=excluded.operating_window", r)
        for r in _read_csv(ref / "asset_reference.csv"):
            for k in ("rated_max_temp_c", "rated_max_vibration_mm_s", "baseline_power_kw"):
                r[k] = float(r[k]) if r[k] not in ("", None) else None
            conn.execute(
                "INSERT INTO assets VALUES (:machine_id,:plant_id,:line_id,:machine_type,:criticality,"
                ":installed_date,:rated_max_temp_c,:rated_max_vibration_mm_s,:baseline_power_kw,:asset_status) "
                "ON CONFLICT(machine_id) DO UPDATE SET plant_id=excluded.plant_id, line_id=excluded.line_id, "
                "machine_type=excluded.machine_type, criticality=excluded.criticality, "
                "rated_max_temp_c=excluded.rated_max_temp_c, "
                "rated_max_vibration_mm_s=excluded.rated_max_vibration_mm_s, "
                "baseline_power_kw=excluded.baseline_power_kw, asset_status=excluded.asset_status", r)


def load_assets(conn: sqlite3.Connection) -> dict[str, dict]:
    return {r["machine_id"]: dict(r) for r in conn.execute("SELECT * FROM assets")}


def dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
