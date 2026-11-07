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
}

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
  band_area_m2: number;
  band_share: Record<"identify" | "recognize" | "observe" | "detect", number>;
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
}

export interface PlanParams {
  fence: [number, number][];
  closed: boolean;
  target_ppm: number;
  mast_m: number;
  line_mode: "facility" | "border" | "highway";
  watch_side: "left" | "right";
  weather?: string;
  spacing_m?: number;
  ptz_camera?: CameraConfig;
  ptz_every_n?: number;
  ptz_patrol_span_deg?: number;
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

export interface CueResult {
  target_ppm: number;
  reachable_area_m2: number;
  mean_response_s: number | null;
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
  overlay: Picture;
  legend: LegendRow[];
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
      p: T & { camera: CameraConfig; x_m: number; y_m: number; mast_m: number; pan_deg: number; tilt_deg: number; range_m: number; lens_mode: "min" | "max"; focal_mm?: number; weather?: string; max_px?: number },
      opts?: CallOptions,
    ) => rpc.call<SingleViewshed>("viewshed.single", p, opts),
    plan: (p: T & PlanParams & { camera: CameraConfig }) => rpc.call<PlanResult>("perimeter.plan", p),
    coverage: (p: T & { layer?: "dori" | "overlap"; band_m?: number; max_px?: number }, opts?: CallOptions) =>
      rpc.call<CoverageResult>("perimeter.coverage", p, { timeoutMs: 0, ...opts }),
    minimizePoles: (p: T & PlanParams & { camera: CameraConfig; goal_frac: number; level: string }, opts?: CallOptions) =>
      rpc.call<PoleSearch>("perimeter.minimize_poles", p, { timeoutMs: 0, ...opts }),
    focalSweep: (
      p: T & { camera: CameraConfig; x_m: number; y_m: number; mast_m: number; pan_deg: number; level: string; pan_search_deg?: number; weather?: string; n_focals?: number },
      opts?: CallOptions,
    ) => rpc.call<FocalSweep>("focal.sweep", p, { timeoutMs: 0, ...opts }),
    ptzSweep: (
      p: T & { camera: CameraConfig; x_m: number; y_m: number; mast_m: number; mode: "rotation" | "sector"; start_pan_deg?: number; end_pan_deg?: number; speed_deg_s: number; end_dwell_s?: number; tilt_deg: number; focal_mm?: number; range_m: number; weather?: string; layer?: "revisit" | "dori"; max_px?: number },
      opts?: CallOptions,
    ) => rpc.call<SweepResult>("ptz.sweep", p, { timeoutMs: 0, ...opts }),
    ptzCue: (
      p: T & { camera: CameraConfig; x_m: number; y_m: number; mast_m: number; home_pan_deg: number; home_tilt_deg: number; target_ppm: number; range_m: number; weather?: string; max_px?: number },
      opts?: CallOptions,
    ) => rpc.call<CueResult>("ptz.cue", p, { timeoutMs: 0, ...opts }),
    mixed: (p: T & { speed_deg_s: number; layer?: "fused" | "cue"; cue_target_ppm?: number; max_px?: number }, opts?: CallOptions) =>
      rpc.call<MixedResult>("perimeter.mixed", p, { timeoutMs: 0, ...opts }),
  };
}

export type TerrainApi = ReturnType<typeof terrainApi>;
