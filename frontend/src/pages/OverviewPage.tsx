import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useReady, useReference, useSummary } from "../api/hooks";
import type { DataQuality, Summary } from "../api/types";
import { AttentionMeter, Chip } from "../components/badges";
import { EmptyState, QueryState, Skeleton } from "../components/feedback";
import { Icon, type IconName } from "../components/Icon";
import { TimeAgo } from "../components/TimeAgo";
import { RankedLines } from "../charts/RankedLines";
import { StatusDistribution } from "../charts/StatusDistribution";
import { pct } from "../lib/format";
import { usePreferences } from "../lib/preferences";
import type { Tone } from "../lib/status";

const QUALITY: [keyof DataQuality, string, Tone, string][] = [
  ["failed_machines", "Failed", "bad", "Records exhausted retries"],
  ["partial_machines", "Partial", "warn", "Some records rejected (bad data)"],
  ["stale_machines", "Stale", "warn", "Latest data older than the freshness window"],
  ["pending_machines", "Processing", "warn", "Records still queued"],
  ["no_data_machines", "No data", "", "No processed events yet"],
];

export default function OverviewPage() {
  const [params, setParams] = useSearchParams();
  const plant = params.get("plant") ?? "";
  const { autoRefresh } = usePreferences();
  const summary = useSummary(plant || undefined, autoRefresh);
  const plants = useReference().data?.plants.map((p) => p.plant_id) ?? [];

  return (
    <>
      <div className="page-head">
        <div><h1>Operations overview</h1><p>Derived machine attention across plants and lines.</p></div>
        <div className="seg" role="group" aria-label="Plant scope">
          {["", ...plants].map((p) => (
            <button key={p || "all"} type="button" aria-pressed={p === plant}
              onClick={() => setParams(p ? { plant: p } : {})}>{p || "All plants"}</button>
          ))}
        </div>
      </div>
      <QueryState query={summary} skeleton={<div className="stack"><Skeleton height={96} /><Skeleton height={260} /></div>}>
        {(s) => <OverviewBody s={s} />}
      </QueryState>
    </>
  );
}

function OverviewBody({ s }: { s: Summary }) {
  const ready = useReady().data;
  const navigate = useNavigate();
  const crit = s.critical_machines;
  const dq = s.data_quality;
  const dqCount = dq.failed_machines.length + dq.partial_machines.length + dq.stale_machines.length;

  return (
    <div className="stack">
      <section className="grid grid-kpi" aria-label="Key figures">
        <Kpi icon="cpu" label="Machines monitored" value={s.machines_total} foot={`${s.by_status.healthy ?? 0} healthy`} />
        <Kpi icon="alert" label="Need attention" value={s.machines_needing_attention}
          foot={`${pct(s.machines_needing_attention, s.machines_total)} of fleet`} alert={s.machines_needing_attention > 0} />
        <Kpi icon="octagon" label="Critical now" value={crit.length}
          foot={crit.length ? crit.map((m) => m.machine_id).join(", ") : "none"} alert={crit.length > 0} />
        <Kpi icon="layers" label="Processing queue" value={ready?.queue_depth ?? "-"}
          foot={ready?.queue_depth ? "records in flight" : "worker idle - all processed"} />
        <Kpi icon="inbox" label="Dead letters" value={ready?.dead_letters ?? "-"} to="/dead-letters"
          foot={ready?.dead_letters ? "rejected or failed records" : "nothing to review"} />
      </section>

      <section className="grid grid-2">
        <div className="card">
          <div className="card-head"><h2>Machines by derived status</h2>
            <span className="sub">{s.machines_total} machines · generated <TimeAgo iso={s.generated_at} /></span></div>
          <div className="chart"><StatusDistribution byStatus={s.by_status} /></div>
          <div className="card-head" style={{ paddingTop: 4 }}><h2>Lines ranked by attention</h2>
            <span className="sub legend">
              <span className="legend-item"><span className="sw" style={{ background: "var(--series-1)" }} />needing attention / total</span>
              <span className="legend-item"><span className="sw fill-critical" />line has a critical machine</span>
            </span>
          </div>
          <div className="chart">
            {s.lines_ranked_by_attention.length ? <RankedLines lines={s.lines_ranked_by_attention} /> : <EmptyState title="No lines" />}
          </div>
        </div>

        <div className="stack">
          <div className="card">
            <div className="card-head"><h2>Critical machines</h2><Link to="/machines?status=critical" className="sub">View all</Link></div>
            <div className="list" style={{ marginTop: 8 }}>
              {crit.length === 0 && <EmptyState title="No critical machines" icon="shield" />}
              {crit.map((m) => (
                <button type="button" key={m.machine_id} className="list-item clickable" style={btnReset}
                  onClick={() => navigate(`/machines/${encodeURIComponent(m.machine_id)}`)}>
                  <AttentionMeter level={m.attention_level} label="critical" />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <b>{m.machine_id}</b> <span className="faint">{m.line_id}</span>
                    <div className="row" style={{ marginTop: 4 }}>
                      {m.reason_codes.slice(0, 2).map((c) => <Chip key={c}>{c}</Chip>)}
                      {m.reason_codes.length > 2 && <Chip>+{m.reason_codes.length - 2}</Chip>}
                    </div>
                  </div>
                  <span className="faint nowrap" style={{ fontSize: 12 }}><TimeAgo iso={m.latest_relevant_event_time} /></span>
                </button>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="card-head"><h2>Data quality &amp; freshness</h2><span className="sub">{dqCount ? `${dqCount} need a look` : "all clear"}</span></div>
            <div className="list" style={{ marginTop: 8 }}>
              {QUALITY.map(([k, label, tone, hint]) => (
                <div key={k} className="list-item">
                  <span style={{ width: 112, flex: "none", display: "flex" }}>
                    <Chip tone={dq[k].length ? tone : ""}>{label} · {dq[k].length}</Chip>
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="faint" style={{ fontSize: 12 }}>{hint}</div>
                    <div className="row" style={{ marginTop: 3 }}>
                      {dq[k].length ? dq[k].map((id) => <Link key={id} to={`/machines/${id}`} className="mono">{id}</Link>) : <span className="faint">-</span>}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

const btnReset: React.CSSProperties = { width: "100%", border: 0, background: "transparent", font: "inherit", color: "inherit", textAlign: "left" };

function Kpi({ icon, label, value, foot, alert = false, to }: {
  icon: IconName; label: string; value: React.ReactNode; foot: string; alert?: boolean; to?: string;
}) {
  const body = <>
    <span className="label"><Icon name={icon} />{label}</span>
    <span className="value">{value}</span>
    <span className="foot">{foot}</span>
  </>;
  const cls = `card kpi${alert ? " alert" : ""}`;
  return to ? <Link to={to} className={cls} style={{ color: "inherit", textDecoration: "none" }}>{body}</Link> : <div className={cls}>{body}</div>;
}
