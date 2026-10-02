# Requirements coverage

Each requirement from the candidate brief and the asset package, mapped to the code that implements it and the test that
proves it. Paths are relative to `backend/` unless they start with `frontend/` or `docs/`.
Status: **Implemented** = built and covered by a passing test; **Partial** = built, with the gap stated.

## Supplied assets

| Asset | Used by | Status |
|---|---|---|
| `asset_reference.csv`, `line_reference.csv` | `data/reference/` (byte-identical) → `app/db/reference.py` `load_reference` (authoritative plant/line/rated limits) | Implemented |
| `vendor_api_samples/*.json` (9 files) | `sample_payloads/` (byte-identical) → `tests/test_api_flow.py`, `tests/test_validation.py`, `/v1/samples` | Implemented |
| `seed_data/pulseforge_events.csv`, `thermexwatch_readings.csv`, `maintaflow_records.csv` | `data/seed/` (byte-identical) → `scripts/load_seed.py` `batches()` | Implemented |
| `seed_data/maintenance_work_orders.csv` | `data/seed/` (byte-identical) → `/v1/ingest/workorders`, rule 8 | Implemented |
| `seed_data/asset_reference.csv`, `line_reference.csv` | identical to the top-level copies; the top-level ones are loaded | Implemented |
| `event_type_reference.md` | `app/domain/normalization/vocabulary.py` (`PF_EVENT_MAP`, `TW_ALERT_MAP`, `TW_LEVEL_SEVERITY`, `MF_RECORD_KINDS`) | Implemented |
| `expected_output_context.md` | `app/schemas/responses.py` `MachineView`, `Summary` | Implemented |

## A. Ingestion layer

| Requirement | Code | Proof | Status |
|---|---|---|---|
| At least two vendor formats | Three vendors + CMMS: `app/api/routes/ingestion.py`, `app/domain/vendors.py` | `test_happy_path_ingest_is_async_then_visible`, `test_plant_and_line_summary` | Implemented |
| Request validation | `app/schemas/vendors.py` (envelopes), `app/main.py` (media type, body size), `app/api/deps.py` (params) | `test_envelope_validation_reports_field_paths`, `test_query_and_path_parameter_validation` | Implemented |
| Malformed inputs | Envelope → 4xx with field paths (`app/core/errors.py`); record → worker reject (`app/domain/normalization/common.py`) | `test_body_must_be_json`, `test_malformed_records_are_rejected_not_fatal` | Implemented |
| Duplicate handling | `app/services/ingestion.py` (request hash, record hash, revisions); `app/worker/runner.py` (fingerprint) | `test_duplicates_within_batch_and_request_replay`, `test_same_record_from_different_json_serializers_is_a_duplicate` | Implemented |
| Versioning assumptions | `/v1` path prefix, `schema_version` on every batch (`app/domain/vendors.py`); assumption documented in the README | `test_batches_list_and_meta` | Implemented |
| Authentication | `app/core/security.py` (`X-API-Key`, constant-time compare) | `test_api_key_auth` | Implemented |

## B. Normalization layer

| Requirement | Code | Proof | Status |
|---|---|---|---|
| Field mapping to a canonical schema | `app/domain/normalization/{pulseforge,thermexwatch,maintaflow,workorders}.py`, `common.make_event` | `test_pulseforge_happy_path`, `test_normalizes_supplied_work_orders` | Implemented |
| Machine / entity resolution | `common.canon_machine_id`, `resolve_asset` (`EQ-1`/`eq_001` → `EQ-001`; line `C` → `LINE-C`) | `test_maintaflow_timestamp_format_and_enums` | Implemented |
| Enum normalization | `vocabulary.py` (severity, alert codes, levels, states, record types, work-order status) | `test_thermex_unit_and_timestamp_normalization`, `test_unknown_alert_code_kept_as_low_signal_with_warning` | Implemented |
| Timestamp normalization | ISO-8601, epoch ms (seconds tolerated), `YYYY/MM/DD HH:MM:SS` + `MAINTAFLOW_TZ` → UTC (`app/core/timeutil.py`) | `test_thermex_unit_and_timestamp_normalization`, `test_maintaflow_timestamp_format_and_enums` | Implemented |
| Unit normalization | °F → °C; vibration in g deliberately not converted to mm/s (needs frequency) | `test_thermex_unit_and_timestamp_normalization` | Implemented |
| Missing / invalid values | Essential → reject with code; optional → null + warning (`common.num`, plausibility `RANGES`) | `test_essential_field_failures_reject`, `test_optional_field_garbage_is_nulled_not_rejected`, `test_bad_optional_fields_become_warnings` | Implemented |
| Raw payload traceability | Byte-exact body per batch (`ingestion_batches.raw_body`, `GET /v1/batches/{id}/raw`); per-record payload + revisions (`GET /v1/records/...`) | `test_vendor_payload_is_preserved_unchanged` | Implemented |
| Idempotency assumptions | Design note "Idempotency summary"; canonical hashing in `app/services/ingestion.py` | `test_same_id_new_payload_creates_revision_latest_wins` | Implemented |
| Duplicate fingerprint scope (distinct observations kept) | `app/domain/normalization/common.py` `FINGERPRINT_FIELDS` | `test_distinct_observation_under_new_id_is_not_a_duplicate`, `test_171_and_171_point_0_are_the_same_record` | Implemented |
| Future-timestamp policy | `app/domain/normalization/__init__.py` (`FUTURE_TIMESTAMP`) | `test_future_timestamp_policy`, `test_future_timestamp_is_rejected_end_to_end` | Implemented |
| Revision ordering (a delayed older revision never replaces a newer one) | `app/worker/runner.py` `revision_key`, `_stale_against_authoritative`; vendor version in `app/domain/vendors.py`, `app/services/ingestion.py` `source_version` | `tests/test_revisions.py` (7 tests: vendor version, epoch-ms version, no-version fallback, both arrival orders, backward time correction, separate events, pending counts) | Implemented |

## C. Async / background processing

| Requirement | Code | Proof | Status |
|---|---|---|---|
| Asynchronous normalization | `app/worker/runner.py` (`claim` → `process_record`); API returns 202 before processing | `test_happy_path_ingest_is_async_then_visible` | Implemented |
| State recomputation | `dirty_machines` marker → `recompute_machine` (pure `derive_machine_state`) | `test_recompute_failure_is_retried_via_dirty_marker` | Implemented |
| Lease fencing and concurrent workers | `app/worker/runner.py` (`lease_token`, `_owns`, recompute in one transaction) | `test_worker_that_lost_its_lease_cannot_overwrite_the_result`, `test_concurrent_workers_process_each_record_exactly_once` | Implemented |
| Retryable processing | Exponential backoff, `MAX_ATTEMPTS`, lease expiry for crashed workers | `test_transient_failure_retries_with_backoff_then_dead_letters`, `test_crashed_worker_lease_expires_and_record_is_picked_up` | Implemented |
| Replay after a reference fix | `POST /v1/dead-letters/{id}/replay` after `load_reference` | `test_replay_after_the_asset_reference_is_fixed` | Implemented |
| Dead-letter / error handling | `rejected` / `dead` statuses, `GET /v1/dead-letters`, replay endpoint | `test_unknown_machine_goes_to_dead_letter_and_can_be_replayed`, `test_corrected_resend_of_a_rejected_record_clears_the_dead_letter` | Implemented |
| Status tracking for accepted payloads | `GET /v1/batches/{id}` (`app/services/ingestion.py` `batch_status`) | `test_retry_cases_partial_failure` | Implemented |

## D. Output serving layer

| Requirement | Code | Proof | Status |
|---|---|---|---|
| Ingestion / processing status API | `GET /v1/batches`, `/v1/batches/{id}`, `/ready` | `test_batches_list_and_meta` | Implemented |
| Machine operational view API | `GET /v1/machines`, `/v1/machines/{id}` (`app/services/views.py`) | `test_machine_filters_and_no_data_machine` | Implemented |
| Plant / line summary API | `GET /v1/summary`, `/v1/summary/plants/{id}` | `test_plant_and_line_summary` | Implemented |
| Explainable output | Every reason has code, level, source, record id, event time, detail (`app/domain/rules.py`) | `test_conflicting_updates_across_vendors` | Implemented |
| Work-order resolution scope (maintenance_type + trigger; reopened; failed / pending checks) | `app/domain/rules.py` rule 8, `vocabulary.WO_TYPE_SCOPE` | `test_power_check_clears_only_power_findings`, `test_reopened_work_order`, `test_failed_check_is_settled_only_by…`, `tests/test_work_orders.py` | Implemented |
| Status-rule boundaries, equal timestamps, corroboration, asset maintenance | `app/domain/rules.py` | `test_*_threshold_boundaries`, `test_equal_time_readings_…`, `test_one_source_…`, `test_asset_under_maintenance_…` | Implemented |
| Sensor relevance vs unresolved findings | `app/domain/rules.py` (rules 2 and 5, v1.2); design note "Freshness: two policies" | `tests/test_freshness.py` (8 tests, incl. the Docker EQ-001 scenario) | Implemented |

## Derived output (brief section 4, `expected_output_context.md`)

| Field / question | Where | Status |
|---|---|---|
| `derived_status`, `needs_attention`, `attention_level` (+ label) | `MachineView` | Implemented |
| `reason_codes` (+ full `reasons`) | `MachineView` | Implemented |
| Latest relevant processed signals | `latest_signals` (newest per source + maintenance context + work orders) | Implemented |
| `latest_relevant_event_time`, `last_processed_at`, `processing_status` (fresh/stale/pending/partial/failed/no_data) | `MachineView` | Implemented |
| `source_event_refs` | `MachineView` | Implemented |
| Machines per status; lines with most machines needing attention; critical machines; stale/failed/partial machines | `Summary.by_status`, `lines_ranked_by_attention`, `critical_machines`, `data_quality` | Implemented |

## Messy data (brief section 8)

| Requirement | Proof | Status |
|---|---|---|
| Not clean | `malformed.json` → `test_malformed_records_are_rejected_not_fatal` | Implemented |
| Not unique | `duplicates.json` → `test_duplicates_within_batch_and_request_replay` | Implemented |
| Not ordered | `out_of_order.json` → `test_out_of_order_delivery_does_not_regress_state`, `test_separate_out_of_order_events_are_all_kept`; revisions → `test_delayed_older_vendor_version_cannot_replace_newer_revision`, `test_out_of_order_work_order_states_never_reopen` | Implemented |
| Not complete | `retry_cases.json` → `test_retry_cases_partial_failure`, `test_corrected_resend_of_a_rejected_record_clears_the_dead_letter` | Implemented |
| Conflicting updates | `conflicting_updates.json` → `test_conflicting_updates_across_vendors`, `test_cross_vendor_conflict_is_pessimistic_and_flagged` | Implemented |

## Deliverables (brief section 6)

| Deliverable | Where | Status |
|---|---|---|
| Source code | `backend/`, `frontend/` | Implemented |
| README (setup, run, test, architecture, assumptions, trade-offs, limitations, next steps) | `README.md` | Implemented |
| API contract | `docs/openapi.json` (generated by `scripts/export_openapi.py`), live `/docs` | Implemented |
| Tests | 153 backend (pytest) + 16 frontend (Vitest) + strict TypeScript check; `scripts/verify_scenarios.py` end-to-end (55 checks against a running server) | Implemented |
| Local run setup | `docker-compose.yml` (api, worker, frontend) + Makefile + PowerShell steps in README and `docs/project-guide.pdf` | Implemented - owner's stack built and ran (v1.1); isolated stack for v1.5.0 passed `verify_scenarios.py` 55/55 with processing in the worker container, nginx routing, persistence across restarts, no dev dependencies in the runtime image. |
| Visible commit history | git history | Reviewer to judge |
| Architecture diagram | `docs/architecture.md` (Mermaid) | Implemented |
| Design note | `docs/design-note.md` | Implemented |
| AI usage disclosure | `README.md` | Implemented |
