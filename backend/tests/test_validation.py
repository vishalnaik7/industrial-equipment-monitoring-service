"""Request validation (tier 1, synchronous) and the dry-run validate endpoint (tier 2, synchronous preview)."""
import pytest

from tests.conftest import sample

JSON = {"content-type": "application/json"}


def err(r):
    return r.json()["error"]


def test_body_must_be_json(client):
    r = client.post("/v1/ingest/pulseforge", content=b"{nope", headers=JSON)
    assert r.status_code == 400 and err(r)["code"] == "INVALID_JSON"
    r = client.post("/v1/ingest/pulseforge", content=b"{}", headers={"content-type": "text/plain"})
    assert r.status_code == 415 and err(r)["code"] == "UNSUPPORTED_MEDIA_TYPE"


@pytest.mark.parametrize("body, field", [
    ([1], "body"),
    ({"plant_id": "PLANT_01"}, "events"),                       # record list missing
    ({"events": []}, "events"),                                 # empty
    ({"events": "x"}, "events"),                                # not a list
    ({"events": [{"event_id": "a"}, 5]}, "events[1]"),          # non-object record, exact path reported
    ({"events": [{}], "plant_id": 7}, "plant_id"),              # envelope metadata type
])
def test_envelope_validation_reports_field_paths(client, body, field):
    r = client.post("/v1/ingest/pulseforge", json=body)
    assert r.status_code == 422
    e = err(r)
    assert e["code"] == "VALIDATION_ERROR" and e["request_id"]
    assert field in {d["field"] for d in e["details"]}, e


def test_vendor_named_in_body_must_match_endpoint(client):
    body = sample("thermexwatch_sample")                        # source=ThermexWatch
    r = client.post("/v1/ingest/pulseforge", json={**body, "events": body["readings"], "vendor": "ThermexWatch"})
    assert r.status_code == 422 and "does not match" in err(r)["details"][0]["message"]
    assert client.post("/v1/ingest/thermexwatch", json=body).status_code == 202


def test_messy_record_values_are_not_rejected_synchronously(client):
    """Tier 2 is async on purpose: '-999', 'bad', 'four' must reach the normalizer, not 422 the batch."""
    r = client.post("/v1/ingest/pulseforge", json=sample("malformed")["pulseforge"])
    assert r.status_code == 202 and r.json()["accepted_records"] == 2


def test_batch_size_and_body_size_limits(client, env):
    env.setenv("MAX_RECORDS_PER_BATCH", "1")
    r = client.post("/v1/ingest/pulseforge", json=sample("pulseforge_sample"))
    assert r.status_code == 413 and err(r)["code"] == "BATCH_TOO_LARGE"
    env.setenv("MAX_BODY_BYTES", "10")
    r = client.post("/v1/ingest/pulseforge", json={"events": [{}]})
    assert r.status_code == 413 and err(r)["code"] == "BODY_TOO_LARGE"


def test_query_and_path_parameter_validation(client):
    assert client.get("/v1/machines", params={"status": "on_fire"}).status_code == 422
    assert client.get("/v1/machines", params={"plant_id": "DROP TABLE;"}).status_code == 422
    assert client.get("/v1/batches/not-a-uuid").status_code == 422
    assert client.get("/v1/dead-letters", params={"limit": 0}).status_code == 422
    assert client.get("/v1/records/acme/X").status_code == 422
    assert client.post("/v1/dead-letters/0/replay").status_code == 422
    assert client.get("/v1/machines/eq_1").json()["machine_id"] == "EQ-001"   # canonicalised, not rejected


def test_validate_endpoint_is_a_dry_run(client, run_worker):
    p = sample("malformed")["pulseforge"]
    rep = client.post("/v1/ingest/pulseforge/validate", json=p).json()
    assert rep["total_records"] == 2 and rep["rejected"] == 2
    codes = {r["error_code"] for r in rep["records"]}
    assert codes == {"MISSING_MACHINE_ID", "INVALID_TIMESTAMP"}
    assert client.get("/v1/batches").json()["count"] == 0                      # nothing stored

    happy = sample("happy_path")["pulseforge"]
    rep = client.post("/v1/ingest/pulseforge/validate", json=happy).json()
    assert rep["valid"] + rep["valid_with_warnings"] == 2
    assert {r["store_action"] for r in rep["records"]} == {"new"}
    client.post("/v1/ingest/pulseforge", json=happy)
    rep = client.post("/v1/ingest/pulseforge/validate", json=happy).json()
    assert {r["store_action"] for r in rep["records"]} == {"duplicate"}       # knows what is already stored


def test_validate_reports_in_batch_duplicates(client):
    rep = client.post("/v1/ingest/pulseforge/validate", json=sample("duplicates")["pulseforge"]).json()
    assert [r["store_action"] for r in rep["records"]] == ["new", "duplicate_in_batch"]


def test_batches_list_and_meta(client, run_worker):
    client.post("/v1/ingest/pulseforge", json=sample("pulseforge_sample"))
    run_worker()
    b = client.get("/v1/batches").json()
    assert b["count"] == 1 and b["items"][0]["state"] == "completed"
    assert client.get("/v1/batches", params={"vendor": "maintaflow"}).json()["count"] == 0
    m = client.get("/v1/meta").json()
    assert m["auth_required"] is False and m["vendors"] == ["pulseforge", "thermexwatch", "maintaflow", "workorders"]
    ref = client.get("/v1/reference").json()
    assert {p["plant_id"] for p in ref["plants"]} == {"PLANT_01", "PLANT_02"}


def test_samples_endpoint_splits_multi_vendor_files(client):
    names = {s["name"] for s in client.get("/v1/samples").json()["items"]}
    assert {"happy_path", "malformed", "pulseforge_sample"} <= names
    assert set(client.get("/v1/samples/happy_path").json()["bodies"]) == {"pulseforge", "thermexwatch", "maintaflow"}
    assert list(client.get("/v1/samples/pulseforge_sample").json()["bodies"]) == ["pulseforge"]
    assert client.get("/v1/samples/..%2Fsecrets").status_code in (404, 422)


def test_unhandled_errors_use_the_error_envelope(client, monkeypatch):
    from app.services import views

    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(views, "summarize", boom)
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/v1/summary")
    assert r.status_code == 500 and err(r)["code"] == "INTERNAL_ERROR" and "kaboom" not in r.text


def test_cors_allows_the_separately_served_dashboard_only(client):
    pre = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
           "Access-Control-Request-Headers": "content-type,x-api-key"}
    r = client.options("/v1/ingest/pulseforge", headers=pre)
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "http://localhost:5173"
    r = client.get("/v1/summary", headers={"Origin": "http://localhost:5173"})
    assert "X-Request-ID" in r.headers["access-control-expose-headers"]
    r = client.options("/v1/ingest/pulseforge", headers={**pre, "Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
