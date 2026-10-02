# Industrial Equipment Monitoring Backend

Ingests machine data from three vendors with different schemas (PulseForge, ThermexWatch, MaintaFlow) plus the internal
maintenance work-order feed (CMMS), normalizes it into one canonical model, processes it asynchronously, and serves an **explainable machine operational attention view** plus
plant/line summaries, plus an **operations dashboard**. Backend: Python 3.12+ · FastAPI · Pydantic · SQLite (WAL).
Frontend: React 19 · TypeScript · Vite · TanStack Query. Built for the Aurik Tech Lead take-home.

* Architecture diagram: [docs/architecture.md](docs/architecture.md) · Design note: [docs/design-note.md](docs/design-note.md)
* API contract: [docs/openapi.json](docs/openapi.json) (also live at `/docs` when running)
* Requirement-by-requirement coverage with code references: [docs/requirements-coverage.md](docs/requirements-coverage.md)

## Run it

**Local (no Docker)**
```bash
python -m venv .venv && source .venv/bin/activate
make install                  # backend pip deps + frontend npm deps (Node 20+)
make run                      # backend: API on :8000 with an embedded worker thread (docs at /docs)
make seed                     # (second terminal) load backend/data/seed/*.csv through the API
make ui                       # (third terminal) frontend: React dev server on http://localhost:5173 (proxies the API)
```
**Windows (PowerShell, no `make`)**, from the project folder:
```powershell
# terminal 1 - backend API on :8000 (embedded worker)
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r backend\requirements-dev.txt
cd backend; uvicorn app.main:app --reload
# terminal 2 - load the supplied seed data
.\.venv\Scripts\Activate.ps1; cd backend; python scripts\load_seed.py
# terminal 3 - dashboard on http://localhost:5173
cd frontend; npm install; npm run dev
```

> **Demo freshness setting.** The bundled data is dated April 2026, so with the production default (`STALE_AFTER_MINUTES=30`)
> every machine is correctly reported as *stale*. For demos only, start the API with `$env:STALE_AFTER_MINUTES=100000000`
> (bash: `STALE_AFTER_MINUTES=100000000 make run`). It changes only the read-time `processing_status`, never the derived status.

**Docker Compose**
```bash
docker compose up --build -d                                                   # api :8000, worker, dashboard :8080
docker compose exec api python scripts/load_seed.py --url http://localhost:8000  # load the seed data (idempotent)
docker compose logs -f worker                                                  # watch background processing
```
API + worker run as separate containers sharing one volume; the dashboard (nginx) is on **http://localhost:8080** and proxies
the API. Compose reads an optional `.env` file next to `docker-compose.yml` - copy `.env.example` to `.env` to set
`API_KEYS` (enables `X-API-Key` auth; the dashboard has a key button) or the demo-only `STALE_AFTER_MINUTES`.
`docker compose down` stops the stack and keeps the data volume.

*Docker verification (October 2026, Windows + Docker Desktop):*
* **Owner's stack (rules v1.1):** all three images built; api healthy, worker and frontend up; the 8 seed batches
  (25 records) all `completed` with every record `processed`; a live ThermexWatch reading for EQ-001
  (`TW-DEMO-EQ001-001`) processed: 171 °F → 77.22 °C, 25.2 kW flagged 30% below the 36 kW baseline, EQ-001 `degraded`
  (level 2) with `HIGH_VIBRATION`, `POWER_DEVIATION` and the open April finding `INSPECTION_MINOR_DEFECT`. This scenario is
  now a regression test (`test_docker_smoke_test_scenario_for_eq001`).
* **Isolated verification stack (v1.5.0, rules v1.3; separate project, ports and volume, so demo data was untouched):**
  `verify_scenarios.py` **55/55 checks passed** with the api's embedded worker disabled, i.e. all processing done by the
  separate worker container; nginx routed `/v1`, `/docs` and SPA deep links (`/machines/EQ-001` → 200); after
  `docker compose restart api worker` the data was still there (31 batches, same EQ-008 state) and the worker processed new
  records; the runtime image contains no dev dependencies (`pytest` absent). The stack was then removed with `down -v`.

## Dashboard (`frontend/`)
A separate React + TypeScript app over the same public API — no private endpoints. In dev the Vite server proxies the
API (same origin); in Docker nginx serves the built bundle and proxies the API. Types in `frontend/src/api/types.ts` mirror
the backend response models. See [frontend/README.md](frontend/README.md).
* **Overview** – KPI tiles, machines by derived status, lines ranked by machines needing attention, critical machines, data-quality/freshness buckets; per-plant scope.
* **Machines** – filterable cards (plant, line, status, needs-attention, search); drawer with every reason, latest signals per source, processing freshness and links to source records.
* **Ingest** – paste or load a bundled sample, live client-side envelope checks, server **dry-run validation** per record, submit, then live batch progress.
* **Batches / Dead letters / Trace** – processing status, rejected/dead records with one-click replay, raw revisions → canonical event.
Light/dark theme, keyboard accessible, status always shown as icon + label (never colour alone), works at phone width.

**Tests** (run on the host; the runtime images contain no test tools): `make test`, or without `make`:
```bash
python -m pytest backend -q                       # backend (from the project folder, venv active)
cd frontend && npm run typecheck && npm test      # frontend: strict TypeScript + Vitest
cd frontend && npm run build                      # production frontend build
```
Backend: 153 tests ( the worker is driven explicitly and
freshness tests use a frozen clock, so they are deterministic and need no running server). Infrastructure retries are tested
with injected transient failures (`test_reliability.py`); `retry_cases.json` only covers bad data. Frontend: `cd frontend && npm run typecheck && npm test` (strict TypeScript + 16 Vitest tests).

**End-to-end check of a running deployment**: start the API on an **empty** database, then
`python backend/scripts/verify_scenarios.py --url http://localhost:8000`. It ingests the seed data and every supplied sample
over HTTP, waits for the asynchronous worker, and checks final processing results (55 checks: seed outcome, invalid
envelopes, duplicates incl. distinct observations, malformed records, partial failures, out-of-order events, conflicting
updates, work-order scope, revisions, future timestamps, byte-exact traceability). Exit code 0 means every check passed.
It uses only the Python standard library, so it runs from the host against any deployment.

## API (short form — full contract in `docs/openapi.json`)
| Method & path | Purpose |
|---|---|
| `POST /v1/ingest/{pulseforge\|thermexwatch\|maintaflow\|workorders}` | Accept a batch → `202` + `batch_id`; identical replay → `200` |
| `POST /v1/ingest/{vendor}/validate` | Dry run: per-record outcome (valid / warnings / rejected) and store action (new / duplicate / revision); stores nothing |
| `GET /v1/batches`, `GET /v1/batches/{id}` | Recent batches; processing status `processing / completed / completed_with_errors / failed`, per-record counts and errors |
| `GET /v1/batches/{id}/raw` | The request body exactly as received (byte-for-byte) |
| `GET /v1/dead-letters`, `POST /v1/dead-letters/{raw_id}/replay` | Inspect and re-queue rejected/dead records |
| `GET /v1/records/{vendor}/{record_id}` | Traceability: every raw revision + resulting canonical event + warnings |
| `GET /v1/machines`, `GET /v1/machines/{id}` | Machine attention view (filters: `plant_id,line_id,status,needs_attention`) |
| `GET /v1/summary`, `GET /v1/summary/plants/{plant}` | Counts by status, lines ranked by machines needing attention, critical machines, stale/failed/partial machines |
| `GET /v1/reference`, `GET /v1/meta` | Plants → lines → machines; service versions, auth mode, limits |
| `GET /v1/samples`, `GET /v1/samples/{name}` | Bundled sample payloads split per vendor (used by the dashboard) |
| `GET /health`, `GET /ready` | Liveness; readiness with queue depth and dead-letter count |

Auth: set `API_KEYS=k1,k2` and send `X-API-Key`; unset = open (dev only). Probes and `/v1/meta` stay open.
Errors always look like `{"error": {"code": "VALIDATION_ERROR", "message": "...", "details": [{"field": "events[1]", "message": "..."}], "request_id": "..."}}`.

### Validation
Two tiers (details in the design note): **envelope** validation is synchronous via Pydantic — wrong media type `415`, bad JSON `400`,
oversized body/batch `413`, missing/empty/non-list record array, non-object records, vendor-name mismatch, bad envelope types `422`
with exact field paths. **Record values** are validated asynchronously by the normalizers (reject to dead letters vs keep with a
warning), because one bad reading must not discard a whole batch. Query/path params are typed (status enum, id patterns, UUIDs, bounded limits).

Example (`GET /v1/machines/EQ-008` after loading `backend/sample_payloads/conflicting_updates.json`, abridged — real output):
```json
{"machine_id":"EQ-008","plant_id":"PLANT_02","line_id":"LINE-E","derived_status":"critical","needs_attention":true,
 "attention_level":4,"reason_codes":["HIGH_TEMPERATURE","TEMP_OVER_RATED_LIMIT","INSPECTION_MAJOR_DEFECT","CONFLICTING_SOURCES"],
 "reasons":[
  {"code":"HIGH_TEMPERATURE","level":4,"source":"ThermexWatch","record_id":"TW-9921"},
  {"code":"INSPECTION_MAJOR_DEFECT","level":3,"source":"MaintaFlow","record_id":"MF-801"},
  {"code":"CONFLICTING_SOURCES","level":1,"source":"system","detail":"PulseForge reports RECOVERY at ... but another source still reports an alert; alert kept (pessimistic)"},
  {"code":"RECOVERY_REPORTED","level":0,"source":"PulseForge","record_id":"PF-1401"}],
 "latest_relevant_event_time":"2026-04-18T08:41:00Z",
 "source_event_refs":[{"source":"MaintaFlow","record_id":"MF-801"},{"source":"PulseForge","record_id":"PF-1401"},{"source":"ThermexWatch","record_id":"TW-9921"}],
 "processing_status":{"status":"fresh","pending_records":0,"failed_records":0,"rejected_records":0}}
```

## How the messy-data requirements are handled
| Concern | Behaviour |
|---|---|
| Malformed envelope | Rejected synchronously with `4xx` and field paths; nothing stored. |
| Malformed / missing essentials | Rejected by the worker with a code (`MISSING_MACHINE_ID`, `UNKNOWN_MACHINE`, `INVALID_TIMESTAMP`, …); visible in batch status and `/v1/dead-letters`; replayable. Batch-level garbage → `400/413/422`. |
| Bad optional values (`-999`, `"bad"`, confidence `1.2`, `NaN`, unknown booleans such as `is_active: "maybe"`, unknown confidence words) | Value nulled + warning code on the canonical event; the rest of the record is kept. Warnings are visible in the trace, the dry run and on each signal in the machine view. |
| Unknown alerts | Unmapped alert / event type → `UNKNOWN_ALERT` with the vendor level (or a low default) and a warning - never treated as healthy. ThermexWatch `OK` with level ≥ 2, or an alert with level 1, is `ALERT_LEVEL_CONFLICT`: the more severe reading wins. |
| Future timestamps | An event time more than `FUTURE_TOLERANCE_MINUTES` (default 10) after the record was received is rejected (`FUTURE_TIMESTAMP`): it would otherwise stay the "newest" reading and mask real data. |
| Duplicates | Request replay, same-id/same-payload, and same-content/different-id are all collapsed (see design note). Content hashes ignore key order, whitespace and `171.0` vs `171` spelling. The same-content fingerprint covers **every** canonical field (incl. signal quality, machine state, confidence), so two readings that differ in any value are distinct observations and both kept. |
| Out-of-order | Derivation uses *event* time and recomputes from all events; a late old alert cannot override a newer recovery. |
| Revisions of the same record | Same vendor + record id, different payload = a new revision. Every revision is kept and processed; the authoritative one is the greatest (vendor batch version, record event time, arrival order). A delayed older revision is recorded as `superseded` / `STALE_REVISION` and never replaces a newer one, whatever order revisions arrive or are processed in. |
| Out-of-order separate events | Different record ids are separate events, never revisions: all are kept, and the rules use event time (newest reading per vendor). |
| Conflicting sources | Cross-vendor disagreement: pessimistic + `CONFLICTING_SOURCES`. |
| Freshness | Sensor readings expire (60 min relevance window, anchored to the machine's newest *sensor* reading). Maintenance findings never expire by age; they stay, with their age stated, until a newer record of the same kind, completed maintenance or a verified work order resolves them. |
| Raw traceability | Every request body is kept byte-for-byte (`/v1/batches/{id}/raw`); every record keeps all fields and values as sent (`/v1/records/...`). Normalization never edits the raw store. |
| Maintenance follow-up | CMMS work orders: closed + passed post-check resolves earlier findings **within its scope** (`maintenance_type` + trigger, e.g. `power_check` → power alerts only); open / reopened work orders are acknowledged without lowering attention; failed / pending checks raise attention. |
| Equal timestamps | Two readings from one vendor with the same event time: the more severe counts (then record id). Two revisions of one record with the same version and time: the later arrival wins. |
| Units & time | °F→°C; epoch-ms, ISO and `YYYY/MM/DD HH:MM:SS` → UTC; vendor severities/levels/alert codes → one enum set. |
| Entity resolution | `EQ-1`/`eq_001` → `EQ-001`; line `C` → `LINE-C`; plant/line taken from the asset reference. |
| Retries | Exponential backoff, `MAX_ATTEMPTS` then dead-letter; worker crashes recovered by lease expiry; recompute failures retried via a durable marker. |

## Assumptions
1. MaintaFlow timestamps are UTC (configurable `MAINTAFLOW_TZ`) — the vendor sends no zone.
2. Vendor record ids are unique per vendor. Severity maps: PulseForge low/medium/high/critical → 1–4; ThermexWatch level 1–5 → 0–4.
3. Thresholds in the rules engine are my choices (rated-limit ratios, power deviation, 60-min relevance window); see design note.
4. `processing_status.stale` is judged against wall-clock now; the bundled samples are from April 2026 so they read as `stale`
   unless you raise `STALE_AFTER_MINUTES` (a demo-only setting). Freshness tests use a frozen clock instead.
5. A machine with no events is `unknown/no_data`; one with `asset_status=maintenance` (EQ-007) reports `under_maintenance`.
6. `seed_data/maintenance_work_orders.csv` is an internal CMMS export (not a vendor). A closed work order with a *passed*
   post-maintenance check is treated as verification of the parts it covered, at its close time (rule 8, assumption 10). Work orders are
   ingested through the same pipeline (`/v1/ingest/workorders`) so they get the same validation, idempotency and traceability.
7. If ThermexWatch `level` is missing/invalid, severity falls back to the alert code (`TEMP_CRIT` → critical, `*_WARN` → medium).
8. **Revision order.** The vendors' own batch timestamps are treated as the version of every record in the batch: PulseForge
   `batch_generated_at`, ThermexWatch `response_time_epoch_ms`. No sample carries a per-record version field. MaintaFlow and
   the CMMS send no version at all, so for them the record's own time (`recorded_at`, work-order state time) decides, then
   arrival order. A revision without a version sorts as oldest, which keeps the order total and deterministic.
9. **Inspection findings do not expire.** An April defect is still reported next to an October reading, because a physical
   defect stays true until someone fixes or re-inspects the machine. This is intentional (rules v1.2) and covered by tests.
10. **Work-order scope** (`WO_TYPE_SCOPE` / `WO_TRIGGER_SCOPE` in `vocabulary.py`) is my mapping of the supplied maintenance types:
   `power_check` → power, `cooling_system` and `heat_shield` → temperature, `sensor_recalibration` → sensor + calibration;
   a trigger of `inspection` / `operator_report` / `scheduled_service` also resolves that kind of finding. Unknown types
   resolve nothing (`UNMAPPED_MAINTENANCE_TYPE`). A reopen must be sent as `status: "reopened"` (optional `updated_at`).
11. **Asset reference status** (EQ-007 `maintenance`): derived status is `under_maintenance`, but the attention level is
   still computed from the evidence, so a real fault keeps `needs_attention = true` (it is not listed under critical).

## Limitations & what I would do next for production
* **Storage/queue**: move to Postgres (`FOR UPDATE SKIP LOCKED` queue, partition `raw_records` by time) or SQS + worker pool; add retention/archival of raw payloads.
* **Security**: per-vendor keys with rotation, HMAC-signed payloads, rate limiting, TLS at the edge, PII review of free-text notes.
* **Observability**: structured logs with batch/record ids, Prometheus metrics (queue depth, age of oldest pending, reject rate by code), tracing, alerting on dead letters.
* **API**: pagination/cursors, ETags, webhook or change feed for state transitions, explicit `/v2` and vendor schema-version negotiation.
* **Worker**: SQLite serializes writers, so N workers are safe but not faster than one; lease fencing prevents a stalled
  worker from overwriting results, but a worker that stalls *mid-normalization* still repeats that work once.
* **Revisions**: for vendors without a batch version, a correction that moves a record's own time *backwards* is treated
  as stale (it stays in the raw history, visible in the trace). Values spelled differently (`"11.8"` from a CSV export vs
  `11.8` in JSON) are different payloads, so they count as a revision; the canonical event is identical.
* **Rules**: externalise thresholds per asset type/criticality, shadow-run rule changes against history (the raw store makes this possible), add trend-based rules and per-line operating windows (`day_shift` machines idle at night are not "stale").
* **Scale**: incremental/windowed recompute; per-machine ordering keys; idempotent downstream publication of state changes.
* **Ops**: migrations tool (Alembic), CI pipeline, container image scanning, load test.

* **Frontend**: add SSO instead of a pasted API key, server-side pagination, live updates (SSE) instead of polling, component/E2E tests (Playwright), and generate `types.ts` from `openapi.json` in CI.

## AI usage disclosure

I used AI coding assistants to understand requirements, explore architecture options, and assist with drafting and refining code, tests, and documentation.

I made the final design decisions based on my understanding of the assignment and its trade-offs, including the raw landing zone, database-backed processing queue, recomputation from events, validation policies, and backend/frontend separation. Thresholds and vendor mappings are documented assumptions rather than verified vendor facts.

I reviewed the AI-assisted changes, built and ran the Docker Compose stack, and performed the EQ-001 ingestion smoke test to verify processing and machine-view updates.

I remain responsible for the submitted implementation and for explaining its architecture, behavior, assumptions, and limitations.

## Layout
```
backend/                      Python service (own Dockerfile, requirements, tests)
  app/
    main.py                   app factory: middleware, CORS, error handlers, routers
    api/                      deps.py (connection + validated params) · routes/ health, ingestion, processing, machines, samples
    schemas/                  vendors.py (request envelopes) · responses.py (response models, enums) - the API contract
    services/                 ingestion (store/dedupe/dry-run/status) · processing (dead letters, trace, reference) · views
    domain/                   vendors.py (registry) · normalization/ (one module per vendor + shared policy) · rules.py
    worker/                   runner.py (lease, retry, dead-letter, recompute) · __main__.py -> python -m app.worker
    db/                       schema.py · session.py · reference.py         core/  config · errors · security · timeutil
  tests/                      normalize · derive · freshness · revisions · api flow · reliability · validation · work orders · audit policies
  scripts/                    load_seed.py · verify_scenarios.py (end-to-end check) · export_openapi.py
  data/                       reference + seed CSVs (as supplied)           sample_payloads/  vendor JSON (as supplied)
frontend/                     React + TS dashboard: src/{api,components,charts,pages,features,lib}; Vite; Dockerfile + nginx.conf
docs/                         architecture.md · design-note.md · openapi.json
docker-compose.yml · Makefile  wire the two together
```
Layering: `api → services → domain`; the domain layer has no I/O, so normalizers and rules are tested as pure functions.
