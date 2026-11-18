import { useEffect, useState } from "react";
import { Cable, Play, RotateCw, Scale, Trophy } from "lucide-react";
import type { CameraConfig, ModelSummary } from "../core/api";
import type { AssistedPlan, PlanResult, TerrainApi } from "../core/terrainApi";
import type { useJob } from "../core/useJob";
import { numberLocale, t, tn } from "../core/i18n";
import { DEFAULT_ASSIST, DEFAULT_NETWORK, networkRequest, type AssistSettings, type NetworkSettings } from "../pages/analysisSettings";
import type { FullRun } from "../pages/fullAnalysis";
import { Button, Panel, fmt } from "./ui";

/** "<name> · <min>–<max> mm" for a model selector; library names taken from brochure titles are cut at 40 characters
 *  (the option's title carries the full name; selection always uses m.name). */
export function modelOptionLabel(m: ModelSummary): string {
  const name = m.name.length > 40 ? `${m.name.slice(0, 39).trimEnd()}…` : m.name;
  const lo = m.focal_min_mm, hi = m.focal_max_mm;
  if (lo == null && hi == null) return name;
  const f = lo != null && hi != null && Math.abs(hi - lo) > 1e-6 ? `${fmt(lo)}–${fmt(hi)} mm` : `${fmt((lo ?? hi) as number)} mm`;
  return `${name} · ${f}`;
}

type Job = ReturnType<typeof useJob>;

export interface PriceBook {
  camera: number; ptz: number; pole: number; switch: number; copper_per_m: number; fibre_per_m: number;
  nvr_per_tb: number; nvr_base: number;
  trench_per_m: number; power_cable_per_m: number; cabinet: number; ups_per_kwh: number; grounding_set: number;
  vms_licence: number; workstation: number; bracket: number; install_per_camera: number; maintenance_per_camera_year: number;
  currency: string;
}
export const EMPTY_PRICES: PriceBook = {
  camera: 0, ptz: 0, pole: 0, switch: 0, copper_per_m: 0, fibre_per_m: 0, nvr_per_tb: 0, nvr_base: 0,
  trench_per_m: 0, power_cable_per_m: 0, cabinet: 0, ups_per_kwh: 0, grounding_set: 0,
  vms_licence: 0, workstation: 0, bracket: 0, install_per_camera: 0, maintenance_per_camera_year: 0, currency: "TL",
};

export interface SwitchMark { index: number; x_m: number; y_m: number; poles: number[]; ports_used: number; poe_w: number; over_budget: boolean }

export interface NetworkResult {
  network: {
    switches: SwitchMark[]; copper_m: number; fibre_m: number; longest_copper_m: number; cameras: number;
    bitrate_mbps: number; storage_tb: number; retention_days: number; poe_total_w: number; warnings: string[]; assumptions: string[];
    peak_bitrate_mbps: number; trench_m: number; cabinets: number; ups_load_w: number; ups_wh: number; autonomy_h: number; fibre_ring: boolean;
  };
  cost: { lines: { item: string; qty: number; unit: string; unit_price: number; total: number }[]; total: number; unpriced: string[]; currency: string };
}

const money = (v: number, cur: string) => `${v.toLocaleString(numberLocale(), { maximumFractionDigits: 0 })} ${cur}`;
const priceFields = (): [keyof PriceBook, string][] => [
  ["camera", t("Sabit kamera (adet)")], ["ptz", t("PTZ (adet)")], ["pole", t("Direk + temel (adet)")], ["switch", t("PoE switch (adet)")],
  ["copper_per_m", t("Bakır kablo (m)")], ["fibre_per_m", t("Fiber (m)")], ["nvr_per_tb", t("Kayıt (TB)")], ["nvr_base", t("NVR / sunucu")],
  ["trench_per_m", t("Hendek (m)")], ["power_cable_per_m", t("Enerji kablosu (m)")], ["cabinet", t("Saha kabini (adet)")],
  ["ups_per_kwh", t("UPS (kWh)")], ["grounding_set", t("Topraklama + parafudr (set)")], ["vms_licence", t("VMS lisansı (kamera)")],
  ["workstation", t("İş istasyonu (adet)")], ["bracket", t("Montaj aparatı (adet)")], ["install_per_camera", t("Montaj + devreye alma (kamera)")],
  ["maintenance_per_camera_year", t("Bakım (kamera·yıl)")],
];

/** Cabling, PoE, NVR sizing and the priced bill of quantities of the current plan. */
export function NetworkPanel({ T, job, terrainId, useObstacles, prices, onPrices, onResult, resetKey, value, onChange, full }: {
  T: TerrainApi; job: Job; terrainId: string; useObstacles: boolean; resetKey: number;
  /** the saved cabling choices (null = never touched) and where an edit goes (kept in the project file) */
  value: NetworkSettings | null; onChange: (v: NetworkSettings) => void;
  /** a "Tam analiz" run: its network result is shown here as if this panel had run it */
  full?: FullRun | null;
  prices: PriceBook; onPrices: (p: PriceBook) => void; onResult: (r: NetworkResult | null) => void;
}) {
  const s = value ?? DEFAULT_NETWORK;
  const cfg = { switch_ports: s.switch_ports, poe_budget_w: s.poe_budget_w, max_copper_m: s.max_copper_m, retention_days: s.retention_days, autonomy_h: s.autonomy_h, night_factor: s.night_factor };
  const setCfg = (c: typeof cfg) => onChange({ ...s, ...c });
  const ring = s.ring;
  const setRing = (r: boolean) => onChange({ ...s, ring: r });
  const [res, setRes] = useState<NetworkResult | null>(null);
  useEffect(() => {
    const r = full?.results.network as NetworkResult | undefined;
    if (r) { setRes(r); onResult(r); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [full?.nonce]);
  const [showPrices, setShowPrices] = useState(false);
  useEffect(() => { setRes(null); }, [resetKey]);       // a new plan: the old network no longer applies
  const run = () => void job.run(t("Ağ planı ve maliyet"), () => T.network({ terrain_id: terrainId, use_obstacles: useObstacles, ...networkRequest(s), prices }))
    .then((r) => { if (r) { setRes(r); onResult(r); } });
  const n = res?.network;
  return (
    <Panel title={t("Ağ, kablo & maliyet")} icon={<Cable size={16} className="text-secondary" />}>
      <div className="grid grid-cols-2 gap-2 text-[12px]">
        {([["switch_ports", t("Switch port")], ["poe_budget_w", t("PoE bütçesi (W)")], ["max_copper_m", t("Bakır sınırı (m)")], ["retention_days", t("Kayıt süresi (gün)")],
          ["autonomy_h", t("UPS otonomi (saat)")], ["night_factor", t("Gece bit hızı (×)")]] as const).map(([k, l]) => (
          <label key={k}><span className="label">{l}</span>
            <input type="number" value={cfg[k]} min={1} onChange={(e) => setCfg({ ...cfg, [k]: Number(e.target.value) || cfg[k] })}
              className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 font-mono" /></label>
        ))}
      </div>
      <label className="mt-2 flex items-center gap-2 text-[12px]"><input type="checkbox" checked={ring} onChange={(e) => setRing(e.target.checked)} /> {t("Fiber halka (çift yol)")}</label>
      <button type="button" className="mt-2 text-[11px] text-text-2 underline" onClick={() => setShowPrices((s) => !s)}>{showPrices ? t("Birim fiyatları gizle") : t("Birim fiyatlar…")}</button>
      {showPrices && (
        <div className="mt-1 grid grid-cols-2 gap-1.5 text-[11px]">
          {priceFields().map(([k, l]) => (
            <label key={k}><span className="text-text-3">{l}</span>
              <input type="number" min={0} value={prices[k] as number} onChange={(e) => onPrices({ ...prices, [k]: Math.max(0, Number(e.target.value) || 0) })}
                className="mt-0.5 h-7 w-full rounded border border-line bg-bg-0 px-1.5 font-mono" /></label>
          ))}
          <label><span className="text-text-3">{t("Para birimi")}</span>
            <input value={prices.currency} onChange={(e) => onPrices({ ...prices, currency: e.target.value || "TL" })} className="mt-0.5 h-7 w-full rounded border border-line bg-bg-0 px-1.5 font-mono" /></label>
        </div>
      )}
      <Button className="mt-2 w-full" disabled={job.running} onClick={run}><Play size={14} /> {t("Ağ Planı ve Maliyeti Hesapla")}</Button>
      {n && res && (
        <div className="mt-2 font-mono text-[11px]">
          <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
            <span className="text-text-3">Switch</span><span className="text-right">{t("{n} adet", { n: n.switches.length })}</span>
            <span className="text-text-3">{t("Bakır / fiber")}</span><span className="text-right">{fmt(n.copper_m, 0)} / {fmt(n.fibre_m, 0)} m</span>
            <span className="text-text-3">{t("En uzun bakır")}</span><span className="text-right">{fmt(n.longest_copper_m, 0)} m</span>
            <span className="text-text-3">{t("PoE toplam")}</span><span className="text-right">{fmt(n.poe_total_w, 0)} W</span>
            <span className="text-text-3">{t("NVR girişi (gece tepe)")}</span><span className="text-right">{fmt(n.peak_bitrate_mbps || n.bitrate_mbps, 0)} Mbps</span>
            <span className="text-text-3">{t("Hendek")}</span><span className="text-right">{fmt(n.trench_m, 0)} m</span>
            <span className="text-text-3">{t("Kabin / UPS")}</span><span className="text-right">{n.cabinets} · {fmt(n.ups_wh / 1000, 1)} kWh</span>
            <span className="text-text-3">{tn(n.retention_days, "Kayıt ({n} gün)")}</span><span className="text-right">{fmt(n.storage_tb, 1)} TB</span>
          </div>
          {n.warnings.map((w) => <div key={w} className="mt-1 text-warn">▲ {w}</div>)}
          <table className="mt-2 w-full border-collapse">
            <tbody>
              {res.cost.lines.map((l) => (
                <tr key={l.item} className="border-b border-line-soft">
                  <td className="py-0.5 font-sans">{l.item}</td>
                  <td className="text-right">{fmt(l.qty, l.qty % 1 ? 1 : 0)} {l.unit}</td>
                  <td className={`text-right ${l.unit_price ? "" : "text-text-3"}`}>{l.unit_price ? money(l.total, res.cost.currency) : "—"}</td>
                </tr>
              ))}
              <tr><td className="pt-1 font-sans font-semibold">{t("Toplam")}</td><td /><td className="pt-1 text-right font-bold">{money(res.cost.total, res.cost.currency)}</td></tr>
            </tbody>
          </table>
          {res.cost.unpriced.length > 0 && <div className="mt-1 font-sans text-text-3">{t("◆ Fiyatı girilmeyen: {items} — toplama dahil değil.", { items: res.cost.unpriced.join(", ") })}</div>}
          <details className="mt-1 font-sans text-text-3"><summary className="cursor-pointer">{t("Varsayımlar")}</summary><ul className="list-disc pl-4">{n.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></details>
        </div>
      )}
    </Panel>
  );
}

/** Fewest poles + PTZs when the fixed cameras detect and a PTZ zooms in on alarm (instead of fixed-only Recognition). */
export function AssistPanel({ T, job, terrainId, useObstacles, models, camera, planArgs, goal, levels, defaultLevel, ptzModelFor, onPlan, onApplied, result: res, onResult: setRes, value, onChange, ptz, onPtz }: {
  /** the saved choices (null = never touched; the PTZ model is kept by name) and where an edit goes */
  value: AssistSettings | null; onChange: (v: AssistSettings) => void;
  /** the chosen PTZ model resolved from the library (the page looks the saved name up when a project opens) */
  ptz: CameraConfig | null; onPtz: (c: CameraConfig | null) => void;
  T: TerrainApi; job: Job; terrainId: string; useObstacles: boolean; models: ModelSummary[]; camera: CameraConfig;
  /** the project's coverage goal (%) */
  goal: number;
  /** levels above Detection the PTZ can be asked for (the camera scale's own list) */
  levels: readonly (readonly [string, string])[]; defaultLevel: string;
  planArgs: Record<string, unknown>;
  ptzModelFor: (name: string) => Promise<CameraConfig>;
  onPlan: (p: PlanResult) => void;
  /** a met plan was applied: its PTZ model and density, for the mixed panel to re-plan with the same PTZs */
  onApplied: (ptz: CameraConfig, everyN: number) => void;
  /** the last assist result. The page owns it (this panel unmounts with its tab) and drops it on a plan change this run did not cause. */
  result: AssistedPlan | null; onResult: (r: AssistedPlan | null) => void;
}) {
  const ptzModels = models.filter((m) => m.is_ptz);
  const s = value ?? { ...DEFAULT_ASSIST, level: defaultLevel };
  const level = levels.some(([k]) => k === s.level) ? s.level : defaultLevel;
  const maxS = s.maxS;
  const setLevel = (l: string) => onChange({ ...s, level: l });
  const setMaxS = (m: number) => onChange({ ...s, maxS: m });
  const setPtz = (c: CameraConfig | null) => { onPtz(c); onChange({ ...s, model: c?.model_name ?? "" }); };
  const run = () => ptz && void job.run(t("PTZ destekli en az direk"), (o) => T.assistedPlan({
    terrain_id: terrainId, use_obstacles: useObstacles, camera, ...planArgs, ptz_camera: ptz,
    goal_frac: goal / 100, level, max_s: maxS, position_steps: 0,
  } as Parameters<TerrainApi["assistedPlan"]>[0], o)).then((r) => {
    if (!r) return;
    // the new plan replaces (and so clears) the old result first; the run's own result is set AFTER it
    if (r.plan) { onPlan(r.plan); onApplied(ptz, r.ptz_every_n); }
    setRes(r);
  });
  const pctOf = (f: number) => t("%{n}", { n: fmt(f * 100, 1) });
  return (
    <Panel title={t("PTZ destekli hedef")} icon={<RotateCw size={16} className="text-ptz" />}>
      <p className="text-[11px] text-text-3">{t("Sabit kameralar bandın %{p}'inde sürekli algılar; alarmda bir PTZ seçilen seviyeyi en çok {s} sn içinde tutar.", { p: fmt(goal, goal % 1 ? 1 : 0), s: fmt(maxS, 0) })}</p>
      <select className="mt-2 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" aria-label={t("PTZ modeli (destekli hedef)")} title={ptz?.model_name}
        value={ptz?.model_name ?? ""} onChange={async (e) => setPtz(e.target.value ? await ptzModelFor(e.target.value) : null)}>
        <option value="">{t("— PTZ modeli seçin —")}</option>
        {ptzModels.map((m) => <option key={m.name} value={m.name} title={m.name}>{modelOptionLabel(m)}</option>)}
      </select>
      <div className="mt-2 grid grid-cols-2 gap-1.5 text-[12px]">
        <label className="flex flex-col gap-0.5 text-text-3">{t("Seviye")}
          <select className="h-8 rounded border border-line bg-bg-0 px-2 text-text" aria-label={t("PTZ seviyesi")} value={level} onChange={(e) => setLevel(e.target.value)}>
            {levels.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-0.5 text-text-3">{t("En çok (sn)")}
          <input type="number" min={1} max={600} step={1} value={maxS} aria-label={t("PTZ varış süresi (sn)")}
            onChange={(e) => setMaxS(Math.min(600, Math.max(1, Number(e.target.value) || 10)))}
            className="h-8 rounded border border-line bg-bg-0 px-2 font-mono text-text" />
        </label>
      </div>
      <Button className="mt-2 w-full" disabled={job.running || !ptz} onClick={run}><Play size={14} /> {t("Aramayı Başlat")}</Button>
      {res && (
        <div className="mt-2 text-[12px]">
          {res.met ? (
            <div className="text-primary">● <b>{tn(res.pole_count, "{n} direk")}</b> · {tn(res.fixed_cameras, "{n} sabit kamera")} · {res.ptz_count} PTZ {tn(res.ptz_every_n, "(her {n}. direk)")}</div>
          ) : (
            <div className="text-warn">{t("▲ Hedef tutmadı; plan değiştirilmedi.")}</div>
          )}
          <div className="mt-1 text-[11px] text-text-2">{t("Sürekli algılama {a} · PTZ destekli {b} (hedef %{g}, {p} px/m, ≤ {s} sn)", { a: pctOf(res.detect_share), b: pctOf(res.assisted_share), g: fmt(res.goal_frac * 100, 0), p: fmt(res.target_ppm, res.target_ppm % 1 ? 2 : 0), s: fmt(res.max_s, 0) })}</div>
          {res.notes.map((n) => <div key={n} className="mt-1 text-[11px] text-text-3">◆ {n}</div>)}
        </div>
      )}
    </Panel>
  );
}

interface CompareOption {
  model_name: string; met: boolean; poles: number; band_frac: number; seconds: number; switches: number; copper_m: number;
  storage_tb: number; cost: NetworkResult["cost"] | null;
}

/** Which library model holds the fence goal with the fewest poles / lowest cost. */
export function ComparePanel({ T, job, terrainId, useObstacles, models, camera, planArgs, prices, onUse, level, levelLabel, goal }: {
  T: TerrainApi; job: Job; terrainId: string; useObstacles: boolean; models: ModelSummary[]; camera: CameraConfig;
  /** the project's requirement: level held on this share (%) of the band */
  level: string; levelLabel: string; goal: number;
  planArgs: Record<string, unknown>; prices: PriceBook; onUse: (plan: PlanResult, modelName: string) => void;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const [modelPrices, setModelPrices] = useState<Record<string, number>>({});
  const [opts, setOpts] = useState<CompareOption[] | null>(null);
  const fixed = models.filter((m) => !m.is_ptz);
  const toggle = (n: string) => setPicked((p) => (p.includes(n) ? p.filter((x) => x !== n) : p.length < 6 ? [...p, n] : p));
  const run = () => void job.run(tn(picked.length, "{n} model karşılaştırılıyor"), (o) => T.compareModels({
    terrain_id: terrainId, use_obstacles: useObstacles, camera, models: picked, goal_frac: goal / 100, level,
    position_steps: 0, prices, model_prices: modelPrices, ...planArgs,
  }, o)).then((r) => { if (r) setOpts(r.options); });

  return (
    <Panel title={t("Model karşılaştır")} icon={<Scale size={16} className="text-secondary" />}>
      <p className="text-[11px] text-text-3">{t("Her model için aynı en az direk araması (direk başına odak/eğim), ağ planı ve maliyet. En çok 6 model. İster: bandın %{p}'inde {level}.", { p: fmt(goal, goal % 1 ? 1 : 0), level: levelLabel })}</p>
      <div className="mt-2 max-h-[160px] overflow-auto rounded bg-bg-1 p-1 text-[11px]">
        {fixed.map((m) => (
          <label key={m.name} className="flex items-center gap-1.5 px-1 py-0.5">
            <input type="checkbox" checked={picked.includes(m.name)} onChange={() => toggle(m.name)} />
            <span className="min-w-0 flex-1 truncate" title={m.name}>{m.name}</span>
            {picked.includes(m.name) && (
              <input type="number" min={0} placeholder={t("fiyat")} value={modelPrices[m.name] ?? ""} aria-label={t("{name} birim fiyat", { name: m.name })}
                onChange={(e) => setModelPrices({ ...modelPrices, [m.name]: Number(e.target.value) || 0 })}
                className="h-6 w-20 rounded border border-line bg-bg-0 px-1 font-mono" />
            )}
          </label>
        ))}
      </div>
      <Button className="mt-2 w-full" disabled={job.running || picked.length < 1} onClick={run}><Play size={14} /> {t("Karşılaştır")}</Button>
      {opts && (
        <table className="mt-2 w-full border-collapse font-mono text-[11px]">
          <thead><tr className="text-text-3"><th className="text-left font-normal">Model</th><th className="text-right font-normal">{t("Direk")}</th><th className="text-right font-normal">{t("Bant")}</th><th className="text-right font-normal">{t("Maliyet")}</th><th /></tr></thead>
          <tbody>
            {opts.map((o, i) => (
              <tr key={o.model_name} className={`border-b border-line-soft ${o.met ? "" : "text-text-3"}`}>
                <td className="max-w-[120px] truncate py-0.5 font-sans" title={o.model_name}>{i === 0 && o.met && <Trophy size={11} className="mr-1 inline text-primary" />}{o.model_name}</td>
                <td className="text-right">{o.poles}</td>
                <td className="text-right">{t("%{n}", { n: fmt(o.band_frac * 100, 0) })}{o.met ? "" : " ▲"}</td>
                <td className="text-right">{o.cost && !o.cost.unpriced.length ? money(o.cost.total, o.cost.currency) : "—"}</td>
                <td className="pl-1 text-right">
                  <button type="button" className="text-secondary underline" onClick={() => void T.compareUse({ terrain_id: terrainId, use_obstacles: useObstacles, camera, model_name: o.model_name, ...planArgs })
                    .then((r) => onUse(r.plan, o.model_name))}>{t("kullan")}</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {opts && opts.some((o) => !o.met) && <p className="mt-1 text-[11px] text-text-3">{t("▲ hedefi tutmayan model: en iyi denemesi gösterilir.")}</p>}
    </Panel>
  );
}
