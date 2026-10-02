import type { DerivedStatus } from "../api/types";
import { Tip } from "../components/Tooltip";
import { plural } from "../lib/format";
import { STATUS, STATUS_ORDER } from "../lib/status";

/** Part-to-whole of machines by derived status: one stacked bar; the legend doubles as the data table. */
export function StatusDistribution({ byStatus }: { byStatus: Record<DerivedStatus, number> }) {
  const total = STATUS_ORDER.reduce((a, s) => a + (byStatus[s] ?? 0), 0);
  const present = STATUS_ORDER.filter((s) => (byStatus[s] ?? 0) > 0);
  const legend = STATUS_ORDER.filter((s) => (byStatus[s] ?? 0) > 0 || s === "critical" || s === "healthy");
  return (
    <>
      <div className="stackbar" role="img" aria-label={present.map((s) => `${STATUS[s].label} ${byStatus[s]}`).join(", ")}>
        {present.length === 0 && <div className="seg-fill fill-unknown" style={{ width: "100%" }} />}
        {present.map((s) => {
          const n = byStatus[s], p = total ? (n / total) * 100 : 0;
          return (
            <Tip key={s} className={`seg-fill fill-${s}`} style={{ width: `${p}%` }}
              content={<><b>{STATUS[s].label}</b><br />{plural(n, "machine")} · {p.toFixed(0)}%</>}>{null}</Tip>
          );
        })}
      </div>
      <div className="legend">
        {legend.map((s) => (
          <span key={s} className="legend-item"><span className={`sw fill-${s}`} />{STATUS[s].label} <b>{byStatus[s] ?? 0}</b></span>
        ))}
      </div>
    </>
  );
}
