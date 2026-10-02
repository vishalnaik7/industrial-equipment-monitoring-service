"""PulseForge: ISO-8601 time, mm/s vibration, Celsius, string severities, sensor_health 0..1."""
from __future__ import annotations

from app.core import timeutil
from app.core.config import Settings

from .common import NormalizationError, clean, fail, make_event, num, quality_from_health, resolve_asset
from .vocabulary import DEFAULT_SEVERITY, MACHINE_STATES, PF_EVENT_MAP, SEVERITY


def normalize_pulseforge(p: dict, env: dict, assets: dict, s: Settings) -> dict:
    w: list[str] = []
    rid = clean(p.get("event_id")) or fail("MISSING_RECORD_ID", "event_id is missing")
    asset = resolve_asset(p.get("machine_id"), assets, env.get("plant_id"), p.get("line_id"), w)
    try:
        t, assumed = timeutil.parse_iso(p.get("event_time"))
        if assumed:
            w.append("TIMESTAMP_ASSUMED_UTC")
    except ValueError:
        raise NormalizationError("INVALID_TIMESTAMP", f"event_time is not ISO-8601: {p.get('event_time')!r}")

    code = PF_EVENT_MAP.get(str(p.get("event_type") or "").strip().upper())
    if code is None:
        code = "UNKNOWN_ALERT"
        w.append("UNMAPPED_EVENT_TYPE")
    sev = SEVERITY.get(str(p.get("severity") or "").strip().lower())
    if sev is None:
        sev = DEFAULT_SEVERITY[code]
        w.append("UNMAPPED_SEVERITY_USED_DEFAULT")
    health = num(p.get("sensor_health"), "sensor_health", w)
    state = str(p.get("machine_state") or "").strip().lower()
    if state not in MACHINE_STATES:
        w.append("UNMAPPED_MACHINE_STATE")
        state = None
    return make_event(
        vendor="PulseForge", source_record_id=rid, machine_id=asset["machine_id"], plant_id=asset["plant_id"],
        line_id=asset["line_id"], event_time=timeutil.to_iso(t), kind="sensor", event_code=code, severity=sev,
        vibration_mm_s=num(p.get("vibration_mm_s"), "vibration_mm_s", w),
        temperature_c=num(p.get("temperature_c"), "temperature_c", w),
        machine_state=state, sensor_health=health, signal_quality=quality_from_health(health),
        confidence=num(p.get("vendor_confidence"), "vendor_confidence", w), warnings=w)
