import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { isTauri } from "@tauri-apps/api/core";
import {
  AlertTriangle, Building2, Download, FolderOpen, Layers, MapPin, Mountain, PencilLine, Play, Save, Trash2, Undo2,
} from "lucide-react";
import { DEFAULT_CAMERA, type CameraConfig, type ModelSummary } from "../core/api";
import { useCoalesced, useCore } from "../core/CoreProvider";
import { usePageActive } from "../core/pageActive";
import type {
  CoverageResult, FocalSweep, LegendRow, PlanResult, PoleSearch, RegionInfo, SingleViewshed, TerrainCatalog, TerrainInfo,
} from "../core/terrainApi";
import { useJob } from "../core/useJob";
import { useActiveTerrain } from "../core/activeTerrain";
import { useProject } from "../core/project";
import MapView from "../components/MapView";
import JobBar from "../components/JobBar";
import { FocalCurve, ProfileChart } from "../components/charts";
import { MixedPanel, PtzPanel, type Layer } from "../components/PtzPanels";
import { Button, Panel, Slider, Tile, fmt } from "../components/ui";

const ha = (m2: number) => `${fmt(m2 / 1e4, 2)} ha`;
const pct = (f: number) => `%${fmt(f * 100, 1)}`;
const LEVELS = [
  ["detect", "Algılama ≥25"],
  ["observe", "Gözlem ≥62,5"],
  ["recognize", "Tanıma ≥125"],
  ["identify", "Teşhis ≥250"],
] as const;

type Tab = "single" | "perimeter";

export default function Terrain() {
  const active = usePageActive();
  const { terrain: T, api, state } = useCore();
  const job = useJob();
  const [catalog, setCatalog] = useState<TerrainCatalog | null>(null);
  const [regions, setRegions] = useState<RegionInfo[]>([]);
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [info, setInfo] = useState<TerrainInfo | null>(null);
  const [base, setBase] = useState<string>("");
  const [mapMode, setMapMode] = useState("hybrid");
  const [useObstacles, setUseObstacles] = useState(true);
  const [tab, setTab] = useState<Tab>("single");
  const [notice, setNotice] = useState("");

  // single camera
  const [camera, setCamera] = useState<CameraConfig>({ ...DEFAULT_CAMERA, name: "KAM-01" });
  const [mount, setMount] = useState({ x: 0, y: 0, mast: 8, pan: 0, tilt: -5, range: 600, lens: "max" as "min" | "max", focal: undefined as number | undefined });
  const [weather, setWeather] = useState("Berrak hava");
  const [sweep, setSweep] = useState<FocalSweep | null>(null);
  const [isPtz, setIsPtz] = useState(false);
  const [ptzLayer, setPtzLayer] = useState<Layer | null>(null);       // single tab: PTZ result replaces the fan
  const [mixedLayer, setMixedLayer] = useState<Layer | null>(null);   // perimeter tab: fixed + PTZ layers
  const [mixedPtz, setMixedPtz] = useState<{ camera: CameraConfig | null; everyN: number; span: number }>({ camera: null, everyN: 4, span: 360 });

  // perimeter
  const [fence, setFence] = useState<[number, number][]>([]);
  const [drawing, setDrawing] = useState(false);
  const [plan, setPlan] = useState<{ closed: boolean; line_mode: "facility" | "border" | "highway"; watch_side: "left" | "right"; target_ppm: number; mast_m: number }>(
    { closed: true, line_mode: "facility", watch_side: "right", target_ppm: 40, mast_m: 6 });
  const [planRes, setPlanRes] = useState<PlanResult | null>(null);
  const [coverage, setCoverage] = useState<CoverageResult | null>(null);
  const [goal, setGoal] = useState(95);
  const [slide, setSlide] = useState(true);       // poles may move along the fence
  const [optLevel, setOptLevel] = useState("detect");
  const [optRes, setOptRes] = useState<{ before_m2: number; after_m2: number; band_area_m2: number; changed: number; moved: number; max_move_m: number; notes: string[] } | null>(null);
  const [poleRes, setPoleRes] = useState<PoleSearch | null>(null);

  useEffect(() => {
    if (state !== "ready") return;
    T.catalog().then(setCatalog).catch(() => undefined);
    T.regions().then((r) => setRegions(r.regions)).catch(() => undefined);
    api.library().then((r) => setModels(r.models)).catch(() => undefined);
  }, [T, api, state]);

  const adopt = useCallback((t: TerrainInfo) => {
    setInfo(t);
    setMount((m) => ({ ...m, x: t.width_m / 2, y: t.height_m / 2 }));
    setFence([]); setPlanRes(null); setCoverage(null); setPoleRes(null); setSweep(null);
    const notes: string[] = [];
    if (!t.is_measured) notes.push("Bu arazi ölçüm değil (prosedürel). Görüş alanı sonuçları bağlayıcı değildir.");
    if (t.save_error) notes.push(`Bölge kaydedilemedi: ${t.save_error}`);
    setNotice(notes.join(" "));
  }, []);

  // base picture follows terrain, mode and obstacle switch
  useEffect(() => {
    if (!info) return;
    let live = true;
    T.image({ terrain_id: info.terrain_id, mode: mapMode, use_obstacles: useObstacles })
      .then((p) => live && setBase(p.data)).catch((e) => live && setNotice(String(e.message ?? e)));
    return () => { live = false; };
  }, [T, info, mapMode, useObstacles]);

  // single-camera viewshed, coalesced while sliders move (Rule 2)
  const vsInput = useMemo(() => (info && tab === "single"
    ? { terrain_id: info.terrain_id, use_obstacles: useObstacles, camera, x_m: mount.x, y_m: mount.y, mast_m: mount.mast,
        pan_deg: mount.pan, tilt_deg: mount.tilt, range_m: mount.range, lens_mode: mount.lens, focal_mm: mount.focal,
        weather, max_px: 1200 }
    : null), [info, tab, useObstacles, camera, mount, weather]);
  const vs = useCoalesced(vsInput, (i) => T.viewshed(i!), !!vsInput && state === "ready" && active);
  const single: SingleViewshed | undefined = vs.value;

  const applyModel = async (name: string) => {
    const { camera: c, is_ptz } = await api.cameraFor(name, camera);
    setCamera(c);
    setIsPtz(is_ptz);
    setMount((m) => ({ ...m, focal: undefined }));
    setSweep(null);
    setPtzLayer(null);
  };

  const pick = (x: number, y: number) => {
    if (tab === "single") { setMount((m) => ({ ...m, x, y })); setPtzLayer(null); }
    else if (drawing) setFence((f) => [...f, [x, y]]);
  };

  const planParams = () => ({
    fence, ...plan, weather,
    ...(mixedPtz.camera ? { ptz_camera: mixedPtz.camera, ptz_every_n: mixedPtz.everyN, ptz_patrol_span_deg: mixedPtz.span } : {}),
  });
  const runPlan = async () => {
    if (!info || fence.length < 2) return;
    const r = await job.run("Çit planı", () => T.plan({ terrain_id: info.terrain_id, use_obstacles: useObstacles, camera, ...planParams() }));
    if (r) { setPlanRes(r); setCoverage(null); setPoleRes(null); setMixedLayer(null); }
  };
  const runCoverage = async () => {
    if (!info) return;
    const r = await job.run("Birleşik görüş alanı", (o) => T.coverage({ terrain_id: info.terrain_id, use_obstacles: useObstacles, max_px: 1200 }, o));
    if (r) setCoverage(r);
  };
  const runPoles = async () => {
    if (!info || fence.length < 2) return;
    const r = await job.run("En az direk araması", (o) => T.minimizePoles(
      { terrain_id: info.terrain_id, use_obstacles: useObstacles, camera, ...planParams(), goal_frac: goal / 100, level: "detect", position_steps: slide ? 2 : 0 }, o));
    if (r) { setPoleRes(r); if (r.plan) { setPlanRes(r.plan); setCoverage(null); } }
  };
  const runSweep = async () => {
    if (!info) return;
    const r = await job.run("Odak taraması", (o) => T.focalSweep({ terrain_id: info.terrain_id, use_obstacles: useObstacles, camera,
      x_m: mount.x, y_m: mount.y, mast_m: mount.mast, pan_deg: mount.pan, level: "recognize", weather, n_focals: 10 }, o));
    if (r) setSweep(r);
  };

  // ── project file: publish this page's state, restore it when a project opens ──
  const { setTerrainBlock, loadedTerrain } = useProject();
  const restoring = useRef(false);     // a project being reopened is not an unsaved change
  useEffect(() => {
    if (!info) return;
    const source = info.source === "region" || info.region_id ? "region" : info.source === "geotiff" ? "geotiff" : "procedural";
    setTerrainBlock({
      terrain_source: source, terrain_region_id: info.region_id, terrain_preset: info.preset || "ridge_and_valley",
      terrain_file: info.file, terrain_width_m: info.width_m, terrain_grid: info.cols, viewshed_range_m: mount.range, weather,
      placements: [{ camera: camera.name, x_m: mount.x, y_m: mount.y, mast_m: mount.mast, pan_deg: mount.pan, tilt_deg: mount.tilt,
        range_m: mount.range, lens_mode: mount.lens }],
      terrain_extra: {
        camera_name: camera.name, camera_model: camera.model_name, planner_mode: tab === "perimeter" ? "perimeter" : "single",
        fence_points: fence, fence_closed: plan.closed, line_mode: plan.line_mode, watch_side: plan.watch_side,
        target_ppm: plan.target_ppm, perimeter_mast_m: plan.mast_m,
        mixed_ptz: { camera: mixedPtz.camera?.model_name ?? "", every_n: mixedPtz.everyN, patrol_span_deg: mixedPtz.span },
      },
    }, { touch: !restoring.current });
  }, [setTerrainBlock, info, mount, weather, camera, tab, fence, plan, mixedPtz]);

  useEffect(() => {
    if (!loadedTerrain || state !== "ready") return;
    const b = loadedTerrain.block;
    const x = (b.terrain_extra ?? {}) as Record<string, unknown>;
    const src = String(b.terrain_source ?? "procedural");
    const load = src === "region" && b.terrain_region_id ? () => T.openRegion(String(b.terrain_region_id))
      : src === "geotiff" && b.terrain_file ? () => T.geotiff(String(b.terrain_file))
      : () => T.procedural(String(b.terrain_preset || "ridge_and_valley"), Number(b.terrain_width_m) || 2000, Number(b.terrain_grid) || 200);
    restoring.current = true;
    void job.run("Projenin arazisi açılıyor", load).then((t) => {
      setTimeout(() => { restoring.current = false; }, 1500);
      if (!t) { setNotice("Projedeki arazi açılamadı (kayıtlı bölge silinmiş ya da dosya taşınmış olabilir)."); return; }
      adopt(t);
      const pl = (Array.isArray(b.placements) ? b.placements[0] : null) as Record<string, number> | null;
      if (pl) setMount((m) => ({ ...m, x: pl.x_m ?? m.x, y: pl.y_m ?? m.y, mast: pl.mast_m ?? m.mast, pan: pl.pan_deg ?? m.pan,
        tilt: pl.tilt_deg ?? m.tilt, range: pl.range_m ?? m.range }));
      if (typeof b.weather === "string" && b.weather) setWeather(b.weather);
      if (Array.isArray(x.fence_points)) setFence(x.fence_points as [number, number][]);
      setPlan((p) => ({
        ...p, closed: typeof x.fence_closed === "boolean" ? x.fence_closed : p.closed,
        line_mode: (["facility", "border", "highway"].includes(String(x.line_mode)) ? x.line_mode : p.line_mode) as typeof p.line_mode,
        watch_side: (x.watch_side === "left" || x.watch_side === "right" ? x.watch_side : p.watch_side) as typeof p.watch_side,
        target_ppm: typeof x.target_ppm === "number" ? x.target_ppm : p.target_ppm,
        mast_m: typeof x.perimeter_mast_m === "number" ? x.perimeter_mast_m : p.mast_m,
      }));
      if (x.planner_mode === "perimeter") setTab("perimeter");
      if (typeof x.camera_model === "string" && x.camera_model) void applyModel(x.camera_model).catch(() => undefined);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadedTerrain?.nonce, state]);

  // publish what has been run on this terrain, for the Reports page
  const { setActive } = useActiveTerrain();
  useEffect(() => {
    setActive(info ? {
      terrain_id: info.terrain_id, label: info.label, is_measured: info.is_measured, use_obstacles: useObstacles,
      weather, camera, ran: { viewshed: !!single, plan: !!planRes, coverage: !!coverage },
    } : null);
  }, [setActive, info, useObstacles, weather, camera, single, planRes, coverage]);

  // a PTZ / mixed result, when one was run, is shown instead of the plain layer
  const extra = tab === "single" ? ptzLayer : mixedLayer;
  const overlay = extra ? extra.overlay : tab === "single" ? single?.overlay.data : coverage?.overlay.data;
  const legend: LegendRow[] | undefined = extra ? extra.legend : tab === "single" ? single?.legend : coverage?.legend;

  return (
    <div className="flex h-full min-h-0 flex-wrap gap-3 p-3 [&>*]:max-h-full">
      {/* sources */}
      <div className="flex w-[300px] shrink-0 flex-col gap-3 overflow-y-auto pr-1">
        <SourcePanel catalog={catalog} regions={regions} busy={job.running}
          onLoad={(fn, label) => job.run(label, fn).then((t) => t && adopt(t as TerrainInfo))}
          T={T} refreshRegions={() => T.regions().then((r) => setRegions(r.regions))} />
        {info && (
          <Panel title="Yüzey Modeli" icon={<Building2 size={16} className="text-secondary" />}>
            <div className="mb-2 text-[11px] text-text-2">{info.has_obstacles ? info.obstacle_note || "Bina / ağaç katmanı yüklü." : "Yüklenmedi — yalnız çıplak arazi (DEM)."}</div>
            <div className="flex flex-col gap-1.5">
              <Button disabled={job.running || info.lat_center === null}
                onClick={() => job.run("Bina & ağaç (OpenStreetMap)", (o) => T.osm(info.terrain_id, o)).then((t) => {
                  if (!t) return;
                  setInfo(t);
                  if (t.message) setNotice(t.message);
                  else if ((t.estimated_share ?? 0) > 0.5)
                    setNotice(`Özelliklerin %${fmt((t.estimated_share ?? 0) * 100, 0)}'inde OSM'de yükseklik yok; varsayılan kullanıldı. Bu engel katmanı ölçüm değildir.`);
                })}>
                <Building2 size={14} /> Bina & Ağaç İndir (OSM)
              </Button>
              <SurfaceRasterButton info={info} busy={job.running} onDone={setInfo} onError={setNotice} T={T} />
              <label className="mt-1 flex items-center gap-2 text-[12px]">
                <input type="checkbox" checked={useObstacles} onChange={(e) => setUseObstacles(e.target.checked)} disabled={!info.has_obstacles} />
                Binalar / ağaçlar görüşü engellesin
              </label>
            </div>
            {info.lat_center === null && <p className="mt-2 text-[11px] text-text-3">Yüzey modeli yalnız konumu bilinen araziye (indirilen / kayıtlı bölge) oturur.</p>}
          </Panel>
        )}
        {info && <RegionSave info={info} T={T} busy={job.running} onSaved={(rid) => { setInfo({ ...info, region_id: rid }); void T.regions().then((r) => setRegions(r.regions)); }} />}
      </div>

      {/* map */}
      <div className="flex min-w-[420px] flex-1 flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          {[["hybrid", "Hibrit"], ["satellite", "Uydu"], ["elevation", "Yükselti"]].map(([id, label]) => (
            <button key={id} type="button" onClick={() => setMapMode(id)}
              className={`h-7 rounded-full px-3 text-[11px] font-semibold ${mapMode === id ? "bg-secondary text-on-secondary" : "bg-bg-5 text-text-2"}`}>{label}</button>
          ))}
          <div className="flex-1" />
          {info && <span className="font-mono text-[10px] text-text-3">{info.label} · {fmt(info.cell_size_m)} m hücre · {info.is_measured ? "● ölçülmüş DEM" : "▲ prosedürel"}</span>}
        </div>
        {notice && (
          <div className="flex items-start gap-2 rounded bg-warn-bg px-3 py-2 text-[12px] text-warn">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" /><span className="flex-1">{notice}</span>
            <button type="button" className="text-[11px] underline" onClick={() => setNotice("")}>kapat</button>
          </div>
        )}
        <JobBar job={job} onCancel={job.cancel} onDismiss={job.clearError} />
        <div className="min-h-0 flex-1">
          {info ? (
            <MapView widthM={info.width_m} heightM={info.height_m} base={base} overlay={overlay}
              camera={tab === "single" && single ? { x: mount.x, y: mount.y, pan: mount.pan, hfov: single.hfov_deg, range: mount.range } : null}
              fence={tab === "perimeter" ? fence : []} fenceClosed={plan.closed}
              poles={tab === "perimeter" ? planRes?.poles : []} ptz={tab === "perimeter" ? planRes?.ptz : []}
              gaps={tab === "perimeter" ? planRes?.gaps : []}
              tool={tab === "single" ? "camera" : drawing ? "fence" : "none"} onPick={pick}>
              {legend && (
                <div className="rounded border border-bg-5 bg-bg-1/85 px-3 py-2 backdrop-blur">
                  <div className="label mb-1">{extra ? extra.title : tab === "single" ? "Görüş alanı · DORI" : "Birleşik kapsama"}</div>
                  {legend.map(([text, [r, g, b, a]]) => (
                    <div key={text} className="flex items-center gap-2 text-[11px]">
                      <span className="h-3 w-3 rounded-sm border border-line" style={{ background: `rgba(${r},${g},${b},${a / 255})` }} />{text}
                    </div>
                  ))}
                </div>
              )}
            </MapView>
          ) : (
            <div className="flex h-full min-h-[360px] items-center justify-center rounded-md bg-bg-2 text-text-3">
              <div className="text-center"><Mountain className="mx-auto mb-2" size={28} />Soldan bir arazi yükleyin.</div>
            </div>
          )}
        </div>
        {tab === "single" && single && <Panel title="Görüş hattı kesiti"><ProfileChart profile={single.profile} /></Panel>}
      </div>

      {/* analysis */}
      <div className="flex w-[340px] shrink-0 flex-col gap-3 overflow-y-auto pr-1">
        <div className="flex gap-1 rounded-md bg-bg-4 p-1">
          {([["single", "Tek Kamera"], ["perimeter", "Çevre Çiti"]] as const).map(([id, label]) => (
            <button key={id} type="button" onClick={() => setTab(id)}
              className={`h-8 flex-1 rounded text-[12px] font-semibold ${tab === id ? "bg-bg-6 text-primary" : "text-text-2"}`}>{label}</button>
          ))}
        </div>

        <Panel title="Kamera">
          <select className="h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" aria-label="Kamera modeli"
            value={models.some((m) => m.name === camera.model_name) ? camera.model_name : ""}
            onChange={(e) => e.target.value && void applyModel(e.target.value)}>
            <option value="">{camera.model_name}</option>
            {models.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
          </select>
          <div className="mt-1 font-mono text-[10px] text-text-3">{camera.sensor_name} · {camera.resolution_name} · {fmt(camera.focal_min_mm)}–{fmt(camera.focal_max_mm)} mm</div>
          <label className="mt-2 block">
            <span className="label">Hava (görüş)</span>
            <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" value={weather} onChange={(e) => setWeather(e.target.value)}>
              {(catalog?.weather ?? [{ label: "Berrak hava", visibility_km: 40 }]).map((w) => (
                <option key={w.label} value={w.label}>{w.label} · {fmt(w.visibility_km)} km</option>
              ))}
            </select>
          </label>
        </Panel>

        {tab === "single" && info && (
          <>
            <Panel title="Yerleşim" right={<span className="font-mono text-[10px] text-text-3">haritaya tıkla: konum</span>}>
              <div className="mb-2 font-mono text-[11px] text-text-2">x {fmt(mount.x, 0)} · y {fmt(mount.y, 0)} m</div>
              <div className="flex flex-col gap-3">
                <Slider label="Direk" unit="m" value={mount.mast} min={1} max={40} step={0.5} onChange={(v) => setMount((m) => ({ ...m, mast: v }))} />
                <Slider label="Yön (pan)" unit="°" value={mount.pan} min={0} max={359} step={1} onChange={(v) => setMount((m) => ({ ...m, pan: v }))} />
                <Slider label="Eğim (tilt)" unit="°" value={mount.tilt} min={-45} max={10} step={0.5} onChange={(v) => setMount((m) => ({ ...m, tilt: v }))} />
                <Slider label="Analiz menzili" unit="m" value={mount.range} min={50} max={Math.max(info.width_m, info.height_m)} step={10} onChange={(v) => setMount((m) => ({ ...m, range: v }))} />
                {camera.focal_max_mm > camera.focal_min_mm && (
                  <Slider label="Odak" unit="mm" value={mount.focal ?? (mount.lens === "max" ? camera.focal_max_mm : camera.focal_min_mm)}
                    min={camera.focal_min_mm} max={camera.focal_max_mm} step={(camera.focal_max_mm - camera.focal_min_mm) / 100}
                    onChange={(v) => setMount((m) => ({ ...m, focal: v }))} />
                )}
              </div>
            </Panel>
            {single && (
              <Panel title="Sonuç" right={<span className="font-mono text-[10px] text-text-3">{vs.busy ? "hesaplanıyor…" : vs.ms !== undefined ? `${fmt(vs.ms, 0)} ms` : ""}</span>}>
                <div className="grid grid-cols-2 gap-1.5">
                  <Tile label="Görünür alan" value={ha(single.visible_area_m2)} />
                  <Tile label="Arazi gölgesi" value={ha(single.occluded_area_m2)} tone="text-warn" />
                  <Tile label="Kapsama" value={`%${fmt(single.coverage_pct, 1)}`} tone="text-secondary" />
                  <Tile label="En uzak görüş" value={fmt(single.max_los_reach_m, 0)} unit="m" />
                </div>
                <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono text-[11px]">
                  {LEVELS.map(([k, l]) => (<div key={k} className="flex justify-between"><span className="text-text-3">{l}</span><span>{ha(single.zone_areas_m2[k])}</span></div>))}
                </div>
                <div className="mt-2 font-mono text-[10px] text-text-3">
                  optik sınır {fmt(single.optical_limit_m, 0)} m{single.atmospheric_limit_m > 0 && single.atmospheric_limit_m < single.optical_limit_m ? ` · atmosfer ${fmt(single.atmospheric_limit_m, 0)} m` : ""}
                </div>
                {single.warnings.map((w) => <div key={w} className="mt-2 text-[11px] text-warn">▲ {w}</div>)}
              </Panel>
            )}
            {isPtz && (
              <>
                <PtzPanel T={T} job={job} terrainId={info.terrain_id} useObstacles={useObstacles} camera={camera}
                  mount={mount} weather={weather} onLayer={setPtzLayer} />
                {ptzLayer && <Button className="w-full" onClick={() => setPtzLayer(null)}>Tekil görüş alanına dön</Button>}
              </>
            )}
            {camera.focal_max_mm > camera.focal_min_mm && (
              <Panel title="Varifokal: tüm odaklar">
                <Button className="w-full" disabled={job.running} onClick={runSweep}><Play size={14} /> Odakları Tara (Tanıma)</Button>
                {sweep && (
                  <div className="mt-2">
                    <FocalCurve curve={sweep.curve} best={sweep.best} level={sweep.level} />
                    <div className="mt-1 font-mono text-[11px]">
                      en iyi {fmt(sweep.best.focal_mm)} mm · eğim {fmt(sweep.best.tilt_deg)}° · {ha(sweep.best.areas_m2.recognize ?? 0)}
                    </div>
                    <Button primary className="mt-2 w-full" onClick={() => setMount((m) => ({ ...m, focal: sweep.best.focal_mm, tilt: sweep.best.tilt_deg }))}>Optimumu Uygula</Button>
                  </div>
                )}
              </Panel>
            )}
          </>
        )}

        {tab === "perimeter" && info && (
          <>
            <Panel title="Çit hattı" right={<span className="font-mono text-[10px] text-text-3">{fence.length} nokta</span>}>
              <div className="flex flex-wrap gap-1.5">
                <Button onClick={() => setDrawing((d) => !d)} className={drawing ? "!bg-ok-bg !text-primary" : ""}><PencilLine size={14} /> {drawing ? "Çizim açık" : "Çiz"}</Button>
                <Button disabled={!fence.length} onClick={() => setFence((f) => f.slice(0, -1))}><Undo2 size={14} /> Geri al</Button>
                <Button disabled={!fence.length} onClick={() => { setFence([]); setPlanRes(null); setCoverage(null); }}><Trash2 size={14} /> Temizle</Button>
              </div>
              <label className="mt-2 flex items-center gap-2 text-[12px]"><input type="checkbox" checked={plan.closed} onChange={(e) => setPlan((p) => ({ ...p, closed: e.target.checked }))} /> Kapalı halka</label>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <label><span className="label">Hat modu</span>
                  <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-1 text-[12px]" value={plan.line_mode}
                    onChange={(e) => setPlan((p) => ({ ...p, line_mode: e.target.value as "facility" | "border" | "highway", closed: e.target.value === "facility" ? p.closed : false }))}>
                    <option value="facility">Tesis</option><option value="border">Sınır</option><option value="highway">Otoyol</option>
                  </select></label>
                <label><span className="label">İzlenen taraf</span>
                  <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-1 text-[12px]" value={plan.watch_side} disabled={plan.line_mode !== "border"}
                    onChange={(e) => setPlan((p) => ({ ...p, watch_side: e.target.value as "left" | "right" }))}>
                    <option value="left">Sol</option><option value="right">Sağ</option>
                  </select></label>
              </div>
              <div className="mt-3 flex flex-col gap-3">
                <Slider label="Hedef" unit="px/m" value={plan.target_ppm} min={25} max={250} step={5} onChange={(v) => setPlan((p) => ({ ...p, target_ppm: v }))} />
                <Slider label="Direk" unit="m" value={plan.mast_m} min={2} max={30} step={0.5} onChange={(v) => setPlan((p) => ({ ...p, mast_m: v }))} />
              </div>
              <Button primary className="mt-3 w-full" disabled={job.running || fence.length < 2} onClick={runPlan}><MapPin size={14} /> Direkleri Diz</Button>
            </Panel>
            {planRes && (
              <Panel title="Plan">
                <div className="grid grid-cols-2 gap-1.5">
                  <Tile label="Direk" value={String(planRes.pole_count)} />
                  <Tile label="Kamera" value={String(planRes.camera_count)} />
                  <Tile label="Çit uzunluğu" value={fmt(planRes.total_fence_length_m, 0)} unit="m" />
                  <Tile label="Çit kapsaması" value={`%${fmt(planRes.coverage_percentage, 1)}`} tone={planRes.coverage_percentage < 95 ? "text-warn" : "text-primary"} />
                </div>
                <div className="mt-2 font-mono text-[10px] text-text-3">ort. aralık {fmt(planRes.avg_spacing_m)} m · {fmt(planRes.estimated_bandwidth_mbps, 0)} Mbps · 30 gün {fmt(planRes.estimated_storage_30days_tb, 1)} TB</div>
                {planRes.gaps.length > 0 && <div className="mt-2 text-[11px] text-danger">■ {planRes.gaps.length} boşluk, toplam {fmt(planRes.gaps.reduce((s, g) => s + g.length_m, 0), 0)} m (haritada kırmızı)</div>}
                <Button className="mt-2 w-full" disabled={job.running} onClick={runCoverage}><Layers size={14} /> Birleşik Kapsamayı Hesapla</Button>
                {coverage && (
                  <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono text-[11px]">
                    <div className="col-span-2 text-text-3">±{fmt(coverage.band_m, 0)} m çit bandı ({ha(coverage.band_area_m2)})</div>
                    {LEVELS.map(([k, l]) => (<div key={k} className="flex justify-between"><span className="text-text-3">{l}</span><span>{pct(coverage.band_share[k])}</span></div>))}
                    {!coverage.measured && <div className="col-span-2 mt-1 text-warn">▲ prosedürel arazi — ölçüm değil</div>}
                  </div>
                )}
              </Panel>
            )}
            <MixedPanel T={T} job={job} terrainId={info.terrain_id} useObstacles={useObstacles} models={models}
              value={mixedPtz} onChange={setMixedPtz} hasPlanWithPtz={!!planRes?.ptz.length} onLayer={setMixedLayer}
              ptzModelFor={async (n) => (await api.cameraFor(n, undefined, true)).camera} />
            {mixedLayer && <Button className="w-full" onClick={() => setMixedLayer(null)}>Birleşik kapsamaya dön</Button>}
            {planRes && (
              <Panel title="Dizilimi optimize et">
                <select className="h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" value={optLevel} onChange={(e) => setOptLevel(e.target.value)} aria-label="Hedef seviye">
                  {LEVELS.map(([k, l]) => <option key={k} value={k}>Bantta {l} alanı</option>)}
                </select>
                <label className="mt-2 flex items-center gap-2 text-[12px]"><input type="checkbox" checked={slide} onChange={(e) => setSlide(e.target.checked)} /> Direkleri çit boyunca kaydır (arazi / bina)</label>
                <Button className="mt-2 w-full" disabled={job.running} onClick={() => job.run("Dizilim optimizasyonu", (o) => T.optimize({
                  terrain_id: info.terrain_id, use_obstacles: useObstacles, level: optLevel, position_steps: slide ? 2 : 0 }, o))
                  .then((r) => { if (!r) return; setOptRes(r); setPlanRes(r.plan); setCoverage(null); setMixedLayer(null); })}>
                  <Play size={14} /> Odak / eğim{slide ? " / konum" : ""} optimize et
                </Button>
                {optRes && (
                  <div className="mt-2 text-[12px]">
                    <div className={optRes.after_m2 > optRes.before_m2 ? "text-primary" : "text-text-2"}>
                      ● Otoriter motor, bant: {ha(optRes.before_m2)} → <b>{ha(optRes.after_m2)}</b> ({pct(optRes.after_m2 / Math.max(optRes.band_area_m2, 1))})
                    </div>
                    <div className="mt-1 text-[11px] text-text-3">{optRes.changed} direğin ayarı değişti{optRes.moved ? ` · ${optRes.moved} direk en çok ${fmt(optRes.max_move_m, 0)} m kaydı` : ""}</div>
                  </div>
                )}
              </Panel>
            )}
            <Panel title="En az direk">
              <Slider label="Algılama hedefi (bant)" unit="%" value={goal} min={50} max={99} step={1} onChange={setGoal} />
              <Button className="mt-2 w-full" disabled={job.running || fence.length < 2} onClick={runPoles}><Play size={14} /> Aramayı Başlat</Button>
              {poleRes && (
                <div className="mt-2 text-[12px]">
                  {poleRes.met && poleRes.best ? (
                    <div className="text-primary">● {poleRes.base.fixed_poles} → <b>{poleRes.best.fixed_poles} direk</b> · bant {pct(poleRes.best.optimised_frac)} (aralık {fmt(poleRes.best.spacing_m, 0)} m)</div>
                  ) : (
                    <div className="text-warn">▲ Hedef tutmadı; plan değiştirilmedi. En iyi: {poleRes.best ? `${poleRes.best.fixed_poles} direk, ${pct(poleRes.best.optimised_frac)}` : "—"}</div>
                  )}
                  {poleRes.notes.map((n) => <div key={n} className="mt-1 text-[11px] text-text-3">◆ {n}</div>)}
                </div>
              )}
            </Panel>
          </>
        )}
      </div>
    </div>
  );
}

function SourcePanel({ catalog, regions, busy, onLoad, T, refreshRegions }: {
  catalog: TerrainCatalog | null;
  regions: RegionInfo[];
  busy: boolean;
  onLoad: (fn: (o: { signal: AbortSignal; onProgress: (p: { done: number; total: number; message: string }) => void }) => Promise<TerrainInfo>, label: string) => void;
  T: ReturnType<typeof useCore>["terrain"];
  refreshRegions: () => void;
}) {
  const [src, setSrc] = useState<"preset" | "region" | "download" | "file">("preset");
  const [preset, setPreset] = useState("ridge_and_valley");
  const [region, setRegion] = useState("");
  const [dl, setDl] = useState({ loc: "", lat: 39.9208, lon: 32.8541, width_m: 1000, source: "esri", quality: "hd", grid: 400, save: true });

  return (
    <Panel title="Arazi" icon={<Mountain size={16} className="text-primary" />}>
      <div className="mb-2 grid grid-cols-4 gap-1">
        {([["preset", "Önayar"], ["region", "Kayıtlı"], ["download", "İndir"], ["file", "Dosya"]] as const).map(([id, l]) => (
          <button key={id} type="button" onClick={() => setSrc(id)}
            className={`h-7 rounded text-[11px] font-semibold ${src === id ? "bg-bg-6 text-primary" : "bg-bg-5 text-text-2"}`}>{l}</button>
        ))}
      </div>
      {src === "preset" && (
        <>
          <select className="h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" value={preset} onChange={(e) => setPreset(e.target.value)} aria-label="Önayar">
            {(catalog?.presets ?? []).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
          <p className="mt-1 text-[11px] text-warn">▲ Prosedürel rölyef ölçüm değildir.</p>
          <Button primary className="mt-2 w-full" disabled={busy} onClick={() => onLoad(() => T.procedural(preset), "Prosedürel arazi")}>Yükle</Button>
        </>
      )}
      {src === "region" && (
        <>
          {regions.length ? (
            <select className="h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" value={region} onChange={(e) => setRegion(e.target.value)} aria-label="Kayıtlı bölge">
              <option value="">Bölge seç…</option>
              {regions.map((r) => <option key={r.region_id} value={r.region_id}>{r.label}</option>)}
            </select>
          ) : <p className="text-[12px] text-text-3">Kayıtlı bölge yok. "İndir" ile indirip kaydedin.</p>}
          <div className="mt-2 flex gap-1.5">
            <Button primary className="flex-1" disabled={busy || !region} onClick={() => onLoad(() => T.openRegion(region), "Kayıtlı bölge")}><FolderOpen size={14} /> Aç</Button>
            <Button disabled={busy || !region} title="Bölgeyi diskten kalıcı olarak sil"
              onClick={() => { if (window.confirm("Bölge diskten silinsin mi? Geri alınamaz.")) void T.deleteRegion(region).then(() => { setRegion(""); refreshRegions(); }); }}><Trash2 size={14} /></Button>
          </div>
        </>
      )}
      {src === "download" && (
        <div className="flex flex-col gap-2 text-[12px]">
          <select className="h-8 w-full rounded border border-line bg-bg-0 px-2" value={dl.loc} aria-label="Hazır konum"
            onChange={(e) => { const l = catalog?.locations.find((x) => x.label === e.target.value); setDl((d) => l ? { ...d, loc: l.label, lat: l.lat, lon: l.lon } : { ...d, loc: "" }); }}>
            <option value="">Özel koordinat</option>
            {(catalog?.locations ?? []).map((l) => <option key={l.label} value={l.label}>{l.label}</option>)}
          </select>
          <div className="grid grid-cols-2 gap-2">
            <NumField label="Enlem" value={dl.lat} onChange={(v) => setDl((d) => ({ ...d, lat: v, loc: "" }))} />
            <NumField label="Boylam" value={dl.lon} onChange={(v) => setDl((d) => ({ ...d, lon: v, loc: "" }))} />
            <NumField label="Genişlik (m)" value={dl.width_m} onChange={(v) => setDl((d) => ({ ...d, width_m: v }))} />
            <label><span className="label">Izgara</span>
              <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-1" value={dl.grid} onChange={(e) => setDl((d) => ({ ...d, grid: Number(e.target.value) }))}>
                {[200, 400, 600].map((g) => <option key={g} value={g}>{g} hücre</option>)}
              </select></label>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <select className="h-8 rounded border border-line bg-bg-0 px-1" value={dl.source} onChange={(e) => setDl((d) => ({ ...d, source: e.target.value }))} aria-label="Harita kaynağı">
              {(catalog?.map_sources ?? []).map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
            <select className="h-8 rounded border border-line bg-bg-0 px-1" value={dl.quality} onChange={(e) => setDl((d) => ({ ...d, quality: e.target.value }))} aria-label="Kalite">
              {(catalog?.qualities ?? []).map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
          </div>
          <label className="flex items-center gap-2"><input type="checkbox" checked={dl.save} onChange={(e) => setDl((d) => ({ ...d, save: e.target.checked }))} /> Çevrimdışı kullanım için kaydet</label>
          <Button primary disabled={busy} onClick={() => onLoad((o) => T.download({ ...dl, label: dl.loc || undefined, save_name: dl.loc || undefined }, o).then((t) => { refreshRegions(); return t; }), "Harita ve DEM indiriliyor")}>
            <Download size={14} /> İndir
          </Button>
        </div>
      )}
      {src === "file" && (
        <>
          <p className="text-[12px] text-text-2">Yerel DEM GeoTIFF (ölçülmüş yükselti).</p>
          <Button primary className="mt-2 w-full" disabled={busy || !isTauri()} onClick={async () => {
            const path = await open({ title: "Yükselti modeli (GeoTIFF)", filters: [{ name: "GeoTIFF", extensions: ["tif", "tiff"] }] });
            if (typeof path === "string") onLoad(() => T.geotiff(path), "GeoTIFF");
          }}><FolderOpen size={14} /> Dosya Seç</Button>
        </>
      )}
    </Panel>
  );
}

function NumField({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <label><span className="label">{label}</span>
      <input type="number" className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 font-mono text-[12px]" value={value}
        onChange={(e) => { const v = Number(e.target.value.replace(",", ".")); if (Number.isFinite(v)) onChange(v); }} />
    </label>
  );
}

function SurfaceRasterButton({ info, busy, onDone, onError, T }: {
  info: TerrainInfo; busy: boolean; onDone: (t: TerrainInfo) => void; onError: (m: string) => void; T: ReturnType<typeof useCore>["terrain"];
}) {
  const [ask, setAsk] = useState<string | null>(null);
  const run = (path: string, mode: "auto" | "ndsm" | "dsm") =>
    T.surfaceRaster(info.terrain_id, path, mode).then((t) => { setAsk(null); onDone(t); }).catch((e) => {
      if (e?.data?.needs_mode) setAsk(path); else onError(String(e.message ?? e));
    });
  return (
    <>
      <Button disabled={busy || info.lat_center === null || !isTauri()} onClick={async () => {
        const path = await open({ title: "Yüzey modeli (nDSM / DSM GeoTIFF)", filters: [{ name: "GeoTIFF", extensions: ["tif", "tiff"] }] });
        if (typeof path === "string") void run(path, "auto");
      }}><Layers size={14} /> Yerel nDSM / DSM</Button>
      {ask && (
        <div className="rounded bg-bg-3 p-2 text-[11px]">
          Dosyadaki değerler zemin üstü yükseklik mi (nDSM), mutlak yüzey kotu mu (DSM)?
          <div className="mt-1.5 flex gap-1.5">
            <Button onClick={() => void run(ask, "ndsm")}>nDSM</Button>
            <Button onClick={() => void run(ask, "dsm")}>DSM</Button>
            <Button onClick={() => setAsk(null)}>Vazgeç</Button>
          </div>
        </div>
      )}
    </>
  );
}

function RegionSave({ info, T, busy, onSaved }: { info: TerrainInfo; T: ReturnType<typeof useCore>["terrain"]; busy: boolean; onSaved: (rid: string) => void }) {
  const [name, setName] = useState(info.label);
  const [msg, setMsg] = useState("");
  useEffect(() => { setName(info.label); setMsg(""); }, [info.terrain_id, info.label]);
  return (
    <Panel title="Bölgeyi Kaydet">
      <input className="h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" value={name} onChange={(e) => setName(e.target.value)} aria-label="Bölge adı" />
      <Button className="mt-2 w-full" disabled={busy || !name.trim()}
        onClick={() => T.saveRegion(info.terrain_id, name).then((r) => { setMsg(`Kaydedildi (${fmt(r.size_bytes / 1e6, 1)} MB)`); onSaved(r.region_id); }).catch((e) => setMsg(String(e.message ?? e)))}>
        <Save size={14} /> Kaydet
      </Button>
      {msg && <div className="mt-1 text-[11px] text-text-2">{msg}</div>}
    </Panel>
  );
}
