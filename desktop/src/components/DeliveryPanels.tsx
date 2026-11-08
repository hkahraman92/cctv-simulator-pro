import { useEffect, useState } from "react";
import { Bookmark, Moon, Play, Trash2 } from "lucide-react";
import type { CameraConfig } from "../core/api";
import type { NightCoverage, NightTimeline, Scenario, TerrainApi } from "../core/terrainApi";
import type { useJob } from "../core/useJob";
import type { Layer } from "./PtzPanels";
import { Button, Panel, fmt } from "./ui";

type Job = ReturnType<typeof useJob>;
const pct = (f: number | null | undefined) => (f == null ? "—" : `%${fmt(f * 100, 1)}`);
const LEVELS = [["detect", "Algılama"], ["observe", "Gözlem"], ["recognize", "Tanıma"], ["identify", "Teşhis"]] as const;

const GLOW: [string, number][] = [["Kırsal (karanlık)", 0], ["Banliyö", 0.01], ["Şehir", 0.05]];
const MODE_TEXT: Record<string, string> = { termal: "Termal", pasif: "Ortam ışığı", ir: "IR", beyaz: "Beyaz ışık", yok: "Görüntü yok" };
const MODE_TONE: Record<string, string> = { termal: "bg-secondary", pasif: "bg-primary", ir: "bg-info", beyaz: "bg-warn", yok: "bg-danger" };

const localNow = (h = 23) => {
  const d = new Date();
  d.setHours(h, 0, 0, 0);
  const z = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}T${z(h)}:00`;
};

/** Night: what lights the scene at a date / time, and the plan's fence band by night vs by day. */
export function NightPanel({ T, job, terrainId, useObstacles, camera, hasLatLon, onLayer, onResult, resetKey }: {
  T: TerrainApi; job: Job; terrainId: string; useObstacles: boolean; camera: CameraConfig; hasLatLon: boolean; resetKey: number;
  onLayer: (l: Layer | null) => void; onResult: (r: NightCoverage | null) => void;
}) {
  const [when, setWhen] = useState(localNow());
  const [pos, setPos] = useState({ lat: 39.92, lon: 32.85 });
  const [cfg, setCfg] = useState({ cloud: 0, glow: 0, ir_on: true, white: 0, ir: camera.ir_range_m, min_lux: camera.min_lux });
  const [res, setRes] = useState<NightCoverage | null>(null);
  const [tl, setTl] = useState<NightTimeline | null>(null);
  useEffect(() => { setCfg((c) => ({ ...c, ir: camera.ir_range_m, min_lux: camera.min_lux })); }, [camera.ir_range_m, camera.min_lux]);
  useEffect(() => { setRes(null); setTl(null); onResult(null); }, [terrainId, resetKey, onResult]);

  const cam = { ...camera, ir_range_m: cfg.ir, min_lux: cfg.min_lux };
  const common = {
    cloud_cover: cfg.cloud / 100, sky_glow_lux: cfg.glow, ir_on: cfg.ir_on, white_light_range_m: cfg.white, camera: cam,
    ...(hasLatLon ? {} : pos),
  };
  const run = () => void job.run("Gece kapsaması", (o) => T.nightCoverage({ terrain_id: terrainId, use_obstacles: useObstacles, datetime: when, max_px: 1200, ...common }, o))
    .then((r) => {
      if (!r) return;
      setRes(r); onResult(r);
      onLayer(r.overlay ? { overlay: r.overlay.data, legend: r.legend, title: `Gece · ${r.mode.text}` } : null);
      void T.nightTimeline({ terrain_id: terrainId, date: when.slice(0, 10), step_min: 60, ...common }).then(setTl).catch(() => undefined);
    });
  const c = res?.condition;
  return (
    <Panel title="Gece senaryosu" icon={<Moon size={16} className="text-secondary" />}>
      <label className="block text-[12px]"><span className="label">Yerel tarih / saat</span>
        <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)}
          className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 font-mono text-[12px]" /></label>
      {!hasLatLon && (
        <div className="mt-2 grid grid-cols-2 gap-2 text-[12px]">
          {(["lat", "lon"] as const).map((k) => (
            <label key={k}><span className="label">{k === "lat" ? "Enlem" : "Boylam"}</span>
              <input type="number" value={pos[k]} step={0.01} onChange={(e) => setPos({ ...pos, [k]: Number(e.target.value) || 0 })}
                className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 font-mono" /></label>
          ))}
          <p className="col-span-2 text-[11px] text-text-3">Arazinin konumu yok: güneş / ay için konum girin.</p>
        </div>
      )}
      <div className="mt-2 grid grid-cols-2 gap-2 text-[12px]">
        <label><span className="label">Bulut (%)</span>
          <input type="number" min={0} max={100} value={cfg.cloud} onChange={(e) => setCfg({ ...cfg, cloud: Math.min(100, Math.max(0, Number(e.target.value) || 0)) })}
            className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 font-mono" /></label>
        <label><span className="label">Gökyüzü parıltısı</span>
          <select value={cfg.glow} onChange={(e) => setCfg({ ...cfg, glow: Number(e.target.value) })} className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-1">
            {GLOW.map(([l, v]) => <option key={l} value={v}>{l}</option>)}
          </select></label>
        <label><span className="label">Min. aydınlatma (lux)</span>
          <input type="number" min={0} step={0.001} value={cfg.min_lux} onChange={(e) => setCfg({ ...cfg, min_lux: Math.max(0, Number(e.target.value) || 0) })}
            className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 font-mono" /></label>
        <label><span className="label">IR menzili (m)</span>
          <input type="number" min={0} value={cfg.ir} onChange={(e) => setCfg({ ...cfg, ir: Math.max(0, Number(e.target.value) || 0) })}
            className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 font-mono" /></label>
        <label className="col-span-2 flex items-center gap-2"><input type="checkbox" checked={cfg.ir_on} onChange={(e) => setCfg({ ...cfg, ir_on: e.target.checked })} /> IR aydınlatma açık</label>
        <label className="col-span-2"><span className="label">Ek beyaz ışık menzili (m, 0 = yok)</span>
          <input type="number" min={0} value={cfg.white} onChange={(e) => setCfg({ ...cfg, white: Math.max(0, Number(e.target.value) || 0) })}
            className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 font-mono" /></label>
      </div>
      <Button className="mt-2 w-full" disabled={job.running} onClick={run}><Play size={14} /> Gece Kapsamasını Hesapla</Button>
      {res && c && (
        <div className="mt-2 text-[12px]">
          <div className={res.mode.mode === "yok" ? "text-danger" : res.mode.range_cap_m != null ? "text-warn" : "text-primary"}>● {res.mode.text}</div>
          <div className="mt-1 font-mono text-[11px] text-text-2">
            {c.period} · {fmt(c.lux, c.lux < 1 ? 4 : 0)} lux · güneş {fmt(c.sun_el_deg, 0)}° · ay {fmt(c.moon_el_deg, 0)}° (%{fmt(c.moon_fraction * 100, 0)} aydınlık)
          </div>
          <table className="mt-2 w-full font-mono text-[11px]">
            <thead><tr className="text-text-3"><th className="text-left font-normal">±{fmt(res.band_m, 0)} m bant</th><th className="text-right font-normal">Gündüz</th><th className="text-right font-normal">Gece</th></tr></thead>
            <tbody>
              {LEVELS.map(([k, l]) => (
                <tr key={k}><td className="font-sans">{l}</td><td className="text-right">{pct(res.day_band_share[k])}</td>
                  <td className={`text-right ${res.night_band_share[k] < res.day_band_share[k] - 1e-9 ? "text-warn" : ""}`}>{pct(res.night_band_share[k])}</td></tr>
              ))}
            </tbody>
          </table>
          {tl && (
            <div className="mt-2">
              <div className="label mb-1">24 saat (öğlen → öğlen)</div>
              <div className="flex h-4 overflow-hidden rounded">
                {tl.steps.slice(0, -1).map((s) => (
                  <div key={s.when} className={`flex-1 ${MODE_TONE[s.mode ?? "pasif"]}`} title={`${s.when.slice(11)} · ${MODE_TEXT[s.mode ?? "pasif"]} · ${fmt(s.lux, s.lux < 1 ? 4 : 0)} lux`} />
                ))}
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 text-[10px] text-text-3">
                {[...new Set(tl.steps.map((s) => s.mode ?? "pasif"))].map((m) => <span key={m}><i className={`mr-1 inline-block h-2 w-2 rounded-sm ${MODE_TONE[m]}`} />{MODE_TEXT[m]}</span>)}
              </div>
            </div>
          )}
          <p className="mt-2 text-[10px] text-text-3">Aydınlatma menzili motorun kamera başına menzilini sınırlar; IR huzme açısı ve hedef yansıtıcılığı modellenmez.</p>
        </div>
      )}
    </Panel>
  );
}

/** Save the current plan (+ whatever was run on it) as a scenario; compare saved ones side by side. */
export function ScenarioPanel({ T, terrainId, scenarios, onChange, canSnapshot }: {
  T: TerrainApi; terrainId: string; scenarios: Scenario[]; onChange: (s: Scenario[]) => void; canSnapshot: boolean;
}) {
  const [label, setLabel] = useState("");
  const [cmp, setCmp] = useState<{ rows: string[][]; best: Record<string, number | null> } | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (scenarios.length < 1) { setCmp(null); return; }
    let live = true;
    T.compareScenarios(scenarios).then((r) => live && setCmp(r)).catch((e) => live && setErr(String(e.message ?? e)));
    return () => { live = false; };
  }, [T, scenarios]);
  const add = () => T.snapshot({ terrain_id: terrainId, label: label.trim() || `Senaryo ${scenarios.length + 1}` })
    .then((r) => { onChange([...scenarios, r.scenario].slice(-12)); setLabel(""); setErr(""); })
    .catch((e) => setErr(String(e.message ?? e)));
  // best column per row, by the metric the core marked (first row = header)
  const bestCol: Record<string, number | null> = cmp ? {
    Direk: cmp.best.poles, "Toplam maliyet": cmp.best.cost_total, "Bant Algılama": cmp.best.band_detect, "Bant Gözlem": cmp.best.band_observe,
    "Bant Tanıma": cmp.best.band_recognize, "Bant Teşhis": cmp.best.band_identify, "Gece bant Algılama": cmp.best.night_detect, Kayıt: cmp.best.storage_tb,
  } : {};
  return (
    <Panel title="Senaryolar" icon={<Bookmark size={16} className="text-secondary" />} right={<span className="font-mono text-[10px] text-text-3">{scenarios.length}/12</span>}>
      <div className="flex gap-1.5">
        <input className="h-8 min-w-0 flex-1 rounded border border-line bg-bg-0 px-2 text-[12px]" placeholder={`Senaryo ${scenarios.length + 1}`}
          value={label} onChange={(e) => setLabel(e.target.value)} aria-label="Senaryo adı" />
        <Button disabled={!canSnapshot} onClick={() => void add()}><Bookmark size={14} /> Kaydet</Button>
      </div>
      <p className="mt-1 text-[11px] text-text-3">O anki plan ile o plana çalıştırılan kapsama, ağ/maliyet ve gece sonuçları saklanır; proje dosyasına yazılır.</p>
      {err && <div className="mt-1 text-[11px] text-danger">■ {err}</div>}
      {cmp && (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full border-collapse font-mono text-[11px]">
            <thead>
              <tr>{cmp.rows[0].map((h, i) => (
                <th key={i} className={`whitespace-nowrap px-1 pb-1 text-left font-sans font-semibold ${i ? "" : "text-text-3"}`}>
                  {h}{i > 0 && <button type="button" className="ml-1 text-text-3 hover:text-danger" aria-label={`${h} senaryosunu sil`}
                    onClick={() => onChange(scenarios.filter((_, j) => j !== i - 1))}><Trash2 size={11} className="inline" /></button>}
                </th>))}</tr>
            </thead>
            <tbody>
              {cmp.rows.slice(1).map((r) => (
                <tr key={r[0]} className="border-t border-line-soft">
                  {r.map((v, i) => <td key={i} className={`whitespace-nowrap px-1 py-0.5 ${i === 0 ? "font-sans text-text-3" : bestCol[r[0]] === i - 1 ? "text-primary" : ""}`}>{v}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-1 text-[10px] text-text-3">Yeşil: o ölçütte en iyi senaryo. "—" = o senaryoda çalıştırılmadı.</p>
        </div>
      )}
    </Panel>
  );
}
