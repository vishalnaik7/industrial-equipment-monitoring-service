// Per-browser UI preferences (theme, auto-refresh), persisted in localStorage.
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { storage } from "./storage";

type Theme = "light" | "dark";
interface Prefs { theme: Theme; toggleTheme: () => void; autoRefresh: boolean; setAutoRefresh: (v: boolean) => void }

const systemDark = () => typeof matchMedia !== "undefined" && matchMedia("(prefers-color-scheme: dark)").matches;
const PrefsContext = createContext<Prefs | null>(null);

export function PreferencesProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => (storage.get("aurik.theme") as Theme) || (systemDark() ? "dark" : "light"));
  const [autoRefresh, setAuto] = useState(() => storage.get("aurik.auto") === "1");

  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  const toggleTheme = useCallback(() => setTheme((t) => {
    const next = t === "dark" ? "light" : "dark";
    storage.set("aurik.theme", next);
    return next;
  }), []);
  const setAutoRefresh = useCallback((v: boolean) => { storage.set("aurik.auto", v ? "1" : "0"); setAuto(v); }, []);

  return <PrefsContext.Provider value={{ theme, toggleTheme, autoRefresh, setAutoRefresh }}>{children}</PrefsContext.Provider>;
}

export function usePreferences(): Prefs {
  const p = useContext(PrefsContext);
  if (!p) throw new Error("usePreferences must be used inside PreferencesProvider");
  return p;
}
