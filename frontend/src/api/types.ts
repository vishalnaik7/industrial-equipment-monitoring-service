// Mirrors backend/app/schemas/responses.py (the published contract in docs/openapi.json).

/** Ingestion sources: three external vendors plus the internal CMMS work-order feed. */
export type VendorSlug = "pulseforge" | "thermexwatch" | "maintaflow" | "workorders";
export const VENDORS: VendorSlug[] = ["pulseforge", "thermexwatch", "maintaflow", "workorders"];

export type DerivedStatus =
  | "critical" | "at_risk" | "degraded" | "watch" | "healthy" | "under_maintenance" | "offline" | "unknown";
export type ProcessingState = "pending" | "failed" | "partial" | "no_data" | "stale" | "fresh";
export type BatchState = "processing" | "completed" | "completed_with_errors" | "failed";

export interface ErrorDetail { field: string; message: string; type?: string | null }

export interface Meta {
  service: string; version: string; rules_version: string; schema_version: string;
  auth_required: boolean; vendors: VendorSlug[]; stale_after_minutes: number; max_records_per_batch: number;
}
export interface Ready { status: string; queue_depth: number; dead_letters: number }

export interface Reason {
  code: string; level: number; level_label: string; source: string;
  record_id: string | null; event_time: string | null; detail: string;
}
export interface SourceRef { source: string; record_id: string }
export interface Signal {
  source: string; record_id: string; kind: string; event_time: string; event_code: string;
  severity?: number; temperature_c?: number; vibration_mm_s?: number; vibration_g?: number; power_kw?: number;
  machine_state?: string; signal_quality?: string; inspection_result?: string; maintenance_status?: string;
  days_since_service?: number; note?: string; warnings?: string[];
}
export interface ProcessingStatus {
  status: ProcessingState; pending_records: number; failed_records: number; rejected_records: number;
  data_age_seconds: number | null; stale_after_minutes: number; last_received_at: string | null;
}
export interface MachineView {
  machine_id: string; plant_id: string; line_id: string; machine_type: string | null; criticality: string | null;
  derived_status: DerivedStatus; needs_attention: boolean; attention_level: number; attention_label: string;
  reason_codes: string[]; reasons: Reason[]; latest_signals: Signal[]; machine_state: string | null;
  latest_relevant_event_time: string | null; source_event_refs: SourceRef[]; last_processed_at: string | null;
  state_version: number; rules_version: string | null; processing_status: ProcessingStatus;
}
export interface MachineList { count: number; items: MachineView[] }
export interface MachineFilters {
  plant_id?: string; line_id?: string; status?: DerivedStatus | ""; needs_attention?: boolean;
}

export interface LineSummary {
  plant_id: string; line_id: string; line_name: string | null; machines_total: number;
  machines_needing_attention: number; critical_machines: number; by_status: Record<string, number>;
}
export interface MachineBrief {
  machine_id: string; plant_id: string; line_id: string; attention_level: number; reason_codes: string[];
  latest_relevant_event_time: string | null;
}
export interface DataQuality {
  stale_machines: string[]; failed_machines: string[]; partial_machines: string[];
  pending_machines: string[]; no_data_machines: string[];
}
export interface Summary {
  scope: Record<string, unknown>; generated_at: string; machines_total: number; machines_needing_attention: number;
  by_status: Record<DerivedStatus, number>; lines_ranked_by_attention: LineSummary[];
  critical_machines: MachineBrief[]; data_quality: DataQuality;
}

export interface LineRef { line_id: string; line_name: string | null; operating_window: string | null; machine_ids: string[] }
export interface PlantRef { plant_id: string; lines: LineRef[] }
export interface ReferenceData { plants: PlantRef[] }

export interface BatchAccepted {
  batch_id: string; vendor: string; schema_version: string; received_at: string; total_records: number;
  accepted_records: number; duplicate_records: number; revised_records: number; replayed: boolean; status_url: string;
}
export interface RecordError {
  raw_id: number; vendor_record_id: string; status: string; error_code: string | null;
  error_message: string | null; attempts: number;
}
export interface BatchListItem {
  batch_id: string; vendor: string; received_at: string; total_records: number; accepted_records: number;
  duplicate_records: number; revised_records: number; state: BatchState;
}
export interface BatchStatus extends BatchListItem {
  schema_version: string; record_status_counts: Record<string, number>; errors: RecordError[];
}
export interface BatchList { count: number; items: BatchListItem[] }

export interface RecordValidation {
  index: number; record_id: string | null; outcome: "valid" | "valid_with_warnings" | "rejected";
  store_action: "new" | "duplicate" | "revision" | "duplicate_in_batch" | "revision_in_batch";
  machine_id?: string | null; event_code?: string | null; event_time?: string | null; warnings: string[];
  error_code?: string | null; error_message?: string | null;
}
export interface ValidationReport {
  vendor: string; total_records: number; valid: number; valid_with_warnings: number; rejected: number;
  records: RecordValidation[];
}

export interface DeadLetter {
  raw_id: number; batch_id: string; vendor: string; vendor_record_id: string; revision: number;
  status: "rejected" | "dead"; error_code: string | null; error_message: string | null; attempts: number;
  received_at: string; processed_at: string | null; payload: unknown;
}
export interface DeadLetterList { count: number; items: DeadLetter[] }

export interface RawRevision {
  /** is_latest = highest revision received; authoritative = the revision the canonical event was built from. */
  raw_id: number; batch_id: string; revision: number; is_latest: number; authoritative: boolean;
  source_version: string | null; status: string; error_code: string | null;
  error_message: string | null; attempts: number; received_at: string; processed_at: string | null; payload: unknown;
}
export interface CanonicalEvent {
  machine_id: string; plant_id: string; line_id: string; event_code: string; event_time: string; kind: string;
  severity: number | null; warnings: string[]; [key: string]: unknown;
}
export interface RecordTrace { vendor: string; record_id: string; raw_revisions: RawRevision[]; normalized_event: CanonicalEvent | null }

export interface SampleList { items: { name: string; vendors: VendorSlug[] }[] }
export interface Sample { name: string; bodies: Partial<Record<VendorSlug, Record<string, unknown>>> }
