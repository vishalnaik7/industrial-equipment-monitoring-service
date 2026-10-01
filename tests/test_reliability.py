"""Async-processing behaviour: retries with backoff, dead-lettering, lease recovery, auth."""
from datetime import timedelta

from app import timeutil, worker
from app.db import connect
from tests.conftest import sample


def _raw(status_of="PF-1101"):
    c = connect()
    r = c.execute("SELECT * FROM raw_records WHERE vendor_record_id=?", (status_of,)).fetchone()
    c.close()
    return r


def test_transient_failure_retries_with_backoff_then_dead_letters(client, monkeypatch):
    monkeypatch.setenv("MAX_ATTEMPTS", "3")
    monkeypatch.setenv("RETRY_BASE_SECONDS", "10")
    client.post("/v1/ingest/pulseforge", json=sample("happy_path")["pulseforge"])

    real = worker.normalize_record

    def boom(*a, **k):
        raise RuntimeError("db unavailable")
    monkeypatch.setattr(worker, "normalize_record", boom)
    t0 = timeutil.utcnow()
    worker.run_once(now=t0)
    r = _raw()
    assert r["status"] == "pending" and r["attempts"] == 1 and r["error_code"] == "TRANSIENT_ERROR"
    assert r["next_attempt_at"] == timeutil.to_iso(t0 + timedelta(seconds=10))
    assert worker.run_once(now=t0 + timedelta(seconds=1)) == 0            # backoff respected
    worker.run_once(now=t0 + timedelta(seconds=11))
    worker.run_once(now=t0 + timedelta(seconds=40))
    r = _raw()
    assert r["status"] == "dead" and r["error_code"] == "RETRIES_EXHAUSTED" and r["attempts"] == 3
    # dead letter is visible and replayable once the fault is fixed
    monkeypatch.setattr(worker, "normalize_record", real)
    for item in client.get("/v1/dead-letters").json()["items"]:
        assert client.post(f"/v1/dead-letters/{item['raw_id']}/replay").status_code == 202
    worker.run_once()
    assert _raw()["status"] == "processed"


def test_crashed_worker_lease_expires_and_record_is_picked_up(client):
    client.post("/v1/ingest/pulseforge", json=sample("happy_path")["pulseforge"])
    c = connect()
    from app.config import get_settings
    now = timeutil.utcnow()
    claimed = worker.claim(c, get_settings(), now)                       # worker A claims then "crashes"
    assert len(claimed) == 2 and worker.claim(c, get_settings(), now) == []
    later = now + timedelta(seconds=get_settings().lease_seconds + 1)
    assert worker.run_once(c, now=later) == 2                             # worker B recovers them
    assert _raw()["status"] == "processed"
    c.close()


def test_recompute_failure_is_retried_via_dirty_marker(client, monkeypatch):
    client.post("/v1/ingest/pulseforge", json=sample("happy_path")["pulseforge"])
    real = worker.derive_machine_state
    monkeypatch.setattr(worker, "derive_machine_state", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    worker.run_once()
    assert client.get("/v1/machines/EQ-001").json()["derived_status"] == "unknown"
    monkeypatch.setattr(worker, "derive_machine_state", real)
    worker.run_once()                                                      # no new records, dirty marker remains
    assert client.get("/v1/machines/EQ-001").json()["derived_status"] != "unknown"


def test_api_key_auth(env):
    env.setenv("API_KEYS", "secret1,secret2")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        body = sample("pulseforge_sample")
        assert c.post("/v1/ingest/pulseforge", json=body).status_code == 401
        assert c.post("/v1/ingest/pulseforge", json=body, headers={"X-API-Key": "nope"}).status_code == 401
        assert c.post("/v1/ingest/pulseforge", json=body, headers={"X-API-Key": "secret2"}).status_code == 202
        assert c.get("/v1/machines").status_code == 401
        assert c.get("/health").status_code == 200                         # probes stay open


def test_oversized_batch_rejected(client, monkeypatch):
    monkeypatch.setenv("MAX_RECORDS_PER_BATCH", "1")
    assert client.post("/v1/ingest/pulseforge", json=sample("pulseforge_sample")).status_code == 413
