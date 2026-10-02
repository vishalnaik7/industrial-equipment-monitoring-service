import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTrace } from "../api/hooks";
import { VENDORS, type RecordTrace, type VendorSlug } from "../api/types";
import { Chip } from "../components/badges";
import { EmptyState, QueryState, Skeleton } from "../components/feedback";
import { Icon } from "../components/Icon";
import { JsonView } from "../components/JsonView";
import { TimeAgo } from "../components/TimeAgo";
import { vendorLabel } from "../lib/format";

export default function TracePage() {
  const params = useParams();
  const vendor = (VENDORS.includes(params.vendor as VendorSlug) ? params.vendor : "pulseforge") as VendorSlug;
  const recordId = params.recordId ?? "";
  const navigate = useNavigate();
  const q = useTrace(recordId ? vendor : undefined, recordId || undefined);

  const [v, setV] = useState<VendorSlug>(vendor);
  const [id, setId] = useState(recordId);
  const [touched, setTouched] = useState(false);
  const invalid = touched && !id.trim();
  useEffect(() => { setV(vendor); setId(recordId); }, [vendor, recordId]);     // follow in-app trace links

  return (
    <>
      <div className="page-head"><div><h1>Record trace</h1>
        <p>From the exact bytes a vendor sent, through every revision, to the canonical event.</p></div></div>
      <form className="toolbar card card-pad" style={{ padding: "14px 16px" }} noValidate onSubmit={(e) => {
        e.preventDefault(); setTouched(true);
        if (id.trim()) navigate(`/trace/${v}/${encodeURIComponent(id.trim())}`);
      }}>
        <div className="field"><label htmlFor="t-v">Vendor</label>
          <select id="t-v" className="select" value={v} onChange={(e) => setV(e.target.value as VendorSlug)}>
            {VENDORS.map((x) => <option key={x} value={x}>{vendorLabel(x)}</option>)}
          </select></div>
        <div className="field grow"><label htmlFor="t-id">Vendor record id</label>
          <input id="t-id" className="input" value={id} maxLength={200} autoComplete="off" required aria-invalid={invalid}
            placeholder="e.g. PF-1001, TW-8801, MF-501" onChange={(e) => setId(e.target.value)} />
          {invalid && <span className="error-text">Enter a record id.</span>}</div>
        <div className="field"><label>&nbsp;</label><button className="btn btn-primary" type="submit"><Icon name="search" />Trace</button></div>
      </form>
      {!recordId
        ? <EmptyState title="Enter a vendor record id" hint="Reason and signal references on the machine view link here too." icon="link" />
        : <QueryState query={q} skeleton={<Skeleton height={240} />}>{(t) => <TraceBody t={t} />}</QueryState>}
    </>
  );
}

function TraceBody({ t }: { t: RecordTrace }) {
  const ev = t.normalized_event;
  return (
    <div className="grid grid-2">
      <div className="card">
        <div className="card-head"><h2>Raw revisions</h2><span className="sub">{t.raw_revisions.length} received · newest vendor version, then record time, wins</span></div>
        <div className="card-pad"><div className="timeline">
          {t.raw_revisions.map((r) => (
            <div key={r.raw_id} className="tl-item">
              <div className="tl-dot">{r.revision}</div>
              <div>
                <div className="row"><b>Revision {r.revision}</b>
                  {r.authoritative && <Chip tone="ok">authoritative</Chip>}
                  {!!r.is_latest && !r.authoritative && <Chip>latest received</Chip>}
                  <Chip tone={r.status === "rejected" || r.status === "dead" ? "bad" : r.status === "processed" ? "ok" : ""}>{r.status}</Chip>
                  {r.error_code && <Chip tone="warn">{r.error_code}</Chip>}
                </div>
                <div className="faint" style={{ fontSize: 12, margin: "3px 0 8px" }}>
                  received <TimeAgo iso={r.received_at} /> · vendor version {r.source_version ?? "none"} · attempts {r.attempts} · <Link to={`/batches/${r.batch_id}`}>batch</Link>
                  {r.error_message && <div className="muted">{r.error_message}</div>}
                </div>
                <JsonView value={r.payload} />
              </div>
            </div>
          ))}
        </div></div>
      </div>

      <div className="card">
        <div className="card-head"><h2>Canonical event</h2>{ev && <Link className="sub" to={`/machines/${ev.machine_id}`}>{ev.machine_id} &rarr;</Link>}</div>
        <div className="card-pad stack" style={{ gap: 12 }}>
          {!ev ? <EmptyState title="No canonical event" hint="The record was rejected, superseded or is still queued." icon="alert" /> : <>
            <div className="kv">
              <div><span>Machine</span><b>{ev.machine_id}</b></div><div><span>Line</span><b>{ev.plant_id} / {ev.line_id}</b></div>
              <div><span>Event</span><b className="mono">{ev.event_code}</b></div><div><span>Event time (UTC)</span><b>{ev.event_time}</b></div>
              <div><span>Severity</span><b>{ev.severity ?? "-"}</b></div><div><span>Kind</span><b>{ev.kind}</b></div>
            </div>
            <div><h3 style={{ marginBottom: 6 }}>Normalization warnings</h3><div className="row">
              {ev.warnings.length ? ev.warnings.map((w) => <Chip key={w} tone="warn">{w}</Chip>) : <Chip tone="ok">none</Chip>}
            </div></div>
            <JsonView value={ev} />
          </>}
        </div>
      </div>
    </div>
  );
}
