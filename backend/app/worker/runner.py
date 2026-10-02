"""Background processing: claim -> normalize -> upsert -> mark dirty -> recompute. Safe to run N copies.

Reliability guarantees
  * Claiming is atomic (one BEGIN IMMEDIATE transaction) and lease-based: a crashed worker's records are re-claimed
    after LEASE_SECONDS; on the last allowed attempt they go to `dead` (LEASE_EXPIRED).
  * Fencing: every claim gets a fresh lease token. A worker may only write a record's outcome while it still holds
    that token, so a worker that stalled past its lease cannot overwrite the result of the worker that took over.
  * Transient failures retry with exponential backoff up to MAX_ATTEMPTS, then `dead` (RETRIES_EXHAUSTED).
    Bad data is a permanent `rejected` - never retried automatically (replay is a deliberate operator action).
  * Recompute reads the machine's events and writes its state in one transaction, and the dirty marker is removed in
    that same transaction, so an event saved concurrently is never lost from the derived state.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import timedelta

from app.core import timeutil
from app.core.config import Settings, get_settings
from app.db import connect, dumps, load_assets, tx
from app.domain.normalization import NormalizationError, normalize_record
from app.domain.rules import derive_machine_state

log = logging.getLogger("worker")
EVENT_COLS = ("vendor", "source_record_id", "raw_record_id", "fingerprint", "machine_id", "plant_id", "line_id",
              "event_time", "kind", "event_code", "severity", "vibration_mm_s", "vibration_g", "temperature_c",
              "power_kw", "machine_state", "signal_quality", "sensor_health", "confidence", "inspection_result",
              "maintenance_status", "days_since_service", "note", "details", "warnings", "normalized_at")


def claim(conn: sqlite3.Connection, s: Settings, now) -> list[dict]:
    """Lease a batch of pending records (atomically). Returns the rows with this claim's lease token."""
    now_s, lease = timeutil.to_iso(now), timeutil.to_iso(now + timedelta(seconds=s.lease_seconds))
    claimed = []
    with tx(conn):
        conn.execute("UPDATE raw_records SET status='dead', error_code='LEASE_EXPIRED', "
                     "error_message='worker died on final attempt', locked_until=NULL, lease_token=NULL "
                     "WHERE status='processing' AND locked_until<? AND attempts>=?", (now_s, s.max_attempts))
        # Every queued revision is processed (not only the latest received): ordering is decided per record by
        # revision_key, so a late-arriving older revision is evaluated and recorded as STALE_REVISION.
        rows = conn.execute(
            "SELECT * FROM raw_records WHERE ((status='pending' AND "
            "(next_attempt_at IS NULL OR next_attempt_at<=?)) OR (status='processing' AND locked_until<?)) "
            "ORDER BY id LIMIT ?", (now_s, now_s, s.worker_batch_size)).fetchall()
        for r in rows:
            token = uuid.uuid4().hex
            conn.execute("UPDATE raw_records SET status='processing', locked_until=?, lease_token=?, "
                         "attempts=attempts+1 WHERE id=?", (lease, token, r["id"]))
            claimed.append({**dict(r), "lease_token": token})
    return claimed


def _owns(conn, rec) -> bool:
    """True while this worker still holds the record's lease (call inside the write transaction)."""
    cur = conn.execute("SELECT status, lease_token FROM raw_records WHERE id=?", (rec["id"],)).fetchone()
    owned = cur is not None and cur["status"] == "processing" and cur["lease_token"] == rec["lease_token"]
    if not owned:
        log.warning("record %s: lease lost (taken over by another worker); result discarded", rec["id"])
    return owned


def _finish(conn, rid, status, code=None, msg=None, now=None, next_at=None):
    conn.execute("UPDATE raw_records SET status=?, error_code=?, error_message=?, locked_until=NULL, lease_token=NULL, "
                 "next_attempt_at=?, processed_at=? WHERE id=?",
                 (status, code, msg, next_at, timeutil.to_iso(now) if status != "pending" else None, rid))


def revision_key(source_version: str | None, event_time: str, raw_id: int) -> tuple[str, str, int]:
    """Total order over revisions of ONE vendor record; the greatest is authoritative.

    1. vendor batch version (when the vendor generated the batch) - a revision without one sorts as oldest;
    2. the record's own event time (what MaintaFlow / CMMS provide, and the tie-break for equal versions);
    3. arrival order (raw id) - so a same-time correction sent later wins.
    Because it is a total order and the worker keeps the maximum, the result does not depend on processing order.
    """
    return (source_version or "", event_time, raw_id)


def _stale_against_authoritative(conn, rec, ev) -> str | None:
    """Explanation if this revision is older than the record's current authoritative revision, else None."""
    cur = conn.execute(
        "SELECT n.event_time, n.raw_record_id, r.source_version, r.revision FROM normalized_events n "
        "JOIN raw_records r ON r.id = n.raw_record_id WHERE n.vendor=? AND n.source_record_id=?",
        (ev["vendor"], ev["source_record_id"])).fetchone()
    if cur is None:
        return None
    mine = revision_key(rec["source_version"], ev["event_time"], rec["id"])
    theirs = revision_key(cur["source_version"], cur["event_time"], cur["raw_record_id"])
    if mine > theirs:
        return None
    if mine[0] != theirs[0]:
        why = f"vendor batch version {mine[0] or '(none)'} is older than {theirs[0]}"
    elif mine[1] != theirs[1]:
        why = f"record time {mine[1]} is older than {theirs[1]}"
    else:
        why = "same version and record time, but it was received earlier"
    return f"revision {rec['revision']} not applied: {why} of authoritative revision {cur['revision']}"


def process_record(conn, rec, assets, s: Settings, now) -> str | None:
    """Returns machine_id to recompute, or None. Raises on transient failure (caller retries)."""
    try:
        ev = normalize_record(rec["vendor"], json.loads(rec["payload"]), json.loads(rec["envelope"]), assets, s,
                              received_at=rec["received_at"])
    except NormalizationError as e:                                # permanent: dead-letter view, replayable
        with tx(conn):
            if _owns(conn, rec):
                _finish(conn, rec["id"], "rejected", e.code, e.message, now)
        return None
    ev["raw_record_id"], ev["normalized_at"] = rec["id"], timeutil.to_iso(now)
    ev["warnings"] = dumps(ev["warnings"])
    ev["details"] = dumps(ev["details"]) if ev["details"] is not None else None
    with tx(conn):
        if not _owns(conn, rec):
            return None
        stale = _stale_against_authoritative(conn, rec, ev)
        if stale:                                                  # a newer revision of this record already won
            _finish(conn, rec["id"], "superseded", "STALE_REVISION", stale, now)
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
    """Rebuild one machine's state from all its events. Read + write + marker removal are one transaction."""
    asset = assets[machine_id]
    with tx(conn):
        rows = [dict(r) for r in conn.execute("SELECT * FROM normalized_events WHERE machine_id=?", (machine_id,))]
        st = derive_machine_state(asset, rows, s)
        core = dumps({k: st[k] for k in ("derived_status", "attention_level", "reasons", "latest_signals",
                                          "latest_relevant_event_time", "rules_version")})
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
                    if not _owns(conn, rec):
                        continue
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
