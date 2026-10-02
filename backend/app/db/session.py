"""SQLite connections and transactions. WAL mode lets the API process and a separate worker process share one file."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.core.config import get_settings


def connect(path: str | None = None) -> sqlite3.Connection:
    p = path or get_settings().db_path
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection):
    """Write transaction (BEGIN IMMEDIATE avoids upgrade deadlocks between api and worker)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def dumps(obj) -> str:
    """Canonical JSON (sorted keys, compact) so hashes and equality checks are stable."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
