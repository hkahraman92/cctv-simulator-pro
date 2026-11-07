import { useEffect, useMemo, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { isTauri } from "@tauri-apps/api/core";
import { CheckCircle2, ScanLine, XCircle } from "lucide-react";
import type { Catalog, OpticResult } from "../core/api";
import { useCoalesced, useCore } from "../core/CoreProvider";
import { usePageActive } from "../core/pageActive";
import { useProject } from "../core/project";
import type { BenchResult, EdgeMeasure } from "../core/benchApi";
import PlanView from "../components/PlanView";
import { Button, Panel, Slider, Tile, fmt } from "../components/ui";

const TASKS = [
  ["Teşhis (Identification)", 250],
  ["Tanıma (Recognition)", 125],
  ["Gözlem (Observation)", 62.5],
  ["Algılama (Detection)", 25],
] as const;

/** EN 62676-4 optics bench: one camera, one task at one distance, a verdict with reasons. */
export default function Optics() {
  const active = usePageActive();
  const { api, bench, terrain, state } = useCore();
  const { site, update } = useProject();
  const c = site.camera;
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [weathers, setWeathers] = useState<{ label: string; visibility_km: number }[]>([]);
  const [mode, setMode] = useState<"min" | "max">("min");
  const [task, setTask] = useState(0);
  const [dist, setDist] = useState(20);
  const [weather, setWeather] = useState("Berrak hava");
  const [edge, setEdge] = useState<EdgeMeasure | null>(null);
  const [edgeErr, setEdgeErr] = useState("");

  useEffect(() => {
    if (state !== "ready") return;
    api.catalog().then(setCatalog).catch(() => undefined);
    terrain.catalog().then((t) => setWeathers(t.weather)).catch(() => undefined);
  }, [api, terrain, state]);

  const input = useMemo(() => ({ c, mode, task, dist, weather }), [c, mode, task, dist, weather]);
  const res = useCoalesced(input, async (i) => {
    const [plan, verdict] = await Promise.all([
      api.optics(i.c, { mode: i.mode }),
      bench.bench({ camera: i.c, mode: i.mode, req_ppm: TASKS[i.task][1], distance_m: i.dist, weather: i.weather }),
    ]);
    return { plan, verdict } as { plan: OpticResult; verdict: BenchResult };
  }, state === "ready" && active);
  const v = res.value?.verdict;
  const mp = (() => {
    const r = catalog?.resolutions.find((x) => x.name === c.resolution_name);
    return r ? (r.width_px * r.height_px) / 1e6 : 0;
  })();

  const measure = async () => {
    setEdgeErr("");
    const path = await open({ title: "Eğik kenar fotoğrafı (yüksek kontrast, ~5° eğik)", filters: [{ name: "Görsel", extensions: ["png", "jpg", "jpeg", "tif", "tiff", "bmp"] }] });
    if (typeof path !== "string") return;
    try {
      const m = await bench.measureEdge(path, mp || undefined);
      setEdge(m);
      update({ effective_px_ratio: Math.round(m.k * 100) / 100 });
    } catch (e) {
      setEdgeErr(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="flex min-h-full flex-wrap content-start gap-3 p-3">
      <Panel title="Kamera ve montaj" className="w-[300px] shrink-0">
        <div className="mb-2 truncate text-[12px] font-semibold">{c.name} · <span className="text-text-2">{c.model_name}</span></div>
        <div className="flex flex-col gap-2">
          <Select label="Sensör" value={c.sensor_name} options={(catalog?.sensors ?? []).map((s) => s.name)} onChange={(v) => update({ sensor_name: v })} />
          <Select label="Çözünürlük" value={c.resolution_name} options={(catalog?.resolutions ?? []).map((s) => s.name)} onChange={(v) => update({ resolution_name: v })} />
        </div>
        <div className="mt-3 flex flex-col gap-3">
          <Slider label="Odak (geniş)" unit="mm" value={c.focal_min_mm} min={1} max={Math.max(c.focal_max_mm, 1)} step={0.1} onChange={(f) => update({ focal_min_mm: f })} />
          <Slider label="Odak (dar)" unit="mm" value={c.focal_max_mm} min={c.focal_min_mm} max={Math.max(400, c.focal_max_mm)} step={0.5} onChange={(f) => update({ focal_max_mm: f })} />
          <Slider label="Direk" unit="m" value={c.pole_height_m} min={1} max={30} step={0.5} onChange={(x) => update({ pole_height_m: x })} />
          <Slider label="Eğim (aşağı)" unit="°" value={c.tilt_deg} min={0} max={60} step={0.5} onChange={(x) => update({ tilt_deg: x })} />
          <Slider label="Hedef yüksekliği" unit="m" value={c.target_height_m} min={0.5} max={2.5} step={0.1} onChange={(x) => update({ target_height_m: x })} />
          <Slider label="IR menzili" unit="m" value={c.ir_range_m} min={0} max={500} step={5} onChange={(x) => update({ ir_range_m: x })} />
          <Slider label="En düşük ışık" unit="lux" value={c.min_lux} min={0} max={2} step={0.01} onChange={(x) => update({ min_lux: x })} />
        </div>
      </Panel>

      <div className="flex min-w-[420px] flex-[3_1_480px] flex-col gap-3">
        <Panel title="Kuş bakışı" right={
          <div className="flex gap-1">
            {(["min", "max"] as const).map((m) => (
              <button key={m} type="button" onClick={() => setMode(m)}
                className={`h-7 rounded-full px-3 text-[11px] font-semibold ${mode === m ? "bg-secondary text-on-secondary" : "bg-bg-5 text-text-2"}`}>
                {m === "min" ? `Geniş ${fmt(c.focal_min_mm)} mm` : `Dar ${fmt(c.focal_max_mm)} mm`}
              </button>
            ))}
          </div>
        }>
          {res.value ? <PlanView r={res.value.plan} requirement={{ distance_m: dist, pass: res.value.verdict.pass }} />
            : <div className="flex h-[300px] items-center justify-center text-text-3">{res.error ?? "Hesaplanıyor…"}</div>}
        </Panel>
        <Panel title="Gereksinim">
          <div className="grid grid-cols-2 gap-3">
            <Select label="Görev (EN 62676-4)" value={TASKS[task][0]} options={TASKS.map((t) => t[0])} onChange={(v) => setTask(TASKS.findIndex((t) => t[0] === v))} />
            <Select label="Hava" value={weather} options={weathers.length ? weathers.map((w) => w.label) : ["Berrak hava"]} onChange={setWeather} />
          </div>
          <div className="mt-3"><Slider label="Hedef mesafesi" unit="m" value={dist} min={1} max={Math.max(200, Math.ceil((v?.dori_reach_m.detect ?? 0) * 1.2))} step={0.5} onChange={setDist} /></div>
        </Panel>
      </div>

      <div className="flex w-[320px] shrink-0 flex-col gap-3">
        {v && (
          <>
            <section className={`rounded-md p-3 ${v.pass ? "bg-ok-bg" : "bg-danger-bg"}`} aria-live="polite">
              <div className={`flex items-center gap-2 text-[16px] font-bold ${v.pass ? "text-primary" : "text-danger"}`}>
                {v.pass ? <CheckCircle2 size={20} /> : <XCircle size={20} />}{v.pass ? "UYGUN" : "UYGUN DEĞİL"}
              </div>
              <div className="mt-1 text-[12px] text-text-2">{TASKS[task][0].split(" (")[0]} · {fmt(dist)} m · gereken {fmt(v.required_ppm, v.required_ppm % 1 ? 1 : 0)} px/m</div>
              {!v.pass && <ul className="mt-2 list-disc pl-5 text-[12px] text-danger">{v.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
            </section>
            <div className="grid grid-cols-2 gap-1.5">
              <Tile label="Elde edilen" value={fmt(v.achieved_ppm, 0)} unit="px/m" tone={v.achieved_ppm >= v.required_ppm ? "text-dori-identify" : "text-danger"} />
              <Tile label="Görev menzili" value={fmt(v.reach_m)} unit="m" />
              <Tile label="Yatay FOV" value={fmt(v.hfov_deg)} unit="°" tone="text-secondary" />
              <Tile label="Dikey FOV" value={fmt(v.vfov_deg)} unit="°" />
              <Tile label="Kör nokta" value={v.dead_zone_m < 0.05 ? "yok" : fmt(v.dead_zone_m)} unit={v.dead_zone_m < 0.05 ? "" : "m"} tone={v.dead_zone_m > 4 ? "text-warn" : "text-text"} />
              <Tile label="Geometrik sınır" value={v.max_geom_dist_m == null ? "açık" : fmt(v.max_geom_dist_m)} unit={v.max_geom_dist_m == null ? "" : "m"} />
            </div>
            <Panel title="DORI menzilleri (yer)">
              {([["identify", "Teşhis ≥250"], ["recognize", "Tanıma ≥125"], ["observe", "Gözlem ≥62,5"], ["detect", "Algılama ≥25"]] as const).map(([k, l]) => (
                <div key={k} className="flex justify-between font-mono text-[12px]"><span className="text-text-3">{l}</span><span>{fmt(v.dori_reach_m[k])} m</span></div>
              ))}
              {v.atmospheric_limit_m != null && <div className="mt-2 font-mono text-[11px] text-warn">▲ atmosferik menzil {fmt(v.atmospheric_limit_m, 0)} m ({weather})</div>}
            </Panel>
          </>
        )}
        <Panel title="Ölçülen kalite (MTF)">
          <Slider label="Etkin piksel oranı k" unit="" value={c.effective_px_ratio} min={0.05} max={1} step={0.01} onChange={(k) => update({ effective_px_ratio: k })} />
          <div className="mt-1 font-mono text-[11px] text-text-2">
            etiket {fmt(mp, 1)} MP → etkin {fmt(mp * c.effective_px_ratio ** 2, 1)} MP · {v ? `${fmt(v.res_width_px, 0)} / ${fmt(v.nominal_res_width_px, 0)} px` : ""}
          </div>
          <Button className="mt-2 w-full" disabled={!isTauri()} onClick={measure}><ScanLine size={14} /> Eğik kenar → k ölç</Button>
          {edge && <div className="mt-1 text-[11px] text-text-2">● Ölçüldü: k = {fmt(edge.k, 2)} ({edge.image_size[0]}×{edge.image_size[1]} px)</div>}
          {edgeErr && <div className="mt-1 text-[11px] text-danger">■ {edgeErr}</div>}
          <p className="mt-2 text-[11px] text-text-3">◆ FOV optiktir, değişmez; k yalnız px/m ve menzili ölçekler.</p>
        </Panel>
      </div>
    </div>
  );
}

function Select({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (v: string) => void }) {
  return (
    <label className="block"><span className="label">{label}</span>
      <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" value={value} onChange={(e) => onChange(e.target.value)}>
        {!options.includes(value) && <option value={value}>{value}</option>}
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );
}
