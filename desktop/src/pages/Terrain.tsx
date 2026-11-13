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
  AreaPlanResult, CityInfo, CoverageLayer, CoverageResult, DoriLevel, FocalSweep, OptimizeResult, PlaybackPtz, LegendRow, NightCoverage, PlanResult, PoleSearch, RegionInfo, Scenario,
  SingleViewshed, TerrainCatalog, TerrainInfo,
} from "../core/terrainApi";
import { useJob } from "../core/useJob";
import { useActiveTerrain } from "../core/activeTerrain";
import { undoScope, useProject } from "../core/project";
import { confirmDialog } from "../components/dialogs";
import MapView from "../components/MapView";
import JobBar from "../components/JobBar";
import { FocalCurve, ProfileChart } from "../components/charts";
import { MixedPanel, PtzPanel, type Layer, type Patrol } from "../components/PtzPanels";
import PtzSweep from "../components/PtzSweep";
import { ComparePanel, EMPTY_PRICES, NetworkPanel, type NetworkResult, type PriceBook } from "../components/PlanningPanels";
import { NightPanel, ScenarioPanel } from "../components/DeliveryPanels";
import { ApproachPanel, type ApproachMarks } from "../components/ApproachPanel";
import { CityPanel } from "../components/CityPanel";
import { Button, Panel, Slider, Tile, fmt } from "../components/ui";

const ha = (m2: number) => `${fmt(m2 / 1e4, 2)} ha`;
const pct = (f: number) => `%${fmt(f * 100, 1)}`;
const LEVELS = [
  ["detect", "Algılama ≥25"],
  ["observe", "Gözlem ≥62,5"],
  ["recognize", "Tanıma ≥125"],
  ["identify", "Teşhis ≥250"],
] as const;

// thermal views: the same zone codes on Johnson thresholds (viewshed_3d.zone_codes)
const THERMAL_LEVELS = [
  ["detect", "Johnson algılama ≥1,3"],
  ["observe", "Johnson tanıma ≥6,7"],
  ["recognize", "Johnson teşhis ≥13,3"],
] as const;
// one list for every level selector: a thermal has three levels and none of them is a face
const levelsFor = (thermal: boolean): readonly (readonly [DoriLevel, string])[] => (thermal ? THERMAL_LEVELS : LEVELS);
const levelName = (k: string, thermal: boolean) => { const l = levelsFor(thermal).find(([x]) => x === k)?.[1] ?? ""; return l.slice(0, l.lastIndexOf(" ")); };
const clampLevel = <L extends string>(l: L, thermal: boolean) => (thermal && l === "identify" ? "recognize" as L : l);
// fence targets a designer actually picks (a 25–250 slider in steps of 5 could not land on 62,5)
const TARGETS: readonly (readonly [number, string])[] = [[25, "Algılama · 25"], [40, "Güçlendirilmiş çit · 40"], [62.5, "Gözlem · 62,5"], [125, "Tanıma · 125"], [250, "Teşhis · 250"]];
const THERMAL_TARGETS: readonly (readonly [number, string])[] = [[1.3, "Johnson algılama · 1,3"], [6.67, "Johnson tanıma · 6,67"], [13.33, "Johnson teşhis · 13,33"]];
// "%100,0" beside "3 m uncovered" read as a contradiction: 100 is shown only when nothing is left out
const pctCapped = (f: number, uncoveredM: number) => (uncoveredM >= 0.5 && f * 100 >= 99.95 ? "%99,9" : pct(f));

type Tab = "single" | "perimeter";
type Step = "define" | "plan" | "verify" | "improve" | "deliver";
const STEPS: readonly (readonly [Step, string])[] = [["define", "Tanımla"], ["plan", "Planla"], ["verify", "Doğrula"], ["improve", "İyileştir"], ["deliver", "Teslim"]];
// The requirement is ONE target px/m; its level is the highest one that target reaches
// (plan_statement.declared_level says the same on the core side: 40 px/m -> Detection).
const LEVEL_PPM: Record<DoriLevel, number> = { detect: 25, observe: 62.5, recognize: 125, identify: 250 };
const LEVEL_PPM_THERMAL: Record<DoriLevel, number> = { detect: 1.3, observe: 6.67, recognize: 13.33, identify: 13.33 };
const levelPpm = (k: DoriLevel, thermal: boolean) => (thermal ? LEVEL_PPM_THERMAL : LEVEL_PPM)[k];
const levelOf = (ppm: number, thermal: boolean): DoriLevel =>
  levelsFor(thermal).reduce<DoriLevel>((best, [k]) => (ppm >= levelPpm(k, thermal) - 1e-9 ? k : best), "detect");
type LineMode = "facility" | "border" | "highway" | "area";

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
  // DORI target: a person this tall on each cell (sight line + framing to the head)
  const [targetH, setTargetH] = useState(1.8);
  const [sweep, setSweep] = useState<FocalSweep | null>(null);
  const [sweepLevel, setSweepLevel] = useState<DoriLevel>("recognize");
  const [isPtz, setIsPtz] = useState(false);
  const [isThermal, setIsThermal] = useState(false);
  const [patrol, setPatrol] = useState<Patrol>({ speed: 20, dwell: 2 });
  const [customTarget, setCustomTarget] = useState(false);
  const [ptzReach, setPtzReach] = useState(false);     // coverage: also what a PTZ can be turned to on demand
  const [ptzLayer, setPtzLayer] = useState<Layer | null>(null);       // single tab: PTZ result replaces the fan
  const [mixedLayer, setMixedLayer] = useState<Layer | null>(null);   // perimeter tab: fixed + PTZ layers
  const [mixedPtz, setMixedPtz] = useState<{ camera: CameraConfig | null; everyN: number; span: number }>({ camera: null, everyN: 4, span: 360 });

  // perimeter
  const [fence, setFence] = useState<[number, number][]>([]);
  const [drawing, setDrawing] = useState(false);
  const [plan, setPlan] = useState<{ closed: boolean; line_mode: LineMode; watch_side: "left" | "right"; target_ppm: number; mast_m: number; lens_mode: "min" | "max"; back_to_back: boolean }>(
    { closed: true, line_mode: "facility", watch_side: "right", target_ppm: 40, mast_m: 6, lens_mode: "min", back_to_back: false });
  // the band a fence plan protects: outside / inside widths (inside only for a closed fence)
  const [band, setBand] = useState({ out: 40, in: 40 });
  const [covLayer, setCovLayer] = useState<CoverageLayer>("target");
  const [covLevel, setCovLevel] = useState<"plan" | DoriLevel>("plan");
  const [showFov, setShowFov] = useState(true);
  // live PTZ sweep: pictures from the core once, the map turns the cones
  const [play, setPlay] = useState<{ ptz: PlaybackPtz[]; tab: Tab; camera: string } | null>(null);
  const [playing, setPlaying] = useState(true);
  const [rate, setRate] = useState(1);
  const [playT, setPlayT] = useState(0);
  // area goal: the drawn polygon itself is what must be seen (car park, yard, square)
  const [area, setArea] = useState<{ interior: boolean; perPole: number; streetsOnly: boolean; maxHole: number; minCams: number; maxCams: number }>(
    { interior: true, perPole: 2, streetsOnly: false, maxHole: 0, minCams: 1, maxCams: 120 });
  const [city, setCity] = useState<CityInfo | null>(null);   // OSM streets / homes / places of this terrain
  const [areaRes, setAreaRes] = useState<AreaPlanResult | null>(null);
  const isArea = plan.line_mode === "area";
  const reqLevel = levelOf(plan.target_ppm, isThermal);
  // an area plan is built for the level (its threshold); a fence plan for the px/m itself
  const reqPpm = isArea ? levelPpm(reqLevel, isThermal) : plan.target_ppm;
  const [planRes, setPlanRes] = useState<PlanResult | null>(null);
  const [coverage, setCoverage] = useState<CoverageResult | null>(null);
  // THE requirement: target px/m (plan.target_ppm) + the share of the protected area that must hold it
  const [goal, setGoal] = useState(95);
  const [step, setStep] = useState<Step>("define");
  const [prices, setPrices] = useState<PriceBook>(EMPTY_PRICES);
  const [network, setNetwork] = useState<NetworkResult | null>(null);
  const [slide, setSlide] = useState(true);       // poles may move along the fence
  const [panSearch, setPanSearch] = useState(false);
  const [optRes, setOptRes] = useState<OptimizeResult | null>(null);
  const [poleRes, setPoleRes] = useState<PoleSearch | null>(null);
  const [night, setNight] = useState<NightCoverage | null>(null);
  const [approachMarks, setApproachMarks] = useState<ApproachMarks | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  // A new plan drops what was computed from the old one (the core does the same: set_plan).
  const [planVer, setPlanVer] = useState(0);       // remounts panels that hold results of the old plan
  const replacePlan = useCallback((p: PlanResult | null) => {
    setPlanVer((v) => v + 1);
    setPlanRes(p); setCoverage(null); setNetwork(null); setNight(null); setMixedLayer(null); setApproachMarks(null); setPlay(null);
    setOptRes(null);
    if (!p) setAreaRes(null);
  }, []);

  useEffect(() => {
    if (state !== "ready") return;
    T.catalog().then(setCatalog).catch(() => undefined);
    T.regions().then((r) => setRegions(r.regions)).catch(() => undefined);
    api.library().then((r) => setModels(r.models)).catch(() => undefined);
  }, [T, api, state]);

  const adopt = useCallback((t: TerrainInfo) => {
    setInfo(t);
    setMount((m) => ({ ...m, x: t.width_m / 2, y: t.height_m / 2 }));
    setFence([]); setPlanRes(null); setCoverage(null); setNetwork(null); setNight(null); setPoleRes(null); setSweep(null); setAreaRes(null);
    setCity(null);
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
        weather, max_px: 1200, target_height_m: targetH }
    : null), [info, tab, useObstacles, camera, mount, weather, targetH]);
  // the core keeps the target height with the terrain; a change drops results scored with the old one
  useEffect(() => {
    if (!info || state !== "ready") return;
    void T.settings(info.terrain_id, { target_height_m: targetH }).then((r) => {
      if (r.changed) { setCoverage(null); setNetwork(null); setNight(null); setMixedLayer(null); setApproachMarks(null); }
    }).catch(() => undefined);
  }, [T, info, targetH, state]);
  const vs = useCoalesced(vsInput, (i) => T.viewshed(i!), !!vsInput && state === "ready" && active);
  const single: SingleViewshed | undefined = vs.value;

  const applyModel = async (name: string, opts?: { keepMount?: boolean }) => {
    const { camera: c, is_ptz, thermal } = await api.cameraFor(name, camera);
    setCamera(c);
    setIsPtz(is_ptz);
    setIsThermal(thermal);
    // the old model's zoom and tilt do not carry over; a PTZ opens on its wide (patrol) view, not at full tele
    setMount((m) => (opts?.keepMount ? { ...m, focal: undefined }
      : { ...m, focal: undefined, tilt: -5, lens: is_ptz ? "min" : m.lens }));
    // level selectors follow the camera's scale
    setSweepLevel((l) => clampLevel(l, thermal)); setCovLevel((l) => clampLevel(l, thermal));
    setPlan((p) => {
      const onScale = (thermal ? THERMAL_TARGETS : TARGETS).some(([v]) => v === p.target_ppm);
      const otherScale = (thermal ? TARGETS : THERMAL_TARGETS).some(([v]) => v === p.target_ppm);
      return !onScale && otherScale ? { ...p, target_ppm: thermal ? 1.3 : 40 } : p;
    });
    setSweep(null);
    setPtzLayer(null);
  };

  const pick = (x: number, y: number) => {
    if (tab === "single") { setMount((m) => ({ ...m, x, y })); setPtzLayer(null); }
    else if (drawing) setFence((f) => [...f, [x, y]]);
  };

  const startPlayback = async (args: Record<string, unknown>) => {
    if (!info) return;
    const r = await job.run("Canlı PTZ taraması hazırlanıyor", (o) => T.ptzPlayback({ terrain_id: info.terrain_id, use_obstacles: useObstacles,
      max_px: 900, speed_deg_s: patrol.speed, end_dwell_s: patrol.dwell, ...args }, o));
    if (r) { setPlay({ ptz: r.ptz, tab, camera: r.camera_name }); setPlaying(true); setPlayT(0); }
  };
  const ptzArgs = () => (mixedPtz.camera ? { ptz_camera: mixedPtz.camera, ptz_every_n: mixedPtz.everyN, ptz_patrol_span_deg: mixedPtz.span } : {});
  // band widths every band-scored call sends (coverage, optimise, fewest poles, model compare, night)
  const bandArgs = () => (isArea ? {} : { band_m: band.out, ...(plan.closed && band.in !== band.out ? { band_in_m: band.in } : {}) });
  const planParams = () => ({ fence, ...plan, weather, ...ptzArgs(), ...bandArgs() });
  const warn = (w?: string[]) => { if (w?.length) setNotice(w.join(" ")); };
  const runPlan = async () => {
    if (!info || fence.length < 2) return;
    const r = await job.run("Çit planı", () => T.plan({ terrain_id: info.terrain_id, use_obstacles: useObstacles, camera, ...planParams() }));
    if (r) { replacePlan(r); setPoleRes(null); setAreaRes(null); warn(r.warnings); }
  };
  const runArea = async () => {
    if (!info || fence.length < 3) return;
    const r = await job.run("Alan kapsama planı", (o) => T.areaPlan({ terrain_id: info.terrain_id, use_obstacles: useObstacles, camera,
      fence, level: reqLevel, goal_frac: goal / 100, mast_m: plan.mast_m, interior: area.interior, max_per_pole: area.perPole, weather,
      streets_only: area.streetsOnly && !!city, ...(area.maxHole > 0 ? { max_hole_m2: area.maxHole } : {}),
      min_cameras: area.minCams, max_cameras: area.maxCams, ...ptzArgs() }, o));
    if (r) { replacePlan(r.plan); setAreaRes(r); setPoleRes(null); warn(r.warnings); }
  };
  const runCoverage = async (layer: CoverageLayer = covLayer, level: "plan" | DoriLevel = covLevel) => {
    if (!info) return;
    setCovLayer(layer); setCovLevel(level);
    const r = await job.run("Birleşik görüş alanı", (o) => T.coverage({ terrain_id: info.terrain_id, use_obstacles: useObstacles, max_px: 1200,
      layer, level, ptz_speed_deg_s: patrol.speed, ptz_end_dwell_s: patrol.dwell, ptz_reach: ptzReach, ...bandArgs() }, o));
    if (r) setCoverage(r);
  };
  const runPoles = async () => {
    if (!info || fence.length < 2) return;
    const r = await job.run("En az direk araması", (o) => T.minimizePoles(
      { terrain_id: info.terrain_id, use_obstacles: useObstacles, camera, ...planParams(), goal_frac: goal / 100, level: reqLevel, position_steps: slide ? 2 : 0 }, o));
    if (r) { setPoleRes(r); if (r.plan) replacePlan(r.plan); }
  };
  const runSweep = async () => {
    if (!info) return;
    const r = await job.run("Odak taraması", (o) => T.focalSweep({ terrain_id: info.terrain_id, use_obstacles: useObstacles, camera,
      x_m: mount.x, y_m: mount.y, mast_m: mount.mast, pan_deg: mount.pan, level: sweepLevel, weather, n_focals: 10,
      // the engine scores the optimum against what the map shows now, over the same analysis range
      range_m: mount.range, current_tilt_deg: mount.tilt,
      current_focal_mm: mount.focal ?? (mount.lens === "max" ? camera.focal_max_mm : camera.focal_min_mm) }, o));
    if (r) setSweep(r);
  };

  // ── project file: publish this page's state, restore it when a project opens ──
  const { setTerrainBlock, loadedTerrain, sites: projectSites } = useProject();
  // Fence edits undo with Ctrl+Z while this tab is in front (ProjectBar asks undoScope first).
  // Changes closer than 500 ms (a corner drag) are one step.
  const fenceHist = useRef<{ past: [number, number][][]; future: [number, number][][]; last: number; prev: [number, number][] | null; skip: boolean }>(
    { past: [], future: [], last: 0, prev: null, skip: false });
  useEffect(() => {
    const h = fenceHist.current;
    if (h.prev === fence) return;
    if (h.prev === null) { h.prev = fence; return; }      // the first render is not an edit
    if (!h.skip) {
      const now = Date.now();
      // only a drag (same number of corners) is merged; every added / removed corner is its own step
      if (now - h.last > 500 || h.prev.length !== fence.length) { h.past.push(h.prev); if (h.past.length > 100) h.past.shift(); h.future = []; }
      h.last = now;
    }
    h.skip = false; h.prev = fence;
  }, [fence]);
  useEffect(() => {
    if (!active || tab !== "perimeter") return;
    const step = (from: "past" | "future", to: "past" | "future") => {
      const h = fenceHist.current, f = h[from].pop();
      if (!f) return false;
      h[to].push(h.prev ?? []); h.skip = true; h.last = 0;
      setFence(f); replacePlan(null);
      return true;
    };
    undoScope.current = { undo: () => step("past", "future"), redo: () => step("future", "past") };
    return () => { undoScope.current = null; };
  }, [active, tab, replacePlan]);
  const adoptProjectCamera = async (i: number) => {
    const s = projectSites[i];
    if (!s) return;
    // flags come from the library when the model is in it; a custom camera is a fixed, visible one
    const flags = await api.cameraFor(s.camera.model_name, s.camera).catch(() => ({ is_ptz: false, thermal: false }));
    setCamera(s.camera); setIsPtz(flags.is_ptz); setIsThermal(flags.thermal);
    setMount((m) => ({ ...m, mast: s.camera.pole_height_m || m.mast, focal: s.focal, tilt: -5 }));
    setPlan((p) => ({ ...p, mast_m: s.camera.pole_height_m || p.mast_m }));
    setSweep(null); setPtzLayer(null);
  };
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
        target_ppm: plan.target_ppm, perimeter_mast_m: plan.mast_m, lens_mode: plan.lens_mode, back_to_back: plan.back_to_back, band_out_m: band.out, band_in_m: band.in, target_height_m: targetH,
        requirement: { target_ppm: plan.target_ppm, goal_pct: goal },
        // level / goal_pct repeated for readers of the older schema
        area_goal: { level: reqLevel, goal_pct: goal, interior: area.interior, per_pole: area.perPole, streets_only: area.streetsOnly,
          max_hole_m2: area.maxHole, min_cameras: area.minCams, max_cameras: area.maxCams },
        mixed_ptz: { camera: mixedPtz.camera?.model_name ?? "", every_n: mixedPtz.everyN, patrol_span_deg: mixedPtz.span },
        prices, scenarios,
      },
    }, { touch: !restoring.current });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setTerrainBlock, info, mount, weather, camera, tab, fence, plan, band, targetH, area, mixedPtz, prices, scenarios, goal, isThermal]);

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
        line_mode: (["facility", "border", "highway", "area"].includes(String(x.line_mode)) ? x.line_mode : p.line_mode) as typeof p.line_mode,
        watch_side: (x.watch_side === "left" || x.watch_side === "right" ? x.watch_side : p.watch_side) as typeof p.watch_side,
        target_ppm: typeof x.target_ppm === "number" ? x.target_ppm : p.target_ppm,
        mast_m: typeof x.perimeter_mast_m === "number" ? x.perimeter_mast_m : p.mast_m,
        lens_mode: x.lens_mode === "max" ? "max" : "min",
        back_to_back: x.back_to_back === true,
      }));
      setBand((bd) => ({ out: typeof x.band_out_m === "number" ? x.band_out_m : bd.out, in: typeof x.band_in_m === "number" ? x.band_in_m : bd.in }));
      if (typeof x.target_height_m === "number") setTargetH(x.target_height_m);
      if (x.planner_mode === "perimeter") setTab("perimeter");
      const ag = (x.area_goal ?? {}) as Record<string, unknown>;
      const rq = (x.requirement ?? {}) as Record<string, unknown>;
      if (typeof rq.goal_pct === "number") setGoal(rq.goal_pct);
      else if (x.line_mode === "area" && typeof ag.goal_pct === "number") setGoal(ag.goal_pct);
      // an older area project kept its level apart from the fence target
      if (typeof rq.target_ppm !== "number" && x.line_mode === "area" && typeof ag.level === "string" && ag.level in LEVEL_PPM)
        setPlan((p) => ({ ...p, target_ppm: LEVEL_PPM[ag.level as DoriLevel] }));
      setStep(Array.isArray(x.fence_points) && x.fence_points.length >= 2 ? "plan" : "define");
      setArea((a) => ({
        interior: typeof ag.interior === "boolean" ? ag.interior : a.interior,
        perPole: typeof ag.per_pole === "number" ? ag.per_pole : a.perPole,
        streetsOnly: typeof ag.streets_only === "boolean" ? ag.streets_only : a.streetsOnly,
        maxHole: typeof ag.max_hole_m2 === "number" ? ag.max_hole_m2 : a.maxHole,
        minCams: typeof ag.min_cameras === "number" ? ag.min_cameras : a.minCams,
        maxCams: typeof ag.max_cameras === "number" ? ag.max_cameras : a.maxCams,
      }));
      if (x.prices && typeof x.prices === "object") setPrices({ ...EMPTY_PRICES, ...(x.prices as Partial<PriceBook>) });
      setScenarios(Array.isArray(x.scenarios) ? (x.scenarios as Scenario[]) : []);
      if (typeof x.camera_model === "string" && x.camera_model) void applyModel(x.camera_model, { keepMount: true }).catch(() => undefined);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadedTerrain?.nonce, state]);

  // publish what has been run on this terrain, for the Reports page
  const { setActive } = useActiveTerrain();
  useEffect(() => {
    setActive(info ? {
      terrain_id: info.terrain_id, label: info.label, is_measured: info.is_measured, use_obstacles: useObstacles,
      weather, camera, ran: { viewshed: !!single, plan: !!planRes, coverage: !!coverage, network: !!network, night: !!night },
      scenarios, goal_frac: goal / 100,
    } : null);
  }, [setActive, info, useObstacles, weather, camera, single, planRes, coverage, network, night, scenarios, goal]);

  const stepDone: Record<Step, boolean> = { define: fence.length >= (isArea ? 3 : 2), plan: !!planRes, verify: !!coverage,
    improve: !!optRes?.applied || !!poleRes?.met, deliver: !!network };
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
        {play && play.tab === tab && (
          <div className="flex flex-wrap items-center gap-2 rounded bg-bg-2 px-3 py-1.5 text-[12px]">
            <span className="font-semibold text-ptz">● Canlı tarama</span>
            <span className="text-text-2">{play.ptz.length} PTZ · {play.camera}</span>
            <span className="font-mono text-text-2">t = {fmt(playT, 1)} sn</span>
            <span className="font-mono text-[11px] text-text-3">
              {play.ptz.length === 1
                ? `${fmt(play.ptz[0].focal_mm, 1)} mm · FOV ${fmt(play.ptz[0].hfov_deg, 1)}° · ${fmt(play.ptz[0].speed_deg_s, 0)}°/sn · tur ${fmt(play.ptz[0].period_s, 1)} sn`
                : `${fmt(Math.min(...play.ptz.map((p) => p.focal_mm)), 1)} mm · FOV ${fmt(play.ptz[0].hfov_deg, 1)}° · tur ${fmt(Math.max(...play.ptz.map((p) => p.period_s)), 1)} sn`}
            </span>
            <div className="flex-1" />
            {[1, 4, 10].map((k) => (
              <button key={k} type="button" onClick={() => setRate(k)}
                className={`h-6 rounded px-2 text-[11px] font-semibold ${rate === k ? "bg-bg-6 text-ptz" : "bg-bg-5 text-text-2"}`}>{k}×</button>
            ))}
            <Button onClick={() => setPlaying((v) => !v)}>{playing ? "Duraklat" : "Devam"}</Button>
            <Button onClick={() => setPlay(null)}>Kapat</Button>
          </div>
        )}
        <div className="min-h-0 flex-1">
          {info ? (
            <MapView widthM={info.width_m} heightM={info.height_m} base={base} overlay={overlay}
              camera={tab === "single" && single ? { x: mount.x, y: mount.y, pan: mount.pan, hfov: single.hfov_deg,
                // the wedge ends where the frame does: a steep tilt holds only the mast's foot
                range: Math.max(Math.min(mount.range, single.framed_reach_m > 0 ? single.framed_reach_m : mount.range), 15) } : null}
              fence={tab === "perimeter" ? fence : []} fenceClosed={plan.closed || isArea}
              // an edited fence no longer has the plan that was built on the old one
              onFenceChange={tab === "perimeter" ? (f) => { setFence(f); if (planRes) replacePlan(null); } : undefined}
              poles={tab === "perimeter" ? planRes?.poles : []} ptz={tab === "perimeter" ? planRes?.ptz : []}
              gaps={tab === "perimeter" && planRes?.line_mode !== "border" ? planRes?.gaps : []} showFov={showFov}
              inMap={play && play.tab === tab ? <PtzSweep ptz={play.ptz} heightM={info.height_m} playing={playing} rate={rate} onTime={setPlayT} /> : null}
              switches={tab === "perimeter" ? network?.network.switches : []}
              approach={tab === "perimeter" && mixedLayer?.title === "Gizli yaklaşma" ? approachMarks : null}
              tool={tab === "single" ? "camera" : drawing ? "fence" : "none"} onPick={pick}>
              {legend && (
                <div className="rounded border border-bg-5 bg-bg-1/85 px-3 py-2 backdrop-blur">
                  <div className="label mb-1">{extra ? extra.title : tab === "single" ? "Görüş alanı · DORI"
                    : covLayer === "target" ? `Hedef · ${coverage?.target.label ?? ""}` : covLayer === "overlap" ? "Yedeklilik" : "Birleşik kapsama · DORI"}</div>
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
          {/* the CAD page's cameras: same lens, sensor and mast here, instead of setting them up twice */}
          <select className="mt-2 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" aria-label="Proje kamerasını kullan" value=""
            onChange={(e) => { if (e.target.value !== "") void adoptProjectCamera(Number(e.target.value)); }}>
            <option value="">Proje kamerasını kullan…</option>
            {projectSites.map((s, i) => <option key={i} value={i}>{s.camera.name} · {s.camera.model_name} · direk {fmt(s.camera.pole_height_m, 1)} m</option>)}
          </select>
          <label className="mt-2 block">
            <span className="label">Hava (görüş)</span>
            <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" value={weather} onChange={(e) => setWeather(e.target.value)}>
              {(catalog?.weather ?? [{ label: "Berrak hava", visibility_km: 40 }]).map((w) => (
                <option key={w.label} value={w.label}>{w.label} · {fmt(w.visibility_km)} km</option>
              ))}
            </select>
          </label>
          <div className="mt-2">
            <Slider label="Hedef boyu (insan)" unit="m" value={targetH} min={0} max={2.5} step={0.1} onChange={setTargetH} />
            <p className="mt-1 text-[10px] text-text-3">{targetH > 0
              ? "Görüş hattı ve kadraj başa göre: baş kadrajda değilse hücre sayılmaz, alçak duvar arkasındaki kişi görülür."
              : "0 = zemin noktası (insan boyu modellenmez)."}</p>
          </div>
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
                  {/* a thermal is classed on Johnson thresholds and never reaches EN identification */}
                  {(single.thermal ? THERMAL_LEVELS : LEVELS).map(([k, l]) => (<div key={k} className="flex justify-between"><span className="text-text-3">{l}</span><span>{ha(single.zone_areas_m2[k])}</span></div>))}
                </div>
                <div className="mt-2 font-mono text-[10px] text-text-3">
                  optik sınır {fmt(single.optical_limit_m, 0)} m{single.atmospheric_limit_m > 0 && single.atmospheric_limit_m < single.optical_limit_m ? ` · atmosfer ${fmt(single.atmospheric_limit_m, 0)} m` : ""}
                </div>
                {single.notes.map((n) => <div key={n} className="mt-1 text-[11px] text-text-3">◆ {n}</div>)}
                {single.warnings.map((w) => <div key={w} className="mt-2 text-[11px] text-warn">▲ {w}</div>)}
              </Panel>
            )}
            {isPtz && (
              <>
                <PtzPanel T={T} job={job} terrainId={info.terrain_id} useObstacles={useObstacles} camera={camera}
                  thermal={isThermal} mount={mount} weather={weather} onLayer={setPtzLayer} onPlay={startPlayback} />
                {ptzLayer && <Button className="w-full" onClick={() => setPtzLayer(null)}>Tekil görüş alanına dön</Button>}
              </>
            )}
            {camera.focal_max_mm > camera.focal_min_mm && (
              <Panel title="Varifokal: tüm odaklar">
                <select className="h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" value={sweepLevel} aria-label="Varifokal hedef seviyesi"
                  onChange={(e) => { setSweepLevel(e.target.value as DoriLevel); setSweep(null); }}>
                  {levelsFor(isThermal).map(([k, l]) => <option key={k} value={k}>En geniş {l} alanı</option>)}
                </select>
                <p className="mt-1 text-[10px] text-text-3">Optimum, seçilen seviyede EN GENİŞ alanı verir; daha düşük seviyelerin alanı küçülebilir.</p>
                <Button className="mt-2 w-full" disabled={job.running} onClick={runSweep}><Play size={14} /> Odakları Tara</Button>
                {sweep && (
                  <div className="mt-2">
                    <FocalCurve curve={sweep.curve} best={sweep.best} level={sweep.level} />
                    <div className="mt-1 font-mono text-[11px]">
                      en iyi {fmt(sweep.best.focal_mm)} mm · eğim {fmt(sweep.best.tilt_deg)}° · {levelName(sweepLevel, isThermal)} {ha(sweep.verified.area_m2)}
                      {sweep.current && <span className="text-text-3"> (şimdi {ha(sweep.current.area_m2)})</span>}
                      <span className="text-text-3"> · motor</span>
                    </div>
                    {sweep.note && <div className={`mt-1 text-[11px] ${sweep.worth_applying ? "text-text-3" : "text-warn"}`}>{sweep.worth_applying ? "◆" : "▲"} {sweep.note}</div>}
                    <Button primary className="mt-2 w-full" disabled={!sweep.worth_applying}
                      onClick={() => { setMount((m) => ({ ...m, focal: sweep.best.focal_mm, tilt: sweep.best.tilt_deg })); setSweep(null); }}>Optimumu Uygula</Button>
                  </div>
                )}
              </Panel>
            )}
          </>
        )}

        {tab === "perimeter" && info && (
          <>
            {/* one long scroll became five steps; every panel stays mounted (hidden) so its results survive a step change */}
            <div className="grid grid-cols-5 gap-1 rounded-md bg-bg-4 p-1">
              {STEPS.map(([id, label], i) => (
                <button key={id} type="button" onClick={() => setStep(id)} title={`${i + 1}. adım: ${label}`}
                  className={`h-8 rounded text-[11px] font-semibold ${step === id ? "bg-bg-6 text-primary" : "text-text-2"}`}>
                  <span className={stepDone[id] ? "text-primary" : "text-text-3"}>{stepDone[id] ? "●" : "○"}</span> {label}
                </button>
              ))}
            </div>
            <div className="rounded bg-bg-2 px-3 py-1.5 font-mono text-[11px] text-text-2">
              İster: <span className="text-text">{levelName(reqLevel, isThermal)}</span> · ≥ {fmt(reqPpm, reqPpm % 1 ? 2 : 0)} px/m ·{" "}
              {isArea ? "alanın" : plan.line_mode === "border" ? "izlenen şeridin" : "bandın"} %{fmt(goal, goal % 1 ? 1 : 0)}'i
            </div>
            {mixedLayer && <Button className="w-full" onClick={() => setMixedLayer(null)}>Birleşik kapsamaya dön</Button>}

            {/* ── 1. Tanımla: what is protected, and to which requirement ── */}
            <div className={step === "define" ? "flex flex-col gap-3" : "hidden"}>
              <Panel title={isArea ? "Alan" : "Çit hattı"} right={<span className="font-mono text-[10px] text-text-3">{fence.length} nokta</span>}>
              <div className="flex flex-wrap gap-1.5">
                <Button onClick={() => setDrawing((d) => !d)} className={drawing ? "!bg-ok-bg !text-primary" : ""}><PencilLine size={14} /> {drawing ? "Çizim açık" : "Çiz"}</Button>
                <Button disabled={!fence.length} onClick={() => setFence((f) => f.slice(0, -1))}><Undo2 size={14} /> Geri al</Button>
                <Button disabled={!fence.length} onClick={() => { setFence([]); replacePlan(null); }}><Trash2 size={14} /> Temizle</Button>
              </div>
              {!isArea && <label className="mt-2 flex items-center gap-2 text-[12px]"><input type="checkbox" checked={plan.closed} onChange={(e) => setPlan((p) => ({ ...p, closed: e.target.checked }))} /> Kapalı halka</label>}
              <div className="mt-2 grid grid-cols-2 gap-2">
                <label><span className="label">Hat modu</span>
                  <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-1 text-[12px]" value={plan.line_mode}
                    onChange={(e) => {
                      const m = e.target.value as LineMode;
                      setPlan((p) => ({ ...p, line_mode: m, closed: m === "facility" || m === "area" ? (m === "area" || p.closed) : false }));
                      // a border watches a zone beyond the line: a deeper default strip
                      if (m === "border") setBand((b) => ({ ...b, out: Math.max(b.out, 150) }));
                    }}>
                    <option value="facility">Tesis</option><option value="border">Sınır</option><option value="highway">Otoyol</option>
                    <option value="area">Alan (poligon)</option>
                  </select></label>
                {!isArea && <label><span className="label">İzlenen taraf</span>
                  <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-1 text-[12px]" value={plan.watch_side} disabled={plan.line_mode !== "border"}
                    onChange={(e) => setPlan((p) => ({ ...p, watch_side: e.target.value as "left" | "right" }))}>
                    <option value="left">Sol</option><option value="right">Sağ</option>
                  </select></label>}
              </div>
                {fence.length > 0 && <p className="mt-2 text-[11px] text-text-3">◆ Haritada köşeyi sürükle: taşı · çift tık: sil · kenar ortasındaki kutu: köşe ekle.</p>}
                {isArea && <p className="mt-2 text-[11px] text-text-3">Alanın köşelerini haritaya tıklayarak çizin. Hedef, alanın kendisidir (otopark, avlu, meydan); kameralar alanın kenarına ve içine dizilir.</p>}
              </Panel>
              <Panel title="İster" right={<span className="font-mono text-[10px] text-text-3">her adım bunu okur</span>}>
                <div className="flex flex-col gap-3">
                  {isArea ? (
                    <label><span className="label">Hedef seviye</span>
                      <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" value={reqLevel} aria-label="İster seviyesi"
                        onChange={(e) => { setCustomTarget(false); setPlan((p) => ({ ...p, target_ppm: levelPpm(e.target.value as DoriLevel, isThermal) })); }}>
                        {levelsFor(isThermal).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                      </select></label>
                  ) : (
                    <label><span className="label">Hedef (px/m)</span>
                      <div className="mt-1 flex gap-1.5">
                        <select className="h-8 min-w-0 flex-1 rounded border border-line bg-bg-0 px-1 text-[12px]" aria-label="Çit hedefi"
                          value={!customTarget && (isThermal ? THERMAL_TARGETS : TARGETS).some(([v]) => v === plan.target_ppm) ? String(plan.target_ppm) : "custom"}
                          onChange={(e) => { const c = e.target.value === "custom"; setCustomTarget(c); if (!c) setPlan((p) => ({ ...p, target_ppm: Number(e.target.value) })); }}>
                          {(isThermal ? THERMAL_TARGETS : TARGETS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                          <option value="custom">Özel…</option>
                        </select>
                        {(customTarget || !(isThermal ? THERMAL_TARGETS : TARGETS).some(([v]) => v === plan.target_ppm)) && (
                          <input type="number" min={1} max={500} step={0.5} value={plan.target_ppm} aria-label="Özel hedef (px/m)"
                            onChange={(e) => { const v = Number(e.target.value); if (v >= 1 && v <= 500) setPlan((p) => ({ ...p, target_ppm: v })); }}
                            className="h-8 w-20 rounded border border-line bg-bg-0 px-1.5 text-right font-mono text-[12px]" />
                        )}
                      </div></label>
                  )}
                  <Slider label={isArea ? "Alanın en az" : plan.line_mode === "border" ? "İzlenen şeridin en az" : "Bandın en az"} unit="%" value={goal} min={50} max={99} step={1} onChange={setGoal} />
                  {!isArea && (
                    <>
                    <Slider label={plan.line_mode === "border" ? "İzlenen şerit derinliği" : plan.closed ? "Bant · dış" : "Bant (iki yan)"}
                      unit="m" value={band.out} min={5} max={plan.line_mode === "border" ? 1000 : 200} step={5}
                      onChange={(v) => setBand((b) => ({ out: v, in: b.in === b.out ? v : b.in }))} />
                    {plan.closed && <Slider label="Bant · iç (steril alan)" unit="m" value={band.in} min={0} max={200} step={5} onChange={(v) => setBand((b) => ({ ...b, in: v }))} />}
                    </>
                  )}
                </div>
                <p className="mt-2 text-[11px] text-text-3">◆ Plan, kapsama, optimizasyon, en az direk, model karşılaştırma ve uygunluk beyanı bu isteri kullanır.</p>
              </Panel>
              <Button primary className="w-full" disabled={fence.length < (isArea ? 3 : 2)} onClick={() => setStep("plan")}>Planlamaya geç</Button>
            </div>

            {/* ── 2. Planla: the layout ── */}
            <div className={step === "plan" ? "flex flex-col gap-3" : "hidden"}>
              <Panel title="Dizilim">
                {isArea ? (
                  <div className="flex flex-col gap-3">
                  <Slider label="Direk" unit="m" value={plan.mast_m} min={2} max={30} step={0.5} onChange={(v) => setPlan((p) => ({ ...p, mast_m: v }))} />
                  <div className="grid grid-cols-2 gap-2">
                    <label className="flex items-center gap-2 text-[12px]"><input type="checkbox" checked={area.interior} disabled={area.streetsOnly && !!city} onChange={(e) => setArea((a) => ({ ...a, interior: e.target.checked }))} /> Alan içine de direk</label>
                    <label><span className="label">Direk başına kamera</span>
                      <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-1 text-[12px]" value={area.perPole}
                        onChange={(e) => setArea((a) => ({ ...a, perPole: Number(e.target.value) }))}>
                        {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
                      </select></label>
                  </div>
                  <label><span className="label">Yedeklilik</span>
                    <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-1 text-[12px]" value={area.minCams} aria-label="En az kamera"
                      onChange={(e) => setArea((a) => ({ ...a, minCams: Number(e.target.value) }))}>
                      <option value={1}>Her hücre en az 1 kamera</option><option value={2}>En az 2 kamera (kritik alan)</option>
                      <option value={3}>En az 3 kamera</option>
                    </select></label>
                  <Slider label="Kamera tavanı" unit="" value={area.maxCams} min={10} max={500} step={10}
                    onChange={(v) => setArea((a) => ({ ...a, maxCams: v }))} />
                  <Slider label="Delik sınırı (0 = kapalı)" unit="m²" value={area.maxHole} min={0} max={1000} step={25}
                    onChange={(v) => setArea((a) => ({ ...a, maxHole: v }))} />
                  {area.maxHole > 0 && <p className="-mt-2 text-[11px] text-text-3">Hedef tuttuktan sonra {fmt(area.maxHole, 0)} m²'den büyük her açık kalan parçaya kamera eklenir.</p>}
                  <label className="flex items-center gap-2 text-[12px]" title={city ? "" : "Önce Doğrula adımındaki Şehir güvenliği panelinden sokakları indirin"}>
                    <input type="checkbox" checked={area.streetsOnly && !!city} disabled={!city} onChange={(e) => setArea((a) => ({ ...a, streetsOnly: e.target.checked }))} />
                    Yalnız sokaklar (hedef sokaklar, direkler sokakta)</label>
                    <Button primary className="w-full" disabled={job.running || fence.length < 3} onClick={runArea}><MapPin size={14} /> Alanı Kapla</Button>
                  </div>
                ) : (
                  <>
                    <div className="flex flex-col gap-3">
                    <Slider label="Direk" unit="m" value={plan.mast_m} min={2} max={30} step={0.5} onChange={(v) => setPlan((p) => ({ ...p, mast_m: v }))} />
                    <div>
                      <span className="label">Lens (aralık hesabı)</span>
                      <div className="mt-1 grid grid-cols-2 gap-1">
                        {([["min", `Geniş ${fmt(camera.focal_min_mm, 1)} mm`], ["max", `Tele ${fmt(camera.focal_max_mm, 1)} mm`]] as const).map(([k, l]) => (
                          <button key={k} type="button" onClick={() => setPlan((p) => ({ ...p, lens_mode: k }))}
                            className={`h-7 rounded text-[11px] font-semibold ${plan.lens_mode === k ? "bg-bg-6 text-primary" : "bg-bg-5 text-text-2"}`}>{l}</button>
                        ))}
                      </div>
                    </div>
                    {plan.line_mode === "facility" && (
                      <label className="flex items-center gap-2 text-[12px]" title="Her direğe ters yöne bakan ikinci kamera: yedekli, direk dibinde kör bölge kalmaz">
                        <input type="checkbox" checked={plan.back_to_back} onChange={(e) => setPlan((p) => ({ ...p, back_to_back: e.target.checked }))} />
                        Sırt sırta (direk başına 2 kamera)</label>
                    )}
                    </div>
                    <Button primary className="mt-3 w-full" disabled={job.running || fence.length < 2} onClick={runPlan}><MapPin size={14} /> Direkleri Diz</Button>
                  </>
                )}
                {fence.length < (isArea ? 3 : 2) && <p className="mt-2 text-[11px] text-text-3">◆ Önce 1. adımda {isArea ? "alanı" : "çit hattını"} çizin.</p>}
              </Panel>
              {planRes && (
                <Panel title="Plan">
                <div className="grid grid-cols-2 gap-1.5">
                  <Tile label="Direk" value={String(planRes.pole_count)} />
                  <Tile label="Kamera" value={String(planRes.camera_count)} />
                  {planRes.line_mode === "area" && areaRes ? (
                    <>
                      <Tile label="Alan" value={ha(areaRes.area_m2)} />
                      <Tile label={`Alan · ${levelName(areaRes.level, isThermal)}`} value={pct(areaRes.engine_frac)}
                        tone={areaRes.met ? "text-primary" : "text-warn"} />
                    </>
                  ) : (
                    <>
                      <Tile label="Çit uzunluğu" value={fmt(planRes.total_fence_length_m, 0)} unit="m" />
                      {/* one headline number: the engine's fence line once coverage ran, else the planner's estimate (labelled) */}
                      {planRes.line_mode === "border" ? (
                        // a border pole looks ACROSS the line: the line itself reads ~0 % by design, the watched strip is the result
                        coverage ? <Tile label={`İzlenen şerit · motor · ${coverage.target.label}`} value={pct(coverage.target.held)}
                          tone={coverage.target.held < 0.95 ? "text-warn" : "text-primary"} />
                          : <Tile label="İzlenen şerit" value="—" />
                      ) : coverage?.fence_line ? (
                        <Tile label={`Çit çizgisi · motor · ${coverage.target.label}`} value={pctCapped(coverage.fence_line.held, coverage.fence_line.uncovered_m)}
                          tone={coverage.fence_line.held < 0.95 ? "text-warn" : "text-primary"} />
                      ) : (
                        <Tile label="Çit · ön tahmin (planlayıcı)" value={pctCapped(planRes.coverage_percentage / 100, (100 - planRes.coverage_percentage) / 100 * planRes.total_fence_length_m)}
                          tone={planRes.coverage_percentage < 95 ? "text-warn" : "text-primary"} />
                      )}
                    </>
                  )}
                </div>
                {planRes.line_mode === "area" && areaRes && (
                  <div className="mt-2 text-[12px]">
                    <div className={areaRes.met ? "text-primary" : "text-warn"}>
                      {areaRes.met ? "●" : "▲"} Otoriter motor: alanda hedef seviye payı {pct(areaRes.engine_frac)} (hedef {pct(areaRes.goal_frac)})
                    </div>
                    <div className="mt-1 text-[11px] text-text-2">
                      {areaRes.holes_count ? `■ ${areaRes.holes_count} açık parça · en büyüğü ${fmt(areaRes.largest_hole_m2, 0)} m² · toplam ${fmt(areaRes.uncovered_m2, 0)} m²`
                        : "● Açık kalan parça yok"}
                    </div>
                    {areaRes.notes.map((n) => <div key={n} className="mt-1 text-[11px] text-text-3">◆ {n}</div>)}
                  </div>
                )}
                <div className="mt-2 font-mono text-[10px] text-text-3">{planRes.line_mode === "area" ? "direkler arası ort." : "ort. aralık"} {fmt(planRes.avg_spacing_m)} m · {fmt(planRes.estimated_bandwidth_mbps, 0)} Mbps · 30 gün {fmt(planRes.estimated_storage_30days_tb, 1)} TB</div>
                {planRes.line_mode === "border" && (
                  <div className="mt-2 text-[11px] text-text-3">◆ Sınır modunda kameralar hatta dik bakar; çizginin kendisi değil izlenen şerit puanlanır{coverage ? "" : " (Birleşik Kapsamayı hesaplayın)"}.</div>
                )}
                {planRes.line_mode !== "area" && planRes.line_mode !== "border" && (() => {
                  // one number for one plan: the engine's, at the selected target, once coverage ran
                  const fl = coverage?.fence_line;
                  const m = fl ? fl.uncovered_m : (100 - planRes.coverage_percentage) / 100 * planRes.total_fence_length_m;
                  if (m < 0.5) return null;
                  return (
                    <div className="mt-2 text-[11px] text-danger">
                      ■ Kapsanmayan çit ≈ {fmt(m, 0)} m
                      {fl ? ` · motor · ${coverage.target.label}`
                        : planRes.gaps.length > 0 ? ` · ${planRes.gaps.length} boşluk ≥ 4 m (haritada kırmızı)` : " · hepsi 4 m'den kısa parçalar"}
                      {!fl && <span className="text-text-3"> — ön tahmin; Birleşik Kapsama motorla doğrular</span>}
                    </div>
                  );
                })()}
                </Panel>
              )}
              <MixedPanel T={T} job={job} terrainId={info.terrain_id} useObstacles={useObstacles} models={models}
                value={mixedPtz} onChange={setMixedPtz} hasPlanWithPtz={!!planRes?.ptz.length} onLayer={setMixedLayer} onPlay={startPlayback}
                resetKey={planVer} patrol={patrol} onPatrol={setPatrol}
                level={reqLevel} levelLabel={levelName(reqLevel, isThermal)} band={bandArgs()} onPlan={replacePlan}
                ptzModelFor={async (n) => (await api.cameraFor(n, undefined, true)).camera} />
            </div>

            {/* ── 3. Doğrula: what the layout really delivers ── */}
            <div className={step === "verify" ? "flex flex-col gap-3" : "hidden"}>
              {!planRes && <Panel title="Doğrula"><p className="text-[12px] text-text-3">Önce 2. adımda planı oluşturun.</p></Panel>}
              {planRes && (
                <Panel title="Birleşik kapsama">
                <label className="mt-2 flex items-center gap-2 text-[12px]"><input type="checkbox" checked={showFov} onChange={(e) => setShowFov(e.target.checked)} /> Haritada FOV kamaları, odak ve PTZ devriyesi</label>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <label><span className="label">Katman</span>
                    <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-1 text-[12px]" value={covLayer} aria-label="Kapsama katmanı"
                      onChange={(e) => { const l = e.target.value as CoverageLayer; if (coverage) void runCoverage(l, covLevel); else setCovLayer(l); }}>
                      <option value="target">Hedef tutuyor mu</option><option value="dori">DORI seviyeleri</option><option value="overlap">Yedeklilik</option>
                    </select></label>
                  <label><span className="label">İncelenen seviye</span>
                    <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-1 text-[12px]" value={covLevel} aria-label="İncelenen seviye"
                      onChange={(e) => { const l = e.target.value as "plan" | DoriLevel; if (coverage) void runCoverage(covLayer, l); else setCovLevel(l); }}>
                      <option value="plan">{planRes.line_mode === "area" && areaRes ? `İster (${levelName(areaRes.level, isThermal)})` : `İster (${fmt(planRes.target_ppm, planRes.target_ppm % 1 ? 2 : 0)} px/m)`}</option>
                      {levelsFor(isThermal).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                    </select></label>
                </div>
                {planRes.ptz.length > 0 && (
                  <label className="mt-2 flex items-center gap-2 text-[12px]" title="PTZ'nin herhangi bir yön / eğim / zoom ile çevrilebildiği yer; sürekli ya da devriye kapsaması değildir">
                    <input type="checkbox" checked={ptzReach} onChange={(e) => setPtzReach(e.target.checked)} /> PTZ talep üzerine erişimini de hesapla</label>
                )}
                <Button className="mt-2 w-full" disabled={job.running} onClick={() => void runCoverage()}><Layers size={14} /> Birleşik Kapsamayı Hesapla</Button>
                {coverage && <CoverageSummary c={coverage} thermal={isThermal} />}
                </Panel>
              )}
              {planRes && (
                <NightPanel resetKey={planVer} T={T} job={job} terrainId={info.terrain_id} useObstacles={useObstacles} camera={camera}
                  hasLatLon={info.lat_center !== null} onLayer={setMixedLayer} onResult={setNight} band={bandArgs()} />
              )}
              {planRes && (
                <ApproachPanel resetKey={planVer} T={T} job={job} terrainId={info.terrain_id} useObstacles={useObstacles}
                  onLayer={setMixedLayer} onMarks={setApproachMarks} />
              )}
              <CityPanel resetKey={planVer} T={T} job={job} terrainId={info.terrain_id} useObstacles={useObstacles}
                hasLatLon={info.lat_center !== null} hasPlan={!!planRes} city={city} onCity={setCity} onLayer={setMixedLayer} />
            </div>

            {/* ── 4. İyileştir: the same requirement with fewer / better-aimed cameras ── */}
            <div className={step === "improve" ? "flex flex-col gap-3" : "hidden"}>
              {planRes ? (
                <Panel title="Dizilimi optimize et">
                  <p className="text-[11px] text-text-3">{planRes.line_mode === "area" ? "Alanda" : "Bantta"} {levelName(reqLevel, isThermal)} alanı büyütülür (ister).</p>
                  {planRes.line_mode !== "area" && <label className="mt-2 flex items-center gap-2 text-[12px]"><input type="checkbox" checked={slide} onChange={(e) => setSlide(e.target.checked)} /> Direkleri çit boyunca kaydır (arazi / bina)</label>}
                  <label className="mt-1 flex items-center gap-2 text-[12px]"><input type="checkbox" checked={panSearch} onChange={(e) => setPanSearch(e.target.checked)} /> Kamera yönünü de ara (±30°)</label>
                  <Button className="mt-2 w-full" disabled={job.running} onClick={() => job.run("Dizilim optimizasyonu", (o) => T.optimize({
                    terrain_id: info.terrain_id, use_obstacles: useObstacles, level: reqLevel, position_steps: slide ? 2 : 0,
                    ...(panSearch ? { pan_search_deg: 30 } : {}), ...bandArgs() }, o))
                    .then((r) => {
                      if (!r) return;
                      // not applied: the engine did not find the layout better, the plan (and its results) stay
                      if (r.applied) replacePlan(r.plan);       // replacePlan clears results of the old plan
                      setOptRes(r);
                      // the area tile shows the plan's engine share: keep it true to the optimised plan
                      if (r.applied && areaRes && r.plan.line_mode === "area") {
                        const f = r.after_m2 / Math.max(r.band_area_m2, 1);
                        setAreaRes(reqLevel === areaRes.level
                          ? { ...areaRes, plan: r.plan, engine_frac: f, met: f >= areaRes.goal_frac }
                          : { ...areaRes, plan: r.plan, notes: [...areaRes.notes.filter((n) => !n.startsWith("Optimizasyon")),
                              "Optimizasyon başka bir seviye için yapıldı; alan payı için Birleşik Kapsamayı yeniden hesaplayın."] });
                      }
                    })}>
                    <Play size={14} /> Odak / eğim{slide && planRes.line_mode !== "area" ? " / konum" : ""} optimize et
                  </Button>
                {optRes && (
                  <div className="mt-2 text-[12px]">
                    <div className={!optRes.applied ? "text-warn" : optRes.after_m2 > optRes.before_m2 ? "text-primary" : "text-text-2"}>
                      {optRes.applied ? "●" : "▲"} Otoriter motor, {planRes.line_mode === "area" ? "alan" : "bant"}: {ha(optRes.before_m2)} → <b>{ha(optRes.after_m2)}</b> ({pct(optRes.after_m2 / Math.max(optRes.band_area_m2, 1))})
                    </div>
                    {optRes.fence_line && (
                      <div className="mt-1 text-[11px] text-text-2">Çit çizgisi: kapsanmayan {fmt(optRes.fence_line.before_uncovered_m, 0)} m → {fmt(optRes.fence_line.after_uncovered_m, 0)} m · boşluk {optRes.fence_line.before_gaps} → {optRes.fence_line.after_gaps}</div>
                    )}
                    {optRes.applied && <div className="mt-1 text-[11px] text-text-3">{optRes.changed} direğin ayarı değişti{optRes.moved ? ` · ${optRes.moved} direk en çok ${fmt(optRes.max_move_m, 0)} m kaydı` : ""}</div>}
                    {optRes.notes.map((n) => <div key={n} className="mt-1 text-[11px] text-text-3">◆ {n}</div>)}
                  </div>
                )}
                </Panel>
              ) : <Panel title="Dizilimi optimize et"><p className="text-[12px] text-text-3">Önce 2. adımda planı oluşturun.</p></Panel>}
              {!isArea && (
                <Panel title="En az direk">
                  <p className="text-[11px] text-text-3">Bandın %{fmt(goal, goal % 1 ? 1 : 0)}'inde {levelName(reqLevel, isThermal)} tutan en az direk (ister).</p>
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
              )}
              {fence.length >= 2 && !isArea && (
                <ComparePanel T={T} job={job} terrainId={info.terrain_id} useObstacles={useObstacles} models={models} camera={camera}
                  planArgs={planParams()} prices={prices} level={reqLevel} levelLabel={levelName(reqLevel, isThermal)} goal={goal}
                  onUse={(p, name) => { replacePlan(p); void applyModel(name); }} />
              )}
              {isArea && <p className="text-[11px] text-text-3">◆ En az direk ve model karşılaştırma çit içindir; alan planı kamera sayısını kendisi en aza indirir.</p>}
            </div>

            {/* ── 5. Teslim: quantities, cost, scenarios ── */}
            <div className={step === "deliver" ? "flex flex-col gap-3" : "hidden"}>
              {!planRes && scenarios.length === 0 && <Panel title="Teslim"><p className="text-[12px] text-text-3">Önce 2. adımda planı oluşturun.</p></Panel>}
              {planRes && (
                <NetworkPanel resetKey={planVer} T={T} job={job} terrainId={info.terrain_id} useObstacles={useObstacles} prices={prices} onPrices={setPrices} onResult={setNetwork} />
              )}
              {(planRes || scenarios.length > 0) && (
                <ScenarioPanel T={T} terrainId={info.terrain_id} scenarios={scenarios} onChange={setScenarios} canSnapshot={!!planRes} />
              )}
              {planRes && <p className="text-[11px] text-text-3">◆ KML / DXF, plan uygunluk beyanı ve teklif paketi "Raporlar" sayfasında; beyan bu isterin %{fmt(goal, goal % 1 ? 1 : 0)} hedefine göre yazılır.</p>}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/** What coverage says, split the way a designer reads it: the fence line, the
 *  band by target (fixed / PTZ only / below / unseen), then DORI levels. */
function CoverageSummary({ c, thermal }: { c: CoverageResult; thermal: boolean }) {
  const t = c.target;
  const where = c.roi_kind === "area" ? `Alan (${ha(c.band_area_m2)})`
    : c.roi_kind === "side" ? `İzlenen taraf (${c.watch_side === "left" ? "sol" : "sağ"}), ${fmt(c.band_m, 0)} m derinlik (${ha(c.band_area_m2)})`
    : c.band_in_m != null && c.band_in_m !== c.band_m ? `Bant: dış ${fmt(c.band_m, 0)} m · iç ${fmt(c.band_in_m, 0)} m (${ha(c.band_area_m2)})`
    : `±${fmt(c.band_m, 0)} m çit bandı (${ha(c.band_area_m2)})`;
  const row = (label: string, v: string, tone = "") => (
    <div key={label} className="flex justify-between"><span className="text-text-3">{label}</span><span className={tone}>{v}</span></div>);
  return (
    <div className="mt-2 flex flex-col gap-0.5 font-mono text-[11px]">
      {c.fence_line && (
        <>
          <div className="text-text-2">Çit çizgisi · {t.label}</div>
          {row("sabit kameralarla", pct(c.fence_line.held), c.fence_line.held >= 0.95 ? "text-primary" : "text-warn")}
          {c.ptz_count > 0 && row("PTZ ile birlikte", pct(c.fence_line.with_ptz))}
        </>
      )}
      <div className="mt-1 text-text-2">{where} · {t.label}</div>
      {row("hedef tutuyor (sürekli)", pct(t.held), "text-primary")}
      {row("  …en az 2 kamerayla (yedekli)", pct(t.held_2))}
      {c.ptz_count > 0 && row("yalnız PTZ (aralıklı)", pct(t.ptz_only), "text-secondary")}
      {row("görülüyor, hedef altı", pct(t.below), "text-warn")}
      {row("hiç görülmüyor", pct(t.unseen), "text-danger")}
      <div className="mt-1 text-text-2">DORI · {c.ptz_count > 0 ? (c.band_share_reach ? "sürekli + devriye + talep üzerine" : "sürekli + devriye (yalnız PTZ)") : "sabit kameralar"}</div>
      {levelsFor(thermal).map(([k, l]) => row(l, c.ptz_count > 0
        ? `${pct(c.band_share[k])} + ${pct(c.band_share_ptz[k])}${c.band_share_reach ? ` + ${pct(c.band_share_reach[k])}` : ""}` : pct(c.band_share[k])))}
      {c.band_share_reach && <div className="mt-1 text-text-3">◆ Talep üzerine: PTZ çevrilirse ulaşır; kendiliğinden izlenmez.</div>}
      {c.ptz_count > 0 && <div className="mt-1 text-text-3">◆ {c.ptz_count} PTZ devriyesi; en uzun izlenmeyen süre {fmt(c.ptz_worst_revisit_s, 0)} sn</div>}
      {!c.measured && <div className="mt-1 text-warn">▲ prosedürel arazi — ölçüm değil</div>}
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
              onClick={async () => { if (await confirmDialog("Bölge diskten silinsin mi? Geri alınamaz.", { okLabel: "Sil", danger: true })) void T.deleteRegion(region).then(() => { setRegion(""); refreshRegions(); }); }}><Trash2 size={14} /></Button>
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
        <Save size={14} /> Bölgeyi Kaydet
      </Button>
      {msg && <div className="mt-1 text-[11px] text-text-2">{msg}</div>}
    </Panel>
  );
}
