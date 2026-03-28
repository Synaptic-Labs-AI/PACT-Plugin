import {
  createContext,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { Preferences } from "@/lib/types";

const STORAGE_KEY = "pact-dashboard-prefs";

const defaultPrefs: Preferences = {
  theme: "dark",
  showEndedSessions: true,
  showNonPactTeams: false,
  activityFeedLimit: 50,
};

function loadPrefs(): Preferences {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return { ...defaultPrefs, ...JSON.parse(raw) };
  } catch {
    // ignore
  }
  return defaultPrefs;
}

interface PreferencesContextValue {
  prefs: Preferences;
  updatePrefs: (patch: Partial<Preferences>) => void;
  toggleTheme: () => void;
}

export const PreferencesContext =
  createContext<PreferencesContextValue | null>(null);

export function PreferencesProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefs] = useState(loadPrefs);

  const updatePrefs = useCallback((patch: Partial<Preferences>) => {
    setPrefs((prev) => {
      const next = { ...prev, ...patch };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  const toggleTheme = useCallback(() => {
    updatePrefs({
      theme: prefs.theme === "dark" ? "light" : "dark",
    });
  }, [prefs.theme, updatePrefs]);

  // Apply theme class to <html>
  useEffect(() => {
    const root = document.documentElement;
    if (prefs.theme === "dark") {
      root.classList.add("dark");
    } else {
      root.classList.remove("dark");
    }
  }, [prefs.theme]);

  const value = useMemo(
    () => ({ prefs, updatePrefs, toggleTheme }),
    [prefs, updatePrefs, toggleTheme],
  );

  return (
    <PreferencesContext.Provider value={value}>
      {children}
    </PreferencesContext.Provider>
  );
}
