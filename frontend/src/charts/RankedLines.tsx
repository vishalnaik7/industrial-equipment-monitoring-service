import type { LineSummary } from "../api/types";
import { Tip } from "../components/Tooltip";

/** Lines ranked by machines needing attention: one series of horizontal bars, value labelled at the end. */
export function RankedLines({ lines }: { lines: LineSummary[] }) {
  const max = Math.max(1, ...lines.map((l) => l.machines_total));
  return (
    <div className="bars">
      {lines.map((l) => (
        <Tip key={`${l.plant_id}/${l.line_id}`} className="bar-row" content={<>
          <b>{l.line_id} · {l.line_name}</b><br />{l.machines_needing_attention} of {l.machines_total} need attention
          {l.critical_machines > 0 && <><br />{l.critical_machines} critical</>}
        </>}>
          <div className="name">{l.line_id}<small>{l.plant_id} · {l.line_name}</small></div>
          <div className="bar-track">
            <div className={`bar-fill${l.critical_machines ? " crit" : ""}`} style={{ width: `${(l.machines_needing_attention / max) * 100}%` }} />
          </div>
          <div className="val">{l.machines_needing_attention}/{l.machines_total}</div>
        </Tip>
      ))}
    </div>
  );
}
