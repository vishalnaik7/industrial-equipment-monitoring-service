import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { API_BASE, getApiKey } from "../api/client";
import { useMeta, useReady } from "../api/hooks";
import { usePreferences } from "../lib/preferences";
import { ApiKeyDialog } from "./ApiKeyDialog";
import { Icon, type IconName } from "./Icon";

const NAV: { to: string; label: string; icon: IconName; badge?: "dead" }[] = [
  { to: "/overview", label: "Overview", icon: "grid" },
  { to: "/machines", label: "Machines", icon: "cpu" },
  { to: "/ingest", label: "Ingest", icon: "upload" },
  { to: "/batches", label: "Batches", icon: "layers" },
  { to: "/dead-letters", label: "Dead letters", icon: "inbox", badge: "dead" },
  { to: "/trace", label: "Trace", icon: "search" },
];

export function Layout() {
  const { theme, toggleTheme, autoRefresh, setAutoRefresh } = usePreferences();
  const meta = useMeta().data;
  const ready = useReady();
  const qc = useQueryClient();
  const [menuOpen, setMenuOpen] = useState(false);
  const [keyOpen, setKeyOpen] = useState(false);
  const { pathname } = useLocation();

  useEffect(() => { setMenuOpen(false); }, [pathname]);
  useEffect(() => {
    const page = NAV.find((n) => pathname.startsWith(n.to));
    document.title = `${page?.label ?? "Overview"} · Aurik Monitor`;
  }, [pathname]);

  const dead = ready.data?.dead_letters ?? 0;
  return (
    <div className="shell">
      <a href="#main" className="sr-only">Skip to content</a>
      <aside className={`sidebar${menuOpen ? " open" : ""}`} aria-label="Primary">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <div><div className="brand-name">AURIK</div><div className="brand-sub">Equipment monitoring</div></div>
        </div>
        <nav className="nav">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => (isActive ? "active" : undefined)}>
              <Icon name={n.icon} /><span>{n.label}</span>
              {n.badge === "dead" && dead > 0 && <span className="count" aria-label={`${dead} dead letters`}>{dead}</span>}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <a href={`${API_BASE}/docs`} target="_blank" rel="noopener">API docs</a>
            <span className="faint">{meta && `v${meta.version}`}</span>
          </div>
          {meta && <div className="faint" style={{ marginTop: 6 }}>rules v{meta.rules_version} · stale after {meta.stale_after_minutes}m</div>}
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button className="btn btn-icon btn-ghost menu-btn" aria-label="Open navigation" onClick={() => setMenuOpen((o) => !o)}><Icon name="menu" /></button>
          <span className="pill" aria-live="polite" title={ready.isError ? `No response from ${API_BASE || "the API"}` : undefined}>
            {ready.isError
              ? <><span className="dot bad" /><b>Offline</b> · API unreachable</>
              : ready.data
                ? <><span className="dot ok pulse" /><b>Live</b>{meta?.auth_required && ` · ${getApiKey() ? "key set" : "key needed"}`}</>
                : <><span className="dot" />Connecting...</>}
          </span>
          {ready.data && (
            <span className="pill hide-sm">
              <span className={`dot ${ready.data.queue_depth ? "warn" : "ok"}`} />Queue <b>{ready.data.queue_depth || "empty"}</b>
            </span>
          )}
          <span className="spacer" />
          <label className="switch hide-sm" title="Refresh the overview every 10 seconds">
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} /> Auto-refresh
          </label>
          <button className="btn btn-icon btn-ghost" aria-label="Refresh data" onClick={() => qc.invalidateQueries()}><Icon name="refresh" /></button>
          <button className="btn btn-icon btn-ghost" aria-label="API key" onClick={() => setKeyOpen(true)}><Icon name="key" /></button>
          <button className="btn btn-icon btn-ghost" aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} onClick={toggleTheme}>
            <Icon name={theme === "dark" ? "sun" : "moon"} />
          </button>
        </header>
        <main className="content" id="main" tabIndex={-1}><Outlet /></main>
      </div>
      {keyOpen && <ApiKeyDialog onClose={() => setKeyOpen(false)} />}
    </div>
  );
}
