import { createContext, useCallback, useContext, useRef, useState } from "react";
import { Icon } from "./Icon";

type Toast = { id: number; message: string; tone: "ok" | "bad" };
const ToastContext = createContext<(message: string, tone?: "ok" | "bad") => void>(() => {});

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);
  const push = useCallback((message: string, tone: "ok" | "bad" = "ok") => {
    const id = ++seq.current;
    setToasts((t) => [...t, { id, message, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`}><Icon name={t.tone === "bad" ? "alert" : "check"} /><span>{t.message}</span></div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
