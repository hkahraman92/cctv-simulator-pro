/**
 * Terrain page <-> project file mapping, kept pure so it can be tested without React.
 *
 * The page publishes `terrain_extra` (GUI-only keys; the core's project_io passes unknown keys through
 * untouched) and restores from it when a project opens. Every field read here is optional: a project
 * saved before a key existed loads exactly as it did, and a value of the wrong type is ignored.
 * Computed results are never persisted; they are recomputed.
 */
import type { CameraConfig } from "../core/api";
import type { CoverageLayer, DoriLevel, Scenario, TerrainLevel } from "../core/terrainApi";

export type LineMode = "facility" | "border" | "highway" | "area";
export type Pt = [number, number];

/** Highest DORI level a px/m target reaches, per level (EN 62676-4). Old area projects stored a level, not a px/m. */
export const LEVEL_PPM: Record<TerrainLevel, number> = { monitor: 12.5, detect: 25, observe: 62.5, recognize: 125, identify: 250 };

const LINE_MODES: readonly string[] = ["facility", "border", "highway", "area"];
const MAP_MODES: readonly string[] = ["hybrid", "satellite", "elevation"];
const COV_LAYERS: readonly string[] = ["target", "dori", "overlap"];
const LEVELS: readonly string[] = ["monitor", "detect", "observe", "recognize", "identify"];

export interface AreaSettings { interior: boolean; perPole: number; streetsOnly: boolean; maxHole: number; minCams: number; maxCams: number }

/** Everything the page publishes (see the table in the task report; computed results are not here). */
export interface ExtraState {
  camera: CameraConfig;
  tab: "single" | "perimeter";
  fence: Pt[];
  plan: { closed: boolean; line_mode: LineMode; watch_side: "left" | "right"; target_ppm: number; mast_m: number; lens_mode: "min" | "max"; back_to_back: boolean };
  band: { out: number; in: number };
  targetH: number;
  goal: number;
  reqLevel: TerrainLevel;
  area: AreaSettings;
  mixedPtz: { camera: CameraConfig | null; everyN: number; span: number };
  prices: unknown;
  scenarios: Scenario[];
  // added later: plain page choices
  mapMode: string;
  useObstacles: boolean;
  focal: number | undefined;
  patrol: { speed: number; dwell: number };
  ptzReach: boolean;
  slide: boolean;
  panSearch: boolean;
  covLayer: CoverageLayer;
  covLevel: "plan" | TerrainLevel;
  sweepLevel: TerrainLevel;
}

export function buildTerrainExtra(s: ExtraState): Record<string, unknown> {
  return {
    camera_name: s.camera.name, camera_model: s.camera.model_name, planner_mode: s.tab === "perimeter" ? "perimeter" : "single",
    fence_points: s.fence, fence_closed: s.plan.closed, line_mode: s.plan.line_mode, watch_side: s.plan.watch_side,
    target_ppm: s.plan.target_ppm, perimeter_mast_m: s.plan.mast_m, lens_mode: s.plan.lens_mode, back_to_back: s.plan.back_to_back,
    band_out_m: s.band.out, band_in_m: s.band.in, target_height_m: s.targetH,
    requirement: { target_ppm: s.plan.target_ppm, goal_pct: s.goal },
    // level / goal_pct repeated for readers of the older schema
    area_goal: { level: s.reqLevel, goal_pct: s.goal, interior: s.area.interior, per_pole: s.area.perPole, streets_only: s.area.streetsOnly,
      max_hole_m2: s.area.maxHole, min_cameras: s.area.minCams, max_cameras: s.area.maxCams },
    mixed_ptz: { camera: s.mixedPtz.camera?.model_name ?? "", every_n: s.mixedPtz.everyN, patrol_span_deg: s.mixedPtz.span },
    prices: s.prices, scenarios: s.scenarios,
    // plain page choices
    map_mode: s.mapMode, use_obstacles: s.useObstacles, focal_mm: s.focal ?? null,
    patrol: { speed_deg_s: s.patrol.speed, end_dwell_s: s.patrol.dwell }, ptz_reach: s.ptzReach,
    slide_poles: s.slide, pan_search: s.panSearch,
    coverage_view: { layer: s.covLayer, level: s.covLevel }, sweep_level: s.sweepLevel,
  };
}

export interface ExtraRead {
  fence?: Pt[];
  closed?: boolean;
  lineMode?: LineMode;
  watchSide?: "left" | "right";
  targetPpm?: number;
  mast?: number;
  /** always set: an absent key reads as the default, as it always did */
  lensMode: "min" | "max";
  backToBack: boolean;
  bandOut?: number;
  bandIn?: number;
  targetH?: number;
  goal?: number;
  plannerMode?: "perimeter";
  area: Partial<AreaSettings>;
  prices?: Record<string, unknown>;
  scenarios: Scenario[];
  cameraModel?: string;
  mapMode?: string;
  useObstacles?: boolean;
  focal?: number;
  patrol?: { speed: number; dwell: number };
  ptzReach?: boolean;
  slide?: boolean;
  panSearch?: boolean;
  covLayer?: CoverageLayer;
  covLevel?: "plan" | TerrainLevel;
  sweepLevel?: TerrainLevel;
}

const num = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const bool = (v: unknown): boolean | undefined => (typeof v === "boolean" ? v : undefined);
const rec = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});

export function readTerrainExtra(raw: unknown): ExtraRead {
  const x = rec(raw);
  const ag = rec(x.area_goal), rq = rec(x.requirement), pt = rec(x.patrol), cv = rec(x.coverage_view);
  const lineMode = LINE_MODES.includes(String(x.line_mode)) ? (x.line_mode as LineMode) : undefined;
  // an older area project kept its level apart from the fence target
  const legacyLevelPpm = typeof rq.target_ppm !== "number" && x.line_mode === "area" && typeof ag.level === "string" && ag.level in LEVEL_PPM
    ? LEVEL_PPM[ag.level as DoriLevel] : undefined;
  const goal = num(rq.goal_pct) ?? (x.line_mode === "area" ? num(ag.goal_pct) : undefined);
  const speed = num(pt.speed_deg_s), dwell = num(pt.end_dwell_s);
  return {
    fence: Array.isArray(x.fence_points) ? (x.fence_points as Pt[]) : undefined,
    closed: bool(x.fence_closed),
    lineMode,
    watchSide: x.watch_side === "left" || x.watch_side === "right" ? x.watch_side : undefined,
    targetPpm: legacyLevelPpm ?? num(x.target_ppm),
    mast: num(x.perimeter_mast_m),
    lensMode: x.lens_mode === "max" ? "max" : "min",
    backToBack: x.back_to_back === true,
    bandOut: num(x.band_out_m), bandIn: num(x.band_in_m),
    targetH: num(x.target_height_m),
    goal,
    plannerMode: x.planner_mode === "perimeter" ? "perimeter" : undefined,
    area: {
      interior: bool(ag.interior), perPole: num(ag.per_pole), streetsOnly: bool(ag.streets_only),
      maxHole: num(ag.max_hole_m2), minCams: num(ag.min_cameras), maxCams: num(ag.max_cameras),
    },
    prices: x.prices && typeof x.prices === "object" ? (x.prices as Record<string, unknown>) : undefined,
    scenarios: Array.isArray(x.scenarios) ? (x.scenarios as Scenario[]) : [],
    cameraModel: typeof x.camera_model === "string" && x.camera_model ? x.camera_model : undefined,
    mapMode: MAP_MODES.includes(String(x.map_mode)) ? String(x.map_mode) : undefined,
    useObstacles: bool(x.use_obstacles),
    focal: num(x.focal_mm),
    patrol: speed !== undefined && dwell !== undefined ? { speed, dwell } : undefined,
    ptzReach: bool(x.ptz_reach),
    slide: bool(x.slide_poles),
    panSearch: bool(x.pan_search),
    covLayer: COV_LAYERS.includes(String(cv.layer)) ? (cv.layer as CoverageLayer) : undefined,
    covLevel: cv.level === "plan" || (typeof cv.level === "string" && LEVELS.includes(cv.level)) ? (cv.level as "plan" | TerrainLevel) : undefined,
    sweepLevel: typeof x.sweep_level === "string" && LEVELS.includes(x.sweep_level) ? (x.sweep_level as TerrainLevel) : undefined,
  };
}

/** The saved single-camera placement (block-level `placements[0]`). */
export interface PlacementRead { x?: number; y?: number; mast?: number; pan?: number; tilt?: number; range?: number; lens?: "min" | "max" }
export function readPlacement(block: Record<string, unknown>): PlacementRead | null {
  const p = Array.isArray(block.placements) ? block.placements[0] : null;
  if (!p || typeof p !== "object") return null;
  const r = p as Record<string, unknown>;
  return { x: num(r.x_m), y: num(r.y_m), mast: num(r.mast_m), pan: num(r.pan_deg), tilt: num(r.tilt_deg), range: num(r.range_m),
    lens: r.lens_mode === "max" ? "max" : r.lens_mode === "min" ? "min" : undefined };
}

// ── Terrain page <-> project (CAD) camera ──────────────────────────────────────────────────────────────────

/** Terrain state is the core's convention (tilt negative-down); the CAD camera's tilt_deg is positive-down, 0..60. */
export const CAD_TILT_MIN = 0;
export const CAD_TILT_MAX = 60;
const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);
const nz = (v: number) => (v === 0 ? 0 : v);   // never -0

export interface TerrainCameraState {
  camera: CameraConfig;
  /** mast of the active tab: the single placement's, or the perimeter plan's */
  mast: number;
  /** core convention: negative = looking down */
  tilt: number;
  /** current focal, or undefined = the lens end the page is using */
  focal: number | undefined;
  lens: "min" | "max";
}

/** What "Proje kamerasına yaz" puts into the selected project camera. Position, heading and name stay the project's. */
export function terrainToProjectCamera(s: TerrainCameraState): { patch: Partial<CameraConfig>; focal: number } {
  const c = s.camera;
  const focal = s.focal ?? (s.lens === "max" ? c.focal_max_mm : c.focal_min_mm);
  return {
    patch: {
      model_name: c.model_name, sensor_name: c.sensor_name, resolution_name: c.resolution_name,
      focal_min_mm: c.focal_min_mm, focal_max_mm: c.focal_max_mm,
      pole_height_m: s.mast,
      tilt_deg: nz(clamp(-s.tilt, CAD_TILT_MIN, CAD_TILT_MAX)),
      ir_range_m: c.ir_range_m, min_lux: c.min_lux, effective_px_ratio: c.effective_px_ratio, camera_type: c.camera_type,
    },
    focal: clamp(focal, c.focal_min_mm, c.focal_max_mm),
  };
}

/** The reverse: a project camera seen as terrain mount values (mast, tilt in the core's convention, focal). */
export function projectCameraToTerrain(camera: CameraConfig, focal: number): { mast: number; tilt: number; focal: number } {
  return { mast: camera.pole_height_m, tilt: nz(-camera.tilt_deg), focal };
}

/** Keys of the patch that differ from the project camera (empty = nothing to write). */
export function changedKeys(current: CameraConfig, focalNow: number, w: { patch: Partial<CameraConfig>; focal: number }): string[] {
  const keys = (Object.keys(w.patch) as (keyof CameraConfig)[]).filter((k) => w.patch[k] !== current[k]);
  return Math.abs(w.focal - focalNow) > 1e-9 ? [...keys, "focal"] : keys;
}
