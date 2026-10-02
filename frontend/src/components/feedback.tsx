import { ApiError } from "../api/client";
import { Icon, type IconName } from "./Icon";

export function ErrorBox({ error }: { error: unknown }) {
  const e = error instanceof ApiError ? error : new ApiError(0, "ERROR", error instanceof Error ? error.message : String(error));
  return (
    <div className="alert-box bad" role="alert">
      <Icon name="alert" />
      <div>
        <b>{e.code}</b> - {e.message}
        {!!e.details?.length && (
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {e.details.map((d, i) => <li key={i}><code>{d.field}</code> - {d.message}</li>)}
          </ul>
        )}
        {e.status === 401 && <div style={{ marginTop: 6 }}>Set an API key with the <b>key</b> button in the top bar.</div>}
        {e.requestId && <div className="faint" style={{ marginTop: 4, fontSize: 12 }}>request id {e.requestId}</div>}
      </div>
    </div>
  );
}

export function AlertBox({ tone, icon, children }: { tone: "ok" | "warn" | "bad"; icon: IconName; children: React.ReactNode }) {
  return <div className={`alert-box ${tone}`}><Icon name={icon} /><div>{children}</div></div>;
}

export function EmptyState({ title, hint, icon = "inbox" }: { title: string; hint?: React.ReactNode; icon?: IconName }) {
  return (
    <div className="empty">
      <Icon name={icon} />
      <div style={{ fontWeight: 600, color: "var(--text-2)" }}>{title}</div>
      {hint && <div style={{ marginTop: 4 }}>{hint}</div>}
    </div>
  );
}

export function Skeleton({ height = 120, count = 1 }: { height?: number; count?: number }) {
  return <>{Array.from({ length: count }, (_, i) => <div key={i} className="skeleton" style={{ height }} />)}</>;
}

/** Renders loading / error / content for a query result. */
export function QueryState<T>({ query, skeleton, children }: {
  query: { data: T | undefined; error: unknown; isPending: boolean };
  skeleton?: React.ReactNode; children: (data: T) => React.ReactNode;
}) {
  if (query.error && !query.data) return <ErrorBox error={query.error} />;
  if (query.isPending || query.data === undefined) return <>{skeleton ?? <Skeleton />}</>;
  return <>{children(query.data)}</>;
}
