import { useEffect, useRef, useState } from "react";
import { Play, Plus, RotateCw, X } from "lucide-react";
import type { CameraConfig, ModelSummary } from "../core/api";
import type { CueResult, LegendRow, MixedResult, PatrolResult, PlanResult, SweepResult, TerrainApi, TourResult } from "../core/terrainApi";
import type { useJob } from "../core/useJob";
import { Button, Panel, Slider, Tile, fmt } from "./ui";

export interface Layer {
  overlay: string;
  legend: LegendRow[];
  title: string;
}

type Job = ReturnType<typeof useJob>;
const ha = (m2: number) => `${fmt(m2 / 1e4, 2)} ha`;
const sec = (s: number | null) => (s == null ? "∞" : `${fmt(s, 1)} sn`);
// cue targets on the camera's own scale; the first row sends no px/m (the core's default)
const CUE_TARGETS: [number | null, string][] = [[null, "Teşhis · 250 px/m"], [125, "Tanıma · 125 px/m"], [62.5, "Gözlem · 62,5 px/m"], [25, "Algılama · 25 px/m"]];
const CUE_TARGETS_THERMAL: [number | null, string][] = [[null, "Johnson teşhis · 13,33 px/m"], [6.67, "Johnson tanıma · 6,67 px/m"], [1.3, "Johnson algılama · 1,3 px/m"]];
export interface Patrol { speed: number; dwell: number }

/** Single-camera tab, PTZ model: sweep (360° / sector) or alarm -> identify time. */
export function PtzPanel({ T, job, terrainId, useObstacles, camera, thermal, mount, weather, onLayer, onPlay }: {
  T: TerrainApi;
  job: Job;
  terrainId: string;
  useObstacles: boolean;
  camera: CameraConfig;
  thermal: boolean;
  mount: { x: number; y: number; mast: number; pan: number; tilt: number; range: number; focal?: number; lens?: "min" | "max" };
  weather: string;
  onLayer: (l: Layer | null) => void;
  /** live sweep of this camera with the panel's settings */
  onPlay?: (args: Record<string, unknown>) => void;
}) {
  const [mode, setMode] = useState<"rotation" | "sector" | "cue" | "tour">("rotation");
  const [presets, setPresets] = useState<{ name: string; pan_deg: number; tilt_deg: number; focal_mm?: number; dwell_s: number }[]>([]);
  const [tour, setTour] = useState<TourResult | null>(null);
  const [speed, setSpeed] = useState(20);
  const [span, setSpan] = useState(120);
  const [dwell, setDwell] = useState(2);
  // null = the camera's own identification level (EN 250 px/m, Johnson 13,33 for a thermal): the core decides
  const [target, setTarget] = useState<number | null>(null);
  useEffect(() => { setTarget(null); setCue(null); }, [thermal]);
  // the zoom the map shows: an applied optimum, else the lens end the single view uses
  const focal = mount.focal ?? (mount.lens === "min" ? camera.focal_min_mm : camera.focal_max_mm);
  const [accel, setAccel] = useState(0);        // deg/s^2, 0 = instant top speed
  const [sweep, setSweep] = useState<SweepResult | null>(null);
  const [cue, setCue] = useState<CueResult | null>(null);
  const base = { terrain_id: terrainId, use_obstacles: useObstacles, camera, x_m: mount.x, y_m: mount.y, mast_m: mount.mast, range_m: mount.range, weather, max_px: 1200, slew_accel_deg_s2: accel };

  const run = () => {
    if (mode === "tour") {
      void job.run("Preset turu", (o) => T.ptzTour({ ...base, presets }, o))
        .then((r) => { if (!r) return; setTour(r); setSweep(null); setCue(null); onLayer({ overlay: r.overlay.data, legend: r.legend, title: `Revizit — ${r.preset_labels.length} preset turu` }); });
      return;
    }
    if (mode === "cue") {
      void job.run("Tespit → teşhis süresi", (o) => T.ptzCue({ ...base, home_pan_deg: mount.pan, home_tilt_deg: mount.tilt, ...(target != null ? { target_ppm: target } : {}) }, o))
        .then((r) => { if (!r) return; setCue(r); setSweep(null); onLayer({ overlay: r.overlay.data, legend: r.legend, title: `Tepki süresi · ${fmt(r.target_ppm, r.target_ppm < 20 ? 2 : 0)} px/m` }); });
    } else {
      void job.run(mode === "rotation" ? "360° tarama" : "Sektör devriyesi", (o) => T.ptzSweep({
        ...base, mode, speed_deg_s: speed, tilt_deg: mount.tilt, focal_mm: focal,
        start_pan_deg: (mount.pan - span / 2 + 360) % 360, end_pan_deg: (mount.pan + span / 2) % 360, end_dwell_s: dwell,
      }, o)).then((r) => { if (!r) return; setSweep(r); setCue(null); onLayer({ overlay: r.overlay.data, legend: r.legend, title: "Revizit — en uzun izlenmeyen süre" }); });
    }
  };

  return (
    <Panel title="PTZ" icon={<RotateCw size={16} className="text-ptz" />}>
      <div className="grid grid-cols-2 gap-1">
        {([["rotation", "360° dönüş"], ["sector", "Sektör"], ["tour", "Preset turu"], ["cue", "Tespit→Teşhis"]] as const).map(([id, l]) => (
          <button key={id} type="button" onClick={() => setMode(id)}
            className={`h-8 rounded text-[11px] font-semibold ${mode === id ? "bg-bg-6 text-ptz" : "bg-bg-5 text-text-2"}`}>{l}</button>
        ))}
      </div>
      <div className="mt-3 flex flex-col gap-3">
        {mode !== "cue" && <Slider label="Dönüş hızı" unit="°/sn" value={speed} min={2} max={180} step={1} onChange={setSpeed} />}
        {mode === "sector" && <Slider label="Sektör genişliği (yön merkezli)" unit="°" value={span} min={10} max={350} step={5} onChange={setSpan} />}
        {mode === "sector" && <Slider label="Uçta bekleme" unit="sn" value={dwell} min={0} max={30} step={0.5} onChange={setDwell} />}
        {mode === "cue" && (
          <label><span className="label">Hedef seviye</span>
            <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" aria-label="Tepki hedef seviyesi"
              value={target ?? ""} onChange={(e) => setTarget(e.target.value ? Number(e.target.value) : null)}>
              {(thermal ? CUE_TARGETS_THERMAL : CUE_TARGETS).map(([v, l]) => <option key={l} value={v ?? ""}>{l}</option>)}
            </select></label>
        )}
        {(mode === "cue" || mode === "tour") && <Slider label="Pan/tilt ivmesi (0 = anlık)" unit="°/s²" value={accel} min={0} max={600} step={10} onChange={setAccel} />}
        {mode === "tour" && (
          <div>
            <Button className="w-full" onClick={() => setPresets((ps) => [...ps, { name: `P${ps.length + 1}`, pan_deg: mount.pan, tilt_deg: mount.tilt, focal_mm: mount.focal, dwell_s: 5 }])}>
              <Plus size={14} /> Bu bakışı preset ekle (yön {fmt(mount.pan, 0)}°, eğim {fmt(mount.tilt)}°)
            </Button>
            <ul className="mt-2 flex flex-col gap-1 font-mono text-[11px]">
              {presets.map((p, i) => (
                <li key={i} className="flex items-center gap-1.5 rounded bg-bg-1 px-2 py-1">
                  <span className="w-7 text-text-2">{p.name}</span>
                  <span className="flex-1">{fmt(p.pan_deg, 0)}° · {fmt(p.tilt_deg)}° · {p.focal_mm ? `${fmt(p.focal_mm)} mm` : "dar uç"}</span>
                  <input type="number" min={0.5} step={0.5} value={p.dwell_s} aria-label={`${p.name} bekleme (sn)`}
                    onChange={(e) => { const v = Number(e.target.value); if (v > 0) setPresets((ps) => ps.map((q, j) => (j === i ? { ...q, dwell_s: v } : q))); }}
                    className="h-6 w-14 rounded border border-line bg-bg-0 px-1 text-right" />
                  <span className="text-text-3">sn</span>
                  <button type="button" aria-label={`${p.name} sil`} onClick={() => setPresets((ps) => ps.filter((_, j) => j !== i))} className="text-text-3 hover:text-danger"><X size={13} /></button>
                </li>
              ))}
            </ul>
            {presets.length < 2 && <p className="mt-1 text-[11px] text-text-3">◆ Yön/eğim/odak kaydırıcılarıyla bakışı ayarlayıp en az iki preset ekleyin.</p>}
          </div>
        )}
      </div>
      {mode !== "tour" && <p className="mt-2 text-[11px] text-text-3">◆ Eğim ve odak yukarıdaki kaydırıcılardan; {mode === "cue" ? "ev konumu = yön/eğim." : "bakışı 0,5 sn'den kısa hücreler kapsama sayılmaz."}</p>}
      <Button className="mt-2 w-full" disabled={job.running || (mode === "tour" && presets.length < 2)} onClick={run}><Play size={14} /> {mode === "tour" ? "Turu Çöz" : "Hesapla"}</Button>
      {(mode === "rotation" || mode === "sector") && onPlay && (
        <Button className="mt-1.5 w-full" disabled={job.running} onClick={() => onPlay({
          source: "single", camera, x_m: mount.x, y_m: mount.y, mast_m: mount.mast, range_m: mount.range, weather, mode,
          speed_deg_s: speed, tilt_deg: mount.tilt, end_dwell_s: dwell, focal_mm: focal,
          start_pan_deg: mode === "rotation" ? mount.pan : (mount.pan - span / 2 + 360) % 360, end_pan_deg: (mount.pan + span / 2) % 360,
        })}><RotateCw size={14} /> Canlı taramayı başlat</Button>
      )}
      {tour && mode === "tour" && (
        <div className="mt-2 grid grid-cols-2 gap-1.5">
          <Tile label="Tur süresi" value={fmt(tour.tour_period_s, 1)} unit="sn" />
          {/* nothing covered: there is no revisit to speak of (it used to read "0,0 sn") */}
          <Tile label="En kötü revizit" value={tour.covered_area_m2 > 0 ? sec(tour.worst_revisit_s) : "—"} tone="text-warn" />
          <Tile label="Kapsanan alan" value={ha(tour.covered_area_m2)} tone="text-secondary" />
          <Tile label="Geçiş (hareket+zoom+oturma)" value={fmt(tour.slew_total_s + tour.zoom_total_s + tour.settle_total_s, 1)} unit="sn" />
        </div>
      )}
      {sweep && (
        <div className="mt-2 grid grid-cols-2 gap-1.5">
          <Tile label="Tur süresi" value={fmt(sweep.period_s, 1)} unit="sn" />
          <Tile label="En kötü revizit" value={sec(sweep.worst_revisit_s)} tone="text-warn" />
          <Tile label="İzlenen alan" value={ha(sweep.seen_area_m2)} tone="text-secondary" />
          <Tile label="Yalnız bakış" value={ha(sweep.glimpse_area_m2)} />
          {sweep.truncated && <div className="col-span-2 text-[11px] text-warn">▲ Kameranın görüşü analiz menzilini aşıyor; menzili büyütün.</div>}
        </div>
      )}
      {cue && (
        <div className="mt-2">
          <div className="grid grid-cols-2 gap-1.5">
            <Tile label="Ulaşılabilir" value={ha(cue.reachable_area_m2)} tone="text-secondary" />
            <Tile label="Medyan" value={sec(cue.median_response_s)} />
            <Tile label="%90" value={sec(cue.p90_response_s)} />
            <Tile label="En kötü" value={sec(cue.worst_response_s)} tone="text-warn" />
          </div>
          {cue.unreachable.slice(0, 3).map((u) => <div key={u.reason} className="mt-1 text-[11px] text-text-3">◆ {u.reason}: {ha(u.area_m2)}</div>)}
        </div>
      )}
    </Panel>
  );
}

/** Perimeter tab: co-mount a PTZ on every Nth pole and evaluate fixed + PTZ together. */
export function MixedPanel({ T, job, terrainId, useObstacles, models, value, onChange, hasPlanWithPtz, onLayer, ptzModelFor, onPlay, resetKey, patrol, onPatrol, levels, band, onPlan }: {
  T: TerrainApi;
  job: Job;
  terrainId: string;
  useObstacles: boolean;
  models: ModelSummary[];
  value: { camera: CameraConfig | null; everyN: number; span: number };
  onChange: (v: { camera: CameraConfig | null; everyN: number; span: number }) => void;
  hasPlanWithPtz: boolean;
  onLayer: (l: Layer | null) => void;
  ptzModelFor: (name: string) => Promise<CameraConfig>;
  /** live sweep of every PTZ in the plan */
  onPlay?: (args: Record<string, unknown>) => void;
  /** changes with every new plan: results of the old one are dropped */
  resetKey?: number;
  /** one patrol speed / end dwell for coverage, the mixed evaluation and the live sweep */
  patrol: Patrol;
  onPatrol: (p: Patrol) => void;
  /** patrol search: the PTZ's own level scale, the band the plan protects, and where the new plan goes */
  levels: readonly (readonly [string, string])[];
  band: Record<string, unknown>;
  onPlan: (p: PlanResult) => void;
}) {
  const [res, setRes] = useState<MixedResult | null>(null);
  const [search, setSearch] = useState<PatrolResult | null>(null);
  const [searchLevel, setSearchLevel] = useState("detect");
  const ownPlan = useRef(false);          // the next plan change is this panel's own search result
  useEffect(() => { setRes(null); if (ownPlan.current) ownPlan.current = false; else setSearch(null); }, [resetKey, terrainId]);
  const level = levels.some(([k]) => k === searchLevel) ? searchLevel : "detect";
  const runSearch = () => void job.run("PTZ devriye araması", (o) => T.ptzPatrol({ terrain_id: terrainId, use_obstacles: useObstacles,
    level, speed_deg_s: patrol.speed, end_dwell_s: patrol.dwell, ...band }, o))
    .then((r) => { if (!r) return; if (r.applied) { ownPlan.current = true; onPlan(r.plan); } setSearch(r); });
  const [layer, setLayer] = useState<"fused" | "cue">("fused");
  const ptzModels = models.filter((m) => m.is_ptz);
  const run = (l: "fused" | "cue") => {
    setLayer(l);
    void job.run("Karma kapsama (sabit + PTZ)", (o) => T.mixed({ terrain_id: terrainId, use_obstacles: useObstacles, speed_deg_s: patrol.speed, end_dwell_s: patrol.dwell, layer: l, max_px: 1200 }, o))
      .then((r) => { if (!r) return; setRes(r); onLayer({ overlay: r.overlay.data, legend: r.legend, title: l === "fused" ? "Algılama: sabit + PTZ" : "Tespit → teşhis süresi" }); });
  };
  return (
    <Panel title="Karma dizilim: sabit + PTZ" icon={<RotateCw size={16} className="text-ptz" />}>
      <select className="h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" aria-label="PTZ modeli"
        value={value.camera?.model_name ?? ""} onChange={async (e) => onChange({ ...value, camera: e.target.value ? await ptzModelFor(e.target.value) : null })}>
        <option value="">— PTZ yok —</option>
        {ptzModels.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
      </select>
      {value.camera && (
        <>
          <div className="mt-3 flex flex-col gap-3">
            <Slider label="Her N direkte bir PTZ" unit="" value={value.everyN} min={1} max={20} step={1} onChange={(n) => onChange({ ...value, everyN: n })} />
            <Slider label="Devriye hızı" unit="°/sn" value={patrol.speed} min={2} max={120} step={1} onChange={(v) => onPatrol({ ...patrol, speed: v })} />
            {value.span < 360 && <Slider label="Uçta bekleme" unit="sn" value={patrol.dwell} min={0} max={30} step={0.5} onChange={(v) => onPatrol({ ...patrol, dwell: v })} />}
          </div>
          <div className="mt-2 grid grid-cols-3 gap-1">
            {[360, 180, 90].map((s) => (
              <button key={s} type="button" onClick={() => onChange({ ...value, span: s })}
                className={`h-7 rounded text-[11px] font-semibold ${value.span === s ? "bg-bg-6 text-ptz" : "bg-bg-5 text-text-2"}`}>{s}°</button>
            ))}
          </div>
          <p className="mt-2 text-[11px] text-text-3">◆ Planı yeniden kurun ("Direkleri Diz" / "Alanı Kapla"): PTZ'ler direklere eklenir ve Birleşik Kapsama onları aralıklı kapsama olarak sayar.</p>
          <div className="mt-2 flex gap-1.5">
            <Button className="flex-1" disabled={job.running || !hasPlanWithPtz} onClick={() => run("fused")}>Sabit + PTZ</Button>
            <Button className="flex-1" disabled={job.running || !hasPlanWithPtz} onClick={() => run("cue")}>Tespit→Teşhis</Button>
          </div>
          <div className="mt-2 flex gap-1.5">
            <select className="h-8 min-w-0 flex-1 rounded border border-line bg-bg-0 px-1 text-[12px]" aria-label="Devriye arama seviyesi"
              value={level} onChange={(e) => setSearchLevel(e.target.value)}>
              {levels.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
            <Button disabled={job.running || !hasPlanWithPtz} onClick={runSearch}>Devriye odak / eğim ara</Button>
          </div>
          {search && (
            <div className="mt-1.5 text-[11px]">
              <div className={search.applied ? "text-primary" : "text-text-2"}>
                {search.applied ? "●" : "◆"} Yalnız PTZ'nin eklediği alan: {ha(search.before_m2)} → <b>{ha(search.after_m2)}</b> (tarama motoru)
              </div>
              {search.applied && (
                <div className="mt-0.5 text-text-3">{search.changed}/{search.ptz_count} PTZ · odak {fmt(Math.min(...search.choices.map((c) => c.focal_mm)), 1)}–{fmt(Math.max(...search.choices.map((c) => c.focal_mm)), 1)} mm · en uzun izlenmeyen {fmt(search.worst_revisit_after_s, 0)} sn</div>
              )}
              {search.notes.map((n) => <div key={n} className="mt-0.5 text-text-3">◆ {n}</div>)}
            </div>
          )}
          {onPlay && (
            <Button primary className="mt-1.5 w-full" disabled={job.running || !hasPlanWithPtz} onClick={() => onPlay({ source: "plan", speed_deg_s: patrol.speed, end_dwell_s: patrol.dwell })}>
              <RotateCw size={14} /> Taramayı Başlat (tüm PTZ'ler)
            </Button>
          )}
        </>
      )}
      {res && (
        <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono text-[11px]">
          <span className="text-text-3">Sürekli algılama</span><span className="text-right">{ha(res.continuous_detect_m2)}</span>
          <span className="text-text-3">Aralıklı (yalnız PTZ)</span><span className="text-right text-warn">{ha(res.intermittent_detect_m2)}</span>
          <span className="text-text-3">En kötü aralık</span><span className="text-right">{sec(res.worst_intermittent_revisit_s)}</span>
          <span className="text-text-3">{res.ptz_thermal ? "Johnson teşhis edilebilir" : "Teşhis edilebilir"}</span><span className="text-right">%{fmt(res.identifiable_pct, 1)}</span>
          <span className="text-text-3">Tespit→teşhis medyan</span><span className="text-right">{sec(res.median_d2i_s)}</span>
          <span className="text-text-3">%90 / en kötü</span><span className="text-right">{sec(res.p90_d2i_s)} / {sec(res.worst_d2i_s)}</span>
          {res.ptz_detect_floor_ppm < 25 && <span className="col-span-2 mt-1 text-text-3">◆ Termal PTZ: algılama tabanı Johnson {fmt(res.ptz_detect_floor_ppm, 1)} px/m, 25 px/m değil.</span>}
          <span className="col-span-2 mt-1 text-text-3">katman: {layer === "fused" ? "sabit + PTZ" : "tepki süresi"}</span>
        </div>
      )}
    </Panel>
  );
}
