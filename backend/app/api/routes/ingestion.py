"""Vendor ingestion endpoints. One typed endpoint pair per vendor, so OpenAPI documents each envelope exactly.

Note: no `from __future__ import annotations` here - FastAPI must see the real Pydantic body type that each
factory-built handler closes over.
"""
import json

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import get_conn
from app.core.config import get_settings
from app.core.security import require_api_key
from app.domain.vendors import VENDORS
from app.schemas.responses import ERRORS, BatchAccepted, ValidationReport
from app.schemas.vendors import BATCH_MODELS
from app.services import ingestion

router = APIRouter(prefix="/v1/ingest", tags=["ingestion"], dependencies=[Depends(require_api_key)],
                   responses=ERRORS)


async def _received(request: Request) -> tuple[dict, bytes]:
    # Work from what the vendor actually sent (traceability), not the Pydantic-coerced model.
    raw = await request.body()
    return json.loads(raw), raw


def _register(slug: str) -> None:
    spec, model = VENDORS[slug], BATCH_MODELS[slug]

    async def ingest(request: Request, batch: model = Body(...), conn=Depends(get_conn)):  # type: ignore[valid-type]
        body, raw = await _received(request)
        result = ingestion.ingest_batch(conn, slug, body, get_settings(), raw_body=raw)
        result["status_url"] = f"/v1/batches/{result['batch_id']}"
        return JSONResponse(status_code=200 if result["replayed"] else 202, content=result)

    async def validate(request: Request, batch: model = Body(...), conn=Depends(get_conn)):  # type: ignore[valid-type]
        body, _ = await _received(request)
        return ingestion.validate_batch(conn, slug, body, get_settings())

    router.add_api_route(
        f"/{slug}", ingest, methods=["POST"], status_code=202, response_model=BatchAccepted,
        name=f"ingest_{slug}", summary=f"Ingest a {spec.name} batch",
        description=f"Accepts a {spec.name} envelope with a non-empty `{spec.records_key}` list. Returns **202** "
                    "with a `batch_id` immediately; normalization and state derivation run asynchronously - poll "
                    "`status_url`. Re-sending an identical body returns **200** with the original batch "
                    "(`replayed: true`). Record *values* are validated asynchronously (warn vs reject).",
        responses={200: {"model": BatchAccepted, "description": "Identical request already received"}})
    router.add_api_route(
        f"/{slug}/validate", validate, methods=["POST"], response_model=ValidationReport,
        name=f"validate_{slug}", summary=f"Dry-run validate a {spec.name} batch (nothing stored)",
        description="Runs envelope validation and full per-record normalization synchronously and reports, for "
                    "every record, whether it would be valid / valid with warnings / rejected and whether it "
                    "would be stored as new, duplicate or a revision.")


for _slug in VENDORS:
    _register(_slug)
