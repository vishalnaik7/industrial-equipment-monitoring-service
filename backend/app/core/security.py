"""API-key auth. Keys come from API_KEYS (comma separated); unset means open (dev only)."""
from __future__ import annotations

import secrets

from fastapi import Security
from fastapi.security import APIKeyHeader

from .config import get_settings
from .errors import ApiError

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="Required when API_KEYS is set")


def require_api_key(x_api_key: str | None = Security(_api_key_header)) -> None:
    keys = get_settings().api_keys
    if keys and not any(secrets.compare_digest(x_api_key or "", k) for k in keys):
        raise ApiError(401, "UNAUTHORIZED", "missing or invalid X-API-Key")
