"""Time helpers. Everything is stored and served as UTC ISO-8601 with a 'Z' suffix."""
from __future__ import annotations

import re
from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_iso(dt: datetime) -> str:
    """Canonical storage/output format: UTC, second resolution, 'Z' suffix (sorts lexically)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def parse_iso(value) -> tuple[datetime, bool]:
    """Strict ISO-8601 parse. Returns (utc_datetime, assumed_utc). Raises ValueError otherwise."""
    if not isinstance(value, str) or not _ISO.match(value.strip()):
        raise ValueError(f"not an ISO-8601 timestamp: {value!r}")
    s = value.strip().replace(" ", "T").replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    assumed = dt.tzinfo is None
    if assumed:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0), assumed


def zone(name: str) -> tzinfo:
    """Resolve a zone name. UTC never touches the tz database (absent on Windows without `tzdata`)."""
    if name.strip().upper() in {"UTC", "Z", "ETC/UTC"}:
        return timezone.utc
    return ZoneInfo(name)
