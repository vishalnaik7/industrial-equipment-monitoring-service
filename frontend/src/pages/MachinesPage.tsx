import { useDeferredValue, useMemo } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMachines, useReference } from "../api/hooks";
import type { DerivedStatus, MachineView } from "../api/types";
import { AttentionMeter, Chip, ProcessingChip, StatusBadge } from "../components/badges";
import { EmptyState, ErrorBox, Skeleton } from "../components/feedback";
import { TimeAgo } from "../components/TimeAgo";
import { plural } from "../lib/format";
import { STATUS, STATUS_ORDER } from "../lib/status";
import { MachineDrawer } from "./MachineDrawer";

const FILTER_KEYS = ["q", "plant_id", "line_id", "status", "needs_attention"] as const;

export default function MachinesPage() {
  const { machineId } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const f = Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k) ?? ""])) as Record<(typeof FILTER_KEYS)[number], string>;

  const plants = useReference().data?.plants ?? [];
  const lines = plants.filter((p) => !f.plant_id || p.plant_id === f.plant_id).flatMap((p) => p.lines);
  const query = useMachines({
    plant_id: f.plant_id || undefined, line_id: f.line_id || undefined,
    status: (f.status || undefined) as DerivedStatus | undefined, needs_attention: f.needs_attention === "true" || undefined,
  });

  const update = (patch: Partial<typeof f>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) (v ? next.set(k, v) : next.delete(k));
    setParams(next, { replace: true });
  };

  const needle = useDeferredValue(f.q.trim().toLowerCase());
  const items = useMemo(() => (query.data?.items ?? []).filter((m) => !needle ||
    [m.machine_id, m.machine_type, m.line_id, ...m.reason_codes].some((v) => String(v ?? "").toLowerCase().includes(needle))),
  [query.data, needle]);

  const search = params.toString() ? `?${params}` : "";
  return (
    <>
      <div className="page-head"><div><h1>Machine attention view</h1>
        <p>Most urgent first. Click a machine for its full explanation and source traceability.</p></div></div>

      <form className="toolbar card card-pad" style={{ padding: "14px 16px" }} onSubmit={(e) => e.preventDefault()} noValidate>
        <div className="field grow"><label htmlFor="f-q">Search</label>
          <input id="f-q" className="input" placeholder="Machine id, type or reason code" maxLength={40} autoComplete="off"
            value={f.q} onChange={(e) => update({ q: e.target.value })} /></div>
        <div className="field"><label htmlFor="f-plant">Plant</label>
          <select id="f-plant" className="select" value={f.plant_id} onChange={(e) => update({ plant_id: e.target.value, line_id: "" })}>
            <option value="">All plants</option>
            {plants.map((p) => <option key={p.plant_id} value={p.plant_id}>{p.plant_id}</option>)}
          </select></div>
        <div className="field"><label htmlFor="f-line">Line</label>
          <select id="f-line" className="select" value={f.line_id} onChange={(e) => update({ line_id: e.target.value })}>
            <option value="">All lines</option>
            {lines.map((l) => <option key={l.line_id} value={l.line_id}>{l.line_id} · {l.line_name}</option>)}
          </select></div>
        <div className="field"><label htmlFor="f-status">Status</label>
          <select id="f-status" className="select" value={f.status} onChange={(e) => update({ status: e.target.value })}>
            <option value="">Any status</option>
            {STATUS_ORDER.map((s) => <option key={s} value={s}>{STATUS[s].label}</option>)}
          </select></div>
        <div className="field"><label>&nbsp;</label>
          <label className="switch"><input type="checkbox" checked={f.needs_attention === "true"}
            onChange={(e) => update({ needs_attention: e.target.checked ? "true" : "" })} /> Needs attention only</label></div>
        <div className="field"><label>&nbsp;</label>
          <button type="button" className="btn btn-ghost" onClick={() => setParams({}, { replace: true })}>Reset</button></div>
      </form>

      {query.error ? <ErrorBox error={query.error} /> : (
        <>
          <div className="faint" style={{ margin: "0 2px 10px", fontSize: 12.5 }}>{query.data && plural(items.length, "machine")}</div>
          <div className="grid grid-machines" aria-busy={query.isFetching}>
            {query.isPending ? <Skeleton height={170} count={6} /> : items.length === 0
              ? <EmptyState title="No machines match these filters" hint="Try clearing a filter." icon="search" />
              : items.map((m) => <MachineCard key={m.machine_id} m={m}
                  onOpen={() => navigate(`/machines/${encodeURIComponent(m.machine_id)}${search}`)} />)}
          </div>
        </>
      )}

      {machineId && <MachineDrawer machineId={machineId} onClose={() => navigate(`/machines${search}`, { replace: true })} />}
    </>
  );
}

function MachineCard({ m, onOpen }: { m: MachineView; onOpen: () => void }) {
  return (
    <button type="button" className={`card mcard s-${m.derived_status}`} onClick={onOpen}
      aria-label={`${m.machine_id}: ${STATUS[m.derived_status].label}`}>
      <div className="top">
        <div><div className="id">{m.machine_id}</div>
          <div className="meta">{m.machine_type} · {m.plant_id} / {m.line_id} · {m.criticality} criticality</div></div>
        <StatusBadge status={m.derived_status} />
      </div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <AttentionMeter level={m.attention_level} label={m.attention_label} />
        <span className="faint" style={{ fontSize: 12 }}>attention <b style={{ color: "var(--text)" }}>{m.attention_label}</b></span>
      </div>
      <div className="reasons">
        {m.reason_codes.length === 0 && <span className="faint" style={{ fontSize: 12.5 }}>No active reasons</span>}
        {m.reason_codes.slice(0, 4).map((c) => <Chip key={c}>{c}</Chip>)}
        {m.reason_codes.length > 4 && <Chip>+{m.reason_codes.length - 4}</Chip>}
      </div>
      <div className="foot"><span>Last event <TimeAgo iso={m.latest_relevant_event_time} /></span><ProcessingChip p={m.processing_status} /></div>
    </button>
  );
}
