import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

/** Shared behaviour for drawer + modal: portal, scrim click and Escape close, focus moved in and restored. */
function useOverlay(onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);          // latest callback without re-running the mount effect
  close.current = onClose;
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close.current(); };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, []);
  return ref;
}

export function Drawer({ onClose, label, children }: { onClose: () => void; label: string; children: React.ReactNode }) {
  const ref = useOverlay(onClose);
  return createPortal(
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={label} tabIndex={-1} ref={ref}>{children}</aside>
    </>, document.body);
}

export function Modal({ onClose, label, children }: { onClose: () => void; label: string; children: React.ReactNode }) {
  const ref = useOverlay(onClose);
  return createPortal(
    <>
      <div className="scrim" onClick={onClose} />
      <div className="modal" role="dialog" aria-modal="true" aria-label={label} tabIndex={-1} ref={ref}>{children}</div>
    </>, document.body);
}
