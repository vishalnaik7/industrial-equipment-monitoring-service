"""FastAPI service. Thin HTTP layer over ingest / views / worker."""
from __future__ import annotations

import json
import logging
import secrets
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import ingest, timeutil, views
from .config import get_settings
from .db import connect, init_db, tx
from .worker import worker_loop

log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = connect()
    init_db(conn)
    conn.close()
    stop, thread = threading.Event(), None
    if get_settings().worker_embedded:
        thread = threading.Thread(target=worker_loop, args=(stop,), daemon=True, name="embedded-worker")
        thread.start()
    yield
    stop.set()
    if thread:
        thread.join(timeout=5)


app = FastAPI(title="Aurik Equipment Monitoring API", version="1.0.0", lifespan=lifespan,
              description="Vendor ingestion, normalization and machine operational attention view.")


def get_conn():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def require_key(x_api_key: str | None = Header(default=None)):
    keys = get_settings().api_keys
    if keys and not any(secrets.compare_digest(x_api_key or "", k) for k in keys):
        raise HTTPException(401, detail={"code": "UNAUTHORIZED", "message": "missing or invalid X-API-Key"})


@app.exception_handler(ingest.IngestError)
async def ingest_error_handler(_: Request, exc: ingest.IngestError):
    return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, exc: HTTPException):
    d = exc.detail if isinstance(exc.detail, dict) else {"code": "HTTP_ERROR", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": d})


# ---------------------------------------------------------------- health
@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok"}


@app.get("/ready", tags=["ops"])
def ready(conn=Depends(get_conn)):
    conn.execute("SELECT 1").fetchone()
    q = conn.execute("SELECT COUNT(*) n FROM raw_records WHERE status IN ('pending','processing')").fetchone()["n"]
    dead = conn.execute("SELECT COUNT(*) n FROM raw_records WHERE status='dead'").fetchone()["n"]
    return {"status": "ready", "queue_depth": q, "dead_letters": dead}


# ---------------------------------------------------------------- ingestion
@app.post("/v1/ingest/{vendor}", status_code=202, tags=["ingestion"], dependencies=[Depends(require_key)])
async def ingest_vendor(vendor: str, request: Request, conn=Depends(get_conn)):
    """Accept a vendor batch (pulseforge | thermexwatch | maintaflow). Returns 202 + batch_id immediately;
    normalization and state derivation happen asynchronously. Replaying an identical body returns 200."""
    s = get_settings()
    raw = await request.body()
    if len(raw) > s.max_body_bytes:
        raise ingest.IngestError(413, "BODY_TOO_LARGE", f"body exceeds {s.max_body_bytes} bytes")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ingest.IngestError(400, "INVALID_JSON", "request body is not valid JSON")
    result = ingest.ingest_batch(conn, vendor.lower(), body, s)
    result["status_url"] = f"/v1/batches/{result['batch_id']}"
    return JSONResponse(status_code=200 if result["replayed"] else 202, content=result)


# ---------------------------------------------------------------- processing status
@app.get("/v1/batches/{batch_id}", tags=["processing"], dependencies=[Depends(require_key)])
def get_batch(batch_id: str, conn=Depends(get_conn)):
    r = ingest.batch_status(conn, batch_id)
    if not r:
        raise HTTPException(404, detail={"code": "BATCH_NOT_FOUND", "message": batch_id})
    return r


@app.get("/v1/dead-letters", tags=["processing"], dependencies=[Depends(require_key)])
def dead_letters(limit: int = Query(100, ge=1, le=1000), conn=Depends(get_conn)):
    """Records that were rejected (permanent: bad data) or dead (transient failures exhausted)."""
    rows = conn.execute(
        "SELECT id AS raw_id, batch_id, vendor, vendor_record_id, revision, status, error_code, error_message, "
        "attempts, received_at, processed_at, payload FROM raw_records WHERE is_latest=1 AND status IN "
        "('rejected','dead') ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"count": len(rows), "items": [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]}


@app.post("/v1/dead-letters/{raw_id}/replay", status_code=202, tags=["processing"],
          dependencies=[Depends(require_key)])
def replay_dead_letter(raw_id: int, conn=Depends(get_conn)):
    """Re-queue a rejected/dead record (e.g. after the asset reference or mapping was fixed)."""
    with tx(conn):
        cur = conn.execute("UPDATE raw_records SET status='pending', attempts=0, error_code=NULL, error_message=NULL, "
                           "next_attempt_at=NULL, locked_until=NULL WHERE id=? AND is_latest=1 AND "
                           "status IN ('rejected','dead')", (raw_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, detail={"code": "NOT_REPLAYABLE", "message": f"no replayable record {raw_id}"})
    return {"raw_id": raw_id, "status": "pending"}


@app.get("/v1/records/{vendor}/{record_id}", tags=["processing"], dependencies=[Depends(require_key)])
def trace_record(vendor: str, record_id: str, conn=Depends(get_conn)):
    """Traceability: raw payload revisions + resulting canonical event for one vendor record."""
    v = ingest.VENDORS.get(vendor.lower())
    if not v:
        raise HTTPException(404, detail={"code": "UNKNOWN_VENDOR", "message": vendor})
    raws = [dict(r) for r in conn.execute(
        "SELECT id AS raw_id, batch_id, revision, is_latest, status, error_code, error_message, attempts, "
        "received_at, processed_at, payload FROM raw_records WHERE vendor=? AND vendor_record_id=? "
        "ORDER BY revision", (v[0], record_id))]
    if not raws:
        raise HTTPException(404, detail={"code": "RECORD_NOT_FOUND", "message": record_id})
    for r in raws:
        r["payload"] = json.loads(r["payload"])
    ev = conn.execute("SELECT * FROM normalized_events WHERE vendor=? AND source_record_id=?",
                      (v[0], record_id)).fetchone()
    ev = dict(ev) if ev else None
    if ev:
        ev["warnings"] = json.loads(ev["warnings"])
    return {"vendor": v[0], "record_id": record_id, "raw_revisions": raws, "normalized_event": ev}


# ---------------------------------------------------------------- machine view + summary
@app.get("/v1/machines", tags=["operational-view"], dependencies=[Depends(require_key)])
def machines(plant_id: str | None = None, line_id: str | None = None, status: str | None = None,
             needs_attention: bool | None = None, conn=Depends(get_conn)):
    items = views.list_machines(conn, get_settings(), timeutil.utcnow(), plant_id, line_id, status, needs_attention)
    return {"count": len(items), "items": items}


@app.get("/v1/machines/{machine_id}", tags=["operational-view"], dependencies=[Depends(require_key)])
def machine(machine_id: str, conn=Depends(get_conn)):
    m = views.get_machine(conn, get_settings(), timeutil.utcnow(), machine_id.upper())
    if not m:
        raise HTTPException(404, detail={"code": "MACHINE_NOT_FOUND", "message": machine_id})
    return m


@app.get("/v1/summary", tags=["operational-view"], dependencies=[Depends(require_key)])
def summary_all(conn=Depends(get_conn)):
    return views.summarize(conn, get_settings(), timeutil.utcnow())


@app.get("/v1/summary/plants/{plant_id}", tags=["operational-view"], dependencies=[Depends(require_key)])
def summary_plant(plant_id: str, conn=Depends(get_conn)):
    plant_id = plant_id.upper()
    if not conn.execute("SELECT 1 FROM assets WHERE plant_id=?", (plant_id,)).fetchone():
        raise HTTPException(404, detail={"code": "PLANT_NOT_FOUND", "message": plant_id})
    return views.summarize(conn, get_settings(), timeutil.utcnow(), plant_id)
