// Typed surface of core API v1 (cctv_simulator/service/methods.py).
// Field names mirror the Python dataclasses one to one. `null` in a number
// field means "unbounded" (the core sends non-finite floats as null).

import type { CallOptions, RpcClient } from "./rpc";

export const API_VERSION = "1.0";

export interface CameraConfig {
  name: string;
  model_name: string;
  sensor_name: string;
  resolution_name: string;
  focal_min_mm: number;
  focal_max_mm: number;
  pole_height_m: number;
  tilt_deg: number;
  target_height_m: number;
  pos_x_m: number;
  pos_y_m: number;
  heading_deg: number;
  ir_range_m: number;
  min_lux: number;
  effective_px_ratio: number;
  camera_type: string;
}

export interface PPMLevel {
  key: string;
  name: string;
  ppm: number;
  color: string;
  level_type: string;
}

export interface AnalysisRow {
  camera: string;
  mode: string;
  level: string;
  level_type: string;
  ppm: number;
  optical_distance_m: number;
  ground_distance_m: number;
  status: string;
}

export interface CameraOptics {
  focal_mm: number;
  sensor_w_mm: number;
  sensor_h_mm: number;
  nominal_res_w: number;
  nominal_res_h: number;
  res_w: number;
  hfov_deg: number;
  vfov_deg: number;
}

export interface OpticResult {
  camera: CameraConfig;
  mode: "min" | "max";
  focal_mm: number;
  hfov_deg: number;
  vfov_deg: number;
  bottom_ray_deg: number;
  top_ray_deg: number;
  dead_zone_m: number;
  max_geom_dist_m: number | null;
  vertical_drop_m: number;
  sensor_width_mm: number;
  res_width_px: number;
  ground_distances: Record<string, number | null>;
  rows: AnalysisRow[];
  recommendations: string[];
  dead_zone_area_m2: number;
  nominal_res_width_px: number;
  effective_px_ratio: number;
  optics: CameraOptics;
}

export interface Catalog {
  sensors: { name: string; width_mm: number; height_mm: number }[];
  resolutions: { name: string; width_px: number; height_px: number }[];
  levels: PPMLevel[];
}

export interface ModelSummary {
  name: string;
  sensor_name: string;
  resolution_name: string;
  focal_min_mm: number | null;
  focal_max_mm: number | null;
  ir_range_m: number | null;
  camera_type: string;
  is_ptz: boolean;
}

export interface SystemInfo {
  api_version: string;
  python: string;
  platform: string;
  frozen: boolean;
}

export function api(rpc: RpcClient) {
  return {
    info: () => rpc.call<SystemInfo>("system.info"),
    catalog: () => rpc.call<Catalog>("catalog.get"),
    library: () => rpc.call<{ count: number; models: ModelSummary[] }>("library.list"),
    model: (name: string) =>
      rpc.call<{ summary: ModelSummary; record: Record<string, unknown> }>("library.get", { name }),
    cameraFor: (name: string, base?: CameraConfig, withMounting = false) =>
      rpc.call<{ camera: CameraConfig; is_ptz: boolean }>("library.camera", {
        name,
        ...(base ? { base } : {}),
        with_mounting: withMounting,
      }),
    optics: (
      camera: Partial<CameraConfig>,
      sel: { mode?: "min" | "max"; focal_mm?: number; levels?: PPMLevel[] } = {},
      opts?: CallOptions,
    ) => rpc.call<OpticResult>("optics.calculate", { camera, ...sel }, opts),
  };
}

export type Api = ReturnType<typeof api>;

/** CameraConfig defaults — identical to cctv_simulator/models.py. */
export const DEFAULT_CAMERA: CameraConfig = {
  name: "Kamera 1",
  model_name: "Özel kamera",
  sensor_name: '1/2.8"',
  resolution_name: "4 MP (2K - 2688x1520)",
  focal_min_mm: 2.8,
  focal_max_mm: 12.0,
  pole_height_m: 4.0,
  tilt_deg: 15.0,
  target_height_m: 1.8,
  pos_x_m: 0,
  pos_y_m: 0,
  heading_deg: 0,
  ir_range_m: 30,
  min_lux: 0.01,
  effective_px_ratio: 1.0,
  camera_type: "",
};

/** EN 62676-4 DORI levels as keyed in models.DEFAULT_LEVELS. */
export const DORI = [
  { key: "Characterize", label: "Teşhis", ppm: 250, tone: "dori-identify" },
  { key: "Perceive", label: "Tanıma", ppm: 125, tone: "dori-recognize" },
  { key: "Discern", label: "Gözlem", ppm: 62.5, tone: "dori-observe" },
  { key: "Outline", label: "Algılama", ppm: 25, tone: "dori-detect" },
] as const;
