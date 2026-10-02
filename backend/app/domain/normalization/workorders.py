"""Internal CMMS work orders (seed_data/maintenance_work_orders.csv): ISO-8601 open/close times, status, post-check.

A work order is one record whose state changes over time (open -> closed -> possibly reopened); each change arrives as
a new revision of the same `work_order_id`. Its canonical event time is the moment of the latest state change:
closed_at for a closed order, opened_at for an open one, the later of the two for a reopened one, and `updated_at`
when the CMMS sends it (optional; not present in the supplied data). Revisions are ordered by that time.

`details` carries the structured fields the rules need: maintenance type, trigger, and the resolution scope (which
reason categories this work order can resolve - see vocabulary.WO_TYPE_SCOPE / WO_TRIGGER_SCOPE).
"""
from __future__ import annotations

from app.core import timeutil
from app.core.config import Settings

from .common import NormalizationError, clean, fail, make_event, num, resolve_asset
from .vocabulary import WO_OPEN_STATES, WO_POST_CHECK, WO_STATUS, WO_TRIGGER_SCOPE, WO_TYPE_SCOPE


def _iso(value, field: str, w: list[str], required: bool):
    if clean(value) is None:
        if required:
            raise NormalizationError("INVALID_TIMESTAMP", f"{field} is missing")
        return None
    try:
        t, assumed = timeutil.parse_iso(str(value).strip())
    except ValueError:
        if required:
            raise NormalizationError("INVALID_TIMESTAMP", f"{field} is not ISO-8601: {value!r}")
        w.append(f"INVALID_{field.upper()}")
        return None
    if assumed:
        w.append("TIMESTAMP_ASSUMED_UTC")
    return t


def normalize_work_order(p: dict, env: dict, assets: dict, s: Settings) -> dict:
    w: list[str] = []
    rid = clean(p.get("work_order_id")) or fail("MISSING_RECORD_ID", "work_order_id is missing")
    asset = resolve_asset(p.get("machine_id"), assets, p.get("plant_id") or env.get("plant_id"), p.get("line_id"), w)
    opened = _iso(p.get("opened_at"), "opened_at", w, required=True)
    closed = _iso(p.get("closed_at"), "closed_at", w, required=False)
    updated = _iso(p.get("updated_at"), "updated_at", w, required=False)

    status = (clean(p.get("status")) or "").lower()
    if status not in WO_STATUS:
        w.append("UNMAPPED_WORK_ORDER_STATUS")
        status = "unknown"
    if status == "closed" and closed is None:
        w.append("CLOSED_WITHOUT_CLOSED_AT")
    if status in ("open", "in_progress") and closed is not None:
        w.append("OPEN_WITH_CLOSED_AT_IGNORED")       # a reopen must say status=reopened; see module docstring
        closed = None
    if closed is not None and closed < opened:
        w.append("CLOSED_BEFORE_OPENED")

    check = (clean(p.get("post_maintenance_check")) or "").lower() or None
    if check is not None and check not in WO_POST_CHECK:
        w.append("UNMAPPED_POST_MAINTENANCE_CHECK")
        check = None
    downtime = num(p.get("downtime_minutes"), "downtime_minutes", w)

    mtype = (clean(p.get("maintenance_type")) or "").lower() or None
    trigger = (clean(p.get("trigger_source")) or "").lower() or None
    if mtype not in WO_TYPE_SCOPE:
        w.append("UNMAPPED_MAINTENANCE_TYPE")         # resolves nothing on its own: no guessed scope
    scope = sorted(WO_TYPE_SCOPE.get(mtype, set()) | WO_TRIGGER_SCOPE.get(trigger, set()))

    if status == "closed":
        state_time = closed or opened
    elif status == "reopened":
        state_time = max(t for t in (opened, closed) if t is not None)
    else:
        state_time = opened
    if updated is not None:
        state_time = max(state_time, updated)

    notes = clean(p.get("notes"))
    details = {"maintenance_type": mtype, "trigger_source": trigger, "opened_at": timeutil.to_iso(opened),
               "closed_at": timeutil.to_iso(closed) if closed else None, "downtime_minutes": downtime,
               "resolution_scope": scope, "reopened": status == "reopened"}
    return make_event(
        vendor="CMMS", source_record_id=rid, machine_id=asset["machine_id"], plant_id=asset["plant_id"],
        line_id=asset["line_id"], event_time=timeutil.to_iso(state_time), kind="work_order",
        event_code=f"WORK_ORDER_{status.upper()}", maintenance_status=status, inspection_result=check,
        note=f"{mtype or 'unspecified'} (trigger: {trigger or 'unspecified'}, opened {timeutil.to_iso(opened)})"
             + (f": {notes[:400]}" if notes else ""),
        details=details, warnings=w)


__all__ = ["normalize_work_order", "WO_OPEN_STATES"]
