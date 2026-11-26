import { describe, expect, it } from "vitest";
import { DEFAULT_CAMERA } from "../core/api";
import { DEFAULT_BITRATE, buildRequest, type RowSource } from "./bitrateRows";

const rows: RowSource[] = [
  { id: "site-0", label: "Kamera 1", camera: DEFAULT_CAMERA },
  { id: "lib-1", label: "Model X", model: "Model X" },
];

describe("buildRequest", () => {
  it("maps project cameras to camera configs and added rows to model names", () => {
    const r = buildRequest(rows, {}, DEFAULT_BITRATE);
    expect(r.cameras[0].camera).toBe(DEFAULT_CAMERA);
    expect(r.cameras[0].model).toBeUndefined();
    expect(r.cameras[1]).toEqual({ label: "Model X", model: "Model X" });
  });
  it("sends percentages as fractions and keeps defaults separate from overrides", () => {
    const r = buildRequest(rows, { "lib-1": { codec: "mjpeg", fps: 10 } }, { ...DEFAULT_BITRATE, motion_pct: 40, raid_pct: 20, retention_days: 14 });
    expect(r.defaults).toMatchObject({ codec: "h265", motion_share: 0.4, fps: 25 });
    expect(r.raid_overhead).toBeCloseTo(0.2);
    expect(r.retention_days).toBe(14);
    expect(r.cameras[1]).toMatchObject({ codec: "mjpeg", fps: 10 });
    expect(r.cameras[0].codec).toBeUndefined();
  });
  it("clamps nonsense input instead of sending it", () => {
    const r = buildRequest(rows, { "lib-1": { fps: -3 } }, { ...DEFAULT_BITRATE, fps: NaN, gop_s: 0, motion_pct: 500 });
    expect(r.defaults?.fps).toBe(25);
    expect(r.defaults?.gop_s).toBe(0.5);
    expect(r.defaults?.motion_share).toBe(1);
    expect(r.cameras[1].fps).toBeUndefined();
  });
});
