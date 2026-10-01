from app.config import get_settings
from app.derive import derive_machine_state

S = get_settings()
ASSET = dict(machine_id="EQ-001", plant_id="PLANT_01", line_id="LINE-A", rated_max_temp_c=85.0,
             rated_max_vibration_mm_s=9.0, baseline_power_kw=36.0, asset_status="active")


def ev(rid, t, vendor="PulseForge", kind="sensor", code="HIGH_VIBRATION", sev=2, **kw):
    base = dict(vendor=vendor, source_record_id=rid, event_time=t, kind=kind, event_code=code, severity=sev,
                vibration_mm_s=None, vibration_g=None, temperature_c=None, power_kw=None, machine_state="running",
                signal_quality="good", sensor_health=None, confidence=None, inspection_result=None,
                maintenance_status=None, days_since_service=None, note=None)
    base.update(kw)
    return base


def test_no_events_is_unknown():
    r = derive_machine_state(ASSET, [], S)
    assert r["derived_status"] == "unknown" and not r["needs_attention"]


def test_order_independent():
    a = ev("1", "2026-04-18T08:00:00Z", sev=3)
    b = ev("2", "2026-04-18T08:05:00Z", code="RECOVERY", sev=0)
    assert derive_machine_state(ASSET, [a, b], S) == derive_machine_state(ASSET, [b, a], S)


def test_newer_recovery_clears_same_vendor_alert():
    r = derive_machine_state(ASSET, [ev("1", "2026-04-18T08:00:00Z", sev=3),
                                     ev("2", "2026-04-18T08:05:00Z", code="RECOVERY", sev=0, vibration_mm_s=3.0)], S)
    assert r["derived_status"] == "healthy" and not r["needs_attention"]


def test_late_arriving_old_alert_does_not_override_newer_recovery():
    r = derive_machine_state(ASSET, [ev("2", "2026-04-18T08:05:00Z", code="RECOVERY", sev=0),
                                     ev("1", "2026-04-18T08:00:00Z", sev=3)], S)
    assert r["derived_status"] == "healthy"


def test_threshold_rule_over_rated_limit():
    r = derive_machine_state(ASSET, [ev("1", "2026-04-18T08:00:00Z", code="RECOVERY", sev=0, temperature_c=97.0)], S)
    assert "TEMP_OVER_RATED_LIMIT" in r["reason_codes"] and r["derived_status"] == "critical"


def test_cross_vendor_conflict_is_pessimistic_and_flagged():
    r = derive_machine_state(ASSET, [
        ev("1", "2026-04-18T08:39:00Z", code="RECOVERY", sev=0, temperature_c=70.0),
        ev("2", "2026-04-18T08:40:00Z", vendor="ThermexWatch", code="HIGH_TEMPERATURE", sev=4, temperature_c=93.9)], S)
    assert r["attention_level"] == 4 and "CONFLICTING_SOURCES" in r["reason_codes"]


def test_poor_signal_quality_downgrades():
    r = derive_machine_state(ASSET, [ev("1", "2026-04-18T08:00:00Z", sev=3, signal_quality="poor")], S)
    assert r["attention_level"] == 2


def test_completed_maintenance_supersedes_older_note():
    r = derive_machine_state(ASSET, [
        ev("n", "2026-04-18T08:05:00Z", vendor="MaintaFlow", kind="operator_note", code="OPERATOR_NOTE", sev=None,
           note="Machine stalled before shift start", confidence=0.7, machine_state=None),
        ev("m", "2026-04-18T08:40:00Z", vendor="MaintaFlow", kind="maintenance_update", code="MAINTENANCE_UPDATE",
           sev=None, maintenance_status="completed", note="Power module replaced", machine_state=None)], S)
    assert not r["needs_attention"] and "SUPERSEDED_BY_COMPLETED_MAINTENANCE" in {x["code"] for x in r["reasons"]}


def test_multi_source_corroboration_escalates():
    r = derive_machine_state(ASSET, [
        ev("1", "2026-04-18T08:00:00Z", sev=3),
        ev("m", "2026-04-18T08:01:00Z", vendor="MaintaFlow", kind="inspection", code="INSPECTION",
           sev=None, inspection_result="major_defect_found", machine_state=None, confidence=0.9, note="x")], S)
    assert r["attention_level"] == 4 and "MULTI_SOURCE_CORROBORATION" in r["reason_codes"]


def test_stale_vendor_signal_ignored():
    r = derive_machine_state(ASSET, [ev("1", "2026-04-18T05:00:00Z", sev=4),
                                     ev("2", "2026-04-18T08:00:00Z", vendor="ThermexWatch", code="NOMINAL", sev=0)], S)
    assert r["derived_status"] == "healthy"
