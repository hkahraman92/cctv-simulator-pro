// Typed surface of the bitrate part of core API v1 (service/bitrate_methods.py).
// Every number is an estimate computed by the core (cctv_simulator/bitrate.py).

import type { CameraConfig } from "./api";
import type { CallOptions, RpcClient } from "./rpc";

export type Codec = "h264" | "h265" | "smart_h265" | "mjpeg";
export type Scene = "low" | "medium" | "high";
export type RecordMode = "continuous" | "motion" | "mixed";

export interface BitrateOptions {
  fps?: number;
  codec?: Codec;
  scene?: Scene;
  gop_s?: number;
  mode?: RecordMode;
  motion_share?: number;
}

export interface BitrateCameraRequest extends BitrateOptions {
  /** library model name (a new row) or ... */
  model?: string;
  /** ... a camera config (a project camera) */
  camera?: CameraConfig;
  label?: string;
}

export interface BitrateRequest {
  cameras: BitrateCameraRequest[];
  defaults?: BitrateOptions;
  retention_days?: number;
  raid_overhead?: number;
  night_factor?: number;
  night_share?: number;
}

export interface BitrateRow {
  label: string;
  model: string;
  pixels: number;
  fps_used: number;
  fps_clamped: boolean;
  codec: Codec;
  scene: Scene;
  mode: RecordMode;
  day_mbps: number;
  night_mbps: number;
  peak_mbps: number;
  avg_record_mbps: number;
  storage_tb_per_30d: number;
  retention_days: number;
  storage_tb: number;
  recommended_cbr_mbps: number;
  recommended_vbr_max_mbps: number;
  thermal: boolean;
  supported_codecs: Codec[];
  notes: string[];
  warnings: string[];
  is_estimate: boolean;
}

export interface BitrateResult {
  rows: BitrateRow[];
  totals: { avg_record_mbps: number; peak_mbps: number; storage_tb: number; retention_days: number };
  assumptions: string[];
}

export function bitrateApi(rpc: RpcClient) {
  return {
    estimate: (p: BitrateRequest, opts?: CallOptions) => rpc.call<BitrateResult>("bitrate.estimate", p, opts),
  };
}

export type BitrateApi = ReturnType<typeof bitrateApi>;
