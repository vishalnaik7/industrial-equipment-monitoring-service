"""Asset / line reference data (authoritative for plant and line), loaded from CSV at startup."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from app.core.config import get_settings

from .schema import SCHEMA
from .session import tx


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    load_reference(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive column migrations for databases created by an older version (CREATE IF NOT EXISTS won't add them)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(ingestion_batches)")}
    if "raw_body" not in cols:
        conn.execute("ALTER TABLE ingestion_batches ADD COLUMN raw_body BLOB")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(raw_records)")}
    if "source_version" not in cols:
        conn.execute("ALTER TABLE raw_records ADD COLUMN source_version TEXT")
    if "lease_token" not in cols:
        conn.execute("ALTER TABLE raw_records ADD COLUMN lease_token TEXT")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(normalized_events)")}
    if "details" not in cols:
        conn.execute("ALTER TABLE normalized_events ADD COLUMN details TEXT")


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
