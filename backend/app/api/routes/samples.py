"""Bundled sample payloads (the assessment's edge-case batches) so the dashboard can demo ingestion.
Read-only, files from a fixed directory, names restricted to a safe pattern."""
from __future__ import annotations

import json
from pathlib import Path as FsPath

from fastapi import APIRouter, Depends, Path

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.security import require_api_key
from app.domain.vendors import VENDORS

router = APIRouter(prefix="/v1/samples", tags=["demo"], dependencies=[Depends(require_api_key)])


def _vendors_in(doc: dict) -> list[str]:
    multi = [slug for slug in VENDORS if isinstance(doc.get(slug), dict)]
    if multi:
        return multi
    return [slug for slug, v in VENDORS.items() if isinstance(doc.get(v.records_key), list)]


@router.get("", summary="List bundled sample payload files and the vendors each contains")
def list_samples():
    out = []
    for f in sorted(FsPath(get_settings().samples_dir).glob("*.json")):
        try:
            out.append({"name": f.stem, "vendors": _vendors_in(json.loads(f.read_text(encoding="utf-8")))})
        except (OSError, ValueError):
            continue
    return {"items": out}


@router.get("/{name}", summary="Get one sample, split into one request body per vendor")
def get_sample(name: str = Path(pattern=r"^[a-z0-9_]{1,64}$")):
    f = FsPath(get_settings().samples_dir) / f"{name}.json"
    if not f.is_file():
        raise ApiError(404, "SAMPLE_NOT_FOUND", f"no sample named '{name}'")
    doc = json.loads(f.read_text(encoding="utf-8"))
    vendors = _vendors_in(doc)
    bodies = {slug: doc[slug] for slug in vendors} if any(isinstance(doc.get(s), dict) for s in VENDORS) \
        else {vendors[0]: doc} if vendors else {}
    return {"name": name, "bodies": bodies}
