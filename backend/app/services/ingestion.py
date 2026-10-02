"""Vendor ingestion: store every record raw (append-only), dedupe, return fast (202). Envelope shape is validated
by the Pydantic models in `app.schemas.vendors` before this runs.

Idempotency layers
  1. Whole-request replay: same vendor + same canonical body hash => same batch returned, nothing re-inserted.
  2. Record level: same (vendor, record id) + identical payload => counted as duplicate, not stored again.
  3. Same id, different payload => new revision. Every revision is kept and processed; the worker decides which one is
     authoritative by (vendor batch version, record event time, arrival order) - see `app/worker/runner.py`. A delayed
     older revision therefore cannot replace a newer one.
  4. Same content under a different id => caught later by the worker (content fingerprint).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from app.core import timeutil
from app.core.config import Settings
from app.core.errors import ApiError
from app.db import dumps, load_assets, tx
from app.domain.normalization import NormalizationError, canon_machine_id, normalize_record
from app.domain.vendors import SCHEMA_VERSION, VENDORS, VendorSpec


def _split(spec: VendorSpec, body: dict, s: Settings) -> tuple[list, dict]:
    records = body[spec.records_key]
    if len(records) > s.max_records_per_batch:
        raise ApiError(413, "BATCH_TOO_LARGE",
                       f"{len(records)} records; max {s.max_records_per_batch} per batch - split the batch")
    return records, {k: v for k, v in body.items() if k != spec.records_key}


def _record_id(spec: VendorSpec, rec: dict) -> str | None:
    v = rec.get(spec.id_field)
    if v is None:
        return None
    return str(v).strip() or None


def _canonical(v):
    """JSON number spelling is not identity: `6.0` (Python) and `6` (JavaScript) are the same value."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, dict):
        return {k: _canonical(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_canonical(x) for x in v]
    return v


def source_version(spec: VendorSpec, envelope: dict) -> str | None:
    """The vendor's own batch version (when it generated the batch) as UTC ISO, or None if absent/unreadable."""
    if not spec.version_field:
        return None
    v = envelope.get(spec.version_field)
    try:
        if spec.version_format == "epoch_ms" and not isinstance(v, bool):
            return timeutil.to_iso(datetime.fromtimestamp(float(v) / 1000, tz=timezone.utc))
        if spec.version_format == "iso":
            return timeutil.to_iso(timeutil.parse_iso(v)[0])
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    return None


def _hash(obj) -> str:
    """Content hash for idempotency: key order, whitespace and integral-float spelling don't matter."""
    return hashlib.sha256(dumps(_canonical(obj)).encode()).hexdigest()


def ingest_batch(conn, slug: str, body: dict, s: Settings, now=None, raw_body: bytes | None = None) -> dict:
    now = now or timeutil.utcnow()
    spec = VENDORS[slug]
    records, envelope = _split(spec, body, s)
    req_hash = _hash(body)

    existing = conn.execute("SELECT * FROM ingestion_batches WHERE vendor=? AND request_hash=?",
                            (spec.name, req_hash)).fetchone()
    if existing:
        return {**_batch_summary(existing), "replayed": True}

    batch_id = str(uuid.uuid4())
    accepted = dup = revised = 0
    env_json = dumps(envelope)
    version = source_version(spec, envelope)
    with tx(conn):
        for i, rec in enumerate(records):
            rid = _record_id(spec, rec) or f"missing-id:{batch_id}:{i}"   # keeps the row traceable; worker rejects
            phash = _hash(rec)
            last = conn.execute("SELECT revision, payload_hash FROM raw_records WHERE vendor=? AND "
                                "vendor_record_id=? ORDER BY revision DESC LIMIT 1", (spec.name, rid)).fetchone()
            if last and last["payload_hash"] == phash:
                dup += 1
                continue
            revision = 1
            if last:
                revision = last["revision"] + 1
                revised += 1
                # Older revisions stay queued: the worker orders all revisions, arrival order alone never decides.
                conn.execute("UPDATE raw_records SET is_latest=0 WHERE vendor=? AND vendor_record_id=?",
                             (spec.name, rid))
            conn.execute(
                "INSERT INTO raw_records (batch_id,vendor,vendor_record_id,revision,payload_hash,payload,envelope,"
                "machine_hint,source_version,received_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (batch_id, spec.name, rid, revision, phash, dumps(rec), env_json,
                 canon_machine_id(rec.get(spec.machine_field)), version, timeutil.to_iso(now)))
            accepted += 1
        conn.execute("INSERT INTO ingestion_batches (batch_id,vendor,schema_version,request_hash,received_at,"
                     "total_records,accepted_records,duplicate_records,revised_records,raw_body) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (batch_id, spec.name, SCHEMA_VERSION, req_hash, timeutil.to_iso(now), len(records), accepted,
                      dup, revised, raw_body))
    row = conn.execute("SELECT * FROM ingestion_batches WHERE batch_id=?", (batch_id,)).fetchone()
    return {**_batch_summary(row), "replayed": False}


def validate_batch(conn, slug: str, body: dict, s: Settings) -> dict:
    """Dry run: what WOULD happen to each record (normalization outcome + dedupe action). Stores nothing."""
    spec = VENDORS[slug]
    records, envelope = _split(spec, body, s)
    assets = load_assets(conn)
    seen: dict[str, str] = {}
    out = []
    for i, rec in enumerate(records):
        rid, phash = _record_id(spec, rec), _hash(rec)
        if rid and rid in seen:
            action = "duplicate_in_batch" if seen[rid] == phash else "revision_in_batch"
        else:
            last = rid and conn.execute(
                "SELECT payload_hash FROM raw_records WHERE vendor=? AND vendor_record_id=? "
                "ORDER BY revision DESC LIMIT 1", (spec.name, rid)).fetchone()
            action = "new" if not last else "duplicate" if last["payload_hash"] == phash else "revision"
        if rid:
            seen[rid] = phash
        item = {"index": i, "record_id": rid, "store_action": action}
        try:
            ev = normalize_record(spec.name, rec, envelope, assets, s, received_at=timeutil.to_iso(timeutil.utcnow()))
            item.update(outcome="valid_with_warnings" if ev["warnings"] else "valid", machine_id=ev["machine_id"],
                        event_code=ev["event_code"], event_time=ev["event_time"], warnings=ev["warnings"])
        except NormalizationError as e:
            item.update(outcome="rejected", error_code=e.code, error_message=e.message)
        out.append(item)
    count = lambda o: sum(r["outcome"] == o for r in out)
    return {"vendor": spec.name, "total_records": len(out), "valid": count("valid"),
            "valid_with_warnings": count("valid_with_warnings"), "rejected": count("rejected"), "records": out}


def _batch_summary(b) -> dict:
    return {"batch_id": b["batch_id"], "vendor": b["vendor"], "schema_version": b["schema_version"],
            "received_at": b["received_at"], "total_records": b["total_records"],
            "accepted_records": b["accepted_records"], "duplicate_records": b["duplicate_records"],
            "revised_records": b["revised_records"]}


def _state(counts: dict[str, int]) -> str:
    open_n = counts.get("pending", 0) + counts.get("processing", 0)
    bad_n = counts.get("rejected", 0) + counts.get("dead", 0)
    if open_n:
        return "processing"
    if bad_n and bad_n == sum(counts.values()):
        return "failed"
    return "completed_with_errors" if bad_n else "completed"


def batch_status(conn, batch_id: str) -> dict | None:
    b = conn.execute("SELECT * FROM ingestion_batches WHERE batch_id=?", (batch_id,)).fetchone()
    if not b:
        return None
    counts = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) n FROM raw_records WHERE batch_id=? GROUP BY status", (batch_id,))}
    errors = [dict(r) for r in conn.execute(
        "SELECT id AS raw_id, vendor_record_id, status, error_code, error_message, attempts FROM raw_records "
        "WHERE batch_id=? AND status IN ('rejected','dead','duplicate','superseded') ORDER BY id", (batch_id,))]
    return {**_batch_summary(b), "state": _state(counts), "record_status_counts": counts, "errors": errors}


def raw_body(conn, batch_id: str) -> bytes | None:
    """The request body exactly as the vendor sent it (None if the batch is unknown or predates raw capture)."""
    r = conn.execute("SELECT raw_body FROM ingestion_batches WHERE batch_id=?", (batch_id,)).fetchone()
    return bytes(r["raw_body"]) if r and r["raw_body"] is not None else None


def list_batches(conn, limit: int, vendor: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM ingestion_batches", []
    if vendor:
        q, args = q + " WHERE vendor=?", [vendor]
    batches = conn.execute(q + " ORDER BY received_at DESC, rowid DESC LIMIT ?", (*args, limit)).fetchall()
    counts: dict[str, dict] = {}
    if batches:
        marks = ",".join("?" * len(batches))
        for r in conn.execute(f"SELECT batch_id, status, COUNT(*) n FROM raw_records WHERE batch_id IN ({marks}) "
                              "GROUP BY batch_id, status", [b["batch_id"] for b in batches]):
            counts.setdefault(r["batch_id"], {})[r["status"]] = r["n"]
    return [{**_batch_summary(b), "state": _state(counts.get(b["batch_id"], {}))} for b in batches]
