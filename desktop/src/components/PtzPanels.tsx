import { useState } from "react";
import { Play, RotateCw } from "lucide-react";
import type { CameraConfig, ModelSummary } from "../core/api";
import type { CueResult, LegendRow, MixedResult, SweepResult, TerrainApi } from "../core/terrainApi";
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

/** Single-camera tab, PTZ model: sweep (360° / sector) or alarm -> identify time. */
export function PtzPanel({ T, job, terrainId, useObstacles, camera, mount, weather, onLayer }: {
  T: TerrainApi;
  job: Job;
  terrainId: string;
  useObstacles: boolean;
  camera: CameraConfig;
  mount: { x: number; y: number; mast: number; pan: number; tilt: number; range: number; focal?: number };
  weather: string;
  onLayer: (l: Layer | null) => void;
}) {
  const [mode, setMode] = useState<"rotation" | "sector" | "cue">("rotation");
  const [speed, setSpeed] = useState(20);
  const [span, setSpan] = useState(120);
  const [dwell, setDwell] = useState(2);
  const [target, setTarget] = useState(250);
  const [sweep, setSweep] = useState<SweepResult | null>(null);
  const [cue, setCue] = useState<CueResult | null>(null);
  const base = { terrain_id: terrainId, use_obstacles: useObstacles, camera, x_m: mount.x, y_m: mount.y, mast_m: mount.mast, range_m: mount.range, weather, max_px: 1200 };

  const run = () => {
    if (mode === "cue") {
      void job.run("Tespit → teşhis süresi", (o) => T.ptzCue({ ...base, home_pan_deg: mount.pan, home_tilt_deg: mount.tilt, target_ppm: target }, o))
        .then((r) => { if (!r) return; setCue(r); setSweep(null); onLayer({ overlay: r.overlay.data, legend: r.legend, title: `Tepki süresi · ${fmt(r.target_ppm, 0)} px/m` }); });
    } else {
      void job.run(mode === "rotation" ? "360° tarama" : "Sektör devriyesi", (o) => T.ptzSweep({
        ...base, mode, speed_deg_s: speed, tilt_deg: mount.tilt, focal_mm: mount.focal,
        start_pan_deg: (mount.pan - span / 2 + 360) % 360, end_pan_deg: (mount.pan + span / 2) % 360, end_dwell_s: dwell,
      }, o)).then((r) => { if (!r) return; setSweep(r); setCue(null); onLayer({ overlay: r.overlay.data, legend: r.legend, title: "Revizit — en uzun izlenmeyen süre" }); });
    }
  };

  return (
    <Panel title="PTZ" icon={<RotateCw size={16} className="text-ptz" />}>
      <div className="grid grid-cols-3 gap-1">
        {([["rotation", "360° dönüş"], ["sector", "Sektör"], ["cue", "Tespit→Teşhis"]] as const).map(([id, l]) => (
          <button key={id} type="button" onClick={() => setMode(id)}
            className={`h-8 rounded text-[11px] font-semibold ${mode === id ? "bg-bg-6 text-ptz" : "bg-bg-5 text-text-2"}`}>{l}</button>
        ))}
      </div>
      <div className="mt-3 flex flex-col gap-3">
        {mode !== "cue" && <Slider label="Dönüş hızı" unit="°/sn" value={speed} min={2} max={180} step={1} onChange={setSpeed} />}
        {mode === "sector" && <Slider label="Sektör genişliği (yön merkezli)" unit="°" value={span} min={10} max={350} step={5} onChange={setSpan} />}
        {mode === "sector" && <Slider label="Uçta bekleme" unit="sn" value={dwell} min={0} max={30} step={0.5} onChange={setDwell} />}
        {mode === "cue" && <Slider label="Hedef (teşhis 250)" unit="px/m" value={target} min={25} max={500} step={5} onChange={setTarget} />}
      </div>
      <p className="mt-2 text-[11px] text-text-3">◆ Eğim ve odak yukarıdaki kaydırıcılardan; {mode === "cue" ? "ev konumu = yön/eğim." : "bakışı 0,5 sn'den kısa hücreler kapsama sayılmaz."}</p>
      <Button className="mt-2 w-full" disabled={job.running} onClick={run}><Play size={14} /> Hesapla</Button>
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
            <Tile label="Medyan" value={sec(cue.mean_response_s)} />
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
export function MixedPanel({ T, job, terrainId, useObstacles, models, value, onChange, hasPlanWithPtz, onLayer, ptzModelFor }: {
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
}) {
  const [res, setRes] = useState<MixedResult | null>(null);
  const [layer, setLayer] = useState<"fused" | "cue">("fused");
  const ptzModels = models.filter((m) => m.is_ptz);
  const run = (l: "fused" | "cue") => {
    setLayer(l);
    void job.run("Karma kapsama (sabit + PTZ)", (o) => T.mixed({ terrain_id: terrainId, use_obstacles: useObstacles, speed_deg_s: 20, layer: l, max_px: 1200 }, o))
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
          </div>
          <div className="mt-2 grid grid-cols-3 gap-1">
            {[360, 180, 90].map((s) => (
              <button key={s} type="button" onClick={() => onChange({ ...value, span: s })}
                className={`h-7 rounded text-[11px] font-semibold ${value.span === s ? "bg-bg-6 text-ptz" : "bg-bg-5 text-text-2"}`}>{s}°</button>
            ))}
          </div>
          <p className="mt-2 text-[11px] text-text-3">◆ "Direkleri Diz" PTZ'leri plana ekler; sonra karma kapsamayı hesaplayın.</p>
          <div className="mt-2 flex gap-1.5">
            <Button className="flex-1" disabled={job.running || !hasPlanWithPtz} onClick={() => run("fused")}>Sabit + PTZ</Button>
            <Button className="flex-1" disabled={job.running || !hasPlanWithPtz} onClick={() => run("cue")}>Tespit→Teşhis</Button>
          </div>
        </>
      )}
      {res && (
        <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono text-[11px]">
          <span className="text-text-3">Sürekli algılama</span><span className="text-right">{ha(res.continuous_detect_m2)}</span>
          <span className="text-text-3">Aralıklı (yalnız PTZ)</span><span className="text-right text-warn">{ha(res.intermittent_detect_m2)}</span>
          <span className="text-text-3">En kötü aralık</span><span className="text-right">{sec(res.worst_intermittent_revisit_s)}</span>
          <span className="text-text-3">Teşhis edilebilir</span><span className="text-right">%{fmt(res.identifiable_pct, 1)}</span>
          <span className="text-text-3">Tespit→teşhis medyan</span><span className="text-right">{sec(res.median_d2i_s)}</span>
          <span className="text-text-3">%90 / en kötü</span><span className="text-right">{sec(res.p90_d2i_s)} / {sec(res.worst_d2i_s)}</span>
          {res.ptz_detect_floor_ppm < 25 && <span className="col-span-2 mt-1 text-text-3">◆ Termal PTZ: algılama tabanı Johnson {fmt(res.ptz_detect_floor_ppm, 1)} px/m, 25 px/m değil.</span>}
          <span className="col-span-2 mt-1 text-text-3">katman: {layer === "fused" ? "sabit + PTZ" : "tepki süresi"}</span>
        </div>
      )}
    </Panel>
  );
}
