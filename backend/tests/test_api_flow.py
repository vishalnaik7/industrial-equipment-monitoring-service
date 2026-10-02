"""End-to-end through the HTTP API + worker using the provided edge-case payloads."""
import json

from tests.conftest import ROOT, post_all, sample


def machine(client, mid):
    r = client.get(f"/v1/machines/{mid}")
    assert r.status_code == 200
    return r.json()


def test_happy_path_ingest_is_async_then_visible(client, run_worker):
    res = post_all(client, sample("happy_path"))
    assert all(v["accepted_records"] >= 1 for v in res.values())
    # nothing processed yet: accepted != processed
    b = client.get(res["pulseforge"]["status_url"]).json()
    assert b["state"] == "processing" and b["record_status_counts"] == {"pending": 2}
    assert machine(client, "EQ-001")["processing_status"]["status"] == "pending"
    run_worker()
    assert client.get(res["pulseforge"]["status_url"]).json()["state"] == "completed"
    m = machine(client, "EQ-001")
    assert m["needs_attention"] and "HIGH_VIBRATION" in m["reason_codes"]
    assert {"source": "PulseForge", "record_id": "PF-1101"} in m["source_event_refs"]   # traceability
    assert machine(client, "EQ-003")["derived_status"] == "healthy"


def test_duplicates_within_batch_and_request_replay(client, run_worker):
    p = sample("duplicates")
    r1 = client.post("/v1/ingest/pulseforge", json=p["pulseforge"])
    assert r1.status_code == 202 and r1.json()["accepted_records"] == 1 and r1.json()["duplicate_records"] == 1
    r2 = client.post("/v1/ingest/pulseforge", json=p["pulseforge"])
    assert r2.status_code == 200 and r2.json()["replayed"] and r2.json()["batch_id"] == r1.json()["batch_id"]
    client.post("/v1/ingest/thermexwatch", json=p["thermexwatch"])
    run_worker()
    # TW-8801 and TW-8801-R carry identical content under different ids -> semantic duplicate
    trace = client.get("/v1/records/thermexwatch/TW-8801-R").json()
    assert trace["raw_revisions"][0]["status"] == "duplicate"
    assert trace["raw_revisions"][0]["error_code"] == "SEMANTIC_DUPLICATE"


def test_out_of_order_delivery_does_not_regress_state(client, run_worker):
    p = sample("out_of_order")
    client.post("/v1/ingest/pulseforge", json=p["pulseforge"])        # recovery listed BEFORE the earlier alert
    client.post("/v1/ingest/maintaflow", json=p["maintaflow"])
    run_worker()
    m = machine(client, "EQ-001")
    assert m["derived_status"] == "healthy"                          # newest event (RECOVERY 08:32) wins
    assert m["latest_relevant_event_time"] == "2026-04-18T08:32:05Z"
    m4 = machine(client, "EQ-004")                                   # completed maintenance after stall note
    assert not m4["needs_attention"]


def test_malformed_records_are_rejected_not_fatal(client, run_worker):
    p = sample("malformed")
    res = post_all(client, p)
    run_worker()
    b = client.get(res["thermexwatch"]["status_url"]).json()
    assert b["state"] == "completed_with_errors"
    assert {e["error_code"] for e in b["errors"]} == {"MISSING_MACHINE_ID"}
    dl = client.get("/v1/dead-letters").json()
    assert {i["error_code"] for i in dl["items"]} == {"MISSING_MACHINE_ID", "INVALID_TIMESTAMP"}
    # EQ-008 still got the (unmapped-alert) reading with warnings
    t = client.get("/v1/records/thermexwatch/TW-9912").json()
    assert "UNMAPPED_ALERT_CODE" in t["normalized_event"]["warnings"]
    assert machine(client, "EQ-002")["processing_status"]["status"] == "partial"


def test_conflicting_updates_across_vendors(client, run_worker):
    post_all(client, sample("conflicting_updates"))
    run_worker()
    m = machine(client, "EQ-008")
    assert m["derived_status"] == "critical" and m["attention_level"] == 4
    assert "CONFLICTING_SOURCES" in m["reason_codes"] and "INSPECTION_MAJOR_DEFECT" in m["reason_codes"]
    explained = {r["source"] for r in m["reasons"]}
    assert {"PulseForge", "ThermexWatch", "MaintaFlow", "system"} <= explained


def test_retry_cases_partial_failure(client, run_worker):
    res = post_all(client, sample("retry_cases"))
    run_worker()
    assert client.get(res["pulseforge"]["status_url"]).json()["state"] == "completed_with_errors"
    assert machine(client, "EQ-002")["reason_codes"].count("MAINTENANCE_OVERDUE") == 1


def test_same_id_new_payload_creates_revision_latest_wins(client, run_worker):
    p = sample("happy_path")["pulseforge"]
    client.post("/v1/ingest/pulseforge", json=p)
    run_worker()
    p2 = json.loads(json.dumps(p))
    p2["events"][0].update(event_type="RECOVERY_SIGNAL", severity="low", vibration_mm_s=2.0, temperature_c=60.0)
    r = client.post("/v1/ingest/pulseforge", json=p2).json()
    assert r["revised_records"] == 1
    run_worker()
    tr = client.get("/v1/records/pulseforge/PF-1101").json()
    assert [x["revision"] for x in tr["raw_revisions"]] == [1, 2] and tr["normalized_event"]["event_code"] == "RECOVERY"
    assert machine(client, "EQ-001")["state_version"] == 2


def test_unknown_vendor_is_404(client):
    assert client.post("/v1/ingest/acme", json={}).status_code == 404


def test_unknown_machine_goes_to_dead_letter_and_can_be_replayed(client, run_worker):
    body = {"vendor": "PulseForge", "plant_id": "PLANT_01", "events": [dict(
        event_id="PF-X1", machine_id="EQ-099", line_id="LINE-A", event_time="2026-04-18T08:00:00Z",
        event_type="TEMP_SPIKE", severity="high", temperature_c=90, vibration_mm_s=1, machine_state="running",
        sensor_health=0.9, vendor_confidence=0.9)]}
    client.post("/v1/ingest/pulseforge", json=body)
    run_worker()
    item = client.get("/v1/dead-letters").json()["items"][0]
    assert item["error_code"] == "UNKNOWN_MACHINE"
    assert client.post(f"/v1/dead-letters/{item['raw_id']}/replay").status_code == 202
    run_worker()
    assert client.get("/v1/dead-letters").json()["count"] == 1      # still unknown -> rejected again
    assert client.post("/v1/dead-letters/999999/replay").status_code == 404


def test_plant_and_line_summary(client, run_worker):
    for name in ("pulseforge_sample", "thermexwatch_sample", "maintaflow_sample"):
        d = sample(name)
        slug = name.split("_")[0]
        client.post(f"/v1/ingest/{slug}", json=d)
    run_worker()
    s = client.get("/v1/summary/plants/PLANT_01").json()
    assert s["scope"] == {"plant_id": "PLANT_01"} and s["machines_total"] == 4
    assert sum(s["by_status"].values()) == 4
    top = s["lines_ranked_by_attention"][0]
    assert top["line_id"] == "LINE-A" and top["machines_needing_attention"] == 2   # EQ-001 + EQ-002
    assert any(m["machine_id"] == "EQ-002" for m in s["critical_machines"])
    assert client.get("/v1/summary/plants/NOPE").status_code == 404
    assert client.get("/v1/summary").json()["machines_total"] == 8


def test_machine_filters_and_no_data_machine(client, run_worker):
    post_all(client, sample("happy_path"))
    run_worker()
    items = client.get("/v1/machines", params={"needs_attention": "true"}).json()["items"]
    assert items and all(i["needs_attention"] for i in items)
    assert machine(client, "EQ-007")["processing_status"]["status"] == "no_data"
    assert client.get("/v1/machines/EQ-404").status_code == 404



def test_same_record_from_different_json_serializers_is_a_duplicate(client):
    """Python serializes 80.0, a JavaScript client re-serializes it as 80: same value, not a new revision."""
    ev = dict(event_id="PF-N1", machine_id="EQ-001", line_id="LINE-A", event_time="2026-04-18T08:00:00Z",
              event_type="TEMP_SPIKE", severity="high", temperature_c=80.0, vibration_mm_s=2.0)
    first = client.post("/v1/ingest/pulseforge", json={"plant_id": "PLANT_01", "events": [ev]}).json()
    assert first["accepted_records"] == 1
    as_js = '{"events": [{"event_id": "PF-N1", "machine_id": "EQ-001", "line_id": "LINE-A", "event_time": '             '"2026-04-18T08:00:00Z", "event_type": "TEMP_SPIKE", "severity": "high", "temperature_c": 80, '             '"vibration_mm_s": 2}], "plant_id": "PLANT_01"}'
    r = client.post("/v1/ingest/pulseforge", content=as_js, headers={"content-type": "application/json"})
    assert r.status_code == 200 and r.json()["replayed"], r.json()          # whole request recognised
    as_js_new_envelope = as_js.replace('"PLANT_01"}', '"PLANT_01", "batch_generated_at": "x"}')
    r = client.post("/v1/ingest/pulseforge", content=as_js_new_envelope, headers={"content-type": "application/json"}).json()
    assert (r["accepted_records"], r["duplicate_records"], r["revised_records"]) == (0, 1, 0)


def test_vendor_payload_is_preserved_unchanged(client, run_worker):
    """The exact request bytes are retrievable per batch; each record keeps every field and value as sent."""
    original = (ROOT / "sample_payloads" / "malformed.json").read_bytes()
    body = json.loads(original)["thermexwatch"]
    sent = json.dumps(body, indent=3).encode()                      # vendor-specific formatting and key order
    r = client.post("/v1/ingest/thermexwatch", content=sent, headers={"content-type": "application/json"}).json()
    run_worker()
    raw = client.get(f"/v1/batches/{r['batch_id']}/raw")
    assert raw.status_code == 200 and raw.content == sent           # byte-for-byte
    for rec in body["readings"]:
        stored = client.get(f"/v1/records/thermexwatch/{rec['readingId']}").json()["raw_revisions"][0]["payload"]
        assert stored == rec                                          # nothing coerced, nulled or dropped
    assert client.get("/v1/batches/00000000-0000-0000-0000-000000000000/raw").status_code == 404


def test_corrected_resend_of_a_rejected_record_clears_the_dead_letter(client, run_worker):
    """retry_cases.json: PF-1502 has event_time 'bad-time' (permanent error, not retried). When the vendor resends the
    same id with a valid time it becomes a new revision, is processed, and the dead letter disappears."""
    body = sample("retry_cases")["pulseforge"]
    client.post("/v1/ingest/pulseforge", json=body)
    run_worker()
    assert [d["vendor_record_id"] for d in client.get("/v1/dead-letters").json()["items"]] == ["PF-1502"]
    assert machine(client, "EQ-005")["processing_status"]["status"] == "partial"

    fixed = json.loads(json.dumps(body))
    fixed["events"][1]["event_time"] = "2026-04-18T08:47:30Z"
    assert client.post("/v1/ingest/pulseforge", json=fixed).json()["revised_records"] == 1
    run_worker()
    assert client.get("/v1/dead-letters").json()["count"] == 0
    m = machine(client, "EQ-005")
    assert m["processing_status"]["status"] == "fresh" and "HIGH_TEMPERATURE" in m["reason_codes"]
    revs = client.get("/v1/records/pulseforge/PF-1502").json()["raw_revisions"]
    assert [(r["revision"], r["status"]) for r in revs] == [(1, "rejected"), (2, "processed")]   # history kept
