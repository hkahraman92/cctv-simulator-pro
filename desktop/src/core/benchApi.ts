// Typed surface of the bench part of core API v1 (service/bench_methods.py).

import type { CameraConfig } from "./api";
import type { Picture } from "./terrainApi";
import type { CallOptions, RpcClient } from "./rpc";

export interface BenchResult {
  focal_mm: number;
  hfov_deg: number;
  vfov_deg: number;
  top_ray_deg: number;
  bottom_ray_deg: number;
  dead_zone_m: number;
  dead_zone_area_m2: number;
  dead_zone_half_width_m: number;
  max_geom_dist_m: number | null;
  res_width_px: number;
  nominal_res_width_px: number;
  effective_px_ratio: number;
  achieved_ppm: number;
  required_ppm: number;
  distance_m: number;
  atmospheric_limit_m: number | null;
  reach_m: number;
  dori_reach_m: Record<"identify" | "recognize" | "observe" | "detect", number>;
  pass: boolean;
  reasons: string[];
}

export interface EdgeMeasure {
  k: number;
  mtf50_cy_px?: number;
  effective_mp?: number;
  image_size: [number, number];
  [key: string]: unknown;
}

export interface SceneFrame {
  frame: Picture;
  width: number;
  height: number;
  palette: string;
  horizon_v: number;
  grid: { u1: number; v1: number; u2: number; v2: number; major: boolean; center: boolean; type: string; label: string; lu: number | null; lv: number | null }[];
  target_head: { u: number; v: number; visible: boolean };
  ppm: number;
  target_px: number;
  target_pct_h: number;
  dori_name: string;
  dori_desc: string;
  is_thermal: boolean;
  hfov_deg: number;
  transmittance: number;
  visibility_km: number;
  weather: string;
}

export function benchApi(rpc: RpcClient) {
  return {
    bench: (p: { camera: CameraConfig; mode: "min" | "max"; req_ppm: number; distance_m: number; weather?: string }, opts?: CallOptions) =>
      rpc.call<BenchResult>("optics.bench", p, opts),
    measureEdge: (path: string, nominal_mp?: number) => rpc.call<EdgeMeasure>("iq.measure", { path, nominal_mp }),
    scene: (
      p: { camera: CameraConfig; focal_mm: number; width: number; height: number; target: string; target_dist_m: number; lateral_m: number; palette: string; weather?: string; show_dori: boolean; max_dist_m?: number; fast?: boolean },
      opts?: CallOptions,
    ) => rpc.call<SceneFrame>("scene.render", p, opts),
  };
}

export type BenchApi = ReturnType<typeof benchApi>;
