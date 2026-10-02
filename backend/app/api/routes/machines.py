"""Machine operational attention view and plant/line summaries."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import LineFilter, MachineIdPath, PlantFilter, PlantIdPath, get_conn
from app.core import timeutil
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.security import require_api_key
from app.domain.normalization import canon_machine_id
from app.schemas.responses import ERRORS, DerivedStatus, MachineList, MachineView, ReferenceData, Summary
from app.services import processing, views

router = APIRouter(prefix="/v1", tags=["operational-view"], dependencies=[Depends(require_api_key)],
                   responses=ERRORS)


@router.get("/machines", response_model=MachineList, summary="Machine attention view (most urgent first)")
def list_machines(plant_id: PlantFilter = None, line_id: LineFilter = None, status: DerivedStatus | None = None,
                  needs_attention: bool | None = None, conn=Depends(get_conn)):
    items = views.list_machines(conn, get_settings(), timeutil.utcnow(), plant_id and plant_id.upper(),
                                line_id and line_id.upper(), status.value if status else None, needs_attention)
    return {"count": len(items), "items": items}


@router.get("/machines/{machine_id}", response_model=MachineView,
            summary="One machine: status, attention, reasons, signals, source refs, freshness")
def get_machine(machine_id: MachineIdPath, conn=Depends(get_conn)):
    mid = canon_machine_id(machine_id)
    m = views.get_machine(conn, get_settings(), timeutil.utcnow(), mid)
    if not m:
        raise ApiError(404, "MACHINE_NOT_FOUND", f"machine '{mid}' is not in the asset reference")
    return m


@router.get("/summary", response_model=Summary, summary="All plants: status counts, ranked lines, data quality")
def summary_all(conn=Depends(get_conn)):
    return views.summarize(conn, get_settings(), timeutil.utcnow())


@router.get("/summary/plants/{plant_id}", response_model=Summary, summary="One plant: same shape as /v1/summary")
def summary_plant(plant_id: PlantIdPath, conn=Depends(get_conn)):
    plant_id = plant_id.upper()
    if not conn.execute("SELECT 1 FROM assets WHERE plant_id=?", (plant_id,)).fetchone():
        raise ApiError(404, "PLANT_NOT_FOUND", f"plant '{plant_id}' is not in the asset reference")
    return views.summarize(conn, get_settings(), timeutil.utcnow(), plant_id)


@router.get("/reference", response_model=ReferenceData, tags=["reference"],
            summary="Plants -> lines -> machine ids (from the asset/line reference)")
def reference(conn=Depends(get_conn)):
    return processing.reference(conn)
