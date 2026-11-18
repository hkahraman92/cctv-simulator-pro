import { describe, expect, it } from "vitest";
import { EN } from "./i18n";

// Files whose user-visible text is fully wrapped in t(...). Paths are relative to desktop/src.
// A page agent adds its files here when it finishes them; the tests below then fail on
//  (a) a Turkish literal that is not inside t(...) (JSX text, props, message strings, any string),
//  (b) a t("...") key without an entry in locale/en/*.json.
// Opt out of (a) for a genuine non-UI string (stored data, a value the core matches by name, a
// regex source) with "// i18n-ignore" on the same or the previous line (in JSX use a comment
// child containing the word i18n-ignore, on the line above).
export const TRANSLATED_FILES: string[] = [
  "App.tsx",
  "components/ProjectBar.tsx",
  "components/SettingsDialog.tsx",
  "components/dialogs.tsx",
  "components/ui.tsx",
  "components/JobBar.tsx",
  "core/project.tsx",
  "core/CoreProvider.tsx",
  "pages/Terrain.tsx",
  "core/terrainLevels.ts",
  "pages/terrainPersist.ts",
  "pages/Library.tsx",
  "pages/Spec.tsx",
  "pages/Reports.tsx",
  "components/LibraryEditor.tsx",
  "core/rpc.ts",
  "core/tauriTransport.ts",
  "pages/Workspace.tsx",
  "pages/Optics.tsx",
  "pages/View3D.tsx",
  "components/AnprBench.tsx",
  "components/LevelEditor.tsx",
  "components/SitePlan.tsx",
  "components/PlanView.tsx",
  "core/api.ts",
  "components/PlanningPanels.tsx",
  "components/DeliveryPanels.tsx",
  "components/PtzPanels.tsx",
  "components/PtzSweep.tsx",
  "components/MapView.tsx",
  "components/ApproachPanel.tsx",
  "components/SensitivityPanel.tsx",
  "components/CityPanel.tsx",
  "components/charts.tsx",
  "components/LegendSwatch.tsx",
  "components/patrolSpeeds.ts",
  "core/activeTerrain.tsx",
  "core/benchApi.ts",
  "core/clarify.ts",
  "core/i18n.tsx",
  "core/levelLabel.ts",
  "core/pageActive.ts",
  "core/prefs.ts",
  "core/specApi.ts",
  "core/terrainApi.ts",
  "core/useJob.ts",
  "core/weather.tsx",
  "main.tsx",
  "pages/opticsSeed.ts",
  "pages/terrainTilt.ts",
  "pages/analysisSettings.ts",
  "pages/fullAnalysis.ts",
  "components/FullAnalysisPanel.tsx",
];

// ---------------------------------------------------------------------------------------------
// Scanner: a small character-level tokenizer (comments, strings, template literals, regex
// literals), so apostrophes in JSX text and `${...}` inside templates do not confuse it.

/** Turkish-specific letters. */
const TR_LETTER = /[çğıİöşüÇĞÖŞÜ]/;
/** Turkish UI words that contain no Turkish-specific letter. Extend when a new one slips through. */
const ASCII_WORDS = [
  "Kaydet", "Kamera", "Kameralar", "Sabit", "Hava", "Ekle", "Sil", "Yok", "Tamam", "Hata", "Raporlar", "Ayarlar",
  "Mesafe", "Direk", "Alan", "Hedef", "Seviye", "Kapat", "Yeni", "Proje", "Arazi", "Harita", "Plan", "Rapor",
  "Sonuc", "Baslat", "Durdur", "Hesapla", "Sec", "Yukle",
];
const WORD_RE = new RegExp(`(?<![\\p{L}\\d_])(?:${ASCII_WORDS.join("|")})(?![\\p{L}\\d_])`, "u");
const looksTurkish = (s: string) => TR_LETTER.test(s) || WORD_RE.test(s);

export interface Hit { line: number; text: string; why: string }
export interface ScanResult { violations: Hit[]; keys: { key: string; line: number }[] }

/** What may precede an opening quote for it to be a string (not an apostrophe inside JSX text). */
const STRING_START = /(?:^|[(,=:[!&|?{;+]|=>|\breturn|\bcase|\bfrom|\bextends|\bdefault|\bof|\bin)\s*$/;
// `}` is deliberately absent: after it a `/` is JSX (`} />`) far more often than a regex
const REGEX_START = /(?:^|[(,=:[!&|?{;]|\breturn|\btypeof)\s*$/;
const WRAPPED_T = /(?:^|[^\w.$])t\(\s*$/;
const WRAPPED_TN = /(?:^|[^\w.$])tn\([^"'`]*,\s*$/;

export function scanSource(src: string): ScanResult {
  const ignored = new Set<number>();
  src.split("\n").forEach((l, i) => { if (l.includes("i18n-ignore")) ignored.add(i + 1); });
  const isIgnored = (line: number) => ignored.has(line) || ignored.has(line - 1);

  const violations: Hit[] = [];
  const keys: { key: string; line: number }[] = [];
  let i = 0;
  let line = 1;
  let tail = "";          // recent code (comments dropped), to see what precedes a literal
  let residue = "";       // code text outside literals on the current line: JSX text lands here
  const flag = (l: number, text: string, why: string) => { if (!isIgnored(l)) violations.push({ line: l, text: text.trim(), why }); };
  const emit = (s: string) => { tail = (tail + s).slice(-80); residue += s; };
  const newline = () => {
    if (looksTurkish(residue)) flag(line, residue, "Turkish text outside t(...) (JSX text?)");
    residue = ""; line++;
  };
  const wrapped = () => WRAPPED_T.test(tail) || WRAPPED_TN.test(tail);
  const unescape = (s: string) => s.replace(/\\(.)/g, (_m, c: string) => (c === "n" ? "\n" : c));

  function readString(q: string) {
    const startLine = line;
    i++;
    let body = "";
    while (i < src.length && src[i] !== q && src[i] !== "\n") {
      if (src[i] === "\\" && i + 1 < src.length) { body += src[i] + src[i + 1]; i += 2; } else body += src[i++];
    }
    i++;                                   // closing quote
    if (wrapped()) keys.push({ key: unescape(body), line: startLine });
    else if (looksTurkish(body)) flag(startLine, body, "Turkish string literal not wrapped in t(...)");
    emit('""');
  }

  function readTemplate() {
    const startLine = line;
    const wasWrapped = wrapped();
    i++;
    let text = "";
    let hasExpr = false;
    while (i < src.length && src[i] !== "`") {
      if (src[i] === "\\" && i + 1 < src.length) { text += src[i] + src[i + 1]; i += 2; continue; }
      if (src[i] === "$" && src[i + 1] === "{") {
        hasExpr = true; i += 2;
        scanCode(true);                    // consumes the closing }
        continue;
      }
      if (src[i] === "\n") { line++; text += "\n"; i++; continue; }
      text += src[i++];
    }
    i++;                                   // closing backtick
    if (wasWrapped) {
      if (hasExpr) flag(startLine, text, "t(...) key must be a plain literal with {name} placeholders, not a template with ${...}");
      else keys.push({ key: unescape(text), line: startLine });
    } else if (looksTurkish(text)) flag(startLine, text, "Turkish template literal not wrapped in t(...)");
    emit('``');
  }

  function readRegex() {
    i++;
    let inClass = false;
    while (i < src.length && src[i] !== "\n") {
      const c = src[i];
      if (c === "\\") { i += 2; continue; }
      if (c === "[") inClass = true; else if (c === "]") inClass = false;
      else if (c === "/" && !inClass) { i++; break; }
      i++;
    }
    while (/[a-z]/.test(src[i] ?? "")) i++;   // flags
    emit("/re/");
  }

  function scanCode(untilBrace: boolean) {
    let depth = 0;
    while (i < src.length) {
      const c = src[i];
      const n = src[i + 1];
      if (c === "\n") { newline(); emit(""); i++; continue; }
      if (c === "/" && n === "/") { while (i < src.length && src[i] !== "\n") i++; continue; }
      if (c === "/" && n === "*") {
        i += 2;
        while (i < src.length && !(src[i] === "*" && src[i + 1] === "/")) { if (src[i] === "\n") newline(); i++; }
        i += 2; continue;
      }
      if (c === "`") { readTemplate(); continue; }
      if ((c === '"' || c === "'") && STRING_START.test(tail)) { readString(c); continue; }
      if (c === "/" && n !== ">" && REGEX_START.test(tail)) { readRegex(); continue; }
      if (c === "{") depth++;
      if (c === "}") { if (untilBrace && depth === 0) { i++; return; } depth--; }
      emit(c); i++;
    }
  }

  scanCode(false);
  if (looksTurkish(residue)) flag(line, residue, "Turkish text outside t(...) (JSX text?)");
  return { violations, keys };
}

// ---------------------------------------------------------------------------------------------

const RAW = import.meta.glob<string>(["../**/*.{ts,tsx}", "!../**/*.test.ts"], { query: "?raw", import: "default", eager: true });
// keys come as "../App.tsx" and, for this directory, "./project.tsx": normalise to paths under src
const SOURCES: Record<string, string> = Object.fromEntries(
  Object.entries(RAW).map(([k, v]) => [k.startsWith("./") ? `core/${k.slice(2)}` : k.replace(/^\.\.\//, ""), v]),
);

describe("scanner", () => {
  it("flags unwrapped Turkish and accepts wrapped / ignored text", () => {
    const bad = scanSource([
      'const a = <p title="Kaydet">Vazgeç</p>;',
      'const b = "Tamam";',
      "const c = `Seçili: ${n}`;",
      'const d = t("Kapat");',
      'const e = tn(n, "{n} direk");',
      'const f = "x"; // i18n-ignore',
      "const g = /şçğ/.test(s);",
      "const h = <p>Arazi'de {t(\"Ayarlar\")} çit</p>;",
    ].join("\n"));
    expect(bad.violations.map((v) => v.line)).toEqual([1, 1, 2, 3, 8]);
    expect(bad.keys.map((k) => k.key)).toEqual(["Kapat", "{n} direk", "Ayarlar"]);
  });

  it("does not mistake JSX self-closing tags for regex literals", () => {
    const r = scanSource([
      "const a = <div>{x} /> Kaydet</div>;",
      "const b = <Icon size={1} /> Vazgeç;",
      'const c = <p><Foo bar={y} /> {t("Kapat")} çit</p>;',
    ].join("\n"));
    expect(r.violations.map((v) => v.line)).toEqual([1, 2, 3]);
    expect(r.keys.map((k) => k.key)).toEqual(["Kapat"]);
  });

  it("rejects a template literal as a t() key and an ignore comment on the previous line works", () => {
    expect(scanSource("t(`a ${x}`)").violations).toHaveLength(1);
    expect(scanSource('// i18n-ignore\nconst x = "Kaydet";').violations).toHaveLength(0);
  });
});

describe("translated files", () => {
  for (const file of TRANSLATED_FILES) {
    describe(file, () => {
      const src = SOURCES[file];
      it("exists", () => expect(src, `${file} not found under desktop/src`).toBeTypeOf("string"));
      const scan = scanSource(src ?? "");
      it("has no Turkish text outside t(...)", () => {
        expect(scan.violations.map((v) => `${file}:${v.line}  ${v.text}  (${v.why})`)).toEqual([]);
      });
      it("has an English entry for every t(...) key", () => {
        const missing = scan.keys.filter((k) => !EN[k.key]).map((k) => `${file}:${k.line}  "${k.key}"`);
        expect(missing).toEqual([]);
      });
    });
  }
});

/** Source files that hold no user-visible text. Each needs a one-word reason. */
export const NO_UI_TEXT: Record<string, string> = {
};

describe("every source file is accounted for", () => {
  it("is in TRANSLATED_FILES or NO_UI_TEXT (a new file cannot be forgotten)", () => {
    const all = Object.keys(SOURCES).filter((f) => !/\.d\.ts$/.test(f)).sort();
    const known = new Set([...TRANSLATED_FILES, ...Object.keys(NO_UI_TEXT)]);
    expect(all.filter((f) => !known.has(f)), "add to TRANSLATED_FILES (after wrapping its text) or NO_UI_TEXT (with a reason)").toEqual([]);
  });

  it("lists no file twice and no file that does not exist", () => {
    const both = TRANSLATED_FILES.filter((f) => f in NO_UI_TEXT);
    expect(both).toEqual([]);
    expect([...TRANSLATED_FILES, ...Object.keys(NO_UI_TEXT)].filter((f) => !(f in SOURCES))).toEqual([]);
    expect(new Set(TRANSLATED_FILES).size).toBe(TRANSLATED_FILES.length);
    for (const [f, why] of Object.entries(NO_UI_TEXT)) expect(why, f).toMatch(/^\S+$/);
  });
});
