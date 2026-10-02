"""Shared normalization helpers.

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
import json
import math
import re

from .vocabulary import BOOL_FALSE, BOOL_TRUE, RANGES


class NormalizationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


_MID = re.compile(r"^EQ[-_ ]?(\d{1,4})$")


def canon_machine_id(raw) -> str | None:
    """`EQ-1`, `eq_001`, ` EQ 1 ` -> `EQ-001`; anything else is upper-cased and left for the asset lookup."""
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


def clean(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def fail(code: str, msg: str):
    raise NormalizationError(code, msg)


def num(value, field: str, w: list[str], warn_name: str | None = None) -> float | None:
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


def resolve_asset(raw_id, assets: dict, plant, line, w: list[str]) -> dict:
    mid = canon_machine_id(raw_id)
    if not mid:
        raise NormalizationError("MISSING_MACHINE_ID", "machine identifier is missing or empty")
    asset = assets.get(mid)
    if asset is None:
        raise NormalizationError("UNKNOWN_MACHINE", f"machine '{mid}' is not in the asset reference")
    if plant and str(plant).strip().upper() != asset["plant_id"]:
        w.append("PLANT_MISMATCH_USED_REFERENCE")
    if clean(line) is None:
        w.append("LINE_MISSING_USED_REFERENCE")
    elif canon_line(line) != asset["line_id"]:
        w.append("LINE_MISMATCH_USED_REFERENCE")
    return asset


def quality_from_health(h: float | None) -> str | None:
    if h is None:
        return None
    return "good" if h >= 0.8 else "fair" if h >= 0.6 else "poor"


# Every canonical content field. Only identity (record id) and bookkeeping (warnings, processing timestamps) are left
# out, so a fingerprint match means "the same observation re-sent under another id". Two readings that differ in ANY
# measured or reported value (signal quality, machine state, confidence, ...) are distinct observations.
FINGERPRINT_FIELDS = ("vendor", "machine_id", "event_time", "kind", "event_code", "severity", "vibration_mm_s",
                      "vibration_g", "temperature_c", "power_kw", "machine_state", "signal_quality", "sensor_health",
                      "confidence", "inspection_result", "maintenance_status", "days_since_service", "note", "details")


def _fingerprint(e: dict) -> str:
    """Content identity independent of the vendor record id (catches re-sent copies with a new id)."""
    content = json.dumps([e.get(k) for k in FINGERPRINT_FIELDS], sort_keys=True, default=str)
    return hashlib.sha256(content.encode()).hexdigest()


def boolean(value, field: str, w: list[str]) -> bool | None:
    """Strict boolean: true/false, 1/0, yes/no (any case). Anything else -> None + warning (never guessed)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in BOOL_TRUE:
        return True
    if s in BOOL_FALSE:
        return False
    w.append(f"INVALID_{field.upper()}")
    return None


def make_event(**kw) -> dict:
    """Build a canonical event: every optional field present (None if unknown), warnings deduped, fingerprinted."""
    base = dict(severity=None, vibration_mm_s=None, vibration_g=None, temperature_c=None, power_kw=None,
                machine_state=None, signal_quality=None, sensor_health=None, confidence=None,
                inspection_result=None, maintenance_status=None, days_since_service=None, note=None, details=None)
    base.update(kw)
    base["warnings"] = sorted(set(base["warnings"]))
    base["fingerprint"] = _fingerprint(base)
    return base
