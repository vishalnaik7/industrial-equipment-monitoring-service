import type { BatchState, DerivedStatus, ProcessingStatus } from "../api/types";
import { BATCH, PROCESSING, STATUS, type Tone } from "../lib/status";
import { Icon } from "./Icon";

export function StatusBadge({ status, large = false }: { status: DerivedStatus; large?: boolean }) {
  const m = STATUS[status] ?? STATUS.unknown;
  return (
    <span className={`badge s-${status}${large ? " lg" : ""}`}>
      <Icon name={m.icon} className="ico" />{m.label}
    </span>
  );
}

/** 4-segment attention meter; the level is always also exposed as text for assistive tech. */
export function AttentionMeter({ level, label }: { level: number; label?: string }) {
  const l = Math.max(0, Math.min(4, level | 0));
  return (
    <span className={`meter l${l}`} role="img" aria-label={`Attention ${label ?? l} (${l} of 4)`} title={`Attention: ${label ?? l}`}>
      <i /><i /><i /><i />
    </span>
  );
}

export function Chip({ tone = "", children, title, mono = true }: { tone?: Tone; children: React.ReactNode; title?: string; mono?: boolean }) {
  return <span className={`chip ${tone}`} title={title} style={mono ? undefined : { fontFamily: "var(--sans)" }}>{children}</span>;
}

export function ProcessingChip({ p }: { p: ProcessingStatus }) {
  const [tone, label] = PROCESSING[p.status] ?? ["", p.status];
  return <Chip tone={tone} title={`pending ${p.pending_records} · rejected ${p.rejected_records} · failed ${p.failed_records}`}>{label}</Chip>;
}

export function BatchChip({ state }: { state: BatchState }) {
  const [tone, label] = BATCH[state] ?? ["", state];
  return <Chip tone={tone}>{label}</Chip>;
}
