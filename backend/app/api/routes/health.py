"""Liveness, readiness and service metadata. Open (no API key) so probes and the dashboard can bootstrap."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_conn
from app.core.config import APP_VERSION, get_settings
from app.domain.rules import RULES_VERSION
from app.domain.vendors import SCHEMA_VERSION, VENDORS
from app.schemas.responses import Meta
from app.services import processing

router = APIRouter(tags=["ops"])


@router.get("/health", summary="Liveness probe")
def health():
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe with queue depth and dead-letter count")
def ready(conn=Depends(get_conn)):
    conn.execute("SELECT 1").fetchone()
    return {"status": "ready", **processing.queue_stats(conn)}


@router.get("/v1/meta", response_model=Meta, summary="Service metadata (versions, auth mode, limits)")
def meta():
    s = get_settings()
    return Meta(service="aurik-equipment-monitoring", version=APP_VERSION, rules_version=RULES_VERSION,
                schema_version=SCHEMA_VERSION, auth_required=bool(s.api_keys), vendors=list(VENDORS),
                stale_after_minutes=s.stale_after_minutes, max_records_per_batch=s.max_records_per_batch)
