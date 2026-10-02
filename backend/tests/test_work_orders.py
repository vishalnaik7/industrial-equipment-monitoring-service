"""CMMS work orders (seed_data/maintenance_work_orders.csv): normalization, rule 8, revision ordering, seed outcome."""
import csv

import pytest

from app.core.config import get_settings
from app.domain import normalization as n
from app.domain.normalization import NormalizationError
from app.domain.rules import derive_machine_state
from tests.conftest import ROOT

S = get_settings()
ASSETS = {"EQ-004": dict(machine_id="EQ-004", plant_id="PLANT_01", line_id="LINE-C", machine_type="Compressor",
                         criticality="high", rated_max_temp_c=95.0, rated_max_vibration_mm_s=10.0,
                         baseline_power_kw=42.0, asset_status="active")}


def seed_rows():
    with (ROOT / "data" / "seed" / "maintenance_work_orders.csv").open(encoding="utf-8") as fh:
        return {r["work_order_id"]: {k: (v or None) for k, v in r.items()} for r in csv.DictReader(fh)}


def wo(**kw):
    base = dict(work_order_id="WO-1", plant_id="PLANT_01", machine_id="EQ-004", line_id="LINE-C",
                opened_at="2026-04-18T07:35:00Z", closed_at=None, maintenance_type="power_check",
                trigger_source="operator_report", status="open", downtime_minutes="0",
                post_maintenance_check="pending", notes="investigating")
    base.update(kw)
    return base


def test_normalizes_supplied_work_orders():
    rows = seed_rows()
    closed = n.normalize_record("CMMS", rows["WO-1002"], {"plant_id": "PLANT_01"}, ASSETS, S)
    assert (closed["kind"], closed["event_code"], closed["maintenance_status"], closed["inspection_result"]) == \
           ("work_order", "WORK_ORDER_CLOSED", "closed", "passed")
    assert closed["event_time"] == "2026-04-18T08:10:00Z"           # time of the latest state change
    assert closed["warnings"] == [] and "Power drop investigated and cleared" in closed["note"]


@pytest.mark.parametrize("patch, warning", [
    (dict(status="closed"), "CLOSED_WITHOUT_CLOSED_AT"),
    (dict(status="weird"), "UNMAPPED_WORK_ORDER_STATUS"),
    (dict(closed_at="2026-04-18T09:00:00Z"), "OPEN_WITH_CLOSED_AT_IGNORED"),
    (dict(status="closed", closed_at="2026-04-18T07:00:00Z"), "CLOSED_BEFORE_OPENED"),
    (dict(closed_at="yesterday", status="closed"), "INVALID_CLOSED_AT"),
    (dict(post_maintenance_check="maybe"), "UNMAPPED_POST_MAINTENANCE_CHECK"),
    (dict(downtime_minutes="-5"), "INVALID_DOWNTIME_MINUTES"),
])
def test_bad_optional_fields_become_warnings(patch, warning):
    assert warning in n.normalize_record("CMMS", wo(**patch), {}, ASSETS, S)["warnings"]


@pytest.mark.parametrize("patch, code", [
    (dict(work_order_id=" "), "MISSING_RECORD_ID"),
    (dict(machine_id="EQ-999"), "UNKNOWN_MACHINE"),
    (dict(opened_at=None), "INVALID_TIMESTAMP"),
    (dict(opened_at="18/04/2026"), "INVALID_TIMESTAMP"),
])
def test_bad_essential_fields_reject(patch, code):
    with pytest.raises(NormalizationError) as e:
        n.normalize_record("CMMS", wo(**patch), {}, ASSETS, S)
    assert e.value.code == code


def _event(vendor, rid, t, kind="sensor", **kw):
    base = dict(vendor=vendor, source_record_id=rid, event_time=t, kind=kind, event_code="POWER_ANOMALY", severity=2,
                temperature_c=None, vibration_mm_s=None, vibration_g=None, power_kw=None, machine_state="idle",
                signal_quality="good", inspection_result=None, maintenance_status=None, days_since_service=None,
                note=None, confidence=None)
    base.update(kw)
    return base


def _wo_event(rid, t, status, check, scope=("power",)):
    return _event("CMMS", rid, t, kind="work_order", event_code=f"WORK_ORDER_{status.upper()}", severity=None,
                  machine_state=None, maintenance_status=status, inspection_result=check, note="power_check",
                  details={"maintenance_type": "power_check", "resolution_scope": list(scope)})


def test_verified_work_order_resolves_earlier_alerts_but_not_later_ones():
    alert = _event("PulseForge", "PF-1", "2026-04-18T07:51:00Z")
    done = _wo_event("WO-1", "2026-04-18T08:10:00Z", "closed", "passed")
    st = derive_machine_state(ASSETS["EQ-004"], [alert, done], S)
    assert st["derived_status"] == "healthy" and st["reason_codes"] == []
    resolved = next(r for r in st["reasons"] if r["record_id"] == "PF-1")
    assert resolved["level"] == 0 and "resolved: work order WO-1" in resolved["detail"]
    assert {"source": "CMMS", "record_id": "WO-1"} in st["source_event_refs"]

    later = _event("ThermexWatch", "TW-1", "2026-04-18T08:20:00Z")      # new alert after the fix still counts
    st = derive_machine_state(ASSETS["EQ-004"], [alert, done, later], S)
    assert st["derived_status"] == "degraded" and "POWER_ANOMALY" in st["reason_codes"]


def test_open_work_order_is_acknowledged_but_keeps_attention():
    st = derive_machine_state(ASSETS["EQ-004"], [_event("PulseForge", "PF-1", "2026-04-18T07:51:00Z"),
                                                 _wo_event("WO-1", "2026-04-18T07:55:00Z", "open", "pending")], S)
    assert st["needs_attention"] and "POWER_ANOMALY" in st["reason_codes"]
    assert any(r["code"] == "WORK_ORDER_OPEN" and r["level"] == 0 for r in st["reasons"])


@pytest.mark.parametrize("check, code, level", [("failed", "POST_MAINTENANCE_CHECK_FAILED", 2),
                                                ("pending", "POST_MAINTENANCE_CHECK_PENDING", 1)])
def test_closed_work_order_without_passed_check_raises_attention(check, code, level):
    st = derive_machine_state(ASSETS["EQ-004"], [_wo_event("WO-1", "2026-04-18T08:10:00Z", "closed", check)], S)
    assert st["attention_level"] == level and code in st["reason_codes"]


def test_work_orders_do_not_mask_maintaflow_service_status():
    overdue = _event("MaintaFlow", "MF-1", "2026-04-18T08:05:00Z", kind="maintenance_update",
                     event_code="MAINTENANCE_UPDATE", severity=None, maintenance_status="overdue", days_since_service=46)
    st = derive_machine_state(ASSETS["EQ-004"], [overdue, _wo_event("WO-1", "2026-04-18T08:07:00Z", "open", "pending")], S)
    assert "MAINTENANCE_OVERDUE" in st["reason_codes"]


def _post(client, **wo_fields):
    return client.post("/v1/ingest/workorders", json={"source": "CMMS", "plant_id": "PLANT_01",
                                                      "work_orders": [wo(**wo_fields)]}).json()


def test_out_of_order_work_order_states_never_reopen(client, run_worker):
    _post(client, status="closed", closed_at="2026-04-18T08:10:00Z", post_maintenance_check="passed")
    run_worker()
    r = _post(client)                                                   # the older 'open' state arrives late
    assert r["revised_records"] == 1
    run_worker()
    trace = client.get("/v1/records/workorders/WO-1").json()
    assert trace["raw_revisions"][-1]["status"] == "superseded"
    assert trace["raw_revisions"][-1]["error_code"] == "STALE_REVISION"
    assert trace["normalized_event"]["maintenance_status"] == "closed"


def test_wrong_source_name_and_envelope_are_rejected(client):
    r = client.post("/v1/ingest/workorders", json={"source": "MaintaFlow", "work_orders": [wo()]})
    assert r.status_code == 422
    assert client.post("/v1/ingest/workorders", json={"work_orders": []}).status_code == 422


def test_supplied_seed_resolves_eq004_and_acknowledges_open_orders(client, run_worker):
    from scripts.load_seed import batches
    for slug, body in batches():
        assert client.post(f"/v1/ingest/{slug}", json=body).status_code == 202
    run_worker()
    m = {x["machine_id"]: x for x in client.get("/v1/machines").json()["items"]}
    assert m["EQ-004"]["derived_status"] == "healthy"                  # WO-1002: power drop cleared, check passed
    for mid, wid in (("EQ-002", "WO-1001"), ("EQ-008", "WO-1003"), ("EQ-007", "WO-1004")):
        assert any(r["code"] == "WORK_ORDER_OPEN" and r["record_id"] == wid for r in m[mid]["reasons"])
    assert m["EQ-002"]["derived_status"] == "critical" and m["EQ-008"]["derived_status"] == "critical"
    assert client.get("/v1/dead-letters").json()["count"] == 0
