import pytest

from app.domain import normalization as n
from app.core.config import get_settings

ASSETS = {"EQ-001": dict(machine_id="EQ-001", plant_id="PLANT_01", line_id="LINE-A"),
          "EQ-008": dict(machine_id="EQ-008", plant_id="PLANT_02", line_id="LINE-E")}
S = get_settings()


def pf(**kw):
    base = dict(event_id="PF-1", machine_id="EQ-001", line_id="LINE-A", event_time="2026-04-18T08:00:00Z",
                event_type="HIGH_VIBRATION", severity="high", vibration_mm_s=9.0, temperature_c=80.0,
                machine_state="running", sensor_health=0.9, vendor_confidence=0.8)
    base.update(kw)
    return base


def test_pulseforge_happy_path():
    e = n.normalize_record("PulseForge", pf(), {"plant_id": "PLANT_01"}, ASSETS, S)
    assert (e["machine_id"], e["event_code"], e["severity"], e["warnings"]) == ("EQ-001", "HIGH_VIBRATION", 3, [])
    assert e["event_time"] == "2026-04-18T08:00:00Z"


def test_thermex_unit_and_timestamp_normalization():
    p = dict(readingId="TW-1", assetCode="EQ-001", productionLine="A", timestampMs=1776499152000,
             alertCode="TEMP_CRIT", level=5, vibration_g=0.3, temperature_f=212.0, power_kw=30.0, is_active=True,
             signal_quality="GOOD")
    e = n.normalize_record("ThermexWatch", p, {"site_code": "PLANT_01"}, ASSETS, S)
    assert e["temperature_c"] == 100.0 and e["severity"] == 4 and e["event_code"] == "HIGH_TEMPERATURE"
    assert e["event_time"] == "2026-04-18T07:59:12Z"
    assert e["vibration_mm_s"] is None and e["vibration_g"] == 0.3     # no fake unit conversion
    assert "LINE_MISMATCH_USED_REFERENCE" not in e["warnings"]         # short line "A" resolves to LINE-A


def test_maintaflow_timestamp_format_and_enums():
    p = dict(record_id="MF-1", machine_ref="eq-001", line_ref="C", recorded_at="2026/04/18 08:14:05",
             record_type="inspection", inspection_result="minor_defect_found", maintenance_status="not_due",
             days_since_last_service=18, technician_note=" belt wear ", manual_confidence="medium")
    e = n.normalize_record("MaintaFlow", p, {"factory_id": "PLANT_01"}, ASSETS, S)
    assert e["machine_id"] == "EQ-001" and e["event_time"] == "2026-04-18T08:14:05Z"
    assert e["note"] == "belt wear" and e["confidence"] == 0.7
    assert "LINE_MISMATCH_USED_REFERENCE" in e["warnings"]             # vendor said LINE-C, reference says LINE-A


@pytest.mark.parametrize("override,code", [
    (dict(machine_id=""), "MISSING_MACHINE_ID"), (dict(machine_id="EQ-999"), "UNKNOWN_MACHINE"),
    (dict(event_time="18-04-2026 08:28:20"), "INVALID_TIMESTAMP"), (dict(event_id=None), "MISSING_RECORD_ID")])
def test_essential_field_failures_reject(override, code):
    with pytest.raises(n.NormalizationError) as ei:
        n.normalize_record("PulseForge", pf(**override), {}, ASSETS, S)
    assert ei.value.code == code


def test_optional_field_garbage_is_nulled_not_rejected():
    e = n.normalize_record("PulseForge", pf(temperature_c=-999, sensor_health="bad", vendor_confidence=1.2,
                                            severity="urgent"), {}, ASSETS, S)
    assert e["temperature_c"] is None and e["sensor_health"] is None and e["confidence"] is None
    assert {"INVALID_TEMPERATURE_C", "INVALID_SENSOR_HEALTH", "INVALID_VENDOR_CONFIDENCE",
            "UNMAPPED_SEVERITY_USED_DEFAULT"} <= set(e["warnings"])


def test_unknown_alert_code_kept_as_low_signal_with_warning():
    p = dict(readingId="TW-2", assetCode="EQ-008", productionLine="ZZ", timestampMs=1776500220000,
             alertCode="UNKNOWN_ALERT", level=4, temperature_f=188.0)
    e = n.normalize_record("ThermexWatch", p, {"site_code": "PLANT_02"}, ASSETS, S)
    assert e["event_code"] == "UNKNOWN_ALERT" and "UNMAPPED_ALERT_CODE" in e["warnings"]


def test_non_object_record_rejected():
    with pytest.raises(n.NormalizationError):
        n.normalize_record("PulseForge", "oops", {}, ASSETS, S)


def test_fingerprint_ignores_record_id():
    a = n.normalize_record("PulseForge", pf(event_id="A"), {}, ASSETS, S)
    b = n.normalize_record("PulseForge", pf(event_id="B"), {}, ASSETS, S)
    assert a["fingerprint"] == b["fingerprint"]


@pytest.mark.parametrize("alert, expected", [("TEMP_CRIT", 4), ("TEMP_WARN", 2), ("OK", 0), ("SMOKE", 1)])
def test_thermex_invalid_level_falls_back_to_alert_code_severity(alert, expected):
    p = dict(readingId="TW-9", assetCode="EQ-001", productionLine="A", timestampMs=1776499152000, alertCode=alert,
             level="four", temperature_f=150.0, signal_quality="GOOD")
    e = n.normalize_record("ThermexWatch", p, {}, ASSETS, S)
    assert e["severity"] == expected and "INVALID_LEVEL_USED_DEFAULT" in e["warnings"]
