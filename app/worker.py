"""Background processing: claim -> normalize -> upsert -> mark dirty -> recompute. Safe to run N copies."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import timedelta

from . import timeutil
from .config import Settings, get_settings
from .db import connect, dumps, init_db, load_assets, tx
from .derive import derive_machine_state
from .normalize import NormalizationError, normalize_record

log = logging.getLogger("worker")
EVENT_COLS = ("vendor", "source_record_id", "raw_record_id", "fingerprint", "machine_id", "plant_id", "line_id",
              "event_time", "kind", "event_code", "severity", "vibration_mm_s", "vibration_g", "temperature_c",
              "power_kw", "machine_state", "signal_quality", "sensor_health", "confidence", "inspection_result",
              "maintenance_status", "days_since_service", "note", "warnings", "normalized_at")


def claim(conn: sqlite3.Connection, s: Settings, now) -> list[sqlite3.Row]:
    """Lease a batch of pending records. A crashed worker's lease expires and the record is retried."""
    now_s, lease = timeutil.to_iso(now), timeutil.to_iso(now + timedelta(seconds=s.lease_seconds))
    with tx(conn):
        conn.execute("UPDATE raw_records SET status='dead', error_code='LEASE_EXPIRED', "
                     "error_message='worker died on final attempt', locked_until=NULL "
                     "WHERE status='processing' AND locked_until<? AND attempts>=?", (now_s, s.max_attempts))
        rows = conn.execute(
            "SELECT * FROM raw_records WHERE is_latest=1 AND ((status='pending' AND "
            "(next_attempt_at IS NULL OR next_attempt_at<=?)) OR (status='processing' AND locked_until<?)) "
            "ORDER BY id LIMIT ?", (now_s, now_s, s.worker_batch_size)).fetchall()
        for r in rows:
            conn.execute("UPDATE raw_records SET status='processing', locked_until=?, attempts=attempts+1 "
                         "WHERE id=?", (lease, r["id"]))
    return rows


def _finish(conn, rid, status, code=None, msg=None, now=None, next_at=None):
    conn.execute("UPDATE raw_records SET status=?, error_code=?, error_message=?, locked_until=NULL, "
                 "next_attempt_at=?, processed_at=? WHERE id=?",
                 (status, code, msg, next_at, timeutil.to_iso(now) if status != "pending" else None, rid))


def process_record(conn, rec, assets, s: Settings, now) -> str | None:
    """Returns machine_id to recompute, or None. Raises on transient failure (caller retries)."""
    try:
        ev = normalize_record(rec["vendor"], json.loads(rec["payload"]), json.loads(rec["envelope"]), assets, s)
    except NormalizationError as e:                                # permanent: dead-letter view, replayable
        with tx(conn):
            _finish(conn, rec["id"], "rejected", e.code, e.message, now)
        return None
    ev["raw_record_id"], ev["normalized_at"] = rec["id"], timeutil.to_iso(now)
    ev["warnings"] = dumps(ev["warnings"])
    with tx(conn):
        cur = conn.execute("SELECT is_latest FROM raw_records WHERE id=?", (rec["id"],)).fetchone()
        if not cur["is_latest"]:                                   # a newer revision arrived meanwhile
            _finish(conn, rec["id"], "superseded", now=now)
            return None
        twin = conn.execute("SELECT source_record_id FROM normalized_events WHERE fingerprint=? AND vendor=? "
                            "AND source_record_id<>?", (ev["fingerprint"], ev["vendor"], ev["source_record_id"])
                            ).fetchone()
        if twin:                                                   # same content re-sent under a new id
            _finish(conn, rec["id"], "duplicate", "SEMANTIC_DUPLICATE",
                    f"same content as {twin['source_record_id']}", now)
            return None
        cols = ",".join(EVENT_COLS)
        conn.execute(f"INSERT OR REPLACE INTO normalized_events ({cols}) VALUES ({','.join('?' * len(EVENT_COLS))})",
                     [ev[c] for c in EVENT_COLS])
        conn.execute("INSERT OR IGNORE INTO dirty_machines (machine_id, marked_at) VALUES (?,?)",
                     (ev["machine_id"], timeutil.to_iso(now)))
        _finish(conn, rec["id"], "processed", now=now)
    return ev["machine_id"]


def recompute_machine(conn, machine_id: str, assets: dict, s: Settings, now) -> None:
    asset = assets[machine_id]
    rows = [dict(r) for r in conn.execute("SELECT * FROM normalized_events WHERE machine_id=?", (machine_id,))]
    st = derive_machine_state(asset, rows, s)
    core = dumps({k: st[k] for k in ("derived_status", "attention_level", "reasons", "latest_signals",
                                      "latest_relevant_event_time", "rules_version")})
    with tx(conn):
        old = conn.execute("SELECT * FROM machine_state WHERE machine_id=?", (machine_id,)).fetchone()
        old_core = dumps({"derived_status": old["derived_status"], "attention_level": old["attention_level"],
                          "reasons": json.loads(old["reasons"]), "latest_signals": json.loads(old["latest_signals"]),
                          "latest_relevant_event_time": old["latest_relevant_event_time"],
                          "rules_version": old["rules_version"]}) if old else None
        version = (old["state_version"] if old else 0) + (0 if old_core == core else 1)
        conn.execute(
            "INSERT OR REPLACE INTO machine_state (machine_id,plant_id,line_id,derived_status,attention_level,"
            "needs_attention,reason_codes,reasons,latest_signals,machine_state,latest_relevant_event_time,"
            "source_event_refs,state_version,rules_version,last_processed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (machine_id, st["plant_id"], st["line_id"], st["derived_status"], st["attention_level"],
             int(st["needs_attention"]), dumps(st["reason_codes"]), dumps(st["reasons"]), dumps(st["latest_signals"]),
             st["machine_state"], st["latest_relevant_event_time"], dumps(st["source_event_refs"]), version,
             st["rules_version"], timeutil.to_iso(now)))
        conn.execute("DELETE FROM dirty_machines WHERE machine_id=?", (machine_id,))


def run_once(conn: sqlite3.Connection | None = None, now=None) -> int:
    """One worker cycle. Returns number of records handled (used directly by tests)."""
    s, own = get_settings(), conn is None
    conn = conn or connect()
    now = now or timeutil.utcnow()
    try:
        assets = load_assets(conn)
        recs = claim(conn, s, now)
        for rec in recs:
            try:
                process_record(conn, rec, assets, s, now)
            except Exception as exc:                                # transient: backoff retry, then dead-letter
                attempts = rec["attempts"] + 1                      # rec is the pre-claim row
                log.warning("record %s failed (attempt %s): %s", rec["id"], attempts, exc)
                with tx(conn):
                    if attempts >= s.max_attempts:
                        _finish(conn, rec["id"], "dead", "RETRIES_EXHAUSTED", str(exc)[:500], now)
                    else:
                        delay = min(s.retry_base_seconds * 2 ** (attempts - 1), 300)
                        _finish(conn, rec["id"], "pending", "TRANSIENT_ERROR", str(exc)[:500], now,
                                timeutil.to_iso(now + timedelta(seconds=delay)))
        for d in conn.execute("SELECT machine_id FROM dirty_machines").fetchall():
            try:
                recompute_machine(conn, d["machine_id"], assets, s, now)
            except Exception as exc:
                log.exception("recompute failed for %s", d["machine_id"])
                with tx(conn):
                    conn.execute("UPDATE dirty_machines SET attempts=attempts+1,last_error=? WHERE machine_id=?",
                                 (str(exc)[:500], d["machine_id"]))
        return len(recs)
    finally:
        if own:
            conn.close()


def worker_loop(stop: threading.Event) -> None:
    s = get_settings()
    conn = connect()
    log.info("worker started")
    while not stop.is_set():
        try:
            if run_once(conn) == 0:
                stop.wait(s.worker_poll_seconds)
        except Exception:
            log.exception("worker cycle failed")
            stop.wait(s.worker_poll_seconds)
    conn.close()


if __name__ == "__main__":      # standalone worker process: python -m app.worker
    logging.basicConfig(level=logging.INFO)
    c = connect()
    init_db(c)
    c.close()
    worker_loop(threading.Event())
