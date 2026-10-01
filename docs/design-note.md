# Design note

## Goals
A reviewable first version that is honest about messy vendor data: never lose a payload, never let arrival order
decide truth, and make every derived answer explain itself.

## Key decisions and why
| Decision | Why | Trade-off |
|---|---|---|
| **Raw landing zone first** (`raw_records`, append-only) | Replay, audit and "why did the machine say X" all start from the exact bytes received. Normalizer bugs are fixable by replay. | Extra storage. |
| **Table as queue** (leases + `next_attempt_at`) instead of Redis/Kafka | One moving part, transactional with the data, trivially testable, safe with N workers. | Polling latency (~1 s); SQLite single-writer ceiling. Swap for Postgres `SKIP LOCKED` / SQS when scaling. |
| **SQLite (WAL)** | Zero-setup local run; api + worker processes can share the file. | Not for multi-host. Schema is plain SQL → Postgres port is mechanical. |
| **Recompute-from-events** derivation (pure function) | Idempotent, order-independent, replay-safe; no incremental-state bugs. | O(events per machine) per recompute; fine here, snapshot/window it for volume. |
| **Dirty-machine marker** | Recompute is retried if it fails or the worker dies between "event saved" and "state saved". | One more table. |
| **Reject vs warn policy** | Essential identity/time fields invalid ⇒ reject (dead-letter, replayable). Optional measurements invalid ⇒ null + warning code. One bad sensor value shouldn't hide a real alert. | A bad *value* can hide a threshold breach; surfaced via warnings. |
| **Asset reference is authoritative** for plant/line | Vendors disagree (`C`, `LINE-C`, `ZZ`). Disagreement kept as a warning. | A machine genuinely moved lines needs reference updated first. |
| **Latest revision wins** for same vendor id with different payload | Vendors correct themselves. Old revisions are kept for audit. | If a vendor replays an *older* revision later, it wins; we have no vendor version field to prevent that. |
| **Pessimistic conflict resolution** | Missing a real fault costs more than a false alert. Conflict is flagged `CONFLICTING_SOURCES`. | More noise when one vendor lags. |
| **No g → mm/s conversion** | Acceleration vs velocity needs frequency content; a fake conversion would be wrong. ThermexWatch vibration is stored in g and not used for rated-limit rules. | Less vibration coverage from that vendor. |

## Derivation rules (v1.0, `app/derive.py`)
Per sensor vendor take the newest reading by *event* time; ignore if older than the 60-min relevance window.
Reasons come from the vendor alert and from thresholds vs the asset's rated limits (temp ≥95% near / >100% over / ≥110% critical,
vibration ≥90% / >100% / ≥125%, power ±25% / ±40% from baseline). Poor signal quality or low manual confidence downgrades one level.
Maintenance: major defect (3), minor/observation (1), overdue (2), due soon (1), calibration pending (1), operator-note keywords;
a later `completed` maintenance record supersedes earlier inspection/notes. Level = max reason; +1 if two independent
sources both reach ≥3. Status = healthy / watch / degraded / at_risk / critical (or under_maintenance / offline from the asset reference).
`needs_attention` ⇔ level ≥ 2. **All thresholds are assumptions** I picked for explainability; they live in one file and are versioned (`rules_version`).

## Idempotency summary
Same request body → same batch. Same id + same payload → counted duplicate. Same id + new payload → revision.
Same content + new id → semantic duplicate (fingerprint). Recompute is a pure function → re-running is safe.

## Known gaps (deliberately out of scope)
No per-vendor credentials/rate limits, no metrics/tracing, no pagination on list endpoints, MaintaFlow timezone is a single
configured value, no retention/archival, no schema-version negotiation (`v1` is recorded, not negotiated).
