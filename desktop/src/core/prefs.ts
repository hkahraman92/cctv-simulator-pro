import { useSyncExternalStore } from "react";

/** Interface preferences, kept in the webview's local storage (per user, survives restarts). */
export interface Prefs {
  theme: "dark" | "light";
  /** folder the Reports page last wrote to */
  reportDir: string;
}

const KEY = "cctv.prefs.v1";
const DEFAULTS: Prefs = { theme: "dark", reportDir: "" };

function read(): Prefs {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "{}") as Partial<Prefs>;
    return { ...DEFAULTS, ...raw, theme: raw.theme === "light" ? "light" : "dark" };
  } catch {
    return DEFAULTS;          // storage unavailable or corrupt: defaults, never a crash
  }
}

let cache: Prefs = read();
const listeners = new Set<() => void>();

/** The theme is a data attribute on <html>; index.css redefines the colour tokens under it. */
export function applyTheme() {
  document.documentElement.dataset.theme = cache.theme;
}

export function setPref<K extends keyof Prefs>(key: K, value: Prefs[K]) {
  cache = { ...cache, [key]: value };
  try { localStorage.setItem(KEY, JSON.stringify(cache)); } catch { /* not persisted this session */ }
  applyTheme();
  listeners.forEach((l) => l());
}

export function getPrefs(): Prefs {
  return cache;
}

export function usePrefs(): Prefs {
  return useSyncExternalStore((l) => { listeners.add(l); return () => { listeners.delete(l); }; }, () => cache);
}
