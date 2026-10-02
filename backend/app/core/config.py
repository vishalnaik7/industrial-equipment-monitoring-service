"""Runtime configuration, read from environment variables on each call (easy to override in tests)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # backend/
APP_VERSION = "1.5.0"


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    db_path: str
    reference_dir: str
    samples_dir: str                   # demo payloads exposed to the dashboard
    cors_origins: tuple[str, ...]      # browser origins allowed to call the API (the separately served frontend)
    api_keys: tuple[str, ...]          # empty tuple => auth disabled (dev only)
    max_records_per_batch: int
    max_body_bytes: int
    worker_embedded: bool
    worker_poll_seconds: float
    worker_batch_size: int
    max_attempts: int
    retry_base_seconds: int
    lease_seconds: int
    maintaflow_tz: str                 # MaintaFlow timestamps carry no zone
    relevance_window_minutes: int      # how far back (from the machine's newest event) a signal still counts
    stale_after_minutes: int           # freshness threshold used at read time
    future_tolerance_minutes: int      # clock-skew allowance; later event times are rejected (FUTURE_TIMESTAMP)
    needs_attention_level: int         # attention_level >= this => needs_attention


def get_settings() -> Settings:
    keys = tuple(k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip())
    return Settings(
        db_path=os.getenv("DB_PATH", str(ROOT / "data" / "monitoring.db")),
        reference_dir=os.getenv("REFERENCE_DIR", str(ROOT / "data" / "reference")),
        samples_dir=os.getenv("SAMPLES_DIR", str(ROOT / "sample_payloads")),
        cors_origins=tuple(o.strip() for o in os.getenv(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()),
        api_keys=keys,
        max_records_per_batch=int(os.getenv("MAX_RECORDS_PER_BATCH", "5000")),
        max_body_bytes=int(os.getenv("MAX_BODY_BYTES", str(5 * 1024 * 1024))),
        worker_embedded=_bool("WORKER_EMBEDDED", True),
        worker_poll_seconds=float(os.getenv("WORKER_POLL_SECONDS", "1.0")),
        worker_batch_size=int(os.getenv("WORKER_BATCH_SIZE", "100")),
        max_attempts=int(os.getenv("MAX_ATTEMPTS", "5")),
        retry_base_seconds=int(os.getenv("RETRY_BASE_SECONDS", "5")),
        lease_seconds=int(os.getenv("LEASE_SECONDS", "60")),
        maintaflow_tz=os.getenv("MAINTAFLOW_TZ", "UTC"),
        relevance_window_minutes=int(os.getenv("RELEVANCE_WINDOW_MINUTES", "60")),
        stale_after_minutes=int(os.getenv("STALE_AFTER_MINUTES", "30")),
        future_tolerance_minutes=int(os.getenv("FUTURE_TOLERANCE_MINUTES", "10")),
        needs_attention_level=int(os.getenv("NEEDS_ATTENTION_LEVEL", "2")),
    )
