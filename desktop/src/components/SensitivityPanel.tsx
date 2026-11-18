import { useEffect, useState } from "react";
import { Play, ShieldQuestion } from "lucide-react";
import type { SensitivityResult, TerrainApi, TerrainInfo, TerrainLevel } from "../core/terrainApi";
import type { useJob } from "../core/useJob";
import type { FullRun } from "../pages/fullAnalysis";
import type { Layer } from "./PtzPanels";
import { t, tn } from "../core/i18n";
import { levelsFor } from "../core/terrainLevels";
import { SENSITIVITY_LIMITS, clampRuns, type SensitivitySettings } from "../pages/terrainPersist";
import { Button, Panel, fmt } from "./ui";

type Job = ReturnType<typeof useJob>;

const pct = (f: number) => t("%{n}", { n: fmt(f * 100, 1) });

/** An optional number field: empty = the core's default for this terrain (shown as the placeholder). */
function OptNumber({ label, value, placeholder, min, max, onChange }: {
  label: string; value: number | null; placeholder: string; min: number; max: number; onChange: (v: number | null) => void;
}) {
  return (
    <label className="flex flex-col gap-0.5 text-[12px] text-text-3">{label}
      <input type="number" min={min} max={max} step="any" value={value ?? ""} placeholder={placeholder} aria-label={label}
        onChange={(e) => {
          const raw = e.target.value.trim();
          const n = Number(raw);
          onChange(raw === "" || !Number.isFinite(n) ? null : Math.min(max, Math.max(min, n)));
        }}
        className="h-8 w-full rounded border border-line bg-bg-0 px-2 font-mono text-text" />
    </label>
  );
}

/** How much of the coverage survives wrong data: certain / data-dependent / never, over perturbed copies of the DEM and
 *  the building heights (the engine re-run per copy; run 0 is the data as it is). */
export function SensitivityPanel({ T, job, info, useObstacles, level, band, thermal, resetKey, inputsKey, value, onChange, showing, onLayer, full }: {
  /** a "Tam analiz" run: its result is shown here as if this panel had run it */
  full?: FullRun | null;
  T: TerrainApi; job: Job; info: TerrainInfo; useObstacles: boolean; thermal: boolean;
  /** the coverage report's level choice ("plan" = the plan's own target) and band widths: scored the same way */
  level: "plan" | TerrainLevel; band: { band_m?: number; band_in_m?: number };
  /** a new plan drops the result (the core drops it too) */
  resetKey: number;
  /** anything else a result was computed from (target height, obstacle switch, weather, level, band, grid): a change drops it */
  inputsKey: string;
  value: SensitivitySettings; onChange: (v: SensitivitySettings) => void;
  /** the map shows this panel's picture */
  showing: boolean; onLayer: (l: Layer | null) => void;
}) {
  const [res, setRes] = useState<SensitivityResult | null>(null);
  useEffect(() => {
    setRes(null);
    if (showing) onLayer(null);          // a picture of the old inputs must not stay on the map
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info.terrain_id, resetKey, inputsKey]);
  useEffect(() => {
    const r = full?.results.sensitivity as SensitivityResult | undefined;
    if (r) setRes(r);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [full?.nonce]);
  const d = info.sensitivity_defaults;
  const [runsLo, runsHi] = SENSITIVITY_LIMITS.runs;

  const run = () => void job.run(t("Veri hassasiyeti hesaplanıyor"), (o) => T.sensitivity({
    terrain_id: info.terrain_id, use_obstacles: useObstacles, level, ...band, max_px: 1200, n_runs: clampRuns(value.nRuns),
    dem_sigma_m: value.sigma, dem_corr_m: value.corr }, o)).then((r) => {
    if (!r) return;
    setRes(r);
    onLayer({ overlay: r.overlay.data, legend: r.legend, title: t("Veri hassasiyeti"), kind: "sensitivity" });
  });

  const a = res?.assumptions;
  const objects = a ? [[a.objects.default ?? 0, t("varsayılan")], [a.objects.levels ?? 0, t("kat sayısından")], [a.objects.tagged ?? 0, t("etiketli")]] as const : [];
  const row = (label: string, v: string, tone = "") => (
    <div key={label} className="flex justify-between gap-2"><span className="text-text-3">{label}</span><span className={tone}>{v}</span></div>);
  const levelRows = res ? levelsFor(thermal).flatMap(([k, l]) => (res.levels[k] ? [[k, l, res.levels[k]!] as const] : [])) : [];
  return (
    <Panel title={t("Veri hassasiyeti")} icon={<ShieldQuestion size={16} className="text-secondary" />}>
      <p className="text-[11px] text-text-3">{t("İndirilen yükseltinin metrelerce hatası, çoğu bina yüksekliğinin varsayım olması kapsamayı oynatır. Veri her koşuda hata ile bozulup motor yeniden çalışır; hep tutan yer kesin, bazılarında tutan veriye bağlıdır.")}</p>
      <div className="mt-2 grid grid-cols-3 gap-1.5">
        <label className="flex flex-col gap-0.5 text-[12px] text-text-3">{t("Koşu sayısı ({lo}–{hi})", { lo: runsLo, hi: runsHi })}
          <input type="number" min={runsLo} max={runsHi} step={1} value={value.nRuns} aria-label={t("Koşu sayısı")}
            onChange={(e) => onChange({ ...value, nRuns: clampRuns(e.target.value, value.nRuns) })}
            className="h-8 w-full rounded border border-line bg-bg-0 px-2 font-mono text-text" />
        </label>
        <OptNumber label={t("DEM σ (m)")} value={value.sigma} placeholder={d ? fmt(d.dem_sigma_m, 1) : ""} min={0} max={100}
          onChange={(sigma) => onChange({ ...value, sigma })} />
        <OptNumber label={t("Korelasyon (m)")} value={value.corr} placeholder={d ? fmt(d.dem_corr_m, 0) : ""} min={1} max={5000}
          onChange={(corr) => onChange({ ...value, corr })} />
      </div>
      {d && <p className="mt-1.5 text-[11px] text-text-3">{t("Boş alan = varsayılan: {basis}. Bunlar varsayımdır, ölçülmüş değer değil; verinizin doğruluğunu biliyorsanız girin.", { basis: d.basis })}</p>}
      <Button className="mt-2 w-full" disabled={job.running} onClick={run}><Play size={14} /> {t("Hassasiyeti Hesapla")}</Button>
      {res && a && (
        <div className="mt-2 flex flex-col gap-0.5 font-mono text-[11px]">
          <div className="text-text-2">{res.level_label} · {tn(res.n_runs, "{n} koşu (ilki veriyi olduğu gibi kullanır)")}</div>
          {row(t("kesin (tüm koşularda tutuyor)"), pct(res.certain), "text-primary")}
          {row(t("veriye bağlı (bazı koşularda)"), pct(res.uncertain), "text-warn")}
          {row(t("hiçbir koşuda tutmuyor"), pct(res.never), "text-danger")}
          <div className="mt-1 text-text-2">{t("Pay (korunan alanın)")}</div>
          {row(t("nominal (veri olduğu gibi)"), pct(res.nominal))}
          {row(t("koşuların %10–%90 aralığı"), `${pct(res.share_p10)} – ${pct(res.share_p90)}`)}
          {row(t("en az – en çok"), `${pct(res.share_min)} – ${pct(res.share_max)}`)}
          {levelRows.length > 0 && (
            <table className="mt-1 w-full">
              <thead><tr className="text-text-3"><th className="text-left font-normal">{t("Seviye")}</th><th className="text-right font-normal">{t("nominal")}</th><th className="text-right font-normal">{t("en az – en çok")}</th></tr></thead>
              <tbody>
                {levelRows.map(([k, l, v]) => (
                  <tr key={k}><td className="font-sans">{l}</td><td className="text-right">{pct(v.nominal)}</td><td className="text-right">{pct(v.min)} – {pct(v.max)}</td></tr>
                ))}
              </tbody>
            </table>
          )}
          <label className="mt-1.5 flex items-center gap-2 font-sans text-[12px]">
            <input type="checkbox" checked={showing}
              onChange={(e) => onLayer(e.target.checked ? { overlay: res.overlay.data, legend: res.legend, title: t("Veri hassasiyeti"), kind: "sensitivity" } : null)} />
            {t("Hassasiyet katmanını haritada göster")}
          </label>
          <div className="mt-1 font-sans text-[11px] text-text-2">{res.line}</div>
          <div className="font-sans text-[11px] text-text-3">
            {t("DEM hatası: {basis}.", { basis: a.dem_basis })}
            {objects.some(([n]) => n > 0) ? " " + t("Bina / ağaç yüksekliği: {list}.", { list: objects.filter(([n]) => n > 0).map(([n, txt]) => `${n} ${txt}`).join(", ") }) : ""}
            {a.measured_cells > 0 ? " " + t("Ölçülmüş yüzey: ±1 m.") : ""}
            {a.refine_factor > 1 ? " " + t("Hata özgün {c} m DEM ızgarasında üretilir, analiz ızgarasına aynı aradeğerlemeyle taşınır.", { c: fmt(a.base_cell_m, 1) }) : ""}
          </div>
          <div className="font-sans text-[11px] text-text-3">{t("Direk de zeminle birlikte kayar: önemli olan göreli hatadır. Kapsama geometriktir; hata modeli varsayımdır.")}{res.measured ? "" : " " + t("▲ prosedürel arazi — ölçüm değil")}</div>
        </div>
      )}
    </Panel>
  );
}
