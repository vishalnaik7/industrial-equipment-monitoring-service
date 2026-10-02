import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useDeadLetters, useReplay } from "../api/hooks";
import { VENDORS, type DeadLetter } from "../api/types";
import { Chip } from "../components/badges";
import { EmptyState, QueryState, Skeleton } from "../components/feedback";
import { Icon } from "../components/Icon";
import { JsonView } from "../components/JsonView";
import { TimeAgo } from "../components/TimeAgo";
import { useToast } from "../components/Toasts";
import { vendorLabel, vendorSlug } from "../lib/format";

export default function DeadLettersPage() {
  const [params, setParams] = useSearchParams();
  const vendor = params.get("vendor") ?? "";
  const code = params.get("error_code") ?? "";
  const all = useDeadLetters(vendor || undefined);                   // unfiltered by code: feeds the code dropdown
  const filtered = useDeadLetters(vendor || undefined, code || undefined);

  const codes = useMemo(() => {
    const m = new Map<string, number>();
    for (const i of all.data?.items ?? []) if (i.error_code) m.set(i.error_code, (m.get(i.error_code) ?? 0) + 1);
    return [...m.entries()].sort();
  }, [all.data]);

  const update = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    v ? next.set(k, v) : next.delete(k);
    if (k === "vendor") next.delete("error_code");
    setParams(next, { replace: true });
  };

  return (
    <>
      <div className="page-head"><div><h1>Dead letters</h1>
        <p><b>Rejected</b> = permanent data problem (fix the data or reference, then replay). <b>Dead</b> = transient failures exhausted retries.</p></div></div>
      <div className="toolbar">
        <div className="field"><label htmlFor="dl-v">Vendor</label>
          <select id="dl-v" className="select" value={vendor} onChange={(e) => update("vendor", e.target.value)}>
            <option value="">All vendors</option>{VENDORS.map((v) => <option key={v} value={v}>{vendorLabel(v)}</option>)}
          </select></div>
        <div className="field"><label htmlFor="dl-c">Error code</label>
          <select id="dl-c" className="select" value={code} onChange={(e) => update("error_code", e.target.value)}>
            <option value="">Any code</option>{codes.map(([c, n]) => <option key={c} value={c}>{c} ({n})</option>)}
          </select></div>
      </div>
      <QueryState query={filtered} skeleton={<div className="stack"><Skeleton height={140} count={2} /></div>}>
        {(d) => d.items.length === 0
          ? <div className="card"><EmptyState title="No dead letters" hint="Every received record was processed." icon="shield" /></div>
          : <div className="stack">{d.items.map((x) => <DeadLetterCard key={x.raw_id} d={x} />)}</div>}
      </QueryState>
    </>
  );
}

function DeadLetterCard({ d }: { d: DeadLetter }) {
  const replay = useReplay();
  const toast = useToast();
  const queued = replay.isSuccess;
  async function onReplay() {
    try { await replay.mutateAsync(d.raw_id); toast(`Record ${d.vendor_record_id} re-queued - the worker will retry it`); }
    catch (e) { toast((e as Error).message, "bad"); }
  }
  return (
    <div className="card" style={queued ? { opacity: 0.55 } : undefined}>
      <div className="card-head" style={{ flexWrap: "wrap", paddingBottom: 4 }}>
        <div className="row">
          <Chip tone={d.status === "dead" ? "bad" : "warn"}>{d.status}</Chip>
          <b className="mono">{d.error_code}</b>
          <span className="faint">{d.vendor} · <Link className="mono" to={`/trace/${vendorSlug(d.vendor)}/${encodeURIComponent(d.vendor_record_id)}`}>{d.vendor_record_id}</Link> · rev {d.revision}</span>
        </div>
        <button className="btn btn-sm" onClick={onReplay} disabled={replay.isPending || queued}>
          <Icon name="replay" />{queued ? "Queued" : "Replay"}</button>
      </div>
      <div className="card-pad" style={{ paddingTop: 6 }}>
        <div className="muted">{d.error_message}</div>
        <div className="faint" style={{ fontSize: 12, marginTop: 4 }}>
          received <TimeAgo iso={d.received_at} /> · attempts {d.attempts} · <Link to={`/batches/${d.batch_id}`}>batch</Link></div>
        <details style={{ marginTop: 10 }}><summary>Raw payload as received</summary>
          <div style={{ marginTop: 8 }}><JsonView value={d.payload} /></div></details>
      </div>
    </div>
  );
}
