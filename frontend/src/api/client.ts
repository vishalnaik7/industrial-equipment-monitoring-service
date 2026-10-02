// Fetch wrapper: base URL, optional X-API-Key, and one error type for every failure.
import type {
  BatchAccepted, BatchList, BatchStatus, DeadLetterList, MachineFilters, MachineList, MachineView, Meta,
  Ready, RecordTrace, ReferenceData, Sample, SampleList, Summary, ValidationReport, VendorSlug,
} from "./types";
import type { ErrorDetail } from "./types";
import { storage } from "../lib/storage";

// Empty = same origin (Vite dev proxy / nginx in Docker). Set VITE_API_BASE_URL to call another origin directly.
export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");
const KEY_STORE = "aurik.apiKey";

export const getApiKey = () => storage.get(KEY_STORE) ?? "";
export const setApiKey = (v: string) => (v ? storage.set(KEY_STORE, v) : storage.remove(KEY_STORE));

export class ApiError extends Error {
  constructor(
    public status: number, public code: string, message: string,
    public details?: ErrorDetail[], public requestId?: string | null,
  ) { super(message); }
}

type Query = Record<string, string | number | boolean | undefined | null>;

export async function request<T>(method: "GET" | "POST", path: string, opts: { query?: Query; body?: unknown } = {}): Promise<{ status: number; data: T }> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(opts.query ?? {})) if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  const url = `${API_BASE}${path}${qs.size ? `?${qs}` : ""}`;
  const headers: Record<string, string> = { Accept: "application/json" };
  const key = getApiKey();
  if (key) headers["X-API-Key"] = key;
  let body: string | undefined;
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body);
  }
  let res: Response;
  try {
    res = await fetch(url, { method, headers, body });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", `Cannot reach the API${API_BASE ? ` at ${API_BASE}` : ""}. Is the backend running?`);
  }
  const text = await res.text();
  let data: unknown = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* non-JSON body */ }
  if (!res.ok) {
    const e = (data as { error?: { code?: string; message?: string; details?: ErrorDetail[]; request_id?: string } } | null)?.error ?? {};
    throw new ApiError(res.status, e.code ?? `HTTP_${res.status}`, e.message ?? res.statusText, e.details,
      e.request_id ?? res.headers.get("X-Request-ID"));
  }
  return { status: res.status, data: data as T };
}

const get = <T,>(path: string, query?: Query) => request<T>("GET", path, { query }).then((r) => r.data);
const enc = encodeURIComponent;

export const api = {
  meta: () => get<Meta>("/v1/meta"),
  ready: () => get<Ready>("/ready"),
  reference: () => get<ReferenceData>("/v1/reference"),
  summary: (plant?: string) => get<Summary>(plant ? `/v1/summary/plants/${enc(plant)}` : "/v1/summary"),
  machines: (f: MachineFilters) => get<MachineList>("/v1/machines", { ...f }),
  machine: (id: string) => get<MachineView>(`/v1/machines/${enc(id)}`),
  batches: (q: { limit?: number; vendor?: string }) => get<BatchList>("/v1/batches", q),
  batch: (id: string) => get<BatchStatus>(`/v1/batches/${enc(id)}`),
  deadLetters: (q: { limit?: number; vendor?: string; error_code?: string }) => get<DeadLetterList>("/v1/dead-letters", q),
  replay: (rawId: number) => request<{ raw_id: number; status: string }>("POST", `/v1/dead-letters/${rawId}/replay`).then((r) => r.data),
  trace: (vendor: VendorSlug, id: string) => get<RecordTrace>(`/v1/records/${vendor}/${enc(id)}`),
  samples: () => get<SampleList>("/v1/samples"),
  sample: (name: string) => get<Sample>(`/v1/samples/${enc(name)}`),
  ingest: (vendor: VendorSlug, raw: string | object) => request<BatchAccepted>("POST", `/v1/ingest/${vendor}`, { body: raw }).then((r) => r.data),
  validate: (vendor: VendorSlug, raw: string | object) => request<ValidationReport>("POST", `/v1/ingest/${vendor}/validate`, { body: raw }).then((r) => r.data),
};
