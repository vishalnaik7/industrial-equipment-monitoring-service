import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("WORKER_EMBEDDED", "false")      # tests drive the worker explicitly => deterministic
    monkeypatch.setenv("API_KEYS", "")
    monkeypatch.setenv("STALE_AFTER_MINUTES", "10000000")
    return monkeypatch


@pytest.fixture()
def client(env):
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def run_worker():
    from app import worker

    def _run(times=3, **kw):
        n = 0
        for _ in range(times):
            n += worker.run_once(**kw)
        return n
    return _run


def sample(name):
    return json.loads((ROOT / "sample_payloads" / f"{name}.json").read_text())


def post_all(client, payload):
    """Post every vendor section found in a multi-vendor sample file."""
    out = {}
    for slug in ("pulseforge", "thermexwatch", "maintaflow"):
        if slug in payload:
            r = client.post(f"/v1/ingest/{slug}", json=payload[slug])
            assert r.status_code in (200, 202), r.text
            out[slug] = r.json()
    return out
