import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, api, getApiKey, setApiKey } from "../api/client";
import { useMeta } from "../api/hooks";
import { Icon } from "./Icon";
import { Modal } from "./Overlay";
import { useToast } from "./Toasts";

export function ApiKeyDialog({ onClose }: { onClose: () => void }) {
  const meta = useMeta().data;
  const qc = useQueryClient();
  const toast = useToast();
  const [value, setValue] = useState(getApiKey());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const v = value.trim();
    if (/\s/.test(v)) { setError("Keys cannot contain spaces."); return; }
    const prev = getApiKey();
    setApiKey(v);
    setBusy(true);
    try {
      await api.reference();                       // an authenticated call: proves the key works
      await qc.invalidateQueries();
      toast(v ? "API key accepted" : "API key cleared");
      onClose();
    } catch (ex) {
      setApiKey(prev);
      setError(ex instanceof ApiError && ex.status === 401 ? "Rejected by the server (401)." : String((ex as Error).message));
    } finally { setBusy(false); }
  }

  return (
    <Modal onClose={onClose} label="API key">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>API key</h2>
        <button className="btn btn-icon btn-ghost" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
      </div>
      <p className="muted" style={{ margin: 0 }}>
        {meta?.auth_required
          ? <>This server requires an <code>X-API-Key</code>.</>
          : <>This server currently runs <b>without</b> auth (dev mode). A key is only needed when <code>API_KEYS</code> is set.</>}
        {" "}The key is stored in this browser only.
      </p>
      <form className="stack" style={{ gap: 10 }} onSubmit={save} noValidate>
        <div className="field">
          <label htmlFor="k-in">Key</label>
          <input id="k-in" className="input" type="password" autoComplete="off" maxLength={256} placeholder="e.g. dev-key"
            value={value} aria-invalid={!!error} onChange={(e) => { setValue(e.target.value); setError(""); }} autoFocus />
          {error && <span className="error-text">{error}</span>}
        </div>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn btn-ghost" onClick={() => setValue("")}>Clear</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Testing..." : "Save & test"}</button>
        </div>
      </form>
    </Modal>
  );
}
