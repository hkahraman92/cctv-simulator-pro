// Typed surface of the terrain part of core API v1 (service/terrain_methods.py).

import type { CameraConfig } from "./api";
import type { CallOptions, RpcClient } from "./rpc";

export interface Picture {
  data: string; // data: URL
  width: number;
  height: number;
}

export interface TerrainInfo {
  terrain_id: string;
  label: string;
  name: string;
  source: string;
  region_id: string;
  width_m: number;
  height_m: number;
  cell_size_m: number;
  rows: number;
  cols: number;
  z_min_m: number;
  z_max_m: number;
  lat_center: number | null;
  lon_center: number | null;
  is_measured: boolean;
  source_note: string;
  has_photo: boolean;
  attribution: string;
  has_obstacles: boolean;
  obstacle_note: string;
  preset: string;
  file: string;
  save_error?: string;
  summary?: string;
  message?: string;
  estimated_share?: number;
}

export interface TerrainCatalog {
  presets: { id: string; label: string }[];
  map_sources: { id: string; label: string }[];
  qualities: { id: string; label: string }[];
  weather: { label: string; visibility_km: number }[];
  locations: { label: string; lat: number; lon: number; note: string }[];
}

export interface RegionInfo {
  region_id: string;
  name: string;
  label: string;
  created: string;
  width_m: number;
  is_measured: boolean;
  source: string;
  size_bytes: number;
  obstacle_note: string;
}

export type ZoneAreas = Record<"identify" | "recognize" | "observe" | "detect" | "occluded", number>;
export type LegendRow = [string, [number, number, number, number]];

export interface SingleViewshed {
  /** zones on Johnson thresholds (thermal camera): detect / observe / recognize = Johnson det / rec / id */
  thermal?: boolean;
  hfov_deg: number;
  vfov_deg: number;
  cam_ground_z_m: number;
  cam_total_z_m: number;
  max_range_m: number;
  optical_limit_m: number;
  atmospheric_limit_m: number;
  fov_area_m2: number;
  visible_area_m2: number;
  occluded_area_m2: number;
  coverage_pct: number;
  max_los_reach_m: number;
  /** farthest ground the frame holds, seen or hidden (a steep tilt frames the mast's foot only) */
  framed_reach_m: number;
  zone_areas_m2: ZoneAreas;
  profile: { dist_m: number[]; terrain_m: number[]; ray_m: number[]; visible: boolean[] };
  overlay: Picture;
  legend: LegendRow[];
  warnings: string[];
}

export interface PlacedCamera {
  pole_id: number;
  x_m: number;
  y_m: number;
  ground_z_m: number;
  mast_height_m: number;
  pan_deg: number;
  tilt_deg: number;
  focal_mm: number;
  hfov_deg: number;
  vfov_deg: number;
  effective_range_m: number;
  dead_zone_m: number;
  camera_model: string;
  patrol_span_deg: number;
}

export interface FenceGap {
  start_x: number;
  start_y: number;
  end_x: number;
  end_y: number;
  length_m: number;
  reason: string;
}

export interface PlanResult {
  poles: PlacedCamera[];
  ptz: PlacedCamera[];
  gaps: FenceGap[];
  total_fence_length_m: number;
  camera_count: number;
  pole_count: number;
  target_ppm: number;
  avg_spacing_m: number;
  coverage_percentage: number;
  estimated_bandwidth_mbps: number;
  estimated_storage_30days_tb: number;
  line_mode: string;
  watch_side: string;
  ptz_count?: number;
  /** e.g. a PTZ chosen as the main camera is planned as a fixed lens */
  warnings?: string[];
}

export type CoverageLayer = "target" | "dori" | "overlap";
type Shares = Record<"identify" | "recognize" | "observe" | "detect", number>;

export interface CoverageResult {
  camera_count: number;
  fov_area_m2: number;
  visible_area_m2: number;
  occluded_area_m2: number;
  coverage_pct: number;
  overlap_area_m2: number;
  single_cover_area_m2: number;
  zone_areas_m2: ZoneAreas;
  band_m: number;
  /** inner width of a closed fence's band; null = same as band_m */
  band_in_m: number | null;
  band_area_m2: number;
  /** fixed cameras = continuous coverage */
  band_share: Shares;
  /** reached ONLY by a PTZ patrol (intermittent), on top of band_share */
  band_share_ptz: Shares;
  ptz_count: number;
  ptz_worst_revisit_s: number;
  /** the protected area split by the target: held (fixed) / only a PTZ / seen below it / not seen */
  /** held_2: the target held by >= 2 fixed cameras (redundant) */
  target: { label: string; ppm: number; held: number; ptz_only: number; below: number; unseen: number; held_2: number };
  /** the fence line itself (null for an area plan) */
  /** uncovered_m: metres of fence below the SELECTED target */
  fence_line: { held: number; with_ptz: number; detect: number; uncovered_m: number } | null;
  /** "area": the drawn polygon, "band": the ±band_m fence band, "side": a border's watched side, band_m deep */
  roi_kind: "area" | "band" | "side";
  watch_side?: "left" | "right";
  overlay: Picture;
  legend: LegendRow[];
  measured: boolean;
}

export type DoriLevel = "detect" | "observe" | "recognize" | "identify";

export interface AreaPlanResult {
  met: boolean;
  level: DoriLevel;
  goal_frac: number;
  area_m2: number;
  /** authoritative multi-camera engine share of the polygon at >= level */
  engine_frac: number;
  estimate_frac: number;
  sites_considered: number;
  sites_skipped_obstacle: number;
  curve: { cameras: number; poles: number; frac: number }[];
  notes: string[];
  holes_count: number;
  largest_hole_m2: number;
  uncovered_m2: number;
  hole_cameras: number;
  warnings: string[];
  plan: PlanResult;
}

export interface ApproachResult {
  breach: boolean;
  /** first-seen walking distance to the target; 0 on a breach; null = no walkable approach */
  warning_m: number | null;
  warning_s: Record<string, number | null>;
  response_s: number | null;
  sufficient: Record<string, boolean>;
  breakout_xy: [number, number] | null;
  dash_path: [number, number][];
  breach_points: [number, number][];
  sectors: { x0: number; y0: number; x1: number; y1: number; warning_m: number | null }[];
  covert_area_m2: number;
  depth_m: number;
  notes: string[];
  overlay: Picture;
  legend: LegendRow[];
  measured: boolean;
}

export interface AreaParams {
  fence: [number, number][];
  level: DoriLevel;
  goal_frac: number;
  mast_m: number;
  interior: boolean;
  max_per_pole: number;
  weather?: string;
  /** target = the polygon's streets, poles stand on streets (needs city.osm first) */
  streets_only?: boolean;
  /** close every uncovered hole larger than this (m2) after the goal is met */
  max_hole_m2?: number;
  /** redundancy: a cell counts when this many cameras hold the level */
  min_cameras?: number;
  /** camera cap of the search (service default 120) */
  max_cameras?: number;
  ptz_camera?: CameraConfig;
  ptz_every_n?: number;
  ptz_patrol_span_deg?: number;
}

export interface CityPlace { name: string; kind: string; x_m: number; y_m: number }

export interface CityInfo {
  streets: number;
  residential: number;
  pois: CityPlace[];
  street_area_m2: Record<string, number>;
  residential_area_m2: number;
  attribution: string;
  overlay: Picture;
  legend: LegendRow[];
}

export interface CityAnalysis {
  level: DoriLevel;
  street_area_m2: number;
  share_by_level: Record<DoriLevel, number>;
  by_class: { label: string; area_m2: number; share: number }[];
  face_share: number;
  pois: (CityPlace & { best: DoriLevel | null; face: boolean })[];
  privacy: { label: string; seen_m2: number; private_m2: number; share: number; flagged: boolean }[];
  private_seen_m2: number;
  notes: string[];
  overlay: Picture;
  legend: LegendRow[];
  measured: boolean;
}

export interface PoleTrial {
  spacing_m: number;
  fixed_poles: number;
  total_cameras: number;
  planner_frac: number;
  optimised_frac: number;
  meets: boolean;
}

export interface PoleSearch {
  met: boolean;
  goal_frac: number;
  roi_area_m2: number;
  notes: string[];
  base: PoleTrial;
  best: PoleTrial | null;
  trials: PoleTrial[];
  plan: PlanResult | null;
}

export interface FocalChoice {
  focal_mm: number;
  tilt_deg: number;
  pan_deg: number | null;
  hfov_deg: number;
  score: number;
  areas_m2: Partial<Record<"identify" | "recognize" | "observe" | "detect", number>>;
}

export interface FocalSweep {
  curve: FocalChoice[];
  best: FocalChoice;
  wide: FocalChoice;
  tele: FocalChoice;
  level: string;
  reach_m: number;
  /** the optimum through the authoritative engine (the curve is the mount-raster estimate) */
  verified: EngineView;
  /** the current setting through the same engine; null when it was not sent */
  current: EngineView | null;
  gain_m2: number | null;
  marginal: boolean;
  /** false: applying would not help (or would blind the camera); note says why */
  worth_applying: boolean;
  note: string;
}

export interface EngineView {
  focal_mm: number;
  tilt_deg: number;
  pan_deg: number;
  area_m2: number;
  visible_area_m2: number;
  max_los_reach_m: number;
}

export interface OptimizeResult {
  before_m2: number;
  after_m2: number;
  band_area_m2: number;
  changed: number;
  moved: number;
  max_move_m: number;
  notes: string[];
  /** false: the engine did not find the new layout better; the plan was left as it was */
  applied: boolean;
  /** poles put back because their new setting opened a gap on the fence line */
  reverted: number;
  /** null for an area or border plan (no fence line to keep) */
  fence_line: { before_pct: number; after_pct: number; before_uncovered_m: number; after_uncovered_m: number; before_gaps: number; after_gaps: number } | null;
  plan: PlanResult;
}

export interface PlanParams {
  fence: [number, number][];
  closed: boolean;
  target_ppm: number;
  mast_m: number;
  /** "area" is only meaningful for area.plan; the fence planners refuse it */
  line_mode: "facility" | "border" | "highway" | "area";
  watch_side: "left" | "right";
  weather?: string;
  spacing_m?: number;
  /** lens end the planner spaces poles for: "min" wide, "max" tele */
  lens_mode?: "min" | "max";
  /** facility: a second camera per pole looking back along the fence */
  back_to_back?: boolean;
  ptz_camera?: CameraConfig;
  ptz_every_n?: number;
  ptz_patrol_span_deg?: number;
}

/** One PTZ's live-sweep data (ptz.playback): pictures once, the shell turns the cone. */
export interface PlaybackPtz {
  label: string;
  x_m: number;
  y_m: number;
  mast_m: number;
  focal_mm: number;
  tilt_deg: number;
  mode: "rotation" | "sector";
  start_pan_deg: number;
  span_deg: number;
  speed_deg_s: number;
  end_dwell_s: number;
  period_s: number;
  hfov_deg: number;
  reach_m: number;
  visible_area_m2: number;
  /** x0, y0, x1, y1 (m, local frame) the pictures span */
  box_m: [number, number, number, number];
  lit: Picture;
  shadow: Picture;
}

export interface SweepResult {
  period_s: number;
  focal_mm: number;
  hfov_deg: number;
  min_dwell_s: number;
  seen_area_m2: number;
  glimpse_area_m2: number;
  continuous_area_m2: number;
  occluded_area_m2: number;
  mean_revisit_s: number | null;
  worst_revisit_s: number | null;
  truncated: boolean;
  zone_areas_m2: ZoneAreas;
  overlay: Picture;
  legend: LegendRow[];
}

export interface TourResult {
  tour_period_s: number;
  active_dwell_s: number;
  slew_total_s: number;
  zoom_total_s: number;
  settle_total_s: number;
  mean_revisit_s: number | null;
  worst_revisit_s: number | null;
  covered_area_m2: number;
  preset_labels: string[];
  zone_areas_m2: ZoneAreas;
  overlay: Picture;
  legend: LegendRow[];
}

export interface CueResult {
  target_ppm: number;
  reachable_area_m2: number;
  mean_response_s: number | null;
  median_response_s: number | null;
  thermal: boolean;
  p90_response_s: number | null;
  worst_response_s: number | null;
  unreachable: { reason: string; area_m2: number }[];
  overlay: Picture;
  legend: LegendRow[];
}

export interface MixedResult {
  fixed_poles: number;
  ptz_count: number;
  continuous_detect_m2: number;
  intermittent_detect_m2: number;
  worst_intermittent_revisit_s: number | null;
  detected_area_m2: number;
  identifiable_area_m2: number;
  identifiable_pct: number;
  median_d2i_s: number | null;
  p90_d2i_s: number | null;
  worst_d2i_s: number | null;
  cue_target_ppm: number;
  ptz_detect_floor_ppm: number;
  ptz_camera_name: string;
  ptz_thermal: boolean;
  overlay: Picture;
  legend: LegendRow[];
}

export type LevelShare = Record<"identify" | "recognize" | "observe" | "detect", number>;

export interface LightCondition {
  when: string; sun_el_deg: number; sun_az_deg: number; moon_el_deg: number; moon_az_deg: number;
  moon_fraction: number; lux: number; period: "gündüz" | "alacakaranlık" | "gece";
}
export interface NightMode { mode: "termal" | "pasif" | "ir" | "beyaz" | "yok"; range_cap_m: number | null; text: string }
export interface NightParams {
  datetime: string; lat?: number; lon?: number; utc_offset_h?: number; cloud_cover?: number; sky_glow_lux?: number;
  ir_on?: boolean; white_light_range_m?: number; camera?: CameraConfig;
}
export interface NightCoverage {
  condition: LightCondition; mode: NightMode; band_m: number; band_area_m2: number; roi_kind?: "area" | "band";
  day_band_share: LevelShare; night_band_share: LevelShare; overlay?: Picture; legend: LegendRow[]; measured: boolean;
}
export interface NightTimeline { lat: number; lon: number; steps: (LightCondition & { mode?: NightMode["mode"]; range_cap_m?: number | null })[] }

/** One design alternative, as scenario.snapshot returns it (saved in the project file). */
export interface Scenario {
  label: string; created: string; camera_model: string; ptz_model: string; poles: number; fixed_cameras: number;
  ptz_cameras: number; fence_m: number; avg_spacing_m: number; fence_coverage_pct: number; gap_m: number;
  target_ppm: number; line_mode: string; bitrate_mbps: number | null; storage_tb: number | null; switches: number | null;
  copper_m: number | null; fibre_m: number | null; poe_w: number | null; cost_total: number | null; currency: string;
  unpriced: string[]; band_share: Record<string, number | null>; night_band_share: Record<string, number> | null;
  night_mode: string; measured: boolean | null;
}

type T = { terrain_id: string; use_obstacles?: boolean };

export function terrainApi(rpc: RpcClient) {
  return {
    catalog: () => rpc.call<TerrainCatalog>("terrain.catalog"),
    procedural: (preset: string, width_m = 2000, grid = 200) =>
      rpc.call<TerrainInfo>("terrain.procedural", { preset, width_m, grid }),
    geotiff: (path: string) => rpc.call<TerrainInfo>("terrain.geotiff", { path }),
    download: (
      p: { lat: number; lon: number; width_m: number; source: string; quality: string; grid: number; label?: string; save: boolean; save_name?: string },
      opts?: CallOptions,
    ) => rpc.call<TerrainInfo>("terrain.download", p, { timeoutMs: 0, ...opts }),
    image: (t: T & { mode: string; photo_opacity?: number; max_px?: number }) =>
      rpc.call<Picture & { width_m: number; height_m: number; procedural_texture: boolean }>("terrain.image", t),
    regions: () => rpc.call<{ regions: RegionInfo[] }>("regions.list"),
    openRegion: (region_id: string) => rpc.call<TerrainInfo>("regions.open", { region_id }),
    saveRegion: (terrain_id: string, name: string) =>
      rpc.call<{ region_id: string; size_bytes: number }>("regions.save", { terrain_id, name }),
    deleteRegion: (region_id: string) => rpc.call<{ deleted: string }>("regions.delete", { region_id }),
    osm: (terrain_id: string, opts?: CallOptions) =>
      rpc.call<TerrainInfo>("surface.osm", { terrain_id }, { timeoutMs: 0, ...opts }),
    surfaceRaster: (terrain_id: string, path: string, mode: "auto" | "ndsm" | "dsm") =>
      rpc.call<TerrainInfo>("surface.raster", { terrain_id, path, mode }, { timeoutMs: 0 }),
    viewshed: (
      p: T & { camera: CameraConfig; x_m: number; y_m: number; mast_m: number; pan_deg: number; tilt_deg: number; range_m: number; lens_mode: "min" | "max"; focal_mm?: number; weather?: string; max_px?: number; target_height_m?: number },
      opts?: CallOptions,
    ) => rpc.call<SingleViewshed>("viewshed.single", p, opts),
    plan: (p: T & PlanParams & { camera: CameraConfig }) => rpc.call<PlanResult>("perimeter.plan", p),
    /** analysis settings kept with the terrain (target height: a person on each cell) */
    settings: (terrain_id: string, s: { target_height_m?: number }) =>
      rpc.call<{ target_height_m: number; changed: boolean }>("terrain.settings", { terrain_id, ...s }),
    coverage: (p: T & { layer?: CoverageLayer; level?: "plan" | DoriLevel; band_m?: number; band_in_m?: number; max_px?: number; ptz_speed_deg_s?: number; ptz_end_dwell_s?: number }, opts?: CallOptions) =>
      rpc.call<CoverageResult>("perimeter.coverage", p, { timeoutMs: 0, ...opts }),
    approach: (p: T & { depth_m: number; response_s?: number; speeds?: Record<string, number>; max_px?: number }, opts?: CallOptions) =>
      rpc.call<ApproachResult>("approach.analyse", p, { timeoutMs: 0, ...opts }),
    cityOsm: (terrain_id: string, opts?: CallOptions) =>
      rpc.call<CityInfo>("city.osm", { terrain_id, max_px: 1200 }, { timeoutMs: 0, ...opts }),
    cityAnalyse: (p: T & { level: DoriLevel; flag_share?: number; max_px?: number }, opts?: CallOptions) =>
      rpc.call<CityAnalysis>("city.analyse", p, { timeoutMs: 0, ...opts }),
    areaPlan: (p: T & AreaParams & { camera: CameraConfig }, opts?: CallOptions) =>
      rpc.call<AreaPlanResult>("area.plan", p, { timeoutMs: 0, ...opts }),
    optimize: (p: T & { level: string; position_steps: number; pan_search_deg?: number; n_focals?: number; band_m?: number; band_in_m?: number }, opts?: CallOptions) =>
      rpc.call<OptimizeResult>(
        "perimeter.optimize", p, { timeoutMs: 0, ...opts }),
    minimizePoles: (p: T & PlanParams & { camera: CameraConfig; goal_frac: number; level: string; position_steps?: number; band_m?: number; band_in_m?: number }, opts?: CallOptions) =>
      rpc.call<PoleSearch>("perimeter.minimize_poles", p, { timeoutMs: 0, ...opts }),
    focalSweep: (
      p: T & { camera: CameraConfig; x_m: number; y_m: number; mast_m: number; pan_deg: number; level: string; pan_search_deg?: number; weather?: string; n_focals?: number; range_m?: number; current_tilt_deg?: number; current_focal_mm?: number },
      opts?: CallOptions,
    ) => rpc.call<FocalSweep>("focal.sweep", p, { timeoutMs: 0, ...opts }),
    ptzSweep: (
      p: T & { camera: CameraConfig; x_m: number; y_m: number; mast_m: number; mode: "rotation" | "sector"; start_pan_deg?: number; end_pan_deg?: number; speed_deg_s: number; end_dwell_s?: number; tilt_deg: number; focal_mm?: number; range_m: number; weather?: string; layer?: "revisit" | "dori"; max_px?: number },
      opts?: CallOptions,
    ) => rpc.call<SweepResult>("ptz.sweep", p, { timeoutMs: 0, ...opts }),
    ptzCue: (
      p: T & { camera: CameraConfig; x_m: number; y_m: number; mast_m: number; home_pan_deg: number; home_tilt_deg: number; target_ppm?: number; range_m: number; weather?: string; max_px?: number; slew_accel_deg_s2?: number },
      opts?: CallOptions,
    ) => rpc.call<CueResult>("ptz.cue", p, { timeoutMs: 0, ...opts }),
    ptzTour: (
      p: T & { camera: CameraConfig; x_m: number; y_m: number; mast_m: number; presets: { name: string; pan_deg: number; tilt_deg: number; focal_mm?: number; dwell_s: number }[]; range_m: number; weather?: string; max_px?: number; slew_accel_deg_s2?: number },
      opts?: CallOptions,
    ) => rpc.call<TourResult>("ptz.tour", p, { timeoutMs: 0, ...opts }),
    network: (p: T & { switch_ports: number; poe_budget_w: number; max_copper_m: number; retention_days: number; prices: object; autonomy_h?: number; night_factor?: number; fibre_ring?: boolean }) =>
      rpc.call<import("../components/PlanningPanels").NetworkResult>("network.plan", p, { timeoutMs: 0 }),
    compareModels: (p: T & Record<string, unknown>, opts?: CallOptions) =>
      rpc.call<{ options: { model_name: string; met: boolean; poles: number; band_frac: number; seconds: number; switches: number; copper_m: number; storage_tb: number; cost: import("../components/PlanningPanels").NetworkResult["cost"] | null }[] }>(
        "perimeter.compare_models", p, { timeoutMs: 0, ...opts }),
    compareUse: (p: T & Record<string, unknown>) => rpc.call<{ plan: PlanResult; camera: CameraConfig }>("perimeter.compare_use", p),
    ptzPlayback: (p: T & { source?: "plan" | "single"; speed_deg_s: number; end_dwell_s?: number; max_px?: number } & Record<string, unknown>, opts?: CallOptions) =>
      rpc.call<{ ptz: PlaybackPtz[]; camera_name: string }>("ptz.playback", p, { timeoutMs: 0, ...opts }),
    mixed: (p: T & { speed_deg_s: number; end_dwell_s?: number; layer?: "fused" | "cue"; cue_target_ppm?: number; max_px?: number }, opts?: CallOptions) =>
      rpc.call<MixedResult>("perimeter.mixed", p, { timeoutMs: 0, ...opts }),
    nightCoverage: (p: T & NightParams & { band_m?: number; band_in_m?: number; max_px?: number }, opts?: CallOptions) =>
      rpc.call<NightCoverage>("night.coverage", p, { timeoutMs: 0, ...opts }),
    nightTimeline: (p: Partial<T> & Omit<NightParams, "datetime"> & { date: string; step_min?: number }) =>
      rpc.call<NightTimeline>("night.timeline", p),
    snapshot: (p: T & { label: string }) => rpc.call<{ scenario: Scenario }>("scenario.snapshot", p),
    compareScenarios: (scenarios: Scenario[]) =>
      rpc.call<{ rows: string[][]; best: Record<string, number | null> }>("scenario.compare", { scenarios }),
    exportSite: (p: T & { path: string; real_world?: boolean; name?: string }) =>
      rpc.call<{ path: string; exact_georef: boolean; has_latlon: boolean; with_network: boolean; with_coverage: boolean }>("export.site", p, { timeoutMs: 0 }),
    exportShare: (p: T & { path: string; title: string; scenarios?: Scenario[] }) =>
      rpc.call<{ path: string; bytes: number }>("export.share_html", p, { timeoutMs: 0 }),
    exportPackage: (p: T & { path: string; project_name: string; bidder?: string; spec_result?: object; scenarios?: Scenario[] }, opts?: CallOptions) =>
      rpc.call<{ path: string; files: string[]; skipped: string[] }>("export.package", p, { timeoutMs: 0, ...opts }),
  };
}

export type TerrainApi = ReturnType<typeof terrainApi>;
