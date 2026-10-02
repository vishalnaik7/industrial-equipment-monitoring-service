"""Application factory. HTTP concerns only: middleware, error handlers, routers. The dashboard lives in
../frontend and is served separately (nginx in Docker, any static server in dev)."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, ingestion, machines, processing, samples
from app.core.config import APP_VERSION, get_settings
from app.core.errors import error_body, install_error_handlers
from app.db import connect, init_db
from app.worker import worker_loop

log = logging.getLogger("api")

DESCRIPTION = """
Ingests machine data from three vendors with inconsistent schemas plus the internal CMMS work-order feed, normalizes
it into one canonical model
**asynchronously**, and serves an **explainable machine operational attention view** plus plant/line summaries.

* **Ingestion** returns `202` + `batch_id` immediately; poll `/v1/batches/{id}`.
* **Validation**: envelopes are validated synchronously (4xx); record values asynchronously (warn vs reject).
  Use `/v1/ingest/{vendor}/validate` for a synchronous dry run.
* **Errors** always have the shape `{"error": {"code", "message", "details"?, "request_id"?}}`.
* **Auth**: send `X-API-Key` when the server runs with `API_KEYS` set.
* **Dashboard**: separate static app in `frontend/` (http://localhost:8080 with Docker Compose).
"""


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


def create_app() -> FastAPI:
    app = FastAPI(title="Aurik Equipment Monitoring API", version=APP_VERSION, description=DESCRIPTION,
                  lifespan=lifespan,
                  openapi_tags=[
                      {"name": "ingestion", "description": "Accept vendor batches (async) and dry-run validation"},
                      {"name": "processing", "description": "Batch status, dead letters, replay, traceability"},
                      {"name": "operational-view", "description": "Derived machine view and plant/line summaries"},
                      {"name": "reference", "description": "Asset / line reference data"},
                      {"name": "ops", "description": "Probes and metadata"},
                      {"name": "demo", "description": "Bundled sample payloads for the dashboard"}])
    install_error_handlers(app)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        # Reject wrong media type and oversized ingest bodies before they are read/parsed.
        if request.method == "POST" and request.url.path.startswith("/v1/ingest/"):
            ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype != "application/json":
                return JSONResponse(status_code=415, headers={"X-Request-ID": rid}, content=error_body(
                    request, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json"))
            limit = get_settings().max_body_bytes
            try:
                too_big = int(request.headers.get("content-length") or 0) > limit
            except ValueError:
                too_big = False
            if too_big:
                return JSONResponse(status_code=413, headers={"X-Request-ID": rid}, content=error_body(
                    request, "BODY_TOO_LARGE", f"body exceeds {limit} bytes"))
        t0 = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        log.info("%s %s -> %s %.1fms rid=%s", request.method, request.url.path, response.status_code,
                 (time.perf_counter() - t0) * 1000, rid)
        return response

    for r in (health.router, ingestion.router, processing.router, machines.router, samples.router):
        app.include_router(r)

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/docs")

    # Outermost middleware: answers CORS pre-flights for the dashboard when it runs on another origin.
    origins = list(get_settings().cors_origins)
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                           allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
                           expose_headers=["X-Request-ID"], max_age=600)

    return app


app = create_app()
