import { Link } from "react-router-dom";
import { useMachine } from "../api/hooks";
import type { MachineView, Reason, Signal } from "../api/types";
import { AttentionMeter, Chip, ProcessingChip, StatusBadge } from "../components/badges";
import { AlertBox, EmptyState, QueryState, Skeleton } from "../components/feedback";
import { Icon } from "../components/Icon";
import { JsonView } from "../components/JsonView";
import { Drawer } from "../components/Overlay";
import { TimeAgo } from "../components/TimeAgo";
import { fmtTime, humanAge, vendorSlug } from "../lib/format";

export function MachineDrawer({ machineId, onClose }: { machineId: string; onClose: () => void }) {
  const q = useMachine(machineId);
  return (
    <Drawer onClose={onClose} label={`Machine ${machineId}`}>
      <QueryState query={q} skeleton={<>
        <DrawerHead title={`Loading ${machineId}...`} onClose={onClose} />
        <div className="drawer-body"><Skeleton height={80} count={3} /></div>
      </>}>
        {(m) => <Detail m={m} onClose={onClose} />}
      </QueryState>
    </Drawer>
  );
}

function DrawerHead({ title, children, onClose }: { title: string; children?: React.ReactNode; onClose: () => void }) {
  return (
    <div className="drawer-head">
      <div style={{ flex: 1, minWidth: 0 }}>{children ?? <h2>{title}</h2>}</div>
      <button className="btn btn-icon btn-ghost" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
    </div>
  );
}

const traceLink = (source: string, id: string) => `/trace/${vendorSlug(source)}/${encodeURIComponent(id)}`;

function Detail({ m, onClose }: { m: MachineView; onClose: () => void }) {
  const p = m.processing_status;
  return (
    <>
      <DrawerHead title={m.machine_id} onClose={onClose}>
        <div className="row"><h1 style={{ fontSize: 20 }}>{m.machine_id}</h1><StatusBadge status={m.derived_status} large /></div>
        <div className="faint" style={{ marginTop: 4 }}>{m.machine_type} · {m.plant_id} / {m.line_id} · {m.criticality} criticality</div>
      </DrawerHead>
      <div className="drawer-body">
        {m.needs_attention
          ? <AlertBox tone={m.attention_level >= 3 ? "bad" : "warn"} icon="alert"><b>Needs attention</b> - {m.reason_codes.join(", ")}</AlertBox>
          : <AlertBox tone="ok" icon="check"><b>No attention needed</b>{m.derived_status === "unknown" && " - no processed data yet"}</AlertBox>}

        <div className="card card-pad"><div className="kv">
          <div><span>Attention</span><b><AttentionMeter level={m.attention_level} label={m.attention_label} /> {m.attention_label} ({m.attention_level}/4)</b></div>
          <div><span>Machine state</span><b>{m.machine_state ?? "-"}</b></div>
          <div><span>Latest event</span><b title={fmtTime(m.latest_relevant_event_time)}>{m.latest_relevant_event_time ?? "-"}</b></div>
          <div><span>Processing</span><b><ProcessingChip p={p} /></b></div>
          <div><span>Data age</span><b>{p.data_age_seconds == null ? "-" : humanAge(p.data_age_seconds)}</b></div>
          <div><span>Last processed</span><b><TimeAgo iso={m.last_processed_at} /></b></div>
          <div><span>Last data received</span><b><TimeAgo iso={p.last_received_at} /></b></div>
          <div><span>State version</span><b>v{m.state_version}</b></div>
          <div><span>Rules version</span><b>{m.rules_version ?? "-"}</b></div>
        </div></div>

        <div className="card">
          <div className="card-head"><h2>Why - contributing reasons</h2><span className="sub">{m.reasons.length} total, most severe first</span></div>
          <div className="card-pad" style={{ paddingTop: 6 }}>
            {m.reasons.length ? m.reasons.map((r, i) => <ReasonRow key={i} r={r} />) : <EmptyState title="No reasons" />}
          </div>
        </div>

        <div className="card">
          <div className="card-head"><h2>Latest relevant signals</h2><span className="sub">newest per source</span></div>
          <div className="table-wrap" style={{ marginTop: 8 }}><SignalsTable signals={m.latest_signals} /></div>
        </div>

        <div className="card card-pad">
          <h3 style={{ marginBottom: 10 }}>Source event references</h3>
          <div className="row">
            {m.source_event_refs.length === 0 && <span className="faint">none</span>}
            {m.source_event_refs.map((r) => (
              <Link key={`${r.source}/${r.record_id}`} className="chip" to={traceLink(r.source, r.record_id)} onClick={onClose}>
                <Icon name="link" size={12} />&nbsp;{r.source} · {r.record_id}
              </Link>
            ))}
          </div>
        </div>

        <details><summary>Raw API response</summary><div style={{ marginTop: 10 }}><JsonView value={m} /></div></details>
      </div>
    </>
  );
}

function ReasonRow({ r }: { r: Reason }) {
  return (
    <div className="reason">
      <div><AttentionMeter level={r.level} label={r.level_label} />
        <div className="faint" style={{ fontSize: 11.5, marginTop: 3 }}>{r.level_label}</div></div>
      <div>
        <div className="code">{r.code}</div>
        <div className="detail">{r.detail}</div>
        <div className="src">
          {r.source}
          {r.record_id && <> · <Link to={traceLink(r.source, r.record_id)}>{r.record_id}</Link></>}
          {r.event_time && <> · {r.event_time}</>}
        </div>
      </div>
    </div>
  );
}

const SIG_COLS: [keyof Signal, string][] = [["temperature_c", "Temp °C"], ["vibration_mm_s", "Vib mm/s"], ["vibration_g", "Vib g"], ["power_kw", "Power kW"]];
const SIG_EXTRA: (keyof Signal)[] = ["machine_state", "signal_quality", "inspection_result", "maintenance_status", "days_since_service", "note"];

function SignalsTable({ signals }: { signals: Signal[] }) {
  if (!signals.length) return <EmptyState title="No relevant signals" />;
  return (
    <table>
      <thead><tr><th>Source / event</th>{SIG_COLS.map(([, l]) => <th key={l} className="num">{l}</th>)}<th>Time</th></tr></thead>
      <tbody>
        {signals.map((s) => {
          const extra = SIG_EXTRA.filter((k) => s[k] != null);
          return (
            <tr key={`${s.source}/${s.record_id}`}>
              <td style={{ minWidth: 220 }}>
                <div className="row"><b>{s.source}</b><Chip>{s.event_code}</Chip></div>
                <div className="faint mono" style={{ fontSize: 11.5, marginTop: 2 }}>{s.record_id}</div>
                {extra.length > 0 && <div style={{ fontSize: 12, marginTop: 4 }}>
                  {extra.map((k, i) => <span key={k}>{i > 0 && " · "}<span className="faint">{k.replace(/_/g, " ")}:</span> {String(s[k])}</span>)}
                </div>}
                {!!s.warnings?.length && <div className="row" style={{ marginTop: 4 }} title="Normalization warnings for this reading">
                  {s.warnings.map((w) => <Chip key={w} tone="warn">{w}</Chip>)}
                </div>}
              </td>
              {SIG_COLS.map(([k]) => <td key={k} className="num">{s[k] == null ? <span className="faint">-</span> : String(s[k])}</td>)}
              <td className="nowrap"><TimeAgo iso={s.event_time} /></td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
