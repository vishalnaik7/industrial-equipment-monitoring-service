"""Vendor ingestion: validate envelope, store every record raw (append-only), dedupe, return fast (202).

Idempotency layers
  1. Whole-request replay: same vendor + same canonical body hash => same batch returned, nothing re-inserted.
  2. Record level: same (vendor, record id) + identical payload => counted as duplicate, not stored again.
  3. Same id, different payload => new revision; the latest received revision wins (older ones 'superseded').
  4. Same content under a different id => caught later by the worker (content fingerprint).
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from . import timeutil
from .config import Settings
from .db import dumps, tx
from .normalize import canon_machine_id

# slug -> (canonical vendor, records key, id field, machine field)
VENDORS = {
    "pulseforge": ("PulseForge", "events", "event_id", "machine_id"),
    "thermexwatch": ("ThermexWatch", "readings", "readingId", "assetCode"),
    "maintaflow": ("MaintaFlow", "records", "record_id", "machine_ref"),
}
SCHEMA_VERSION = "v1"


class IngestError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def ingest_batch(conn, slug: str, body: Any, s: Settings, now=None) -> dict:
    now = now or timeutil.utcnow()
    if slug not in VENDORS:
        raise IngestError(404, "UNKNOWN_VENDOR", f"unknown vendor '{slug}'; expected one of {sorted(VENDORS)}")
    vendor, list_key, id_field, machine_field = VENDORS[slug]
    if not isinstance(body, dict):
        raise IngestError(422, "INVALID_ENVELOPE", "request body must be a JSON object")
    records = body.get(list_key)
    if not isinstance(records, list):
        raise IngestError(422, "INVALID_ENVELOPE", f"'{list_key}' must be a list")
    if not records:
        raise IngestError(422, "EMPTY_BATCH", f"'{list_key}' is empty")
    if len(records) > s.max_records_per_batch:
        raise IngestError(413, "BATCH_TOO_LARGE", f"max {s.max_records_per_batch} records per batch")
    envelope = {k: v for k, v in body.items() if k != list_key}
    req_hash = hashlib.sha256(dumps(body).encode()).hexdigest()

    existing = conn.execute("SELECT * FROM ingestion_batches WHERE vendor=? AND request_hash=?",
                            (vendor, req_hash)).fetchone()
    if existing:
        return {**_batch_summary(existing), "replayed": True}

    batch_id = str(uuid.uuid4())
    accepted = dup = revised = 0
    env_json = dumps(envelope)
    with tx(conn):
        for i, rec in enumerate(records):
            is_obj = isinstance(rec, dict)
            payload = rec if is_obj else {"_non_object_record": repr(rec)[:200]}
            rid = str(rec.get(id_field)).strip() if is_obj and rec.get(id_field) not in (None, "") else None
            rid = rid or f"missing-id:{batch_id}:{i}"                 # keeps the row traceable; worker rejects it
            phash = hashlib.sha256(dumps(payload).encode()).hexdigest()
            hint = canon_machine_id(rec.get(machine_field)) if is_obj else None
            last = conn.execute("SELECT revision, payload_hash FROM raw_records WHERE vendor=? AND "
                                "vendor_record_id=? ORDER BY revision DESC LIMIT 1", (vendor, rid)).fetchone()
            if last and last["payload_hash"] == phash:
                dup += 1
                continue
            revision = 1
            if last:
                revision = last["revision"] + 1
                revised += 1
                conn.execute("UPDATE raw_records SET is_latest=0, status=CASE WHEN status IN "
                             "('pending','processing') THEN 'superseded' ELSE status END "
                             "WHERE vendor=? AND vendor_record_id=?", (vendor, rid))
            conn.execute(
                "INSERT INTO raw_records (batch_id,vendor,vendor_record_id,revision,payload_hash,payload,envelope,"
                "machine_hint,received_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (batch_id, vendor, rid, revision, phash, dumps(payload), env_json, hint, timeutil.to_iso(now)))
            accepted += 1
        conn.execute("INSERT INTO ingestion_batches VALUES (?,?,?,?,?,?,?,?,?)",
                     (batch_id, vendor, SCHEMA_VERSION, req_hash, timeutil.to_iso(now), len(records), accepted,
                      dup, revised))
    row = conn.execute("SELECT * FROM ingestion_batches WHERE batch_id=?", (batch_id,)).fetchone()
    return {**_batch_summary(row), "replayed": False}


def _batch_summary(b) -> dict:
    return {"batch_id": b["batch_id"], "vendor": b["vendor"], "schema_version": b["schema_version"],
            "received_at": b["received_at"], "total_records": b["total_records"],
            "accepted_records": b["accepted_records"], "duplicate_records": b["duplicate_records"],
            "revised_records": b["revised_records"]}


def batch_status(conn, batch_id: str) -> dict | None:
    b = conn.execute("SELECT * FROM ingestion_batches WHERE batch_id=?", (batch_id,)).fetchone()
    if not b:
        return None
    counts = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) n FROM raw_records WHERE batch_id=? GROUP BY status", (batch_id,))}
    errors = [dict(r) for r in conn.execute(
        "SELECT id AS raw_id, vendor_record_id, status, error_code, error_message, attempts FROM raw_records "
        "WHERE batch_id=? AND status IN ('rejected','dead','duplicate') ORDER BY id", (batch_id,))]
    open_n = counts.get("pending", 0) + counts.get("processing", 0)
    bad_n = counts.get("rejected", 0) + counts.get("dead", 0)
    if open_n:
        state = "processing"
    elif bad_n and bad_n == sum(counts.values()):
        state = "failed"
    elif bad_n:
        state = "completed_with_errors"
    else:
        state = "completed"
    return {**_batch_summary(b), "state": state, "record_status_counts": counts, "errors": errors}
