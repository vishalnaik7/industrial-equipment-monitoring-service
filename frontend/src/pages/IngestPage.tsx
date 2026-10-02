import { useDeferredValue, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useBatch, useIngest, useInvalidateDerived, useMeta, useSamples, useValidate } from "../api/hooks";
import { VENDORS, type BatchAccepted, type Sample, type ValidationReport, type VendorSlug } from "../api/types";
import { BatchChip, Chip } from "../components/badges";
import { ErrorBox, Skeleton } from "../components/feedback";
import { Icon, type IconName } from "../components/Icon";
import { useToast } from "../components/Toasts";
import { checkBatch, type CheckLevel } from "../features/ingest/checks";
import { vendorLabel } from "../lib/format";
import { storage } from "../lib/storage";

const DRAFT = "aurik.ingestDraft";
const CHECK_ICON: Record<CheckLevel, IconName> = { ok: "check", bad: "octagon", warn: "alert", info: "info", idle: "info" };

export default function IngestPage() {
  const draft = storage.getJson<{ vendor: VendorSlug; body: string }>(DRAFT);
  const [vendor, setVendor] = useState<VendorSlug>(draft && VENDORS.includes(draft.vendor) ? draft.vendor : "pulseforge");
  const [body, setBody] = useState(draft?.body ?? "");
  const [sample, setSample] = useState<Sample | null>(null);
  const [submitted, setSubmitted] = useState<BatchAccepted[]>([]);
  const [report, setReport] = useState<ValidationReport | null>(null);
  const meta = useMeta().data;
  const samples = useSamples();
  const ingest = useIngest();
  const validate = useValidate();
  const toast = useToast();

  const deferred = useDeferredValue(body);
  const check = useMemo(() => checkBatch(deferred, vendor, meta?.max_records_per_batch ?? 5000), [deferred, vendor, meta]);
  const live = deferred === body;

  useEffect(() => { storage.set(DRAFT, JSON.stringify({ vendor, body })); }, [vendor, body]);
  const resetResults = () => { setReport(null); validate.reset(); ingest.reset(); };

  async function loadSample(name: string) {
    if (!name) return;
    try {
      const s = await api.sample(name);
      setSample(s);
      const v = (s.bodies[vendor] ? vendor : Object.keys(s.bodies)[0]) as VendorSlug;
      setVendor(v); setBody(JSON.stringify(s.bodies[v], null, 2)); resetResults();
    } catch (e) { toast((e as Error).message, "bad"); }
  }

  async function sendAll() {
    if (!sample) return;
    const out: BatchAccepted[] = [];
    for (const [v, b] of Object.entries(sample.bodies) as [VendorSlug, object][]) {
      try {
        const r = await ingest.mutateAsync({ vendor: v, body: b });
        out.push(r);
        toast(`${vendorLabel(v)}: ${r.accepted_records} accepted${r.replayed ? " (replay)" : ""}`);
      } catch (e) { toast(`${vendorLabel(v)}: ${(e as Error).message}`, "bad"); }
    }
    setReport(null); setSubmitted(out);
  }

  async function onValidate() {
    if (!check.ok) return;
    setSubmitted([]);
    setReport(await validate.mutateAsync({ vendor, body }).catch(() => null));
  }

  async function onSubmit() {
    if (!check.ok) return;
    try {
      const r = await ingest.mutateAsync({ vendor, body });
      toast(r.replayed ? "Identical batch already received - returning original" : `Accepted ${r.accepted_records} record(s)`);
      setReport(null); setSubmitted([r]);
    } catch { /* shown below */ }
  }

  function format() {
    try { setBody(JSON.stringify(JSON.parse(body), null, 2)); } catch { toast("Not valid JSON - cannot format", "bad"); }
  }

  const busy = ingest.isPending || validate.isPending;
  return (
    <>
      <div className="page-head"><div><h1>Ingest vendor data</h1>
        <p>Paste or load a vendor batch, check it, dry-run it against the real normalizer, then submit. Processing is asynchronous.</p></div></div>

      <div className="grid grid-2">
        <div className="card">
          <div className="card-head" style={{ flexWrap: "wrap" }}>
            <div className="seg" role="group" aria-label="Vendor">
              {VENDORS.map((v) => <button key={v} type="button" aria-pressed={v === vendor}
                onClick={() => { setVendor(v); resetResults(); }}>{vendorLabel(v)}</button>)}
            </div>
            <div className="row">
              <select className="select" aria-label="Load sample payload" value="" disabled={!samples.data}
                onChange={(e) => loadSample(e.target.value)}>
                <option value="">Load sample...</option>
                {samples.data?.items.map((s) => (
                  <option key={s.name} value={s.name}>{s.name.replace(/_/g, " ")} ({s.vendors.map(vendorLabel).join(", ")})</option>
                ))}
              </select>
              <button className="btn btn-sm" type="button" onClick={format} title="Pretty-print JSON">Format</button>
            </div>
          </div>

          {sample && (
            <div className="card-pad" style={{ padding: "10px 20px 0" }}>
              <div className="row">
                <span className="faint" style={{ fontSize: 12.5 }}>Sample <b>{sample.name}</b> contains:</span>
                {(Object.keys(sample.bodies) as VendorSlug[]).map((v) => (
                  <button key={v} className="btn btn-sm" type="button" aria-pressed={v === vendor}
                    onClick={() => { setVendor(v); setBody(JSON.stringify(sample.bodies[v], null, 2)); resetResults(); }}>{vendorLabel(v)}</button>
                ))}
                {Object.keys(sample.bodies).length > 1 && (
                  <button className="btn btn-sm btn-primary" type="button" onClick={sendAll} disabled={busy}>
                    <Icon name="play" />Send all {Object.keys(sample.bodies).length}</button>
                )}
              </div>
            </div>
          )}

          <div className="card-pad">
            <div className="field">
              <label htmlFor="body">Request body <span className="faint">→ POST /v1/ingest/{vendor}</span></label>
              <textarea id="body" className="textarea" spellCheck={false} value={body}
                aria-invalid={!check.ok && body.trim() !== ""} aria-describedby="body-hint"
                placeholder='{ "vendor": "PulseForge", "plant_id": "PLANT_01", "events": [ ... ] }'
                onChange={(e) => { setBody(e.target.value); resetResults(); }}
                onKeyDown={(e) => {
                  if (e.key === "Tab") { e.preventDefault(); e.currentTarget.setRangeText("  ", e.currentTarget.selectionStart, e.currentTarget.selectionEnd, "end"); setBody(e.currentTarget.value); }
                  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); onSubmit(); }
                }} />
              <span className="hint" id="body-hint">{body && `${new Blob([body]).size.toLocaleString()} bytes`}</span>
            </div>
            <div className="row" style={{ marginTop: 14 }}>
              <button className="btn" type="button" disabled={!check.ok || busy} onClick={onValidate}><Icon name="shield" />Dry-run validate</button>
              <button className="btn btn-primary" type="button" disabled={!check.ok || busy} onClick={onSubmit}><Icon name="upload" />Submit batch</button>
              <span className="faint" style={{ fontSize: 12, marginLeft: "auto" }}>Ctrl+Enter to submit</span>
            </div>
          </div>
        </div>

        <div className="stack">
          <div className="card">
            <div className="card-head"><h2>Pre-flight checks</h2><span className="sub">{live ? "client-side, live" : "checking..."}</span></div>
            <div className="card-pad"><div className="checks">
              {check.items.map((c, i) => (
                <div key={i} className={`check ${c.level}`}><Icon name={CHECK_ICON[c.level]} /><div>{c.text}</div></div>
              ))}
              {check.suggest && check.suggest !== vendor && (
                <button className="btn btn-sm" type="button" style={{ alignSelf: "flex-start" }}
                  onClick={() => setVendor(check.suggest!)}>Switch to {vendorLabel(check.suggest)}</button>
              )}
            </div></div>
          </div>

          {validate.isPending && <div className="card card-pad"><Skeleton height={120} /></div>}
          {(validate.error || ingest.error) && <div className="card card-pad"><ErrorBox error={validate.error ?? ingest.error} /></div>}
          {report && <ValidationResult r={report} />}
          {submitted.map((b) => <BatchProgress key={b.batch_id} accepted={b} />)}
        </div>
      </div>
    </>
  );
}

const OUTCOME = { valid: ["ok", "Valid"], valid_with_warnings: ["warn", "Warnings"], rejected: ["bad", "Rejected"] } as const;

function ValidationResult({ r }: { r: ValidationReport }) {
  return (
    <div className="card">
      <div className="card-head"><h2>Dry-run result</h2><span className="sub">nothing was stored</span></div>
      <div className="card-pad" style={{ paddingBottom: 8 }}><div className="row">
        <Chip tone="ok">{r.valid} valid</Chip><Chip tone="warn">{r.valid_with_warnings} with warnings</Chip>
        <Chip tone={r.rejected ? "bad" : ""}>{r.rejected} rejected</Chip>
      </div></div>
      <div className="table-wrap"><table>
        <thead><tr><th>#</th><th>Record</th><th>Outcome</th><th>Store as</th><th>Details</th></tr></thead>
        <tbody>{r.records.map((x) => (
          <tr key={x.index}>
            <td className="faint">{x.index}</td>
            <td className="mono">{x.record_id ?? "-"}</td>
            <td><Chip tone={OUTCOME[x.outcome][0]}>{OUTCOME[x.outcome][1]}</Chip></td>
            <td className="nowrap">{x.store_action.replace(/_/g, " ")}</td>
            <td style={{ fontSize: 12.5 }}>
              {x.outcome === "rejected"
                ? <><b className="mono">{x.error_code}</b><div className="faint">{x.error_message}</div></>
                : <>{x.machine_id} · <span className="mono">{x.event_code}</span> · {x.event_time}
                    {x.warnings.length > 0 && <div className="row" style={{ marginTop: 4 }}>{x.warnings.map((w) => <Chip key={w} tone="warn">{w}</Chip>)}</div>}</>}
            </td>
          </tr>
        ))}</tbody>
      </table></div>
    </div>
  );
}

const SEGMENTS: [string, string][] = [["processed", "var(--st-good)"], ["duplicate", "var(--st-neutral)"], ["superseded", "var(--st-neutral)"],
  ["rejected", "var(--st-critical)"], ["dead", "var(--st-critical)"], ["processing", "var(--st-warning)"], ["pending", "var(--surface-3)"]];

/** Live progress of a just-submitted batch (useBatch polls while the worker is processing). */
function BatchProgress({ accepted }: { accepted: BatchAccepted }) {
  const s = useBatch(accepted.batch_id).data;
  const invalidate = useInvalidateDerived();
  const finished = !!s && s.state !== "processing";
  // Processing is asynchronous: refresh machines, summaries and dead letters when the worker has FINISHED the batch,
  // not only when it was accepted.
  useEffect(() => { if (finished) void invalidate(); }, [finished]);   // runs once, when the batch finishes
  const counts = s?.record_status_counts ?? {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1;
  return (
    <div className="card">
      <div className="card-head"><h2>{accepted.vendor} batch</h2>{s ? <BatchChip state={s.state} /> : <BatchChip state="processing" />}</div>
      <div className="card-pad stack" style={{ gap: 12 }}>
        <div className="kv">
          <div><span>Received</span><b>{accepted.total_records}</b></div><div><span>Accepted</span><b>{accepted.accepted_records}</b></div>
          <div><span>Duplicates</span><b>{accepted.duplicate_records}</b></div><div><span>Revisions</span><b>{accepted.revised_records}</b></div>
        </div>
        <div className="progress" role="img" aria-label="Processing progress">
          {SEGMENTS.filter(([k]) => counts[k]).map(([k, color]) => (
            <div key={k} style={{ width: `${(counts[k] / total) * 100}%`, background: color }} title={`${k}: ${counts[k]}`} />
          ))}
        </div>
        <div className="faint" style={{ fontSize: 12.5 }}>
          {s ? (Object.entries(counts).map(([k, v]) => `${k} ${v}`).join(" · ") || "no new records (all duplicates)") : "Waiting for the worker..."}
        </div>
        {s && s.errors.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Record</th><th>Status</th><th>Error</th></tr></thead>
            <tbody>{s.errors.map((e) => (
              <tr key={e.raw_id}><td className="mono">{e.vendor_record_id}</td><td>{e.status}</td>
                <td><b className="mono">{e.error_code}</b><div className="faint">{e.error_message}</div></td></tr>
            ))}</tbody>
          </table></div>
        )}
        <div className="row"><Link className="btn btn-sm" to={`/batches/${accepted.batch_id}`}>Open batch</Link>
          <span className="faint mono" style={{ fontSize: 11.5 }}>{accepted.batch_id}</span></div>
      </div>
    </div>
  );
}
