"""Revisions of the SAME record (same vendor + record id) vs separate out-of-order events (different ids).

Policy (app/worker/runner.py revision_key): the authoritative revision is the greatest
(vendor batch version, record event time, arrival order). Vendor batch version = PulseForge `batch_generated_at`,
ThermexWatch `response_time_epoch_ms`; MaintaFlow and CMMS send none, so their record time decides.
"""
import json

from tests.conftest import sample


def pf_batch(generated_at, **event):
    base = dict(event_id="PF-R1", machine_id="EQ-001", line_id="LINE-A", event_time="2026-04-18T08:30:00Z",
                event_type="HIGH_VIBRATION", severity="high", vibration_mm_s=9.5, temperature_c=70.0,
                machine_state="running", sensor_health=0.95, vendor_confidence=0.9)
    base.update(event)
    body = {"vendor": "PulseForge", "plant_id": "PLANT_01", "events": [base]}
    if generated_at:
        body["batch_generated_at"] = generated_at
    return body


def mf_batch(**record):
    base = dict(record_id="MF-R1", machine_ref="EQ-001", line_ref="LINE-A", recorded_at="2026/04/18 08:40:00",
                record_type="inspection", inspection_result="minor_defect_found", maintenance_status="not_due",
                days_since_last_service=10, technician_note="belt wear", manual_confidence="high")
    base.update(record)
    return {"provider_name": "MaintaFlow", "factory_id": "PLANT_01", "records": [base]}


def trace(client, vendor, rid):
    return client.get(f"/v1/records/{vendor}/{rid}").json()


def authoritative(t):
    return [r["revision"] for r in t["raw_revisions"] if r["authoritative"]]


def test_delayed_older_vendor_version_cannot_replace_newer_revision(client, run_worker):
    newer = pf_batch("2026-04-18T08:40:00Z", event_type="TEMP_SPIKE", severity="critical", temperature_c=96.0)
    older = pf_batch("2026-04-18T08:35:00Z")                          # generated earlier, delivered later
    client.post("/v1/ingest/pulseforge", json=newer)
    run_worker()
    assert client.post("/v1/ingest/pulseforge", json=older).json()["revised_records"] == 1
    run_worker()

    t = trace(client, "pulseforge", "PF-R1")
    rev2 = t["raw_revisions"][1]
    assert (rev2["status"], rev2["error_code"]) == ("superseded", "STALE_REVISION")
    assert "vendor batch version 2026-04-18T08:35:00Z is older than 2026-04-18T08:40:00Z" in rev2["error_message"]
    assert authoritative(t) == [1] and t["normalized_event"]["event_code"] == "HIGH_TEMPERATURE"
    assert [r["source_version"] for r in t["raw_revisions"]] == ["2026-04-18T08:40:00Z", "2026-04-18T08:35:00Z"]
    assert t["raw_revisions"][0]["payload"] == newer["events"][0]           # raw history kept, unchanged
    assert rev2["payload"] == older["events"][0]
    m = client.get("/v1/machines/EQ-001").json()
    assert m["processing_status"]["status"] == "fresh" and "HIGH_TEMPERATURE" in m["reason_codes"]


def test_result_does_not_depend_on_arrival_or_processing_order(client, run_worker):
    """Both revisions queued before the worker runs, in both arrival orders: the newer vendor version wins either way."""
    # different event times per id, otherwise PF-B would be a semantic duplicate (same content) of PF-A
    v1 = lambda rid, t: pf_batch("2026-04-18T08:35:00Z", event_id=rid, event_time=t)
    v2 = lambda rid, t: pf_batch("2026-04-18T08:40:00Z", event_id=rid, event_time=t, event_type="RECOVERY_SIGNAL",
                                 severity="low")
    cases = {"PF-A": (v2, v1, "2026-04-18T08:30:00Z", ["processed", "superseded"]),   # newer arrived first
             "PF-B": (v1, v2, "2026-04-18T08:31:00Z", ["processed", "processed"])}    # older arrived first
    for rid, (first, second, t, _) in cases.items():
        client.post("/v1/ingest/pulseforge", json=first(rid, t))
        client.post("/v1/ingest/pulseforge", json=second(rid, t))
    run_worker()
    for rid, (*_, statuses) in cases.items():
        t = trace(client, "pulseforge", rid)
        assert t["normalized_event"]["event_code"] == "RECOVERY", rid
        assert [r["status"] for r in t["raw_revisions"]] == statuses, rid


def test_newer_vendor_version_may_correct_the_record_time_backwards(client, run_worker):
    client.post("/v1/ingest/pulseforge", json=pf_batch("2026-04-18T08:35:00Z", event_time="2026-04-18T08:31:00Z"))
    run_worker()
    client.post("/v1/ingest/pulseforge", json=pf_batch("2026-04-18T08:45:00Z", event_time="2026-04-18T08:21:00Z"))
    run_worker()
    t = trace(client, "pulseforge", "PF-R1")
    assert authoritative(t) == [2] and t["normalized_event"]["event_time"] == "2026-04-18T08:21:00Z"


def test_thermexwatch_epoch_ms_version_is_used(client, run_worker):
    body = json.loads(json.dumps(sample("thermexwatch_sample")))
    body["readings"] = body["readings"][:1]
    late_old = json.loads(json.dumps(body))
    late_old["response_time_epoch_ms"] -= 600_000                  # generated 10 minutes earlier
    late_old["readings"][0]["alertCode"] = "OK"
    client.post("/v1/ingest/thermexwatch", json=body)
    client.post("/v1/ingest/thermexwatch", json=late_old)
    run_worker()
    t = trace(client, "thermexwatch", "TW-8801")
    assert authoritative(t) == [1] and t["raw_revisions"][1]["error_code"] == "STALE_REVISION"
    assert t["raw_revisions"][0]["source_version"] == "2026-04-18T08:06:00Z"


def test_without_vendor_version_record_time_then_arrival_decides(client, run_worker):
    """MaintaFlow sends no batch version: the record's own time decides; equal time -> the later arrival wins."""
    client.post("/v1/ingest/maintaflow", json=mf_batch())
    run_worker()
    client.post("/v1/ingest/maintaflow", json=mf_batch(recorded_at="2026/04/18 08:10:00", technician_note="old"))
    run_worker()
    t = trace(client, "maintaflow", "MF-R1")
    assert t["raw_revisions"][1]["error_code"] == "STALE_REVISION"
    assert "record time 2026-04-18T08:10:00Z is older than 2026-04-18T08:40:00Z" in t["raw_revisions"][1]["error_message"]

    client.post("/v1/ingest/maintaflow", json=mf_batch(technician_note="belt wear (corrected)"))   # same time
    run_worker()
    t = trace(client, "maintaflow", "MF-R1")
    assert authoritative(t) == [3] and t["normalized_event"]["note"] == "belt wear (corrected)"


def test_separate_out_of_order_events_are_all_kept(client, run_worker):
    """Different record ids are separate events, not revisions: both are stored; event time orders them in the rules."""
    p = sample("out_of_order")["pulseforge"]                         # PF-1302 (08:32 recovery) listed before PF-1301
    client.post("/v1/ingest/pulseforge", json=p)
    run_worker()
    for rid in ("PF-1301", "PF-1302"):
        t = trace(client, "pulseforge", rid)
        assert t["normalized_event"] and t["raw_revisions"][0]["status"] == "processed"
        assert len(t["raw_revisions"]) == 1
    m = client.get("/v1/machines/EQ-001").json()
    assert m["derived_status"] == "healthy"                         # newest event (the recovery) decides
    assert {"source": "PulseForge", "record_id": "PF-1302"} in m["source_event_refs"]


def test_older_revision_still_queued_counts_as_pending(client, run_worker):
    client.post("/v1/ingest/pulseforge", json=pf_batch("2026-04-18T08:40:00Z"))
    client.post("/v1/ingest/pulseforge", json=pf_batch("2026-04-18T08:35:00Z", severity="low"))
    assert client.get("/v1/machines/EQ-001").json()["processing_status"]["pending_records"] == 2
    run_worker()
    assert client.get("/v1/machines/EQ-001").json()["processing_status"]["status"] == "fresh"
