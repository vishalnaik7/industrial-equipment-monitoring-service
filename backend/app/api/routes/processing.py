"""Processing status: batches, dead letters (inspect + replay), record traceability."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.deps import Limit, RecordIdPath, get_conn
from app.core.errors import ApiError
from app.core.security import require_api_key
from app.domain.vendors import VENDORS
from app.schemas.responses import (ERRORS, BatchList, BatchStatus, DeadLetterList, RecordTrace, ReplayAccepted,
                                   VendorSlug)
from app.services import ingestion, processing

router = APIRouter(prefix="/v1", tags=["processing"], dependencies=[Depends(require_api_key)], responses=ERRORS)


@router.get("/batches", response_model=BatchList, summary="Recent ingestion batches with their processing state")
def list_batches(limit: Limit = 50, vendor: VendorSlug | None = None, conn=Depends(get_conn)):
    items = ingestion.list_batches(conn, limit, VENDORS[vendor.value].name if vendor else None)
    return {"count": len(items), "items": items}


@router.get("/batches/{batch_id}", response_model=BatchStatus, summary="Processing status of one batch")
def get_batch(batch_id: UUID, conn=Depends(get_conn)):
    r = ingestion.batch_status(conn, str(batch_id))
    if not r:
        raise ApiError(404, "BATCH_NOT_FOUND", f"batch {batch_id} not found")
    return r


@router.get("/batches/{batch_id}/raw", response_class=Response, summary="The batch request body exactly as received",
            responses={200: {"content": {"application/json": {}}, "description": "Byte-for-byte original body"}})
def get_batch_raw(batch_id: UUID, conn=Depends(get_conn)):
    raw = ingestion.raw_body(conn, str(batch_id))
    if raw is None:
        raise ApiError(404, "RAW_BODY_NOT_FOUND", f"no raw body stored for batch {batch_id}")
    return Response(content=raw, media_type="application/json")


@router.get("/dead-letters", response_model=DeadLetterList,
            summary="Rejected (bad data) and dead (retries exhausted) records")
def dead_letters(limit: Limit = 100, vendor: VendorSlug | None = None,
                 error_code: str | None = Query(None, pattern=r"^[A-Z_]{2,64}$", examples=["UNKNOWN_MACHINE"]),
                 conn=Depends(get_conn)):
    items = processing.list_dead_letters(conn, limit, vendor.value if vendor else None, error_code)
    return {"count": len(items), "items": items}


@router.post("/dead-letters/{raw_id}/replay", status_code=202, response_model=ReplayAccepted,
             summary="Re-queue a rejected/dead record (e.g. after fixing reference data)")
def replay_dead_letter(raw_id: int = Path(ge=1), conn=Depends(get_conn)):
    return processing.replay_dead_letter(conn, raw_id)


@router.get("/records/{vendor}/{record_id}", response_model=RecordTrace,
            summary="Traceability: every raw revision + resulting canonical event")
def trace_record(vendor: VendorSlug, record_id: RecordIdPath, conn=Depends(get_conn)):
    return processing.trace_record(conn, vendor.value, record_id)
