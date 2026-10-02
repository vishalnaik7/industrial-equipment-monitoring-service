"""Request schemas for vendor batches.

Two-tier validation (deliberate):
  * Tier 1 - ENVELOPE (here, synchronous, 4xx): body is a JSON object, the record list exists, is non-empty and
    contains objects, the vendor named in the body matches the endpoint, envelope metadata has sane types.
    A request failing tier 1 is rejected whole; nothing is stored.
  * Tier 2 - RECORD CONTENT (worker, asynchronous): field values are NOT type-checked here on purpose. Vendors
    send `"-999"`, `"bad"`, `"four"`; the normalizer decides per record between "keep + warning" and "reject to
    dead-letter". Rejecting a whole batch for one bad sensor value would lose good data.
    `POST /v1/ingest/{vendor}/validate` runs tier 2 synchronously as a dry run.
"""
from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

_LENIENT = ConfigDict(extra="allow")


def _f(desc: str, example: Any = None):
    return Field(None, description=desc, examples=[example] if example is not None else None)


# ---- record shapes (documentation + "must be an object"; values are validated by the normalizer) ---------
class PulseForgeEvent(BaseModel):
    model_config = _LENIENT
    event_id: Any = _f("Vendor event id - idempotency key (required)", "PF-1001")
    machine_id: Any = _f("Machine reference, e.g. EQ-001 / eq_1 (required)", "EQ-001")
    line_id: Any = _f("Line reported by vendor; asset reference wins on mismatch", "LINE-A")
    event_time: Any = _f("ISO-8601; zone-less is assumed UTC (required)", "2026-04-18T07:59:12Z")
    event_type: Any = _f("HIGH_VIBRATION | TEMP_SPIKE | SENSOR_HEALTH_DROP | POWER_FLUCTUATION | RECOVERY_SIGNAL",
                         "HIGH_VIBRATION")
    severity: Any = _f("low | medium | high | critical", "high")
    vibration_mm_s: Any = _f("Vibration velocity, mm/s", 11.8)
    temperature_c: Any = _f("Temperature, Celsius", 83.2)
    machine_state: Any = _f("running | idle | stopped | fault", "running")
    sensor_health: Any = _f("0..1; below 0.6 is treated as poor signal quality", 0.91)
    vendor_confidence: Any = _f("0..1", 0.87)


class ThermexWatchReading(BaseModel):
    model_config = _LENIENT
    readingId: Any = _f("Vendor reading id - idempotency key (required)", "TW-8801")
    assetCode: Any = _f("Machine reference (required)", "EQ-001")
    productionLine: Any = _f("Short line code, e.g. A -> LINE-A", "A")
    timestampMs: Any = _f("Epoch milliseconds (epoch seconds tolerated with a warning) (required)", 1776499152000)
    alertCode: Any = _f("VIB_WARN | TEMP_WARN | TEMP_CRIT | POWER_DROP | OK", "VIB_WARN")
    level: Any = _f("1 (nominal) .. 5 (critical)", 4)
    vibration_g: Any = _f("Vibration acceleration, g (not converted to mm/s)", 0.81)
    temperature_f: Any = _f("Temperature, Fahrenheit (converted to Celsius)", 181.2)
    power_kw: Any = _f("Power draw, kW", 37.8)
    is_active: Any = _f("true -> running, false -> idle", True)
    signal_quality: Any = _f("GOOD | FAIR | POOR", "GOOD")


class MaintaFlowRecord(BaseModel):
    model_config = _LENIENT
    record_id: Any = _f("Vendor record id - idempotency key (required)", "MF-501")
    machine_ref: Any = _f("Machine reference (required)", "EQ-001")
    line_ref: Any = _f("Line reference", "LINE-A")
    recorded_at: Any = _f("'YYYY/MM/DD HH:MM:SS' in MAINTAFLOW_TZ, or ISO-8601 (required)", "2026/04/18 08:14:05")
    record_type: Any = _f("inspection | maintenance_update | operator_note | calibration (required)", "inspection")
    inspection_result: Any = _f("passed | passed_with_observation | minor_defect_found | major_defect_found | "
                                "calibration_pending", "minor_defect_found")
    maintenance_status: Any = _f("not_due | due_soon | overdue | completed | unknown", "not_due")
    days_since_last_service: Any = _f("Non-negative integer", 18)
    technician_note: Any = _f("Free text (keywords such as 'leak', 'stall' raise attention)", "Belt wear visible")
    manual_confidence: Any = _f("low | medium | high", "medium")


class WorkOrder(BaseModel):
    model_config = _LENIENT
    work_order_id: Any = _f("Work order id - idempotency key; a status change is a new revision (required)", "WO-1001")
    plant_id: Any = _f("Plant reported by the CMMS; asset reference wins on mismatch", "PLANT_01")
    machine_id: Any = _f("Machine reference (required)", "EQ-002")
    line_id: Any = _f("Line reference", "LINE-A")
    opened_at: Any = _f("ISO-8601 (required)", "2026-04-18T08:07:00Z")
    closed_at: Any = _f("ISO-8601; empty while open", "2026-04-18T08:10:00Z")
    maintenance_type: Any = _f("e.g. cooling_system, power_check", "cooling_system")
    trigger_source: Any = _f("e.g. sensor_alert, operator_report, inspection", "sensor_alert")
    status: Any = _f("open | in_progress | closed | cancelled", "open")
    downtime_minutes: Any = _f("Non-negative number", 0)
    post_maintenance_check: Any = _f("passed | failed | pending", "pending")
    notes: Any = _f("Free text", "Work order created after temperature spike")


# ---- envelopes ---------------------------------------------------------------------------------------------
class _Envelope(BaseModel):
    model_config = _LENIENT
    vendor_name: ClassVar[str] = ""
    name_field: ClassVar[str] = ""

    @model_validator(mode="after")
    def _vendor_matches_endpoint(self):
        claimed = getattr(self, self.name_field, None)
        if claimed is not None and str(claimed).strip().lower() != self.vendor_name.lower():
            raise ValueError(f"{self.name_field}={claimed!r} does not match this endpoint ({self.vendor_name})")
        return self


class PulseForgeBatch(_Envelope):
    vendor_name, name_field = "PulseForge", "vendor"
    vendor: str | None = Field(None, max_length=64, examples=["PulseForge"])
    plant_id: str | None = Field(None, max_length=64, examples=["PLANT_01"])
    batch_generated_at: str | None = Field(None, max_length=64, examples=["2026-04-18T08:05:00Z"])
    events: list[PulseForgeEvent] = Field(..., min_length=1, description="Non-empty list of event objects")


class ThermexWatchBatch(_Envelope):
    vendor_name, name_field = "ThermexWatch", "source"
    source: str | None = Field(None, max_length=64, examples=["ThermexWatch"])
    site_code: str | None = Field(None, max_length=64, examples=["PLANT_01"])
    response_time_epoch_ms: int | None = Field(None, ge=0, examples=[1776499560000])
    readings: list[ThermexWatchReading] = Field(..., min_length=1, description="Non-empty list of reading objects")


class MaintaFlowBatch(_Envelope):
    vendor_name, name_field = "MaintaFlow", "provider_name"
    provider_name: str | None = Field(None, max_length=64, examples=["MaintaFlow"])
    factory_id: str | None = Field(None, max_length=64, examples=["PLANT_01"])
    records: list[MaintaFlowRecord] = Field(..., min_length=1, description="Non-empty list of record objects")


class WorkOrderBatch(_Envelope):
    vendor_name, name_field = "CMMS", "source"
    source: str | None = Field(None, max_length=64, examples=["CMMS"])
    plant_id: str | None = Field(None, max_length=64, examples=["PLANT_01"])
    work_orders: list[WorkOrder] = Field(..., min_length=1, description="Non-empty list of work order objects")


BATCH_MODELS: dict[str, type[_Envelope]] = {
    "pulseforge": PulseForgeBatch, "thermexwatch": ThermexWatchBatch, "maintaflow": MaintaFlowBatch,
    "workorders": WorkOrderBatch}
