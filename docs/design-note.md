# Design note

## Goals
A reviewable first version that is honest about messy vendor data: never lose a payload, never let arrival order
decide truth, and make every derived answer explain itself.

## Key decisions and why
| Decision | Why | Trade-off |
|---|---|---|
| **Raw landing zone first** (`raw_records` per record + `ingestion_batches.raw_body` per request, append-only) | Replay, audit and "why did the machine say X" start from what was received: the request body is kept byte-for-byte, each record with every field and value as sent. Normalizer bugs are fixable by replay. | Extra storage; the per-record copy is canonical JSON (same values, key order/whitespace not kept - the byte-exact copy is the batch body). |
| **Work orders through the same pipeline** (`/v1/ingest/workorders`, source `CMMS`) | The supplied `maintenance_work_orders.csv` changes the answer (WO-1002 clears EQ-004's power alerts). Reusing ingest → normalize → derive gives it validation, idempotency, revisions and traceability for free. | A CMMS would normally push events; here it is loaded like a vendor. |
| **Table as queue** (leases + `next_attempt_at`) instead of Redis/Kafka | One moving part, transactional with the data, trivially testable, safe with N workers. | Polling latency (~1 s); SQLite single-writer ceiling. Swap for Postgres `SKIP LOCKED` / SQS when scaling. |
| **SQLite (WAL)** | Zero-setup local run; api + worker processes can share the file. | Not for multi-host. Schema is plain SQL → Postgres port is mechanical. |
| **Recompute-from-events** derivation (pure function) | Idempotent, order-independent, replay-safe; no incremental-state bugs. | O(events per machine) per recompute; fine here, snapshot/window it for volume. |
| **Dirty-machine marker** | Recompute is retried if it fails or the worker dies between "event saved" and "state saved". | One more table. |
| **Reject vs warn policy** | Essential identity/time fields invalid ⇒ reject (dead-letter, replayable). Optional measurements invalid ⇒ null + warning code. One bad sensor value shouldn't hide a real alert. | A bad *value* can hide a threshold breach; surfaced via warnings. |
| **Asset reference is authoritative** for plant/line | Vendors disagree (`C`, `LINE-C`, `ZZ`). Disagreement kept as a warning. | A machine genuinely moved lines needs reference updated first. |
| **Ordered revisions, not "latest received wins"** for same vendor id with different payload | Vendors correct themselves, but networks also deliver late. All revisions are kept and processed; the authoritative one is the greatest `(vendor batch version, record event time, arrival order)`, so a delayed older revision is recorded as `STALE_REVISION` instead of replacing a newer one. | No supplied vendor sends a per-record version; the batch timestamp stands in for it. Without one (MaintaFlow, CMMS) a correction that moves the record time backwards is treated as stale. |
| **Scoped work-order resolution** (rules 1.3) | A verified repair proves the repaired subsystem works, not the whole machine: `power_check` clears power alerts, not vibration, temperature or inspection findings. Scope = maintenance type + trigger; unknown types clear nothing. | The type → scope map is an assumption; a CMMS with richer data (affected components) would replace it. |
| **Lease fencing** in the worker | Each claim gets a token; outcomes are written only while the token is still current, so a stalled worker cannot overwrite the result of the worker that took over. Recompute reads and writes in one transaction. | A stalled worker may repeat normalization once (no harm, nothing written). |
| **Future timestamps rejected** | A reading dated after its arrival (beyond `FUTURE_TOLERANCE_MINUTES`) would stay "newest" and mask real data until that time. | Requires sane server clocks; tolerance is configurable. |
| **Pessimistic conflict resolution** | Missing a real fault costs more than a false alert. Conflict is flagged `CONFLICTING_SOURCES`. | More noise when one vendor lags. |
| **No g → mm/s conversion** | Acceleration vs velocity needs frequency content; a fake conversion would be wrong. ThermexWatch vibration is stored in g and not used for rated-limit rules. | Less vibration coverage from that vendor. |

## Derivation rules (v1.3, `backend/app/domain/rules.py`)
Per sensor vendor take the newest reading by *event* time; ignore it (`STALE_SIGNAL_IGNORED`) if it is more than 60 minutes
older than the machine's newest *sensor* reading from any vendor.
Reasons come from the vendor alert and from thresholds vs the asset's rated limits (temp ≥95% near / >100% over / ≥110% critical,
vibration ≥90% / >100% / ≥125%, power ±25% / ±40% from baseline). Poor signal quality or low manual confidence downgrades one level.
Maintenance: major defect (3), minor/observation (1), overdue (2), due soon (1), calibration pending (1), operator-note keywords;
a later `completed` maintenance record supersedes earlier inspection/notes. Level = max reason; +1 if two independent
sources both reach ≥3. Status = healthy / watch / degraded / at_risk / critical (or under_maintenance / offline from the asset reference).
**Rule 8 - work orders (scoped since 1.3):** a work order closed with a *passed* post-maintenance check resolves the sensor
alerts and maintenance findings **in its scope** dated at or before its close - kept in `reasons` at level 0 with the work
order named (`RESOLVED_BY_WORK_ORDER` for findings). Scope = categories of its `maintenance_type` plus its trigger:

| maintenance_type | resolves | trigger_source | also resolves |
|---|---|---|---|
| `power_check` | power (`POWER_ANOMALY`, `POWER_DEVIATION`) | `inspection` | inspection findings |
| `cooling_system`, `heat_shield` | temperature | `operator_report` | operator-reported issues |
| `sensor_recalibration` | sensor degradation, pending calibration | `scheduled_service` | service overdue / due soon |
| `vibration_check`, `bearing_replacement`, `belt_replacement` | vibration | `sensor_alert` | nothing extra |
| `general_service` | every category | | |
| anything else | nothing (`UNMAPPED_MAINTENANCE_TYPE`) | | |

So an older repair never clears a newer fault, and a power repair never clears a vibration alert. Alerts after the close still count. An open work order is reported as
`WORK_ORDER_OPEN` (acknowledged, also for `reopened`) but does not lower attention. Closed with a failed check → `POST_MAINTENANCE_CHECK_FAILED` (2);
closed with the check still pending → `POST_MAINTENANCE_CHECK_PENDING` (1). A late-arriving older state of the same work order
cannot reopen it (`STALE_REVISION`).
`needs_attention` ⇔ level ≥ 2. **All thresholds are assumptions** I picked for explainability; they live in one file and are versioned (`rules_version`).

## Reject vs warn (normalization)
| Rejected (dead letter, replayable) | Corrected or nulled with a warning (record kept) |
|---|---|
| missing record id; missing / empty / unknown machine; unparseable or missing essential timestamp; unsupported MaintaFlow record type; event time more than `FUTURE_TOLERANCE_MINUTES` after receipt (`FUTURE_TIMESTAMP`); non-object record | wrong plant or line (reference wins: `PLANT_MISMATCH_USED_REFERENCE`, `LINE_MISMATCH_USED_REFERENCE`, `LINE_MISSING_USED_REFERENCE`); out-of-range / non-finite numbers (`INVALID_*`); unknown booleans (`INVALID_IS_ACTIVE`); unknown enums (`UNMAPPED_*`); alert code vs level conflict (`ALERT_LEVEL_CONFLICT`, more severe wins); naive timestamps (`TIMESTAMP_ASSUMED_UTC`); epoch seconds (`TIMESTAMP_SECONDS_CONVERTED`) |

Warnings are stored on the canonical event and exposed by the trace, the dry run and each signal in the machine view.

## Freshness: two policies (v1.2)
| | Sensor readings | Maintenance findings |
|---|---|---|
| What | Vendor sensor events / readings | Inspection defects and observations, pending calibration, operator-reported issues, service status |
| Nature | A measurement at one instant | A condition that stays true until someone deals with it |
| Expires by age? | **Yes**: ignored when > 60 min older than the machine's newest sensor reading | **No**: kept however old; the reason states "unresolved since …" and its age |
| Replaced by | A newer reading from the same vendor | A newer record of the same kind (passed inspection → `FINDING_RESOLVED_BY_INSPECTION`; non-pending calibration → `FINDING_RESOLVED_BY_CALIBRATION`) |
| Cleared by | A verified work order (closed, check passed) for readings at or before its close | Completed maintenance (`SUPERSEDED_BY_COMPLETED_MAINTENANCE`) or a verified work order |

Why: an April vibration value says nothing about the machine in October, but an April "belt wear" defect is still there
until someone fixes or re-inspects it. Discarding it because it is old would hide a real, unresolved problem. The relevance
anchor is sensor time only: a later inspection or work order must not make current readings look stale. This explains
the observed Docker output for EQ-001: the April PulseForge reading is ignored, the April MaintaFlow minor defect is kept.
Four notions are kept apart: when data last **arrived** (`processing_status.last_received_at`); how old the newest **event**
is (`data_age_seconds` → `stale` against `STALE_AFTER_MINUTES`); whether **processing is complete** (`pending` / `failed` /
`partial`); and which individual **sources** are too old to count (`STALE_SIGNAL_IGNORED`).

## Revisions vs out-of-order events
| | Revisions of the same record | Separate events arriving out of order |
|---|---|---|
| Identity | Same vendor + same record id, different payload | Different record ids |
| Stored | Every revision, unchanged (raw history) | Every event |
| Which counts | One authoritative revision: max `(vendor batch version, record event time, arrival order)`; older ones `superseded` / `STALE_REVISION` | All of them; the rules order them by event time (newest reading per vendor) |
| Vendor version used | PulseForge `batch_generated_at`, ThermexWatch `response_time_epoch_ms` (UTC); none for MaintaFlow and CMMS | - |
| Determinism | A total order + "keep the maximum" inside the write transaction ⇒ same result for any arrival or processing order (tested both ways) | Recompute from all events ⇒ order-independent |

## Validation: two tiers, on purpose
| Tier | Where / when | What | On failure |
|---|---|---|---|
| **1. Envelope** | Pydantic models (`backend/app/schemas/vendors.py`), synchronous | JSON object, `Content-Type`, body size, record list present / non-empty / ≤ limit / objects only, vendor named in body matches the endpoint, envelope metadata types | Whole request rejected: `400/413/415/422` with field paths in `error.details`. Nothing stored. |
| **2. Record content** | Normalizers (`backend/app/domain/normalization/`), asynchronous | ids, machine resolution, timestamps, enums, units, plausibility ranges | Essential field bad → record **rejected** to dead letters (replayable). Optional value bad → kept, value nulled, **warning** code. |

Record values are deliberately *not* type-checked in tier 1: rejecting a whole batch because one reading says `"-999"` would
lose the good records next to it. `POST /v1/ingest/{vendor}/validate` runs tier 2 synchronously as a dry run (nothing stored),
and the dashboard runs a client-side mirror of tier 1 as you type. Read endpoints validate query/path params
(status enum, id patterns, UUID batch ids, limit bounds). Every error, including unhandled ones, uses one envelope:
`{"error": {"code", "message", "details"?, "request_id"}}`; every response carries `X-Request-ID`.

## Code layout (layers)
`api/` (HTTP only) → `services/` (use cases, SQL) → `domain/` (pure normalization + rules, no I/O) ← `worker/`.
`schemas/` is the public contract; `core/` holds config, errors, auth, time; `db/` holds schema, connections, reference load.
The domain layer never imports FastAPI or SQLite, which is what keeps the rules and normalizers unit-testable as pure functions.

**Backend and frontend are separate deployables** (`backend/`, `frontend/`): the dashboard is just another API consumer,
so it cannot depend on anything downstream teams can't use. It is React + TypeScript (Vite): typed API models mirror the backend
contract, TanStack Query owns server state (polling batch progress and queue depth, invalidation after ingest/replay),
and filters live in the URL. Same-origin in both environments: the Vite dev server and nginx in Docker proxy the API, so
no CORS is needed (`CORS_ORIGINS` exists only for direct cross-origin use). Trade-off: a Node toolchain and one more
container, in exchange for independent release/scaling, type safety, and a backend image with no UI assets.

## Idempotency summary
Same request body → same batch. Same id + same payload → counted duplicate. Same id + new payload → revision (ordered as above).
Same content + new id → semantic duplicate (fingerprint). Recompute is a pure function → re-running is safe.
Hashes are taken over canonical JSON (sorted keys, no whitespace, integral floats as integers), so the same record serialized
by different clients (`171.0` from Python, `171` from JavaScript) is a duplicate, not a revision.
**Fingerprint scope:** every canonical content field (vendor, machine, event time, kind, code, severity, all measurements,
machine state, signal quality, sensor health, confidence, inspection / maintenance fields, note, work-order details). Only the
record id and bookkeeping are excluded. A different id with identical content = `SEMANTIC_DUPLICATE`; a different id with any
different value = a distinct observation, kept.

## Known gaps (deliberately out of scope)
No per-vendor credentials/rate limits, no metrics/tracing (request ids + access log only), no cursor pagination on list endpoints (limits only), MaintaFlow timezone is a single
configured value, no retention/archival, no schema-version negotiation (`v1` is recorded, not negotiated).
