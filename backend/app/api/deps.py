"""Shared request dependencies and validated path/query parameter types."""
from __future__ import annotations

from typing import Annotated

from fastapi import Path, Query

from app.db import connect

# Lenient on case/format (EQ-1, eq_001) because the service canonicalises; strict on charset and length.
MachineIdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_\- ]{1,32}$", examples=["EQ-001"])]
PlantIdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_\-]{1,32}$", examples=["PLANT_01"])]
RecordIdPath = Annotated[str, Path(min_length=1, max_length=200, examples=["PF-1001"])]
PlantFilter = Annotated[str | None, Query(pattern=r"^[A-Za-z0-9_\-]{1,32}$", description="e.g. PLANT_01")]
LineFilter = Annotated[str | None, Query(pattern=r"^[A-Za-z0-9_\-]{1,32}$", description="e.g. LINE-A")]
Limit = Annotated[int, Query(ge=1, le=1000, description="Maximum items to return")]


def get_conn():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
