import { fmtTime, relTime } from "../lib/format";

export function TimeAgo({ iso }: { iso: string | null | undefined }) {
  if (!iso) return <span className="faint">-</span>;
  return <time dateTime={iso} title={`${fmtTime(iso)} (${iso})`}>{relTime(iso)}</time>;
}
