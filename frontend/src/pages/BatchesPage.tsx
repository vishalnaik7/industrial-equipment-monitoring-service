import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useBatch, useBatches } from "../api/hooks";
import { VENDORS, type BatchStatus } from "../api/types";
import { BatchChip, Chip } from "../components/badges";
import { EmptyState, QueryState, Skeleton } from "../components/feedback";
import { JsonView } from "../components/JsonView";
import { TimeAgo } from "../components/TimeAgo";
import { vendorLabel, vendorSlug } from "../lib/format";

export default function BatchesPage() {
  const { batchId } = useParams();
  return batchId ? <BatchDetail id={batchId} /> : <BatchList />;
}

function BatchList() {
  const [params, setParams] = useSearchParams();
  const vendor = params.get("vendor") ?? "";
  const q = useBatches(vendor || undefined);
  const navigate = useNavigate();
  return (
    <>
      <div className="page-head">
        <div><h1>Ingestion batches</h1><p>Every accepted request and its asynchronous processing state.</p></div>
        <div className="seg" role="group" aria-label="Vendor">
          {["", ...VENDORS].map((v) => <button key={v || "all"} type="button" aria-pressed={v === vendor}
            onClick={() => setParams(v ? { vendor: v } : {}, { replace: true })}>{v ? vendorLabel(v) : "All"}</button>)}
        </div>
      </div>
      <div className="card">
        <QueryState query={q} skeleton={<Skeleton height={200} />}>
          {(d) => d.items.length === 0
            ? <EmptyState title="No batches yet" hint={<><Link to="/ingest">Ingest a sample</Link> to get started.</>} />
            : (
              <div className="table-wrap"><table>
                <thead><tr><th>Received</th><th>Vendor</th><th>State</th><th className="num">Records</th><th className="num">Accepted</th>
                  <th className="num">Duplicates</th><th className="num">Revisions</th><th className="hide-sm">Batch id</th></tr></thead>
                <tbody>{d.items.map((b) => (
                  <tr key={b.batch_id} className="clickable" tabIndex={0} onClick={() => navigate(`/batches/${b.batch_id}`)}
                    onKeyDown={(e) => { if (e.key === "Enter") navigate(`/batches/${b.batch_id}`); }}>
                    <td className="nowrap"><TimeAgo iso={b.received_at} /></td><td><b>{b.vendor}</b></td><td><BatchChip state={b.state} /></td>
                    <td className="num">{b.total_records}</td><td className="num">{b.accepted_records}</td>
                    <td className="num">{b.duplicate_records}</td><td className="num">{b.revised_records}</td>
                    <td className="mono faint hide-sm" style={{ fontSize: 12 }}>{b.batch_id}</td>
                  </tr>
                ))}</tbody>
              </table></div>
            )}
        </QueryState>
      </div>
    </>
  );
}

function BatchDetail({ id }: { id: string }) {
  const q = useBatch(id);
  return (
    <>
      <div className="page-head">
        <div><Link to="/batches">&larr; All batches</Link><h1 style={{ marginTop: 6 }}>Batch</h1><p className="mono">{id}</p></div>
        <button className="btn" onClick={() => q.refetch()} disabled={q.isFetching}>Refresh</button>
      </div>
      <QueryState query={q} skeleton={<Skeleton height={200} />}>{(b) => <BatchBody b={b} />}</QueryState>
    </>
  );
}

function BatchBody({ b }: { b: BatchStatus }) {
  const slug = vendorSlug(b.vendor);
  const counts = Object.entries(b.record_status_counts);
  return (
    <div className="stack">
      <div className="card card-pad"><div className="kv">
        <div><span>Vendor</span><b>{b.vendor}</b></div><div><span>State</span><b><BatchChip state={b.state} /></b></div>
        <div><span>Received</span><b><TimeAgo iso={b.received_at} /></b></div><div><span>Schema</span><b>{b.schema_version}</b></div>
        <div><span>Records</span><b>{b.total_records}</b></div><div><span>Accepted</span><b>{b.accepted_records}</b></div>
        <div><span>Duplicates</span><b>{b.duplicate_records}</b></div><div><span>Revisions</span><b>{b.revised_records}</b></div>
      </div></div>
      <div className="card card-pad"><h3 style={{ marginBottom: 10 }}>Record status</h3><div className="row">
        {counts.length === 0 && <span className="faint">no new records stored (all duplicates)</span>}
        {counts.map(([k, v]) => <Chip key={k} tone={k === "processed" ? "ok" : k === "rejected" || k === "dead" ? "bad" : ""}>{k} · {v}</Chip>)}
      </div></div>
      <div className="card">
        <div className="card-head"><h2>Records needing review</h2><span className="sub">rejected, dead, semantic duplicates or stale revisions</span></div>
        {b.errors.length === 0 ? <EmptyState title="Nothing to review" icon="shield" /> : (
          <div className="table-wrap" style={{ marginTop: 8 }}><table>
            <thead><tr><th>Record</th><th>Status</th><th>Error</th><th className="num">Attempts</th></tr></thead>
            <tbody>{b.errors.map((e) => (
              <tr key={e.raw_id}>
                <td><Link className="mono" to={`/trace/${slug}/${encodeURIComponent(e.vendor_record_id)}`}>{e.vendor_record_id}</Link></td>
                <td>{e.status}</td><td><b className="mono">{e.error_code}</b><div className="faint">{e.error_message}</div></td>
                <td className="num">{e.attempts}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </div>
      <details><summary>Raw API response</summary><div style={{ marginTop: 10 }}><JsonView value={b} /></div></details>
    </div>
  );
}
