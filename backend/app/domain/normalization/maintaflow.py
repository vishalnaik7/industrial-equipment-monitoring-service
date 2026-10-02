"""MaintaFlow: zone-less `YYYY/MM/DD HH:MM:SS` local time, record types, free-text notes, qualitative confidence."""
from __future__ import annotations

from datetime import datetime

from app.core import timeutil
from app.core.config import Settings

from .common import NormalizationError, clean, fail, make_event, resolve_asset
from .vocabulary import MF_CONFIDENCE, MF_INSPECTION, MF_MAINT_STATUS, MF_RECORD_KINDS


def normalize_maintaflow(p: dict, env: dict, assets: dict, s: Settings) -> dict:
    w: list[str] = []
    rid = clean(p.get("record_id")) or fail("MISSING_RECORD_ID", "record_id is missing")
    asset = resolve_asset(p.get("machine_ref"), assets, env.get("factory_id"), p.get("line_ref"), w)
    kind = str(p.get("record_type") or "").strip().lower()
    if kind not in MF_RECORD_KINDS:
        raise NormalizationError("UNKNOWN_RECORD_TYPE", f"record_type {p.get('record_type')!r} is not supported")
    raw_t = p.get("recorded_at")
    try:
        t = datetime.strptime(str(raw_t).strip(), "%Y/%m/%d %H:%M:%S").replace(
            tzinfo=timeutil.zone(s.maintaflow_tz))
        w.append("TIMESTAMP_ASSUMED_" + s.maintaflow_tz.upper().replace("/", "_"))
    except (ValueError, TypeError):
        try:
            t, _ = timeutil.parse_iso(raw_t)
        except ValueError:
            raise NormalizationError("INVALID_TIMESTAMP", f"recorded_at not parseable: {raw_t!r}")
    insp = clean(p.get("inspection_result"))
    insp = insp.lower() if insp else None
    if insp and insp not in MF_INSPECTION:
        w.append("UNMAPPED_INSPECTION_RESULT")
        insp = None
    ms = (clean(p.get("maintenance_status")) or "unknown").lower()
    if ms not in MF_MAINT_STATUS:
        w.append("UNMAPPED_MAINTENANCE_STATUS")
        ms = "unknown"
    days = None
    if p.get("days_since_last_service") not in (None, ""):
        try:
            days = int(float(p["days_since_last_service"]))
            if days < 0:
                raise ValueError
        except (TypeError, ValueError):
            days = None
            w.append("INVALID_DAYS_SINCE_LAST_SERVICE")
    conf = MF_CONFIDENCE.get(str(p.get("manual_confidence") or "").strip().lower())
    if conf is None and clean(p.get("manual_confidence")) is not None:
        w.append("UNMAPPED_MANUAL_CONFIDENCE")
    note = clean(p.get("technician_note"))
    return make_event(
        vendor="MaintaFlow", source_record_id=rid, machine_id=asset["machine_id"], plant_id=asset["plant_id"],
        line_id=asset["line_id"], event_time=timeutil.to_iso(t), kind=kind, event_code=kind.upper(),
        inspection_result=insp, maintenance_status=ms, days_since_service=days, confidence=conf,
        note=note[:500] if note else None, warnings=w)
