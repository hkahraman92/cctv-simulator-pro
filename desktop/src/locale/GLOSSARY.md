# English interface: glossary and conventions

Turkish is the source language and the default. The Turkish text is the key: `t("Kaydet")` returns
"Save" in English and the Turkish key itself in Turkish (and when a translation is missing).
Wrapping a string therefore can never change the Turkish UI.

## Conventions (page agents: follow exactly)

1. **API**: `import { t, tn, useLang } from "../core/i18n"` (`"./i18n"` inside `core/`).
   - `t(key, vars?)`: `{name}` placeholders are filled from `vars`, in both languages (the Turkish key
     carries the placeholders). An unknown placeholder stays visible as `{name}`.
   - `tn(n, key, vars?)`: count-dependent text, see Plurals.
   - `numberLocale()` ("tr-TR" / "en-US") for `toLocaleString` / `Intl`; `decimalSeparator()`.
     Do not hard-code `"tr-TR"` for display (PlanningPanels `money`, Library sort/search may keep it);
     `fmt()` from `components/ui.tsx` already follows the language.
2. **Live switching.** `Shell` (App.tsx) calls `useLang()`, so a language change re-renders the whole
   page tree (there is no `React.memo` in the tree). **A plain `t(...)` call during render is enough.**
   It is NOT enough when the string is computed once and cached:
   - module-level constants (`const LABELS = [["a", t("X")]]` freezes the language at load): make it a
     function (`const labels = () => [["a", t("X")]]`) and call it in render;
   - `useState(t("..."))` initialisers, `useRef`, values stored in a context;
   - `useMemo` / `useCallback` bodies that call `t()`: add `lang` (`const { lang } = useLang()`) to the deps.
3. **Files.** One English table per page/area: `src/locale/en/<area>.json` (`terrain.json`,
   `optics.json`, `library.json`, `spec.json`, `reports.json`, `workspace.json`, `view3d.json`,
   `panels.json` ...). Flat `{ "<Turkish key>": "<English>" }`, UTF-8, keys sorted. Never edit another
   area's file. `shell.json` = app shell, `common.json` = shared words (Kaydet, Vazgeç, Tamam, Sil, Kapat,
   Evet, Hayır, level names, units). Check `common.json` first; a key defined in two files with different
   English is a test failure (identical duplicates are fine, but prefer reusing `common.json`).
4. **What to wrap.** JSX text, `title=` `aria-label=` `placeholder=` `label=` `alt=` props, `<option>` text,
   messages, `confirmDialog` / `promptDialog` texts, dialog titles (`open({ title })`), job labels
   (`job.run("label", ...)`; the label is only shown), tooltips, table headers, empty states.
   ```tsx
   <Button>{t("Kaydet")}</Button>
   <input aria-label={t("Kamera adı")} placeholder={t("Ara…")} />
   <span>{t("{n} kesimde uyarı süresi müdahaleden kısa", { n })}</span>
   {ok ? t("Tamam") : t("Hata")}                // wrap each branch, never t(ok ? "a" : "b")
   ```
5. **Variables, never concatenation.** One key per sentence with placeholders; word order differs
   between languages. Never `t("a") + x + t("b")` and never a template literal as the key
   (`t(`a ${x}`)` is rejected by the coverage test). Format numbers first: `t("{v} m", { v: fmt(x) })`.
   Wrap the whole sentence including punctuation, units and `%`: the Turkish percent sign is a prefix
   (`%50`), English a suffix: `t("%{n}", { n })` (en: `{n}%`).
6. **Plurals.** Turkish has no plural after a number; English does. Keep it simple: write the count
   sentence as `tn(n, "{n} direk")` and give English both forms: `"{n} direk": "{n} poles"` and
   `"{n} direk#1": "{n} pole"` (the `#1` key is used only when n is exactly 1). If a sentence can be
   written without a count noun, prefer that ("Poles: {n}"). Never use "(s)".
7. **Do NOT wrap**: identifiers, enum/API values (`"facility"`, `"detect"`), CSS classes, model and
   camera names, file paths, user data (project / camera / scenario names, level names that come from the
   project), `lastError` / `Error.message` text from the core, unit symbols that are universal inside a
   composed string (use `{v} m`), and any string sent to the core that the core matches by name (next
   section). Genuinely non-UI Turkish strings in a listed file (stored defaults, regexes over Turkish text)
   get `// i18n-ignore` on the same or the previous line.
8. **Text that comes from the Python core stays Turkish** (notes, warnings, legend labels, error messages,
   report contents). Do not try to translate it by matching its text in the UI.
9. **Finish a file** = add it to `TRANSLATED_FILES` in `src/core/i18nCoverage.test.ts`; the test then
   fails on any Turkish literal outside `t(...)` (Turkish letters plus a list of ASCII-only Turkish UI
   words) and on any `t("key")` without an English entry. Run
   `node node_modules/vitest/vitest.mjs run` from `desktop`.
10. **Style**: sentence case for sentences and labels ("Save as", not "Save As"); keep the Turkish
    symbols `●` `▲` `■` `◆` `→` `·` `…` and ALL-CAPS status words in caps; units `px/m`, `m`, `ha`, `m²`,
    `s` (sn), `min` (dk), `km`, `mm`, `°`. Numbers are formatted with `fmt()`. Keep the translation
    about as long as the Turkish (buttons and table headers have fixed widths).

## Traps: a label that is ALSO an API value

Translating the text shown to the user is fine; translating the **value** that is sent to the core (or
stored in the project file / compared in code) breaks calls. Keep `value=` / ids Turkish and translate only
the visible text, or leave the thing alone.

| Where | Mechanism |
|---|---|
| `core/weather.tsx:4` `CLEAR_AIR = "Berrak hava"`, `:10` | Weather preset NAME is the API value (`weather: string` in terrain / bench / ptz / report calls; the core matches presets by name) and is saved in the project file (`weather`). Never translate the value. |
| `pages/Optics.tsx:147` (`Select` at `:195-203`) | `options` are `weathers.map(w => w.label)` and the same string is `<option value>` and the text. To translate the display, split value and text; fallback `["Berrak hava"]` is an API value. |
| `pages/Terrain.tsx:570-575` | `<option key=value={w.label}>{w.label}</option>`, plus the `w.label === weather` comparison; fallback `{ label: "Berrak hava" }`. Same rule. |
| `pages/View3D.tsx:75, 210-212` | `weathers: string[]` of core labels, `<option value={w}>{w}</option>`, default `["Berrak hava"]`. |
| `pages/Terrain.tsx:1105-1107` | Preset locations: `catalog.locations[].label` is the option value and is looked up with `x.label === e.target.value` (also kept in `dl.loc`). |
| `pages/Workspace.tsx:118-120` | Required-level select: `value={lv.key}`, text `lv.name` is project data (level names are user-editable and saved in the project; they may read "group: TR name / EN name"). Do not `t()` `lv.name`; `core/levelLabel.ts` parses that format. |
| `pages/View3D.tsx:11-16` | `PALETTES` / `TARGETS` are `[id, label]` pairs: ids (`"thermal_ironbow"`, `"human"`) go to the API, labels are safe to translate (wrap at render, see convention 2). |
| `core/terrainLevels.ts`, `pages/Terrain.tsx:40-41` `TARGETS` / `THERMAL_TARGETS`, `components/PtzPanels.tsx:21` `CUE_TARGETS`, `pages/Optics.tsx:17-22` `EN_TASKS` | Level selectors: the first element (`"detect"`, `12.5`, `null`) is the value sent; the Turkish string is the label. Translate the label only. `levelName()` cuts the label at the LAST space to drop the threshold (`"Algılama ≥25"` -> `"Algılama"`): the English label must also be `"<name> ≥<number>"` with the threshold last and a single space before it. |
| `pages/Terrain.tsx:515` + `components/ApproachPanel.tsx:53` | `mixedLayer?.title === "Gizli yaklaşma"` identifies the layer by its Turkish TITLE. If the title is translated where it is set, this comparison silently stops matching: compare an id instead (add a `kind` field) or keep the constant. |
| `components/PtzPanels.tsx:39,75` | Mode ids `"rotation" "sector" "tour" "cue"` are state/API values; labels are free. `:65` job labels are display only. |
| `pages/Terrain.tsx:714-718` | `line_mode` `"facility" "border" "highway" "area"`: values stay, option texts (Tesis, Sınır, Otoyol) translate. |
| `components/ProjectBar.tsx:34` | Check-point `name: "Kontrol Noktası"` is written into the project file (marked `i18n-ignore`). |
| `core/project.tsx:68` `NEW_PROJECT_NAME`, `:81`, `:91` | `"Yeni proje"` is stored and compared (ProjectBar saves the file name instead when the name is still the default); the UI shows `t("Yeni proje")` for it. Camera names `Kamera N` key the reports and the overlap analysis (names must be unique), so they are data, not UI. |
| `pages/Library.tsx:96,194` | Filter ids `"all" "fixed" "ptz" "thermal"` are state; chip labels translate. `camera_type` ("Sabit" / "PTZ" ...) is library DATA compared by the core / `database.is_ptz_camera`: never translate stored values. |
| `core/clarify.ts` | Normalises and compares Turkish text from the core (`toLocaleLowerCase("tr")`): matches core text, leave it. |

## Categories of text the UI shows that originate in the core (stay Turkish)

Errors (`APP_ERROR` / `Error.message`, `JobBar` error line, ProjectBar message), engine notes and
warnings (`note`, `warnings[]`, `ptz_warnings`, optimizer notes, `verified` / `worth_applying` notes,
`truncated` notices), legend rows and titles (`legend[].label`, layer titles), level names inside results
and project levels, weather / location catalog labels, camera library fields and recommendations,
compliance analysis (matrix rows, `spec_quote`, ambiguity and clarification questions, statement text),
report and export contents (PDF / CSV / XLSX / KML / DXF / offer package, BENİOKU), cue-failure reasons,
network plan item names (BOM, switch / cable names), scenario summaries. The Settings dialog tells English
users that analysis notes and generated reports are still Turkish.

## Glossary (Turkish -> English)

| Turkish | English |
|---|---|
| direk | pole |
| direk yüksekliği / montaj yüksekliği | mast height |
| çit | fence |
| çevre çiti | perimeter fence |
| kör nokta | blind spot |
| ölü bölge | dead zone |
| görüş alanı (FOV) | field of view (FOV) |
| görüş alanı analizi | viewshed analysis |
| kapsama | coverage |
| kapsama ısı haritası | coverage heat map |
| örtüşme | overlap |
| yedeklilik | redundancy |
| boşluk (çitte) | gap |
| arazi | terrain |
| yükselti modeli (DEM) | elevation model (DEM) |
| ölçülmüş / ölçülmemiş arazi | measured / unmeasured terrain |
| bina & ağaç / yüzey modeli | building & tree / surface model |
| kayıtlı bölge | saved region |
| vaziyet planı | site plan |
| kontrol noktası | check point |
| hedef (kişi, araç) | target |
| izleme (12,5 px/m) | Monitoring |
| algılama (25) | Detection |
| gözlem (62,5) | Observation |
| tanıma (125) | Recognition |
| teşhis (250) | Identification |
| piksel yoğunluğu (px/m) | pixel density (px/m) |
| yatay / dikey görüş açısı | horizontal / vertical field of view |
| odak uzaklığı | focal length |
| varifokal | varifocal |
| sabit lens | fixed lens |
| sensör | sensor |
| çözünürlük | resolution |
| etkin piksel oranı (k) | effective pixel ratio (k) |
| menzil | range |
| optik menzil | optical range |
| atmosferik menzil | atmospheric range |
| berrak hava | clear air |
| görüş mesafesi (hava) | visibility |
| sis / yağmur | fog / rain |
| termal | thermal |
| Johnson eşikleri | Johnson criteria |
| gece / IR menzili | night / IR range |
| ışık (lux) | illumination (lux) |
| parlama / arkadan ışık | glare / backlight |
| eğim (tilt) | tilt |
| yön (pan) | pan |
| yakınlaştırma | zoom |
| PTZ kamera | PTZ camera |
| devriye | patrol |
| sektör devriyesi | sector patrol |
| 360° dönüş | 360° rotation |
| preset turu | preset tour |
| bekleme | dwell |
| revizit süresi | revisit time |
| tespit → teşhis süresi | detect-to-identify time |
| tepki süresi | response time |
| sürekli / aralıklı kapsama | continuous / intermittent coverage |
| talep üzerine erişim | on-demand reach |
| gizli yaklaşma | covert approach |
| uyarı süresi | warning time |
| müdahale süresi | response (intervention) time |
| plaka okuma (ANPR) | licence plate recognition (ANPR) |
| bulanıklık | motion blur |
| alan hedefi | area target |
| en az direk | minimum poles |
| odak optimizasyonu | focal length optimisation |
| şartname | specification |
| uygunluk / uyum | compliance |
| uygunluk matrisi | compliance matrix |
| ister | requirement |
| belirsizlik | ambiguity |
| kamera kütüphanesi | camera library |
| proje | project |
| rapor / mühendislik raporu | report / engineering report |
| teklif paketi | offer package |
| ağ planı | network plan |
| anahtar (switch) / bakır / fiber | switch / copper / fibre |
| malzeme listesi (BOM) | bill of materials (BOM) |
| çekirdek | core (the Python engine) |
| ayarlar / kısayollar / hakkında | settings / shortcuts / about |
| geri al / yinele | undo / redo |
| kaydet / farklı kaydet | save / save as |
