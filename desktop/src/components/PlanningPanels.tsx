import { useState } from "react";
import { Cable, Play, Scale, Trophy } from "lucide-react";
import type { CameraConfig, ModelSummary } from "../core/api";
import type { PlanResult, TerrainApi } from "../core/terrainApi";
import type { useJob } from "../core/useJob";
import { Button, Panel, fmt } from "./ui";

type Job = ReturnType<typeof useJob>;

export interface PriceBook {
  camera: number; ptz: number; pole: number; switch: number; copper_per_m: number; fibre_per_m: number;
  nvr_per_tb: number; nvr_base: number; currency: string;
}
export const EMPTY_PRICES: PriceBook = { camera: 0, ptz: 0, pole: 0, switch: 0, copper_per_m: 0, fibre_per_m: 0, nvr_per_tb: 0, nvr_base: 0, currency: "TL" };

export interface SwitchMark { index: number; x_m: number; y_m: number; poles: number[]; ports_used: number; poe_w: number; over_budget: boolean }

export interface NetworkResult {
  network: {
    switches: SwitchMark[]; copper_m: number; fibre_m: number; longest_copper_m: number; cameras: number;
    bitrate_mbps: number; storage_tb: number; retention_days: number; poe_total_w: number; warnings: string[]; assumptions: string[];
  };
  cost: { lines: { item: string; qty: number; unit: string; unit_price: number; total: number }[]; total: number; unpriced: string[]; currency: string };
}

const money = (v: number, cur: string) => `${v.toLocaleString("tr-TR", { maximumFractionDigits: 0 })} ${cur}`;
const PRICE_FIELDS: [keyof PriceBook, string][] = [
  ["camera", "Sabit kamera (adet)"], ["ptz", "PTZ (adet)"], ["pole", "Direk + temel (adet)"], ["switch", "PoE switch (adet)"],
  ["copper_per_m", "Bakır kablo (m)"], ["fibre_per_m", "Fiber (m)"], ["nvr_per_tb", "Kayıt (TB)"], ["nvr_base", "NVR / sunucu"],
];

/** Cabling, PoE, NVR sizing and the priced bill of quantities of the current plan. */
export function NetworkPanel({ T, job, terrainId, useObstacles, prices, onPrices, onResult }: {
  T: TerrainApi; job: Job; terrainId: string; useObstacles: boolean;
  prices: PriceBook; onPrices: (p: PriceBook) => void; onResult: (r: NetworkResult | null) => void;
}) {
  const [cfg, setCfg] = useState({ switch_ports: 8, poe_budget_w: 120, max_copper_m: 90, retention_days: 30 });
  const [res, setRes] = useState<NetworkResult | null>(null);
  const [showPrices, setShowPrices] = useState(false);
  const run = () => void job.run("Ağ planı ve maliyet", () => T.network({ terrain_id: terrainId, use_obstacles: useObstacles, ...cfg, prices }))
    .then((r) => { if (r) { setRes(r); onResult(r); } });
  const n = res?.network;
  return (
    <Panel title="Ağ, kablo & maliyet" icon={<Cable size={16} className="text-secondary" />}>
      <div className="grid grid-cols-2 gap-2 text-[12px]">
        {([["switch_ports", "Switch port"], ["poe_budget_w", "PoE bütçesi (W)"], ["max_copper_m", "Bakır sınırı (m)"], ["retention_days", "Kayıt süresi (gün)"]] as const).map(([k, l]) => (
          <label key={k}><span className="label">{l}</span>
            <input type="number" value={cfg[k]} min={1} onChange={(e) => setCfg({ ...cfg, [k]: Number(e.target.value) || cfg[k] })}
              className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 font-mono" /></label>
        ))}
      </div>
      <button type="button" className="mt-2 text-[11px] text-text-2 underline" onClick={() => setShowPrices((s) => !s)}>{showPrices ? "Birim fiyatları gizle" : "Birim fiyatlar…"}</button>
      {showPrices && (
        <div className="mt-1 grid grid-cols-2 gap-1.5 text-[11px]">
          {PRICE_FIELDS.map(([k, l]) => (
            <label key={k}><span className="text-text-3">{l}</span>
              <input type="number" min={0} value={prices[k] as number} onChange={(e) => onPrices({ ...prices, [k]: Math.max(0, Number(e.target.value) || 0) })}
                className="mt-0.5 h-7 w-full rounded border border-line bg-bg-0 px-1.5 font-mono" /></label>
          ))}
          <label><span className="text-text-3">Para birimi</span>
            <input value={prices.currency} onChange={(e) => onPrices({ ...prices, currency: e.target.value || "TL" })} className="mt-0.5 h-7 w-full rounded border border-line bg-bg-0 px-1.5 font-mono" /></label>
        </div>
      )}
      <Button className="mt-2 w-full" disabled={job.running} onClick={run}><Play size={14} /> Ağ Planı ve Maliyeti Hesapla</Button>
      {n && res && (
        <div className="mt-2 font-mono text-[11px]">
          <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
            <span className="text-text-3">Switch</span><span className="text-right">{n.switches.length} adet</span>
            <span className="text-text-3">Bakır / fiber</span><span className="text-right">{fmt(n.copper_m, 0)} / {fmt(n.fibre_m, 0)} m</span>
            <span className="text-text-3">En uzun bakır</span><span className="text-right">{fmt(n.longest_copper_m, 0)} m</span>
            <span className="text-text-3">PoE toplam</span><span className="text-right">{fmt(n.poe_total_w, 0)} W</span>
            <span className="text-text-3">NVR girişi</span><span className="text-right">{fmt(n.bitrate_mbps, 0)} Mbps</span>
            <span className="text-text-3">Kayıt ({n.retention_days} gün)</span><span className="text-right">{fmt(n.storage_tb, 1)} TB</span>
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
              <tr><td className="pt-1 font-sans font-semibold">Toplam</td><td /><td className="pt-1 text-right font-bold">{money(res.cost.total, res.cost.currency)}</td></tr>
            </tbody>
          </table>
          {res.cost.unpriced.length > 0 && <div className="mt-1 font-sans text-text-3">◆ Fiyatı girilmeyen: {res.cost.unpriced.join(", ")} — toplama dahil değil.</div>}
          <details className="mt-1 font-sans text-text-3"><summary className="cursor-pointer">Varsayımlar</summary><ul className="list-disc pl-4">{n.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></details>
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
export function ComparePanel({ T, job, terrainId, useObstacles, models, camera, planArgs, prices, onUse }: {
  T: TerrainApi; job: Job; terrainId: string; useObstacles: boolean; models: ModelSummary[]; camera: CameraConfig;
  planArgs: Record<string, unknown>; prices: PriceBook; onUse: (plan: PlanResult, modelName: string) => void;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const [goal, setGoal] = useState(90);
  const [modelPrices, setModelPrices] = useState<Record<string, number>>({});
  const [opts, setOpts] = useState<CompareOption[] | null>(null);
  const fixed = models.filter((m) => !m.is_ptz);
  const toggle = (n: string) => setPicked((p) => (p.includes(n) ? p.filter((x) => x !== n) : p.length < 6 ? [...p, n] : p));
  const run = () => void job.run(`${picked.length} model karşılaştırılıyor`, (o) => T.compareModels({
    terrain_id: terrainId, use_obstacles: useObstacles, camera, models: picked, goal_frac: goal / 100, level: "detect",
    position_steps: 0, prices, model_prices: modelPrices, ...planArgs,
  }, o)).then((r) => { if (r) setOpts(r.options); });

  return (
    <Panel title="Model karşılaştır" icon={<Scale size={16} className="text-secondary" />}>
      <p className="text-[11px] text-text-3">Her model için aynı en az direk araması (direk başına odak/eğim), ağ planı ve maliyet. En çok 6 model.</p>
      <div className="mt-2 max-h-[160px] overflow-auto rounded bg-bg-1 p-1 text-[11px]">
        {fixed.map((m) => (
          <label key={m.name} className="flex items-center gap-1.5 px-1 py-0.5">
            <input type="checkbox" checked={picked.includes(m.name)} onChange={() => toggle(m.name)} />
            <span className="min-w-0 flex-1 truncate" title={m.name}>{m.name}</span>
            {picked.includes(m.name) && (
              <input type="number" min={0} placeholder="fiyat" value={modelPrices[m.name] ?? ""} aria-label={`${m.name} birim fiyat`}
                onChange={(e) => setModelPrices({ ...modelPrices, [m.name]: Number(e.target.value) || 0 })}
                className="h-6 w-20 rounded border border-line bg-bg-0 px-1 font-mono" />
            )}
          </label>
        ))}
      </div>
      <label className="mt-2 flex items-center justify-between text-[12px]"><span className="label">Algılama hedefi (bant)</span>
        <input type="number" min={30} max={99} value={goal} onChange={(e) => setGoal(Math.min(99, Math.max(30, Number(e.target.value) || 90)))}
          className="h-7 w-16 rounded border border-line bg-bg-0 px-1.5 text-right font-mono" /></label>
      <Button className="mt-2 w-full" disabled={job.running || picked.length < 1} onClick={run}><Play size={14} /> Karşılaştır</Button>
      {opts && (
        <table className="mt-2 w-full border-collapse font-mono text-[11px]">
          <thead><tr className="text-text-3"><th className="text-left font-normal">Model</th><th className="text-right font-normal">Direk</th><th className="text-right font-normal">Bant</th><th className="text-right font-normal">Maliyet</th><th /></tr></thead>
          <tbody>
            {opts.map((o, i) => (
              <tr key={o.model_name} className={`border-b border-line-soft ${o.met ? "" : "text-text-3"}`}>
                <td className="max-w-[120px] truncate py-0.5 font-sans" title={o.model_name}>{i === 0 && o.met && <Trophy size={11} className="mr-1 inline text-primary" />}{o.model_name}</td>
                <td className="text-right">{o.poles}</td>
                <td className="text-right">%{fmt(o.band_frac * 100, 0)}{o.met ? "" : " ▲"}</td>
                <td className="text-right">{o.cost && !o.cost.unpriced.length ? money(o.cost.total, o.cost.currency) : "—"}</td>
                <td className="pl-1 text-right">
                  <button type="button" className="text-secondary underline" onClick={() => void T.compareUse({ terrain_id: terrainId, use_obstacles: useObstacles, camera, model_name: o.model_name, ...planArgs })
                    .then((r) => onUse(r.plan, o.model_name))}>kullan</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {opts && opts.some((o) => !o.met) && <p className="mt-1 text-[11px] text-text-3">▲ hedefi tutmayan model: en iyi denemesi gösterilir.</p>}
    </Panel>
  );
}
