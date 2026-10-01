# Architecture

```mermaid
flowchart LR
  subgraph Vendors
    PF[PulseForge<br/>ISO-8601, mm/s, °C]
    TW[ThermexWatch<br/>epoch ms, g, °F, level 1-5]
    MF[MaintaFlow<br/>local-time string, free text]
  end
  PF & TW & MF -->|POST /v1/ingest/vendor| API

  subgraph Service["API process (FastAPI)"]
    API[Ingest<br/>auth · size/shape validation<br/>request-hash + record dedupe<br/>revisioning]
    VIEWS[Query API<br/>machine view · summaries<br/>batch status · dead letters · trace]
  end

  API -->|202 + batch_id<br/>raw payload appended| RAW[(raw_records<br/>append-only landing zone<br/>+ queue state)]
  subgraph Worker["Worker process(es) - python -m app.worker"]
    CLAIM[Lease & claim] --> NORM[Normalize<br/>map · units · time · entity resolve]
    NORM -->|reject| DLQ[status=rejected / dead]
    NORM --> EV[(normalized_events)]
    EV --> DIRTY[(dirty_machines)] --> DERIVE[Derive<br/>deterministic rules]
  end
  RAW --> CLAIM
  DLQ -.-> RAW
  DERIVE --> MS[(machine_state<br/>reasons · signals · refs · version)]
  REF[(assets / lines<br/>CSV reference)] --> NORM & DERIVE
  MS & RAW & REF --> VIEWS
  VIEWS --> DOWN[Downstream product / backend teams]
```

## Request lifecycle
1. **Accept** – validate envelope, store each record raw, dedupe, answer `202` with a `batch_id`. No normalization on the request path.
2. **Process** – worker leases pending records, normalizes, upserts the canonical event, marks the machine dirty.
3. **Derive** – the machine is recomputed from *all* its canonical events (a pure function), so ordering of arrival cannot change the result.
4. **Serve** – read models join `machine_state` with live queue counters to compute `processing_status` at read time.

## Record state machine (`raw_records.status`)
`pending → processing → processed` · `→ rejected` (permanent data error) · `→ duplicate` (same content, other id) ·
`→ superseded` (newer revision) · transient failure: `pending` again with exponential backoff → `dead` after `MAX_ATTEMPTS`.
A worker that dies mid-record leaves a lease that expires; another worker picks the record up.
