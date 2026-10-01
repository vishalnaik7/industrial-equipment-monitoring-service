# Industrial Equipment Monitoring Backend

Ingests machine data from three vendors with different schemas (PulseForge, ThermexWatch, MaintaFlow), normalizes it into one
canonical model, processes it asynchronously, and serves an **explainable machine operational attention view** plus
plant/line summaries. Python 3.12 · FastAPI · SQLite (WAL). Built for the Aurik Tech Lead take-home.

* Architecture diagram: [docs/architecture.md](docs/architecture.md) · Design note: [docs/design-note.md](docs/design-note.md)
* API contract: [docs/openapi.json](docs/openapi.json) (also live at `/docs` when running)

## Run it

**Local (no Docker)**
```bash
python -m venv .venv && source .venv/bin/activate
make install                  # pip install -r requirements-dev.txt
make run                      # API on :8000 with an embedded worker thread
make seed                     # (second terminal) load data/seed/*.csv through the API
curl localhost:8000/v1/summary
```
**Docker Compose** (api and worker as separate containers sharing a volume): `docker compose up --build`
(set `API_KEYS=dev-key` to enable auth). *Note: written and `compose config`-validated but not run in my authoring sandbox
(no Docker daemon) — please report if the build misbehaves.*

**Tests**: `make test` (37 tests; the worker is driven explicitly so they are deterministic and need no running server).

## API (short form — full contract in `docs/openapi.json`)
| Method & path | Purpose |
|---|---|
| `POST /v1/ingest/{pulseforge\|thermexwatch\|maintaflow}` | Accept a vendor batch → `202` + `batch_id`; identical replay → `200` |
| `GET /v1/batches/{id}` | Processing status: `processing / completed / completed_with_errors / failed`, per-record counts and errors |
| `GET /v1/dead-letters`, `POST /v1/dead-letters/{raw_id}/replay` | Inspect and re-queue rejected/dead records |
| `GET /v1/records/{vendor}/{record_id}` | Traceability: every raw revision + resulting canonical event + warnings |
| `GET /v1/machines`, `GET /v1/machines/{id}` | Machine attention view (filters: `plant_id,line_id,status,needs_attention`) |
| `GET /v1/summary`, `GET /v1/summary/plants/{plant}` | Counts by status, lines ranked by machines needing attention, critical machines, stale/failed/partial machines |
| `GET /health`, `GET /ready` | Liveness; readiness with queue depth and dead-letter count |

Auth: set `API_KEYS=k1,k2` and send `X-API-Key`; unset = open (dev only). Probes stay open.

Example (`GET /v1/machines/EQ-008` after loading the conflicting-updates sample, abridged):
```json
{"machine_id":"EQ-008","plant_id":"PLANT_02","line_id":"LINE-E","derived_status":"critical","needs_attention":true,
 "attention_level":4,"reason_codes":["MULTI_SOURCE_CORROBORATION","HIGH_TEMPERATURE","INSPECTION_MAJOR_DEFECT","CONFLICTING_SOURCES"],
 "reasons":[{"code":"HIGH_TEMPERATURE","level":4,"source":"ThermexWatch","record_id":"TW-9921","detail":"ThermexWatch reported HIGH_TEMPERATURE with severity critical"}],
 "latest_relevant_event_time":"2026-04-18T08:41:00Z","source_event_refs":[{"source":"ThermexWatch","record_id":"TW-9921"}],
 "processing_status":{"status":"fresh","pending_records":0,"failed_records":0,"data_age_seconds":42},"last_processed_at":"..."}
```

## How the messy-data requirements are handled
| Concern | Behaviour |
|---|---|
| Malformed / missing essentials | Rejected by the worker with a code (`MISSING_MACHINE_ID`, `UNKNOWN_MACHINE`, `INVALID_TIMESTAMP`, …); visible in batch status and `/v1/dead-letters`; replayable. Batch-level garbage → `400/413/422`. |
| Bad optional values (`-999`, `"bad"`, confidence `1.2`) | Value nulled + warning code on the canonical event; the rest of the record is kept. |
| Duplicates | Request replay, same-id/same-payload, and same-content/different-id are all collapsed (see design note). |
| Out-of-order | Derivation uses *event* time and recomputes from all events; a late old alert cannot override a newer recovery. |
| Conflicting updates | Same-id revisions: latest wins, history kept. Cross-vendor disagreement: pessimistic + `CONFLICTING_SOURCES`. |
| Units & time | °F→°C; epoch-ms, ISO and `YYYY/MM/DD HH:MM:SS` → UTC; vendor severities/levels/alert codes → one enum set. |
| Entity resolution | `EQ-1`/`eq_001` → `EQ-001`; line `C` → `LINE-C`; plant/line taken from the asset reference. |
| Retries | Exponential backoff, `MAX_ATTEMPTS` then dead-letter; worker crashes recovered by lease expiry; recompute failures retried via a durable marker. |

## Assumptions
1. MaintaFlow timestamps are UTC (configurable `MAINTAFLOW_TZ`) — the vendor sends no zone.
2. Vendor record ids are unique per vendor. Severity maps: PulseForge low/medium/high/critical → 1–4; ThermexWatch level 1–5 → 0–4.
3. Thresholds in the rules engine are my choices (rated-limit ratios, power deviation, 60-min relevance window); see design note.
4. `processing_status.stale` is judged against wall-clock now; the bundled samples are from April 2026 so they read as `stale`
   unless you raise `STALE_AFTER_MINUTES` (tests and the seed demo do).
5. A machine with no events is `unknown/no_data`; one with `asset_status=maintenance` (EQ-007) reports `under_maintenance`.

## Limitations & what I would do next for production
* **Storage/queue**: move to Postgres (`FOR UPDATE SKIP LOCKED` queue, partition `raw_records` by time) or SQS + worker pool; add retention/archival of raw payloads.
* **Security**: per-vendor keys with rotation, HMAC-signed payloads, rate limiting, TLS at the edge, PII review of free-text notes.
* **Observability**: structured logs with batch/record ids, Prometheus metrics (queue depth, age of oldest pending, reject rate by code), tracing, alerting on dead letters.
* **API**: pagination/cursors, ETags, webhook or change feed for state transitions, explicit `/v2` and vendor schema-version negotiation.
* **Rules**: externalise thresholds per asset type/criticality, shadow-run rule changes against history (the raw store makes this possible), add trend-based rules and per-line operating windows (`day_shift` machines idle at night are not "stale").
* **Scale**: incremental/windowed recompute; per-machine ordering keys; idempotent downstream publication of state changes.
* **Ops**: migrations tool (Alembic), CI pipeline, container image scanning, load test.

## AI usage disclosure
I used Claude (Anthropic) as a coding assistant to draft code, tests and documentation under my direction. The architecture
(raw landing zone + table-as-queue, recompute-from-events, reject-vs-warn policy, pessimistic conflict handling) and
the review of every module and test are mine to defend; thresholds and mappings are documented assumptions, not vendor facts.

## Layout
```
app/        main.py (HTTP) · ingest.py · normalize.py · worker.py · derive.py · views.py · db.py · config.py
tests/      normalize · derive (pure rules) · api flow on provided edge-case payloads · reliability (retry, lease, auth)
scripts/    load_seed.py · export_openapi.py          data/  reference + seed CSVs          sample_payloads/  provided JSON
```
