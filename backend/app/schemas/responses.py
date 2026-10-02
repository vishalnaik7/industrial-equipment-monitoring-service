"""Response models: the public API contract (rendered into OpenAPI and docs/openapi.json)."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---- enums -------------------------------------------------------------------------------------------------
class DerivedStatus(str, Enum):
    critical = "critical"
    at_risk = "at_risk"
    degraded = "degraded"
    watch = "watch"
    healthy = "healthy"
    under_maintenance = "under_maintenance"
    offline = "offline"
    unknown = "unknown"


class VendorSlug(str, Enum):
    pulseforge = "pulseforge"
    thermexwatch = "thermexwatch"
    maintaflow = "maintaflow"
    workorders = "workorders"


ProcessingState = Literal["pending", "failed", "partial", "no_data", "stale", "fresh"]
BatchState = Literal["processing", "completed", "completed_with_errors", "failed"]


# ---- errors ------------------------------------------------------------------------------------------------
class ErrorDetail(BaseModel):
    field: str
    message: str
    type: str | None = None


class ErrorInfo(BaseModel):
    code: str = Field(examples=["VALIDATION_ERROR"])
    message: str
    details: list[ErrorDetail] | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorInfo


ERRORS = {s: {"model": ErrorResponse} for s in (400, 401, 404, 413, 422)}


# ---- ingestion / processing ------------------------------------------------------------------------------
class BatchAccepted(BaseModel):
    batch_id: str
    vendor: str
    schema_version: str
    received_at: str
    total_records: int
    accepted_records: int = Field(description="Stored and queued for async processing")
    duplicate_records: int = Field(description="Same id + identical payload as an already stored record")
    revised_records: int = Field(description="Same id, different payload -> new revision (latest wins)")
    replayed: bool = Field(description="True when the identical request was already received (HTTP 200)")
    status_url: str


class RecordError(BaseModel):
    raw_id: int
    vendor_record_id: str
    status: str
    error_code: str | None
    error_message: str | None
    attempts: int


class BatchStatus(BaseModel):
    batch_id: str
    vendor: str
    schema_version: str
    received_at: str
    total_records: int
    accepted_records: int
    duplicate_records: int
    revised_records: int
    state: BatchState
    record_status_counts: dict[str, int]
    errors: list[RecordError]


class BatchListItem(BaseModel):
    batch_id: str
    vendor: str
    received_at: str
    total_records: int
    accepted_records: int
    duplicate_records: int
    revised_records: int
    state: BatchState


class BatchList(BaseModel):
    count: int
    items: list[BatchListItem]


class RecordValidation(BaseModel):
    index: int
    record_id: str | None
    outcome: Literal["valid", "valid_with_warnings", "rejected"]
    store_action: Literal["new", "duplicate", "revision", "duplicate_in_batch", "revision_in_batch"]
    machine_id: str | None = None
    event_code: str | None = None
    event_time: str | None = None
    warnings: list[str] = []
    error_code: str | None = None
    error_message: str | None = None


class ValidationReport(BaseModel):
    vendor: str
    total_records: int
    valid: int
    valid_with_warnings: int
    rejected: int
    records: list[RecordValidation]


class DeadLetter(BaseModel):
    raw_id: int
    batch_id: str
    vendor: str
    vendor_record_id: str
    revision: int
    status: Literal["rejected", "dead"]
    error_code: str | None
    error_message: str | None
    attempts: int
    received_at: str
    processed_at: str | None
    payload: Any


class DeadLetterList(BaseModel):
    count: int
    items: list[DeadLetter]


class ReplayAccepted(BaseModel):
    raw_id: int
    status: Literal["pending"]


class RecordTrace(BaseModel):
    vendor: str
    record_id: str
    raw_revisions: list[dict[str, Any]]
    normalized_event: dict[str, Any] | None


# ---- operational view --------------------------------------------------------------------------------------
class Reason(BaseModel):
    code: str = Field(examples=["TEMP_OVER_RATED_LIMIT"])
    level: int = Field(ge=0, le=4)
    level_label: str
    source: str = Field(description="Vendor name, 'system' (cross-source rule) or 'reference'")
    record_id: str | None
    event_time: str | None
    detail: str


class SourceRef(BaseModel):
    source: str
    record_id: str


class ProcessingStatus(BaseModel):
    status: ProcessingState
    pending_records: int
    failed_records: int
    rejected_records: int
    data_age_seconds: int | None = Field(description="Age of the newest event (event time), at read time")
    stale_after_minutes: int
    last_received_at: str | None = Field(None, description="When data for this machine last arrived (receive time)")


class MachineView(BaseModel):
    machine_id: str
    plant_id: str
    line_id: str
    machine_type: str | None
    criticality: str | None
    derived_status: DerivedStatus
    needs_attention: bool
    attention_level: int = Field(ge=0, le=4, description="0 none .. 4 critical")
    attention_label: str
    reason_codes: list[str] = Field(description="Codes with level >= 1, most severe first")
    reasons: list[Reason] = Field(description="Full explanation, including informational (level 0) reasons")
    latest_signals: list[dict[str, Any]] = Field(description="Newest relevant reading per source")
    machine_state: str | None
    latest_relevant_event_time: str | None
    source_event_refs: list[SourceRef]
    last_processed_at: str | None
    state_version: int = Field(description="Increments only when the derived content changes")
    rules_version: str | None
    processing_status: ProcessingStatus


class MachineList(BaseModel):
    count: int
    items: list[MachineView]


class LineSummary(BaseModel):
    plant_id: str
    line_id: str
    line_name: str | None
    machines_total: int
    machines_needing_attention: int
    critical_machines: int
    by_status: dict[str, int]


class MachineBrief(BaseModel):
    machine_id: str
    plant_id: str
    line_id: str
    attention_level: int
    reason_codes: list[str]
    latest_relevant_event_time: str | None


class DataQuality(BaseModel):
    stale_machines: list[str]
    failed_machines: list[str]
    partial_machines: list[str]
    pending_machines: list[str]
    no_data_machines: list[str]


class Summary(BaseModel):
    scope: dict[str, Any]
    generated_at: str
    machines_total: int
    machines_needing_attention: int
    by_status: dict[str, int]
    lines_ranked_by_attention: list[LineSummary]
    critical_machines: list[MachineBrief]
    data_quality: DataQuality


# ---- reference / meta --------------------------------------------------------------------------------------
class LineRef(BaseModel):
    line_id: str
    line_name: str | None
    operating_window: str | None
    machine_ids: list[str]


class PlantRef(BaseModel):
    plant_id: str
    lines: list[LineRef]


class ReferenceData(BaseModel):
    plants: list[PlantRef]


class Meta(BaseModel):
    service: str
    version: str
    rules_version: str
    schema_version: str
    auth_required: bool
    vendors: list[str]
    stale_after_minutes: int
    max_records_per_batch: int
