# Frontend - Aurik operations dashboard

React 19 + TypeScript + Vite single-page app. It is just another consumer of the backend's public, documented API
(`docs/openapi.json`); it has no private endpoints.

| Concern | Choice | Why |
|---|---|---|
| Server state | TanStack Query | caching, polling (batch progress, queue depth), invalidation after ingest/replay - without hand-written effects |
| Routing | React Router 7 | deep links for every view (`/machines/EQ-008`, `/trace/pulseforge/PF-1202`), filters kept in the URL |
| Styling | one CSS file with design tokens | light/dark themes, no UI kit to learn or override |
| Types | `src/api/types.ts` mirrors `backend/app/schemas/responses.py` | contract drift shows up as a compile error |
| Tests | Vitest | the ingest pre-flight checks are pure functions, tested against the real sample payloads |

## Run locally
```bash
npm install
npm run dev          # http://localhost:5173 - proxies /v1, /docs, /health, /ready to http://localhost:8000
```
Start the backend first (`make run` from the repo root). Point the proxy elsewhere with `VITE_PROXY_TARGET=http://host:port npm run dev`.
To call the API directly instead of through the proxy, build with `VITE_API_BASE_URL=https://api.example.com`
(the backend must allow the origin via `CORS_ORIGINS`).

```bash
npm test             # unit tests (vitest)
npm run typecheck    # tsc --noEmit, strict mode
npm run build        # type-check + production bundle in dist/
```

## Run in Docker
`docker compose up --build` from the repo root serves it on http://localhost:8080. The image is a two-stage build
(Node builds `dist/`, nginx serves it); [`nginx.conf`](nginx.conf) proxies the API to the `api` service, falls back to
`index.html` for client-side routes, and caches the content-hashed assets forever.

## Structure
```
src/
  main.tsx               providers: React Query, preferences (theme, auto-refresh), toasts
  App.tsx                routes; one lazy-loaded chunk per page
  api/
    client.ts            fetch wrapper: base URL, X-API-Key, one ApiError for every failure
    types.ts             response/request types (mirror of the backend contract)
    hooks.ts             React Query hooks: cache keys, polling intervals, invalidation after writes
  components/            Layout (sidebar/top bar), badges, feedback (error/empty/skeleton/QueryState),
                         Drawer/Modal, Toasts, Tooltip, JsonView, TimeAgo, Icon, ApiKeyDialog
  charts/                StatusDistribution, RankedLines (dependency-free)
  features/ingest/       checks.ts (+ checks.test.ts): client-side envelope pre-flight checks
  pages/                 Overview, Machines (+ MachineDrawer), Ingest, Batches, DeadLetters, Trace
  lib/                   format, status vocabulary, preferences, guarded localStorage
  styles/styles.css      design tokens (light/dark) + components
```

## Conventions
* No `dangerouslySetInnerHTML` anywhere; vendor payloads are rendered by `JsonView` as React elements.
* Status is always icon + label; status colours are reserved and never reused for data series.
* Filters and the selected machine live in the URL, so every view can be shared as a link.
* Client-side ingest checks are fast feedback only; the server re-validates everything.
