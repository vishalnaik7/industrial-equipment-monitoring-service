"""Two freshness policies (rules v1.2):
* sensor readings expire: a vendor's newest reading counts only within RELEVANCE_WINDOW_MINUTES of the machine's newest
  SENSOR reading;
* maintenance findings never expire by age: they stay until a newer record of the same kind, completed maintenance or a
  verified work order resolves them.
"""
from app.core.config import get_settings
from app.domain.rules import derive_machine_state
from tests.test_derive import ASSET, ev

S = get_settings()
APRIL, OCT = "2026-04-18T08:00:00Z", "2026-10-01T20:39:56Z"


def mf(rid, t, kind="inspection", result="minor_defect_found", status="not_due", note="belt wear", conf=0.9):
    return ev(rid, t, vendor="MaintaFlow", kind=kind, code=kind.upper(), sev=None, machine_state=None,
              inspection_result=result, maintenance_status=status, note=note, confidence=conf)


def codes(r, min_level=0):
    return [x["code"] for x in r["reasons"] if x["level"] >= min_level]


def test_old_reading_from_one_vendor_is_ignored_next_to_a_new_reading_from_another():
    r = derive_machine_state(ASSET, [ev("PF-1", APRIL, sev=3), ev("TW-1", OCT, vendor="ThermexWatch", sev=2)], S)
    stale = next(x for x in r["reasons"] if x["code"] == "STALE_SIGNAL_IGNORED")
    assert stale["record_id"] == "PF-1" and stale["level"] == 0 and OCT in stale["detail"]
    assert r["attention_level"] == 2                                   # only the October reading counts


def test_relevance_is_anchored_to_sensor_time_not_to_maintenance_records():
    """An inspection filed hours after the last readings must not make those readings stale."""
    readings = [ev("PF-1", "2026-04-18T08:00:00Z", sev=3), ev("TW-1", "2026-04-18T08:05:00Z", vendor="ThermexWatch")]
    later_inspection = mf("MF-1", "2026-04-18T12:00:00Z", result="passed")
    r = derive_machine_state(ASSET, readings + [later_inspection], S)
    assert "STALE_SIGNAL_IGNORED" not in codes(r) and r["attention_level"] == 3
    assert r["latest_relevant_event_time"] == "2026-04-18T12:00:00Z"


def test_unresolved_defect_is_kept_however_old_and_its_age_is_stated():
    r = derive_machine_state(ASSET, [mf("MF-501", APRIL), ev("TW-1", OCT, vendor="ThermexWatch", sev=2)], S)
    finding = next(x for x in r["reasons"] if x["code"] == "INSPECTION_MINOR_DEFECT")
    assert finding["level"] == 1 and finding["record_id"] == "MF-501"
    assert f"unresolved since {APRIL}" in finding["detail"] and "166 days before the newest event" in finding["detail"]


def test_newer_passed_inspection_resolves_an_older_defect():
    r = derive_machine_state(ASSET, [mf("MF-1", APRIL, result="major_defect_found"),
                                     mf("MF-2", "2026-04-20T09:00:00Z", result="passed")], S)
    assert "INSPECTION_MAJOR_DEFECT" not in codes(r) and r["attention_level"] == 0
    res = next(x for x in r["reasons"] if x["code"] == "FINDING_RESOLVED_BY_INSPECTION")
    assert res["record_id"] == "MF-2" and "MF-1 (major_defect_found" in res["detail"]


def test_newer_defect_is_not_hidden_by_an_older_passed_inspection():
    r = derive_machine_state(ASSET, [mf("MF-1", APRIL, result="passed"),
                                     mf("MF-2", "2026-04-20T09:00:00Z", result="major_defect_found")], S)
    assert "INSPECTION_MAJOR_DEFECT" in codes(r, 1) and "FINDING_RESOLVED_BY_INSPECTION" not in codes(r)


def test_completed_calibration_resolves_pending_calibration():
    pending = mf("MF-1", APRIL, kind="calibration", result="calibration_pending", note="awaiting recalibration")
    done = mf("MF-2", "2026-04-19T10:00:00Z", kind="calibration", result="passed", note="recalibrated")
    assert "CALIBRATION_PENDING" in codes(derive_machine_state(ASSET, [pending], S), 1)
    r = derive_machine_state(ASSET, [pending, done], S)
    assert "CALIBRATION_PENDING" not in codes(r) and "FINDING_RESOLVED_BY_CALIBRATION" in codes(r)


def test_completed_maintenance_resolves_old_findings_of_every_kind():
    findings = [mf("MF-1", APRIL), mf("MF-2", APRIL, kind="calibration", result="calibration_pending"),
                mf("MF-3", APRIL, kind="operator_note", result=None, status="unknown", note="machine stalled")]
    done = mf("MF-4", "2026-04-21T10:00:00Z", kind="maintenance_update", result=None, status="completed", note="fixed")
    r = derive_machine_state(ASSET, findings + [done], S)
    assert r["attention_level"] == 0
    assert codes(r).count("SUPERSEDED_BY_COMPLETED_MAINTENANCE") == 3      # inspection, calibration, operator note


def test_docker_smoke_test_scenario_for_eq001(client, run_worker):
    """Reproduces the verified Docker smoke test: seed data (April) + one live ThermexWatch reading (October)."""
    from scripts.load_seed import batches
    for slug, body in batches():
        assert client.post(f"/v1/ingest/{slug}", json=body).status_code == 202
    reading = {"readingId": "TW-DEMO-EQ001-001", "assetCode": "EQ-001", "productionLine": "A",
               "timestampMs": 1790887196000, "alertCode": "VIB_WARN", "level": 3, "vibration_g": 0.42,
               "temperature_f": 171, "power_kw": 25.2, "is_active": True, "signal_quality": "GOOD"}
    client.post("/v1/ingest/thermexwatch", json={"source": "ThermexWatch", "site_code": "PLANT_01", "readings": [reading]})
    run_worker()
    m = client.get("/v1/machines/EQ-001").json()
    assert (m["derived_status"], m["attention_level"]) == ("degraded", 2)
    assert m["reason_codes"] == ["HIGH_VIBRATION", "POWER_DEVIATION", "INSPECTION_MINOR_DEFECT"]
    by_code = {r["code"]: r for r in m["reasons"]}
    assert by_code["STALE_SIGNAL_IGNORED"]["record_id"] == "PF-1001"          # April PulseForge reading expired
    assert by_code["INSPECTION_MINOR_DEFECT"]["record_id"] == "MF-501"        # April defect still unresolved
    assert "25.2 kW deviates 30% from baseline 36.0 kW" in by_code["POWER_DEVIATION"]["detail"]
    ev_ = client.get("/v1/records/thermexwatch/TW-DEMO-EQ001-001").json()["normalized_event"]
    assert ev_["temperature_c"] == 77.22 and ev_["event_time"] == "2026-10-01T20:39:56Z"
