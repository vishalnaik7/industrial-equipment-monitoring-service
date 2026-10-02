"""Processing-side operations: dead-letter inspection and replay, record traceability, reference data."""
from __future__ import annotations

import json

from app.core.errors import ApiError
from app.db import tx
from app.domain.vendors import VENDORS


def list_dead_letters(conn, limit: int, vendor: str | None = None, error_code: str | None = None) -> list[dict]:
    q = ("SELECT id AS raw_id, batch_id, vendor, vendor_record_id, revision, status, error_code, error_message, "
         "attempts, received_at, processed_at, payload FROM raw_records WHERE is_latest=1 AND status IN "
         "('rejected','dead')")
    args: list = []
    if vendor:
        q, args = q + " AND vendor=?", args + [VENDORS[vendor].name]
    if error_code:
        q, args = q + " AND error_code=?", args + [error_code]
    rows = conn.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]


def replay_dead_letter(conn, raw_id: int) -> dict:
    """Re-queue a rejected/dead record (e.g. after the asset reference or a mapping was fixed)."""
    with tx(conn):
        cur = conn.execute("UPDATE raw_records SET status='pending', attempts=0, error_code=NULL, error_message=NULL, "
                           "next_attempt_at=NULL, locked_until=NULL, lease_token=NULL WHERE id=? AND is_latest=1 AND "
                           "status IN ('rejected','dead')", (raw_id,))
    if cur.rowcount == 0:
        raise ApiError(404, "NOT_REPLAYABLE", f"no replayable (latest, rejected or dead) record {raw_id}")
    return {"raw_id": raw_id, "status": "pending"}


def trace_record(conn, vendor: str, record_id: str) -> dict:
    """Raw payload revisions + resulting canonical event for one vendor record."""
    name = VENDORS[vendor].name
    raws = [dict(r) for r in conn.execute(
        "SELECT id AS raw_id, batch_id, revision, is_latest, source_version, status, error_code, error_message, "
        "attempts, received_at, processed_at, payload FROM raw_records WHERE vendor=? AND vendor_record_id=? "
        "ORDER BY revision", (name, record_id))]
    if not raws:
        raise ApiError(404, "RECORD_NOT_FOUND", f"{name} record '{record_id}' was never received")
    for r in raws:
        r["payload"] = json.loads(r["payload"])
    ev = conn.execute("SELECT * FROM normalized_events WHERE vendor=? AND source_record_id=?",
                      (name, record_id)).fetchone()
    ev = dict(ev) if ev else None
    if ev:
        ev["warnings"] = json.loads(ev["warnings"])
    for r in raws:                                   # the revision the canonical event was built from
        r["authoritative"] = bool(ev and ev["raw_record_id"] == r["raw_id"])
    return {"vendor": name, "record_id": record_id, "raw_revisions": raws, "normalized_event": ev}


def queue_stats(conn) -> dict:
    q = conn.execute("SELECT COUNT(*) n FROM raw_records WHERE status IN ('pending','processing')").fetchone()["n"]
    dead = conn.execute("SELECT COUNT(*) n FROM raw_records WHERE is_latest=1 AND status IN ('dead','rejected')"
                        ).fetchone()["n"]
    return {"queue_depth": q, "dead_letters": dead}


def reference(conn) -> dict:
    machines: dict[tuple, list[str]] = {}
    for a in conn.execute("SELECT machine_id, plant_id, line_id FROM assets ORDER BY machine_id"):
        machines.setdefault((a["plant_id"], a["line_id"]), []).append(a["machine_id"])
    plants: dict[str, list] = {}
    for ln in conn.execute("SELECT * FROM lines ORDER BY plant_id, line_id"):
        plants.setdefault(ln["plant_id"], []).append({
            "line_id": ln["line_id"], "line_name": ln["line_name"], "operating_window": ln["operating_window"],
            "machine_ids": machines.get((ln["plant_id"], ln["line_id"]), [])})
    return {"plants": [{"plant_id": p, "lines": ls} for p, ls in plants.items()]}
