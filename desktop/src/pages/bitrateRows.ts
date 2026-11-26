// Pure helpers of the Bitrate page: rows and defaults -> core request. No physics here (the core owns it).

import type { CameraConfig } from "../core/api";
import type { BitrateCameraRequest, BitrateOptions, BitrateRequest, Codec, RecordMode, Scene } from "../core/bitrateApi";

export interface BitrateDefaults {
  codec: Codec;
  fps: number;
  scene: Scene;
  gop_s: number;
  mode: RecordMode;
  /** percent of the day recorded when the mode is not continuous */
  motion_pct: number;
  retention_days: number;
  /** percent */
  raid_pct: number;
}

export const DEFAULT_BITRATE: BitrateDefaults = {
  codec: "h265", fps: 25, scene: "medium", gop_s: 2, mode: "continuous", motion_pct: 30, retention_days: 30, raid_pct: 15,
};

/** One table row: a project camera (config sent as is) or a library model added by the user. */
export interface RowSource {
  id: string;
  label: string;
  model?: string;
  camera?: CameraConfig;
}

/** Per-row overrides: a missing key means "use the defaults". */
export type RowOverrides = Pick<BitrateOptions, "codec" | "fps" | "scene">;

const finite = (v: number, fallback: number) => (Number.isFinite(v) ? v : fallback);

export function buildRequest(rows: RowSource[], overrides: Record<string, RowOverrides | undefined>, d: BitrateDefaults): BitrateRequest {
  const cameras: BitrateCameraRequest[] = rows.map((r) => {
    const o = overrides[r.id] ?? {};
    const out: BitrateCameraRequest = { label: r.label };
    if (r.camera) out.camera = r.camera; else out.model = r.model;
    if (o.codec) out.codec = o.codec;
    if (o.scene) out.scene = o.scene;
    if (typeof o.fps === "number" && o.fps > 0) out.fps = o.fps;
    return out;
  });
  return {
    cameras,
    defaults: {
      codec: d.codec, fps: Math.max(1, finite(d.fps, 25)), scene: d.scene, gop_s: Math.max(0.5, finite(d.gop_s, 2)),
      mode: d.mode, motion_share: Math.min(1, Math.max(0, finite(d.motion_pct, 30) / 100)),
    },
    retention_days: Math.max(1, finite(d.retention_days, 30)),
    raid_overhead: Math.max(0, finite(d.raid_pct, 15) / 100),
  };
}
