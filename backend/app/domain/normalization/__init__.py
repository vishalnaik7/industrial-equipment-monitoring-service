"""Vendor payload -> canonical event. One module per vendor; the shared reject-vs-warn policy lives in `common`."""
from __future__ import annotations

from datetime import timedelta

from app.core import timeutil
from app.core.config import Settings

from .common import NormalizationError, canon_line, canon_machine_id
from .maintaflow import normalize_maintaflow
from .pulseforge import normalize_pulseforge
from .thermexwatch import normalize_thermexwatch
from .workorders import normalize_work_order

NORMALIZERS = {"PulseForge": normalize_pulseforge, "ThermexWatch": normalize_thermexwatch,
               "MaintaFlow": normalize_maintaflow, "CMMS": normalize_work_order}


def normalize_record(vendor: str, payload: dict, envelope: dict, assets: dict, settings: Settings,
                     received_at: str | None = None) -> dict:
    """Vendor record -> canonical event. `received_at` (UTC ISO) enables the future-timestamp policy: an event time
    more than FUTURE_TOLERANCE_MINUTES after the record was received is rejected (FUTURE_TIMESTAMP), because a reading
    from the future would otherwise stay the "newest" reading and mask real data until that time."""
    if not isinstance(payload, dict) or "_non_object_record" in payload:
        raise NormalizationError("RECORD_NOT_AN_OBJECT", "record is not a JSON object")
    ev = NORMALIZERS[vendor](payload, envelope, assets, settings)
    if received_at:
        limit = timeutil.from_iso(received_at) + timedelta(minutes=settings.future_tolerance_minutes)
        if timeutil.from_iso(ev["event_time"]) > limit:
            raise NormalizationError("FUTURE_TIMESTAMP", f"event time {ev['event_time']} is more than "
                                     f"{settings.future_tolerance_minutes} min after it was received ({received_at})")
    return ev


__all__ = ["NORMALIZERS", "NormalizationError", "canon_line", "canon_machine_id", "normalize_maintaflow",
           "normalize_pulseforge", "normalize_record", "normalize_thermexwatch", "normalize_work_order"]
