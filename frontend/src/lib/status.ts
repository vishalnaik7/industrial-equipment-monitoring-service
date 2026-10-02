import type { BatchState, DerivedStatus, ProcessingState } from "../api/types";
import type { IconName } from "../components/Icon";

// Status vocabulary. Colours come from the reserved status palette in styles.css and always ship with icon + label.
export const STATUS: Record<DerivedStatus, { label: string; icon: IconName }> = {
  critical: { label: "Critical", icon: "octagon" },
  at_risk: { label: "At risk", icon: "alert" },
  degraded: { label: "Degraded", icon: "alert" },
  watch: { label: "Watch", icon: "eye" },
  healthy: { label: "Healthy", icon: "check" },
  under_maintenance: { label: "Maintenance", icon: "wrench" },
  offline: { label: "Offline", icon: "power" },
  unknown: { label: "No data", icon: "help" },
};
export const STATUS_ORDER = Object.keys(STATUS) as DerivedStatus[];

export type Tone = "ok" | "warn" | "bad" | "";

export const PROCESSING: Record<ProcessingState, [Tone, string]> = {
  fresh: ["ok", "Fresh"], pending: ["warn", "Processing"], stale: ["warn", "Stale"],
  partial: ["warn", "Partial"], failed: ["bad", "Failed"], no_data: ["", "No data"],
};

export const BATCH: Record<BatchState, [Tone, string]> = {
  processing: ["warn", "Processing"], completed: ["ok", "Completed"],
  completed_with_errors: ["warn", "With errors"], failed: ["bad", "Failed"],
};
