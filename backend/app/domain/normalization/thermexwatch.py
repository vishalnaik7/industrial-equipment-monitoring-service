"""ThermexWatch: epoch-ms time, vibration in g, Fahrenheit, numeric level 1..5, GOOD/FAIR/POOR quality."""
from __future__ import annotations

from datetime import datetime, timezone

from app.core import timeutil
from app.core.config import Settings

from .common import NormalizationError, boolean, clean, fail, make_event, num, resolve_asset
from .vocabulary import DEFAULT_SEVERITY, TW_ALERT_DEFAULT_SEVERITY, TW_ALERT_MAP, TW_LEVEL_SEVERITY, TW_QUALITY


def normalize_thermexwatch(p: dict, env: dict, assets: dict, s: Settings) -> dict:
    w: list[str] = []
    rid = clean(p.get("readingId")) or fail("MISSING_RECORD_ID", "readingId is missing")
    asset = resolve_asset(p.get("assetCode"), assets, env.get("site_code"), p.get("productionLine"), w)

    ts = p.get("timestampMs")
    try:
        if isinstance(ts, bool):
            raise ValueError
        ms = float(ts)
        if ms < 1e11:                       # looks like epoch *seconds*; tolerate but flag
            ms *= 1000
            w.append("TIMESTAMP_SECONDS_CONVERTED")
        t = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        if not (2000 <= t.year <= 2100):
            raise ValueError
    except (TypeError, ValueError, OverflowError, OSError):
        raise NormalizationError("INVALID_TIMESTAMP", f"timestampMs is not epoch milliseconds: {ts!r}")

    raw_alert = str(p.get("alertCode") or "").strip().upper()
    code = TW_ALERT_MAP.get(raw_alert)
    if code is None:
        code = "UNKNOWN_ALERT"
        w.append("UNMAPPED_ALERT_CODE")
    level = None
    try:
        level = int(p.get("level"))
        if level not in TW_LEVEL_SEVERITY:
            raise ValueError
    except (TypeError, ValueError):
        level = None
        w.append("INVALID_LEVEL_USED_DEFAULT")
    sev = TW_LEVEL_SEVERITY[level] if level else TW_ALERT_DEFAULT_SEVERITY.get(raw_alert, DEFAULT_SEVERITY[code])
    # Level guidance: 1 nominal .. 5 critical. If the alert code and the level disagree, the more severe reading wins
    # (pessimistic, like cross-vendor conflicts) and the record is flagged.
    if level and code == "NOMINAL" and level >= 2:
        code = "UNKNOWN_ALERT"                         # "OK" with an attention level is not treated as healthy
        w.append("ALERT_LEVEL_CONFLICT")
    elif level == 1 and code not in ("NOMINAL", "UNKNOWN_ALERT"):
        sev = max(sev, TW_ALERT_DEFAULT_SEVERITY.get(raw_alert, DEFAULT_SEVERITY[code]))
        w.append("ALERT_LEVEL_CONFLICT")

    temp_f = num(p.get("temperature_f"), "temperature_f", w)
    temp_c = round((temp_f - 32) * 5 / 9, 2) if temp_f is not None else None
    active = boolean(p.get("is_active"), "is_active", w)
    state = None if active is None else ("running" if active else "idle")
    q = TW_QUALITY.get(str(p.get("signal_quality") or "").strip().upper())
    if q is None and p.get("signal_quality") is not None:
        w.append("UNMAPPED_SIGNAL_QUALITY")
    # vibration_g (acceleration) is deliberately NOT converted to mm/s (velocity): that needs frequency content.
    return make_event(
        vendor="ThermexWatch", source_record_id=rid, machine_id=asset["machine_id"], plant_id=asset["plant_id"],
        line_id=asset["line_id"], event_time=timeutil.to_iso(t), kind="sensor", event_code=code, severity=sev,
        vibration_g=num(p.get("vibration_g"), "vibration_g", w), temperature_c=temp_c,
        power_kw=num(p.get("power_kw"), "power_kw", w), machine_state=state, signal_quality=q, warnings=w)
