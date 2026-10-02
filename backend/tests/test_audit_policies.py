"""Regression tests for the audited policies: duplicates, resolution, invalid values, timestamps, status rules,
work-order scope, worker fencing/concurrency, replay after a reference fix, freshness with a frozen clock."""
import json
import shutil
import threading
from datetime import datetime, timedelta, timezone

import pytest

from app.core import timeutil
from app.core.config import get_settings
from app.db import connect, load_reference
from app.domain import normalization as n
from app.domain.normalization import NormalizationError
from app.domain.rules import derive_machine_state
from app.worker import runner
from tests.conftest import ROOT
from tests.test_derive import ASSET, ev

S = get_settings()
ASSETS = {"EQ-001": dict(ASSET, machine_type="Press", criticality="high")}
T = "2026-04-18T08:00:00Z"


def tw(**kw):
    base = dict(readingId="TW-1", assetCode="EQ-001", productionLine="A", timestampMs=1776499152000, alertCode="VIB_WARN",
                level=3, temperature_f=150.0, power_kw=36.0, is_active=True, signal_quality="GOOD")
    return {**base, **kw}


def tw_body(*readings):
    return {"source": "ThermexWatch", "site_code": "PLANT_01", "readings": list(readings)}


def status_of(client, vendor, rid, rev=0):
    r = client.get(f"/v1/records/{vendor}/{rid}").json()["raw_revisions"][rev]
    return r["status"], r["error_code"]


# ---- 3. duplicates and fingerprint scope ---------------------------------------------------------------------
def test_distinct_observation_under_new_id_is_not_a_duplicate(client, run_worker):
    """Same values except signal quality: a different observation, so it must be kept (fingerprint covers all content)."""
    client.post("/v1/ingest/thermexwatch", json=tw_body(tw(readingId="TW-Q1", signal_quality="GOOD")))
    client.post("/v1/ingest/thermexwatch", json=tw_body(tw(readingId="TW-Q2", signal_quality="POOR")))
    client.post("/v1/ingest/thermexwatch", json=tw_body(tw(readingId="TW-Q3", signal_quality="GOOD")))   # true copy of Q1
    run_worker()
    assert status_of(client, "thermexwatch", "TW-Q2") == ("processed", None)
    assert status_of(client, "thermexwatch", "TW-Q3") == ("duplicate", "SEMANTIC_DUPLICATE")


def test_171_and_171_point_0_are_the_same_record(client, run_worker):
    client.post("/v1/ingest/thermexwatch", json=tw_body(tw(temperature_f=171.0)))
    raw = json.dumps(tw_body(tw(temperature_f=171)) | {"response_time_epoch_ms": 1}).replace("171.0", "171")
    r = client.post("/v1/ingest/thermexwatch", content=raw, headers={"content-type": "application/json"}).json()
    assert (r["accepted_records"], r["duplicate_records"]) == (0, 1)


# ---- 4. machine / plant / line resolution ----------------------------------------------------------------------
@pytest.mark.parametrize("patch, warning", [
    (dict(productionLine="ZZ"), "LINE_MISMATCH_USED_REFERENCE"),
    (dict(productionLine=None), "LINE_MISSING_USED_REFERENCE"),
    (dict(assetCode="eq_1"), None),                                     # alias -> EQ-001, no warning
])
def test_entity_resolution_corrects_with_warnings(patch, warning):
    e = n.normalize_record("ThermexWatch", tw(**patch), {"site_code": "PLANT_01"}, ASSETS, S)
    assert (e["machine_id"], e["plant_id"], e["line_id"]) == ("EQ-001", "PLANT_01", "LINE-A")   # reference wins
    assert (warning in e["warnings"]) if warning else not [x for x in e["warnings"] if "LINE" in x or "PLANT" in x]


def test_wrong_plant_is_corrected_from_the_reference_with_a_warning():
    e = n.normalize_record("ThermexWatch", tw(), {"site_code": "PLANT_02"}, ASSETS, S)
    assert e["plant_id"] == "PLANT_01" and "PLANT_MISMATCH_USED_REFERENCE" in e["warnings"]


@pytest.mark.parametrize("asset, code", [("", "MISSING_MACHINE_ID"), (None, "MISSING_MACHINE_ID"), ("EQ-999", "UNKNOWN_MACHINE")])
def test_missing_or_unknown_machine_is_rejected(asset, code):
    with pytest.raises(NormalizationError) as e:
        n.normalize_record("ThermexWatch", tw(assetCode=asset), {}, ASSETS, S)
    assert e.value.code == code


def test_original_values_stay_in_the_raw_record(client, run_worker):
    sent = tw(readingId="TW-RAW", assetCode="eq_1", productionLine="ZZ")
    client.post("/v1/ingest/thermexwatch", json=tw_body(sent))
    run_worker()
    t = client.get("/v1/records/thermexwatch/TW-RAW").json()
    assert t["raw_revisions"][0]["payload"] == sent                    # alias and wrong line kept as sent
    assert t["normalized_event"]["machine_id"] == "EQ-001" and "LINE_MISMATCH_USED_REFERENCE" in t["normalized_event"]["warnings"]


# ---- 5. unknown alerts and invalid values ----------------------------------------------------------------------
@pytest.mark.parametrize("value, state, warn", [(True, "running", False), ("false", "idle", False), ("YES", "running", False),
                                                (0, "idle", False), ("maybe", None, True), ("2", None, True)])
def test_is_active_is_parsed_strictly(value, state, warn):
    e = n.normalize_record("ThermexWatch", tw(is_active=value), {}, ASSETS, S)
    assert e["machine_state"] == state and ("INVALID_IS_ACTIVE" in e["warnings"]) == warn


def test_ok_with_an_attention_level_is_not_treated_as_healthy():
    e = n.normalize_record("ThermexWatch", tw(alertCode="OK", level=5), {}, ASSETS, S)
    assert (e["event_code"], e["severity"]) == ("UNKNOWN_ALERT", 4) and "ALERT_LEVEL_CONFLICT" in e["warnings"]
    r = derive_machine_state(ASSET, [dict(e, warnings=json.dumps(e["warnings"]))], S)
    assert r["derived_status"] == "critical" and "UNKNOWN_ALERT" in r["reason_codes"]


def test_alert_with_nominal_level_keeps_the_alert_severity():
    e = n.normalize_record("ThermexWatch", tw(alertCode="VIB_WARN", level=1), {}, ASSETS, S)
    assert (e["event_code"], e["severity"]) == ("HIGH_VIBRATION", 2) and "ALERT_LEVEL_CONFLICT" in e["warnings"]


def test_unknown_alert_and_unsupported_severity_are_explicit():
    pf = dict(event_id="PF-1", machine_id="EQ-001", line_id="LINE-A", event_time=T, event_type="GEARBOX_SMOKE",
              severity="urgent", machine_state="running")
    e = n.normalize_record("PulseForge", pf, {}, ASSETS, S)
    assert e["event_code"] == "UNKNOWN_ALERT" and e["severity"] == 1
    assert {"UNMAPPED_EVENT_TYPE", "UNMAPPED_SEVERITY_USED_DEFAULT"} <= set(e["warnings"])
    assert derive_machine_state(ASSET, [e], S)["derived_status"] == "watch"           # not healthy


def test_unmapped_manual_confidence_and_non_finite_numbers_warn():
    mf = dict(record_id="MF-1", machine_ref="EQ-001", line_ref="LINE-A", recorded_at="2026/04/18 08:00:00",
              record_type="inspection", inspection_result="passed", maintenance_status="not_due", manual_confidence="very sure")
    assert "UNMAPPED_MANUAL_CONFIDENCE" in n.normalize_record("MaintaFlow", mf, {}, ASSETS, S)["warnings"]
    e = n.normalize_record("ThermexWatch", tw(power_kw=float("nan"), temperature_f=float("inf")), {}, ASSETS, S)
    assert e["power_kw"] is None and e["temperature_c"] is None
    assert {"INVALID_POWER_KW", "INVALID_TEMPERATURE_F"} <= set(e["warnings"])


def test_warnings_are_visible_on_the_machine_view(client, run_worker):
    client.post("/v1/ingest/thermexwatch", json=tw_body(tw(readingId="TW-W", productionLine="ZZ", is_active="maybe")))
    run_worker()
    sig = next(s for s in client.get("/v1/machines/EQ-001").json()["latest_signals"] if s["record_id"] == "TW-W")
    assert {"LINE_MISMATCH_USED_REFERENCE", "INVALID_IS_ACTIVE"} <= set(sig["warnings"])


# ---- 6. timestamps and units -------------------------------------------------------------------------------------
def test_future_timestamp_policy():
    received = "2026-04-18T08:00:00Z"
    ok = tw(timestampMs=int(datetime(2026, 4, 18, 8, 9, tzinfo=timezone.utc).timestamp() * 1000))   # within 10 min
    assert n.normalize_record("ThermexWatch", ok, {}, ASSETS, S, received_at=received)
    bad = tw(timestampMs=int(datetime(2026, 4, 18, 8, 11, tzinfo=timezone.utc).timestamp() * 1000))
    with pytest.raises(NormalizationError) as e:
        n.normalize_record("ThermexWatch", bad, {}, ASSETS, S, received_at=received)
    assert e.value.code == "FUTURE_TIMESTAMP"


def test_future_timestamp_is_rejected_end_to_end(client, run_worker):
    far = int((timeutil.utcnow() + timedelta(days=365)).timestamp() * 1000)
    rep = client.post("/v1/ingest/thermexwatch/validate", json=tw_body(tw(readingId="TW-F", timestampMs=far))).json()
    assert rep["records"][0]["error_code"] == "FUTURE_TIMESTAMP"
    client.post("/v1/ingest/thermexwatch", json=tw_body(tw(readingId="TW-F", timestampMs=far)))
    run_worker()
    assert status_of(client, "thermexwatch", "TW-F") == ("rejected", "FUTURE_TIMESTAMP")


def test_units_and_time_zones():
    e = n.normalize_record("ThermexWatch", tw(temperature_f=171, vibration_g=0.42), {}, ASSETS, S)
    assert e["temperature_c"] == 77.22 and e["vibration_g"] == 0.42 and e["vibration_mm_s"] is None
    mf = dict(record_id="MF-2", machine_ref="EQ-001", line_ref="LINE-A", recorded_at="2026/04/18 08:14:05",
              record_type="operator_note", maintenance_status="unknown")
    m = n.normalize_record("MaintaFlow", mf, {}, ASSETS, S)
    assert m["event_time"] == "2026-04-18T08:14:05Z" and "TIMESTAMP_ASSUMED_UTC" in m["warnings"]
    pf = dict(event_id="PF-9", machine_id="EQ-001", line_id="LINE-A", event_time="2026-04-18T10:00:00+02:00",
              event_type="HIGH_VIBRATION", severity="low", machine_state="running")
    assert n.normalize_record("PulseForge", pf, {}, ASSETS, S)["event_time"] == "2026-04-18T08:00:00Z"


def test_vibration_in_g_is_never_compared_with_the_mm_s_limit():
    r = derive_machine_state(ASSET, [ev("TW-1", T, vendor="ThermexWatch", code="NOMINAL", sev=0, vibration_g=50.0)], S)
    assert r["attention_level"] == 0


# ---- 7. status rules -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("code, level, severity", [("OK", 1, 0), ("VIB_WARN", 2, 1), ("VIB_WARN", 3, 2),
                                                    ("VIB_WARN", 4, 3), ("TEMP_CRIT", 5, 4)])
def test_thermexwatch_levels_follow_the_guidance(code, level, severity):
    e = n.normalize_record("ThermexWatch", tw(alertCode=code, level=level), {}, ASSETS, S)
    assert e["severity"] == severity and "ALERT_LEVEL_CONFLICT" not in e["warnings"]


@pytest.mark.parametrize("temp, code, level", [(80.74, None, 0), (80.75, "TEMP_NEAR_RATED_LIMIT", 2),
                                               (85.0, "TEMP_NEAR_RATED_LIMIT", 2), (85.1, "TEMP_OVER_RATED_LIMIT", 3),
                                               (93.5, "TEMP_OVER_RATED_LIMIT", 4)])
def test_temperature_threshold_boundaries(temp, code, level):        # rated max 85.0: 95% = 80.75, 110% = 93.5
    r = derive_machine_state(ASSET, [ev("PF-1", T, code="NOMINAL", sev=0, temperature_c=temp)], S)
    assert r["attention_level"] == level and (code in r["reason_codes"] if code else r["reason_codes"] == [])


@pytest.mark.parametrize("vib, level", [(8.0, 0), (8.1, 2), (9.0, 2), (9.1, 3), (11.25, 4)])
def test_vibration_threshold_boundaries(vib, level):                # rated max 9.0: 90% = 8.1, 125% = 11.25
    assert derive_machine_state(ASSET, [ev("PF-1", T, code="NOMINAL", sev=0, vibration_mm_s=vib)], S)["attention_level"] == level


@pytest.mark.parametrize("kw, state, level", [(27.0, "running", 2), (21.6, "running", 3), (27.1, "running", 0), (10.0, "idle", 0)])
def test_power_deviation_boundaries_only_when_running(kw, state, level):   # baseline 36: 25% -> 27.0, 40% -> 21.6
    r = derive_machine_state(ASSET, [ev("TW-1", T, vendor="ThermexWatch", code="NOMINAL", sev=0, power_kw=kw, machine_state=state)], S)
    assert r["attention_level"] == level


def test_missing_measurements_only_use_the_vendor_alert():
    r = derive_machine_state(ASSET, [ev("PF-1", T, code="HIGH_VIBRATION", sev=1)], S)
    assert r["reason_codes"] == ["HIGH_VIBRATION"] and r["attention_level"] == 1


def test_equal_time_readings_from_one_vendor_keep_the_more_severe_one():
    for a, b in (("PF-A", "PF-B"), ("PF-B", "PF-A")):              # independent of record-id order
        r = derive_machine_state(ASSET, [ev(a, T, code="HIGH_TEMPERATURE", sev=4), ev(b, T, code="RECOVERY", sev=0)], S)
        assert r["derived_status"] == "critical", (a, b)


def test_one_source_with_several_reasons_is_not_corroboration():
    # three high reasons (alert, 109% temperature, 111% vibration) all from PulseForge: still level 3
    r = derive_machine_state(ASSET, [ev("PF-1", T, code="HIGH_TEMPERATURE", sev=3, temperature_c=93.0, vibration_mm_s=10.0)], S)
    assert r["attention_level"] == 3 and "MULTI_SOURCE_CORROBORATION" not in r["reason_codes"]
    # a second, independent source at high severity does corroborate
    r = derive_machine_state(ASSET, [ev("PF-1", T, code="HIGH_TEMPERATURE", sev=3),
                                     ev("TW-1", T, vendor="ThermexWatch", code="HIGH_VIBRATION", sev=3)], S)
    assert r["attention_level"] == 4 and "MULTI_SOURCE_CORROBORATION" in r["reason_codes"]
    r = derive_machine_state(ASSET, [ev("PF-1", T, code="HIGH_TEMPERATURE", sev=3, vibration_mm_s=10.0)], S)
    assert r["attention_level"] == 3 and "MULTI_SOURCE_CORROBORATION" not in r["reason_codes"]


def test_asset_under_maintenance_keeps_evidence_based_attention(client, run_worker):
    """EQ-007 is `maintenance` in the reference: status says so, but a real fault still needs attention."""
    client.post("/v1/ingest/thermexwatch", json={"source": "ThermexWatch", "site_code": "PLANT_02", "readings": [
        dict(tw(readingId="TW-M7", assetCode="EQ-007", productionLine="E", alertCode="TEMP_CRIT", level=5))]})
    run_worker()
    m = client.get("/v1/machines/EQ-007").json()
    assert (m["derived_status"], m["attention_level"], m["needs_attention"]) == ("under_maintenance", 4, True)
    s = client.get("/v1/summary/plants/PLANT_02").json()
    assert s["by_status"]["under_maintenance"] == 1 and "EQ-007" not in [c["machine_id"] for c in s["critical_machines"]]
    assert s["machines_needing_attention"] >= 1


# ---- 8. work-order scope ----------------------------------------------------------------------------------------------
def wo_ev(rid, t, status, check, mtype, trigger="sensor_alert"):
    from app.domain.normalization.vocabulary import WO_TRIGGER_SCOPE, WO_TYPE_SCOPE
    scope = sorted(WO_TYPE_SCOPE.get(mtype, set()) | WO_TRIGGER_SCOPE.get(trigger, set()))
    return ev(rid, t, vendor="CMMS", kind="work_order", code=f"WORK_ORDER_{status.upper()}", sev=None, machine_state=None,
              maintenance_status=status, inspection_result=check, note=mtype,
              details=json.dumps({"maintenance_type": mtype, "trigger_source": trigger, "resolution_scope": scope}))


def insp(rid, t, result="minor_defect_found"):
    return ev(rid, t, vendor="MaintaFlow", kind="inspection", code="INSPECTION", sev=None, machine_state=None,
              inspection_result=result, maintenance_status="not_due", note="belt wear", confidence=0.9)


def test_power_check_clears_only_power_findings():
    evidence = [ev("PF-1", "2026-04-18T07:50:00Z", code="POWER_ANOMALY", sev=2),
                ev("TW-1", "2026-04-18T07:51:00Z", vendor="ThermexWatch", code="HIGH_VIBRATION", sev=3, vibration_mm_s=9.5),
                insp("MF-1", "2026-04-18T07:00:00Z")]
    r = derive_machine_state(ASSET, evidence + [wo_ev("WO-1", "2026-04-18T08:10:00Z", "closed", "passed", "power_check")], S)
    assert "POWER_ANOMALY" not in r["reason_codes"]                                  # in scope: resolved
    assert {"HIGH_VIBRATION", "VIBRATION_OVER_RATED_LIMIT", "INSPECTION_MINOR_DEFECT"} <= set(r["reason_codes"])
    assert r["derived_status"] == "at_risk"


def test_inspection_triggered_repair_resolves_the_inspection_finding():
    r = derive_machine_state(ASSET, [insp("MF-1", "2026-04-18T07:20:00Z", "major_defect_found"),
                                     wo_ev("WO-3", "2026-04-18T09:00:00Z", "closed", "passed", "heat_shield", "inspection")], S)
    assert "INSPECTION_MAJOR_DEFECT" not in r["reason_codes"]
    assert any(x["code"] == "RESOLVED_BY_WORK_ORDER" and x["record_id"] == "MF-1" for x in r["reasons"])


def test_unknown_maintenance_type_resolves_nothing():
    w = dict(work_order_id="WO-X", plant_id="PLANT_01", machine_id="EQ-001", line_id="LINE-A", opened_at=T,
             closed_at="2026-04-18T09:00:00Z", maintenance_type="painting", status="closed", post_maintenance_check="passed")
    e = n.normalize_record("CMMS", w, {}, ASSETS, S)
    assert "UNMAPPED_MAINTENANCE_TYPE" in e["warnings"] and json.loads(json.dumps(e["details"]))["resolution_scope"] == []
    r = derive_machine_state(ASSET, [ev("PF-1", T, code="POWER_ANOMALY", sev=2), dict(e, details=json.dumps(e["details"]))], S)
    assert "POWER_ANOMALY" in r["reason_codes"]


def test_failed_check_is_settled_only_by_a_later_verified_order_of_the_same_scope():
    failed = wo_ev("WO-1", "2026-04-18T08:00:00Z", "closed", "failed", "power_check")
    other = wo_ev("WO-2", "2026-04-18T09:00:00Z", "closed", "passed", "cooling_system")
    same = wo_ev("WO-3", "2026-04-18T10:00:00Z", "closed", "passed", "power_check")
    assert "POST_MAINTENANCE_CHECK_FAILED" in derive_machine_state(ASSET, [failed, other], S)["reason_codes"]
    assert "POST_MAINTENANCE_CHECK_FAILED" not in derive_machine_state(ASSET, [failed, other, same], S)["reason_codes"]


def test_reopened_work_order(client, run_worker):
    base = dict(work_order_id="WO-R", plant_id="PLANT_01", machine_id="EQ-001", line_id="LINE-A",
                opened_at="2026-04-18T07:00:00Z", maintenance_type="power_check", trigger_source="sensor_alert",
                downtime_minutes="5", notes="power")
    body = lambda **k: {"source": "CMMS", "plant_id": "PLANT_01", "work_orders": [dict(base, **k)]}
    pf = {"vendor": "PulseForge", "plant_id": "PLANT_01", "events": [dict(event_id="PF-R", machine_id="EQ-001", line_id="LINE-A",
          event_time="2026-04-18T07:30:00Z", event_type="POWER_FLUCTUATION", severity="medium", machine_state="running")]}
    client.post("/v1/ingest/pulseforge", json=pf)
    client.post("/v1/ingest/workorders", json=body(status="closed", closed_at="2026-04-18T08:00:00Z", post_maintenance_check="passed"))
    run_worker()
    assert "POWER_ANOMALY" not in client.get("/v1/machines/EQ-001").json()["reason_codes"]
    client.post("/v1/ingest/workorders", json=body(status="reopened", closed_at="2026-04-18T08:00:00Z",
                                                   updated_at="2026-04-18T09:00:00Z", post_maintenance_check="failed"))
    run_worker()
    t = client.get("/v1/records/workorders/WO-R").json()
    assert t["normalized_event"]["maintenance_status"] == "reopened" and t["raw_revisions"][-1]["authoritative"]
    m = client.get("/v1/machines/EQ-001").json()
    assert "POWER_ANOMALY" in m["reason_codes"]                                       # no longer verified -> alert back
    assert any(r["code"] == "WORK_ORDER_OPEN" and r["record_id"] == "WO-R" for r in m["reasons"])


# ---- 10. worker reliability ------------------------------------------------------------------------------------------
def _pf(rid, machine="EQ-002"):
    return dict(event_id=rid, machine_id=machine, line_id="LINE-A", event_time=T, event_type="TEMP_SPIKE",
                severity="high", temperature_c=91, machine_state="running")


def test_worker_that_lost_its_lease_cannot_overwrite_the_result(client):
    client.post("/v1/ingest/pulseforge", json={"events": [_pf("PF-L1")]})
    conn, s, t0 = connect(), get_settings(), timeutil.utcnow()
    a = runner.claim(conn, s, t0)                                                     # worker A claims, then stalls
    b = runner.claim(conn, s, t0 + timedelta(seconds=s.lease_seconds + 1))            # lease expired; B takes over
    assets = runner.load_assets(conn)
    runner.process_record(conn, b[0], assets, s, t0)
    before = dict(conn.execute("SELECT status, processed_at, lease_token FROM raw_records WHERE id=?", (b[0]["id"],)).fetchone())
    assert runner.process_record(conn, a[0], assets, s, t0 + timedelta(seconds=999)) is None   # A's write is refused
    after = dict(conn.execute("SELECT status, processed_at, lease_token FROM raw_records WHERE id=?", (b[0]["id"],)).fetchone())
    conn.close()
    assert before == after and before["status"] == "processed"


def test_concurrent_workers_process_each_record_exactly_once(client):
    client.post("/v1/ingest/pulseforge", json={"events": [_pf(f"PF-C{i}", ("EQ-001", "EQ-002")[i % 2]) | {
        "event_time": f"2026-04-18T08:{i:02d}:00Z"} for i in range(40)]})
    errors = []

    def work():
        c = connect()
        try:
            while runner.run_once(c):
                pass
        except Exception as e:                                                         # pragma: no cover
            errors.append(e)
        finally:
            c.close()
    threads = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    c = connect()
    rows = c.execute("SELECT status, attempts FROM raw_records").fetchall()
    events = c.execute("SELECT COUNT(*) n FROM normalized_events").fetchone()["n"]
    dirty = c.execute("SELECT COUNT(*) n FROM dirty_machines").fetchone()["n"]
    c.close()
    assert not errors and events == 40 and dirty == 0
    assert all((r["status"], r["attempts"]) == ("processed", 1) for r in rows)
    m = client.get("/v1/machines/EQ-002").json()
    assert m["latest_relevant_event_time"] == "2026-04-18T08:39:00Z"                   # newest event made it into state


# ---- 11. replay after a reference fix -------------------------------------------------------------------------------
def test_replay_after_the_asset_reference_is_fixed(client, run_worker, env, tmp_path):
    client.post("/v1/ingest/pulseforge", json={"events": [_pf("PF-NEW", "EQ-009")]})
    run_worker()
    item = client.get("/v1/dead-letters").json()["items"][0]
    assert item["error_code"] == "UNKNOWN_MACHINE"
    ref = tmp_path / "ref"
    shutil.copytree(ROOT / "data" / "reference", ref)
    with (ref / "asset_reference.csv").open("a", encoding="utf-8") as fh:
        fh.write("EQ-009,PLANT_01,LINE-A,Press,low,2026-01-01,85.0,9.0,30.0,active\n")
    env.setenv("REFERENCE_DIR", str(ref))
    c = connect()
    load_reference(c)
    c.close()
    assert client.post(f"/v1/dead-letters/{item['raw_id']}/replay").status_code == 202
    run_worker()
    revs = client.get("/v1/records/pulseforge/PF-NEW").json()["raw_revisions"]
    assert revs[0]["status"] == "processed" and client.get("/v1/dead-letters").json()["count"] == 0
    assert client.get("/v1/machines/EQ-009").json()["needs_attention"]


# ---- 9 / 17. freshness with a frozen clock ---------------------------------------------------------------------------
def test_processing_freshness_with_a_frozen_clock(client, run_worker, env, monkeypatch):
    env.setenv("STALE_AFTER_MINUTES", "30")
    frozen = {"now": datetime(2026, 4, 18, 8, 10, tzinfo=timezone.utc)}
    monkeypatch.setattr(timeutil, "utcnow", lambda: frozen["now"])
    client.post("/v1/ingest/pulseforge", json={"events": [_pf("PF-T1", "EQ-001")]})   # event 08:00, received 08:10
    run_worker()
    p = client.get("/v1/machines/EQ-001").json()["processing_status"]
    assert (p["status"], p["data_age_seconds"], p["last_received_at"]) == ("fresh", 600, "2026-04-18T08:10:00Z")
    frozen["now"] = datetime(2026, 4, 18, 9, 0, tzinfo=timezone.utc)
    p = client.get("/v1/machines/EQ-001").json()["processing_status"]
    assert (p["status"], p["data_age_seconds"]) == ("stale", 3600)                    # event older than 30 min
    assert "EQ-001" in client.get("/v1/summary").json()["data_quality"]["stale_machines"]
