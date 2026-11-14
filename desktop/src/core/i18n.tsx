import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";
import { getPrefs, setPref, usePrefs, type Lang } from "./prefs";

/**
 * Interface language. The Turkish source text IS the key: `t("Kaydet")` returns "Save" in English
 * and falls back to the Turkish key itself (also for a missing translation), so wrapping a string
 * can never change what Turkish users see.
 *
 * LIVE switching: the language is a preference (`prefs.lang`). `Shell` (App.tsx) calls `useLang()`,
 * so a switch re-renders the whole page tree (no React.memo in the tree, pages are plain children
 * of Shell). Therefore a plain `t(...)` call during render is enough; nothing else must subscribe.
 * What does NOT refresh by itself: strings computed once and cached (module-level constants, state
 * initialised from `t()`, `useMemo`/`useCallback` bodies that call `t()`): call `t()` at render
 * time, or add `lang` (from `useLang()`) to the dependency list.
 */

export type { Lang };

/** English tables: every `locale/en/*.json` merged. One file per page keeps parallel edits apart. */
export const LOCALE_FILES = import.meta.glob<Record<string, string>>("../locale/en/*.json", {
  eager: true,
  import: "default",
});

/** Merge tables; a key defined twice with a different translation is a conflict (tested). */
export function mergeTables(files: Record<string, Record<string, string>>) {
  const table: Record<string, string> = {};
  const owner: Record<string, string> = {};
  const conflicts: { key: string; a: string; b: string; fileA: string; fileB: string }[] = [];
  for (const [file, entries] of Object.entries(files)) {
    for (const [key, value] of Object.entries(entries)) {
      if (key in table && table[key] !== value) {
        conflicts.push({ key, a: table[key], b: value, fileA: owner[key], fileB: file });
        continue;
      }
      table[key] = value;
      owner[key] = file;
    }
  }
  return { table, conflicts };
}

const merged = mergeTables(LOCALE_FILES);
/** The English table (Turkish key -> English text). */
export const EN: Readonly<Record<string, string>> = merged.table;

export const getLang = (): Lang => getPrefs().lang;
export const setLang = (lang: Lang) => setPref("lang", lang);

const fill = (s: string, vars?: Record<string, string | number>) =>
  vars ? s.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m)) : s;

/**
 * Translate a Turkish source text. `{name}` placeholders are substituted from `vars` in both
 * languages (the Turkish key itself carries the placeholders).
 */
export function t(key: string, vars?: Record<string, string | number>): string {
  const hit = getLang() === "en" ? EN[key] : undefined;
  return fill(hit ? hit : key, vars);
}

/**
 * Count-dependent text. `tn(n, "{n} direk")` always passes `n` as a variable. English has a singular
 * form: add the key `"{n} direk#1"` to the English table and it is used when n === 1 (otherwise the
 * plain key). Turkish never changes.
 */
export function tn(n: number, key: string, vars?: Record<string, string | number>): string {
  const one = getLang() === "en" && n === 1 ? EN[`${key}#1`] : undefined;
  return one ? fill(one, { n, ...vars }) : t(key, { n, ...vars });
}

/** BCP-47 locale for Intl / toLocaleString. */
export const numberLocale = (): string => (getLang() === "en" ? "en-US" : "tr-TR");
/** Decimal separator of the active language ("," Turkish, "." English). */
export const decimalSeparator = (): string => (getLang() === "en" ? "." : ",");

interface LangCtx {
  lang: Lang;
  setLang: (l: Lang) => void;
}
const Ctx = createContext<LangCtx | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const { lang } = usePrefs();
  useEffect(() => { document.documentElement.lang = lang; }, [lang]);
  const value = useMemo(() => ({ lang, setLang }), [lang]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** `{ lang, setLang }`; a component that calls this re-renders when the language changes. */
export function useLang(): LangCtx {
  const v = useContext(Ctx);
  const { lang } = usePrefs();     // also works outside the provider (tests, tools)
  return v ?? { lang, setLang };
}
