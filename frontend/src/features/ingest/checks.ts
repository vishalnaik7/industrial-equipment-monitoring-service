// Client-side pre-flight checks for a vendor batch. Mirrors the backend envelope contract
// (backend/app/schemas/vendors.py) for fast feedback only - the server re-validates everything.
import type { VendorSlug } from "../../api/types";

export interface VendorSpec { key: string; nameField: string; name: string; required: string[] }

export const SPEC: Record<VendorSlug, VendorSpec> = {
  pulseforge: { key: "events", nameField: "vendor", name: "PulseForge", required: ["event_id", "machine_id", "event_time"] },
  thermexwatch: { key: "readings", nameField: "source", name: "ThermexWatch", required: ["readingId", "assetCode", "timestampMs"] },
  maintaflow: { key: "records", nameField: "provider_name", name: "MaintaFlow", required: ["record_id", "machine_ref", "recorded_at", "record_type"] },
  workorders: { key: "work_orders", nameField: "source", name: "CMMS", required: ["work_order_id", "machine_id", "opened_at"] },
};

export type CheckLevel = "ok" | "bad" | "warn" | "info" | "idle";
export interface CheckItem { level: CheckLevel; text: string; code?: string }
export interface CheckResult { ok: boolean; items: CheckItem[]; suggest?: VendorSlug; recordCount?: number }

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const missing = (v: unknown) => v === undefined || v === null || v === "";

export function checkBatch(text: string, vendor: VendorSlug, maxRecords: number): CheckResult {
  const spec = SPEC[vendor];
  const items: CheckItem[] = [];
  let ok = true, suggest: VendorSlug | undefined;
  const bad = (t: string, code?: string) => { items.push({ level: "bad", text: t, code }); ok = false; };

  if (!text.trim()) return { ok: false, items: [{ level: "idle", text: "Paste a JSON body or load a sample to begin." }] };

  let body: unknown;
  try { body = JSON.parse(text); items.push({ level: "ok", text: "Valid JSON" }); }
  catch (e) { bad(`Invalid JSON - ${jsonErrorWhere(e as Error, text)}`, "INVALID_JSON"); return { ok, items }; }

  if (!isObj(body)) { bad("Body must be a JSON object (the vendor envelope), not an array or value.", "NOT_OBJECT"); return { ok, items }; }

  for (const [v, s] of Object.entries(SPEC) as [VendorSlug, VendorSpec][]) {
    if (v !== vendor && Array.isArray(body[s.key]) && !Array.isArray(body[spec.key])) suggest = v;
  }
  const claimed = body[spec.nameField];
  if (claimed != null && String(claimed).toLowerCase() !== spec.name.toLowerCase()) {
    bad(`"${spec.nameField}" is "${String(claimed)}" but this is the ${spec.name} endpoint.`, "VENDOR_MISMATCH");
    const match = (Object.entries(SPEC) as [VendorSlug, VendorSpec][]).find(([, s]) => String(claimed).toLowerCase() === s.name.toLowerCase());
    if (match) suggest = match[0];
  }

  const recs = body[spec.key];
  if (recs === undefined) { bad(`Missing "${spec.key}" - ${spec.name} batches carry their records there.`, "MISSING_RECORDS"); return { ok, items, suggest }; }
  if (!Array.isArray(recs)) { bad(`"${spec.key}" must be a list.`, "RECORDS_NOT_LIST"); return { ok, items, suggest }; }
  if (!recs.length) { bad(`"${spec.key}" is empty - nothing to ingest.`, "EMPTY_BATCH"); return { ok, items, suggest }; }
  if (recs.length > maxRecords) bad(`${recs.length} records exceeds the ${maxRecords} per-batch limit - split the batch.`, "BATCH_TOO_LARGE");
  else items.push({ level: "ok", text: `${recs.length} record${recs.length === 1 ? "" : "s"} in "${spec.key}"` });

  const nonObj = recs.flatMap((r, i) => (isObj(r) ? [] : [i]));
  if (nonObj.length) bad(`Record${nonObj.length > 1 ? "s" : ""} at index ${nonObj.slice(0, 5).join(", ")} ${nonObj.length > 1 ? "are" : "is"} not an object.`, "RECORD_NOT_OBJECT");

  const objs = recs.filter(isObj);
  const incomplete = objs.filter((r) => spec.required.some((f) => missing(r[f])));
  if (incomplete.length) {
    items.push({ level: "warn", code: "MISSING_REQUIRED", text: `${incomplete.length} record(s) missing a required field (${spec.required.join(", ")}). They will be accepted, then rejected by the worker into dead letters.` });
  } else if (objs.length) items.push({ level: "ok", text: "Every record has its required fields" });

  const ids = objs.map((r) => r[spec.required[0]]).filter((v) => !missing(v)).map(String);
  const dup = ids.length - new Set(ids).size;
  if (dup) items.push({ level: "info", code: "DUPLICATE_IDS", text: `${dup} repeated record id(s) in this batch - identical copies are de-duplicated, changed ones become revisions.` });
  items.push({ level: "info", text: "Field values (units, enums, timestamps) are checked by the normalizer - use Dry-run validate to preview." });
  return { ok, items, suggest, recordCount: recs.length };
}

/** Best-effort location of a JSON.parse error. Engines differ: V8 reports "position N" (only for longer
 * inputs), Firefox reports "line L column C", some report neither. */
export function jsonErrorWhere(e: Error, text: string): string {
  const firefox = /^(?:JSON\.parse: )?(.*?) at line (\d+) column (\d+)/.exec(e.message);
  if (firefox) return `${firefox[1]} at line ${firefox[2]}, column ${firefox[3]}`;
  const v8 = /position (\d+)/.exec(e.message);
  const reason = e.message.split(/,? "| in JSON at/)[0];
  if (!v8) return reason;
  const pos = Number(v8[1]), before = text.slice(0, pos);
  return `${reason} at line ${before.split("\n").length}, column ${pos - before.lastIndexOf("\n")}`;
}
