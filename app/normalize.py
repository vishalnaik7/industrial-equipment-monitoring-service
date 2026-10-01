"""Vendor payload -> canonical event.

Policy
------
* ESSENTIAL fields (record id, machine, event time, record type) missing/invalid -> NormalizationError
  (permanent reject; visible in the dead-letter API and replayable).
* OPTIONAL fields (measurements, quality, confidence, line) invalid -> value nulled + a warning code on the
  event. One bad sensor value should not discard an otherwise useful alert.
* The asset reference is authoritative for plant/line; a vendor disagreement becomes a warning.
"""
from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import timeutil
from .config import Settings


class NormalizationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


# ---- canonical vocabularies -------------------------------------------------------------------------------
SEVERITY = {"info": 0, "none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
SEVERITY_LABELS = ["none", "low", "medium", "high", "critical"]
ALERT_CODES = {"HIGH_VIBRATION", "HIGH_TEMPERATURE", "POWER_ANOMALY", "SENSOR_DEGRADED", "UNKNOWN_ALERT"}
CLEAR_CODES = {"RECOVERY", "NOMINAL"}
DEFAULT_SEVERITY = {"HIGH_VIBRATION": 2, "HIGH_TEMPERATURE": 2, "POWER_ANOMALY": 2, "SENSOR_DEGRADED": 1,
                    "UNKNOWN_ALERT": 1, "RECOVERY": 0, "NOMINAL": 0}

PF_EVENT_MAP = {"HIGH_VIBRATION": "HIGH_VIBRATION", "TEMP_SPIKE": "HIGH_TEMPERATURE",
                "SENSOR_HEALTH_DROP": "SENSOR_DEGRADED", "POWER_FLUCTUATION": "POWER_ANOMALY",
                "RECOVERY_SIGNAL": "RECOVERY"}
TW_ALERT_MAP = {"VIB_WARN": "HIGH_VIBRATION", "TEMP_WARN": "HIGH_TEMPERATURE", "TEMP_CRIT": "HIGH_TEMPERATURE",
                "POWER_DROP": "POWER_ANOMALY", "OK": "NOMINAL"}
TW_LEVEL_SEVERITY = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}          # vendor level 1..5 -> canonical 0..4
MACHINE_STATES = {"running", "idle", "stopped", "fault"}
MF_RECORD_KINDS = {"inspection", "maintenance_update", "operator_note", "calibration"}
MF_INSPECTION = {"passed", "passed_with_observation", "minor_defect_found", "major_defect_found",
                 "calibration_pending"}
MF_MAINT_STATUS = {"not_due", "due_soon", "overdue", "completed", "unknown"}
MF_CONFIDENCE = {"low": 0.4, "medium": 0.7, "high": 0.9}
TW_QUALITY = {"GOOD": "good", "FAIR": "fair", "POOR": "poor"}

# plausibility ranges (outside => treated as sentinel/garbage such as -999)
RANGES = {"temperature_c": (-50.0, 1000.0), "vibration_mm_s": (0.0, 1000.0), "vibration_g": (0.0, 1000.0),
          "power_kw": (0.0, 100000.0), "sensor_health": (0.0, 1.0), "vendor_confidence": (0.0, 1.0),
          "temperature_f": (-58.0, 1832.0)}


# ---- helpers -----------------------------------------------------------------------------------------------
_MID = re.compile(r"^EQ[-_ ]?(\d{1,4})$")


def canon_machine_id(raw) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().upper()
    if not s:
        return None
    m = _MID.match(s)
    return f"EQ-{int(m.group(1)):03d}" if m else s


def canon_line(raw) -> str:
    s = str(raw).strip().upper().replace("_", "-").replace(" ", "-")
    return f"LINE-{s}" if re.fullmatch(r"[A-Z]", s) else s


def _clean(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _raise(code: str, msg: str):
    raise NormalizationError(code, msg)


def _num(value, field: str, w: list[str], warn_name: str | None = None) -> float | None:
    """Lenient numeric coercion with plausibility check; invalid -> None + warning."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, bool):
            raise ValueError
        v = float(value)
        lo, hi = RANGES.get(field, (-math.inf, math.inf))
        if not math.isfinite(v) or v < lo or v > hi:
            raise ValueError
        return v
    except (TypeError, ValueError):
        w.append(f"INVALID_{(warn_name or field).upper()}")
        return None


def _resolve_asset(raw_id, assets: dict, plant, line, w: list[str]) -> dict:
    mid = canon_machine_id(raw_id)
    if not mid:
        raise NormalizationError("MISSING_MACHINE_ID", "machine identifier is missing or empty")
    asset = assets.get(mid)
    if asset is None:
        raise NormalizationError("UNKNOWN_MACHINE", f"machine '{mid}' is not in the asset reference")
    if plant and str(plant).strip().upper() != asset["plant_id"]:
        w.append("PLANT_MISMATCH_USED_REFERENCE")
    if _clean(line) is None:
        w.append("LINE_MISSING_USED_REFERENCE")
    elif canon_line(line) != asset["line_id"]:
        w.append("LINE_MISMATCH_USED_REFERENCE")
    return asset


def _quality_from_health(h: float | None) -> str | None:
    if h is None:
        return None
    return "good" if h >= 0.8 else "fair" if h >= 0.6 else "poor"


def _fingerprint(e: dict) -> str:
    """Content identity independent of the vendor record id (catches re-sent copies with a new id)."""
    keys = ("vendor", "machine_id", "event_time", "kind", "event_code", "severity", "temperature_c",
            "vibration_mm_s", "vibration_g", "power_kw", "inspection_result", "maintenance_status", "note")
    return hashlib.sha256("|".join(str(e.get(k)) for k in keys).encode()).hexdigest()


def _event(**kw) -> dict:
    base = dict(severity=None, vibration_mm_s=None, vibration_g=None, temperature_c=None, power_kw=None,
                machine_state=None, signal_quality=None, sensor_health=None, confidence=None,
                inspection_result=None, maintenance_status=None, days_since_service=None, note=None)
    base.update(kw)
    base["warnings"] = sorted(set(base["warnings"]))
    base["fingerprint"] = _fingerprint(base)
    return base


# ---- vendors -----------------------------------------------------------------------------------------------
def normalize_pulseforge(p: dict, env: dict, assets: dict, s: Settings) -> dict:
    w: list[str] = []
    rid = _clean(p.get("event_id")) or _raise("MISSING_RECORD_ID", "event_id is missing")
    asset = _resolve_asset(p.get("machine_id"), assets, env.get("plant_id"), p.get("line_id"), w)
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
    health = _num(p.get("sensor_health"), "sensor_health", w)
    state = str(p.get("machine_state") or "").strip().lower()
    if state not in MACHINE_STATES:
        w.append("UNMAPPED_MACHINE_STATE")
        state = None
    return _event(
        vendor="PulseForge", source_record_id=rid, machine_id=asset["machine_id"], plant_id=asset["plant_id"],
        line_id=asset["line_id"], event_time=timeutil.to_iso(t), kind="sensor", event_code=code, severity=sev,
        vibration_mm_s=_num(p.get("vibration_mm_s"), "vibration_mm_s", w),
        temperature_c=_num(p.get("temperature_c"), "temperature_c", w),
        machine_state=state, sensor_health=health, signal_quality=_quality_from_health(health),
        confidence=_num(p.get("vendor_confidence"), "vendor_confidence", w), warnings=w)


def normalize_thermexwatch(p: dict, env: dict, assets: dict, s: Settings) -> dict:
    w: list[str] = []
    rid = _clean(p.get("readingId")) or _raise("MISSING_RECORD_ID", "readingId is missing")
    asset = _resolve_asset(p.get("assetCode"), assets, env.get("site_code"), p.get("productionLine"), w)

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

    code = TW_ALERT_MAP.get(str(p.get("alertCode") or "").strip().upper())
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
    sev = TW_LEVEL_SEVERITY[level] if level else DEFAULT_SEVERITY[code]

    temp_f = _num(p.get("temperature_f"), "temperature_f", w)
    temp_c = round((temp_f - 32) * 5 / 9, 2) if temp_f is not None else None
    active = p.get("is_active")
    state = None if active is None else ("running" if str(active).strip().lower() in {"true", "1"} else "idle")
    q = TW_QUALITY.get(str(p.get("signal_quality") or "").strip().upper())
    if q is None and p.get("signal_quality") is not None:
        w.append("UNMAPPED_SIGNAL_QUALITY")
    # vibration_g (acceleration) is deliberately NOT converted to mm/s (velocity): that needs frequency content.
    return _event(
        vendor="ThermexWatch", source_record_id=rid, machine_id=asset["machine_id"], plant_id=asset["plant_id"],
        line_id=asset["line_id"], event_time=timeutil.to_iso(t), kind="sensor", event_code=code, severity=sev,
        vibration_g=_num(p.get("vibration_g"), "vibration_g", w), temperature_c=temp_c,
        power_kw=_num(p.get("power_kw"), "power_kw", w), machine_state=state, signal_quality=q, warnings=w)


def normalize_maintaflow(p: dict, env: dict, assets: dict, s: Settings) -> dict:
    w: list[str] = []
    rid = _clean(p.get("record_id")) or _raise("MISSING_RECORD_ID", "record_id is missing")
    asset = _resolve_asset(p.get("machine_ref"), assets, env.get("factory_id"), p.get("line_ref"), w)
    kind = str(p.get("record_type") or "").strip().lower()
    if kind not in MF_RECORD_KINDS:
        raise NormalizationError("UNKNOWN_RECORD_TYPE", f"record_type {p.get('record_type')!r} is not supported")
    raw_t = p.get("recorded_at")
    try:
        t = datetime.strptime(str(raw_t).strip(), "%Y/%m/%d %H:%M:%S").replace(tzinfo=ZoneInfo(s.maintaflow_tz))
        w.append("TIMESTAMP_ASSUMED_" + s.maintaflow_tz.upper().replace("/", "_"))
    except (ValueError, TypeError):
        try:
            t, _ = timeutil.parse_iso(raw_t)
        except ValueError:
            raise NormalizationError("INVALID_TIMESTAMP", f"recorded_at not parseable: {raw_t!r}")
    insp = _clean(p.get("inspection_result"))
    insp = insp.lower() if insp else None
    if insp and insp not in MF_INSPECTION:
        w.append("UNMAPPED_INSPECTION_RESULT")
        insp = None
    ms = (_clean(p.get("maintenance_status")) or "unknown").lower()
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
    note = _clean(p.get("technician_note"))
    return _event(
        vendor="MaintaFlow", source_record_id=rid, machine_id=asset["machine_id"], plant_id=asset["plant_id"],
        line_id=asset["line_id"], event_time=timeutil.to_iso(t), kind=kind, event_code=kind.upper(),
        inspection_result=insp, maintenance_status=ms, days_since_service=days, confidence=conf,
        note=note[:500] if note else None, warnings=w)


NORMALIZERS = {"PulseForge": normalize_pulseforge, "ThermexWatch": normalize_thermexwatch,
               "MaintaFlow": normalize_maintaflow}


def normalize_record(vendor: str, payload: dict, envelope: dict, assets: dict, settings: Settings) -> dict:
    if not isinstance(payload, dict) or "_non_object_record" in payload:
        raise NormalizationError("RECORD_NOT_AN_OBJECT", "record is not a JSON object")
    return NORMALIZERS[vendor](payload, envelope, assets, settings)
