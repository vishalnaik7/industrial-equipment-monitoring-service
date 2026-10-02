import type { VendorSlug } from "../api/types";

export function relTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "-";
  const s = Math.round((now - new Date(iso).getTime()) / 1000);
  const a = Math.abs(s);
  const f = a < 60 ? `${a}s` : a < 3600 ? `${Math.round(a / 60)}m` : a < 86400 ? `${Math.round(a / 3600)}h` : `${Math.round(a / 86400)}d`;
  return s < 0 ? `in ${f}` : `${f} ago`;
}

export const fmtTime = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" }) : "-";

export function humanAge(sec: number): string {
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m ${sec % 60}s`;
}

export const pct = (a: number, b: number) => (b ? `${Math.round((a / b) * 100)}%` : "0%");

const VENDOR_LABEL: Record<VendorSlug, string> = {
  pulseforge: "PulseForge", thermexwatch: "ThermexWatch", maintaflow: "MaintaFlow", workorders: "Work orders",
};
// Canonical source name (as stored on records and reasons) -> API slug. Not derivable by lower-casing: CMMS != workorders.
const SLUG_BY_NAME: Record<string, VendorSlug> = {
  PulseForge: "pulseforge", ThermexWatch: "thermexwatch", MaintaFlow: "maintaflow", CMMS: "workorders",
};
export const vendorLabel = (slug: string) => VENDOR_LABEL[slug as VendorSlug] ?? slug;
export const vendorSlug = (name: string) => SLUG_BY_NAME[name] ?? (name.toLowerCase() as VendorSlug);

export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
