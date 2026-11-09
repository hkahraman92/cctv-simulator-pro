import { describe, expect, it } from "vitest";
import type { PlaybackPtz } from "../core/terrainApi";
import { panAt } from "./PtzSweep";

const base = {
  label: "PTZ", x_m: 0, y_m: 0, mast_m: 8, focal_mm: 5, tilt_deg: -5, hfov_deg: 50, reach_m: 300,
  visible_area_m2: 0, box_m: [0, 0, 1, 1], lit: { data: "" }, shadow: { data: "" },
} as unknown as PlaybackPtz;

// same motion as the core's ptz_animation.pan_at (constant speed, instant reversal)
describe("panAt", () => {
  it("rotates clockwise from the start pan and wraps", () => {
    const p = { ...base, mode: "rotation", start_pan_deg: 350, span_deg: 360, speed_deg_s: 20, end_dwell_s: 0, period_s: 18 } as PlaybackPtz;
    expect(panAt(p, 0)).toBeCloseTo(350);
    expect(panAt(p, 1)).toBeCloseTo(10);
    expect(panAt(p, 18)).toBeCloseTo(350);
  });

  it("patrols a sector: out, dwell, back, dwell", () => {
    // 90 deg at 30 deg/s with 2 s dwells: legs of 3 s, period 10 s
    const p = { ...base, mode: "sector", start_pan_deg: 300, span_deg: 90, speed_deg_s: 30, end_dwell_s: 2, period_s: 10 } as PlaybackPtz;
    expect(panAt(p, 1.5)).toBeCloseTo(345);
    expect(panAt(p, 4)).toBeCloseTo(30);       // dwelling at the far end (300 + 90)
    expect(panAt(p, 6.5)).toBeCloseTo(345);    // half way back
    expect(panAt(p, 9)).toBeCloseTo(300);      // dwelling at the start
    expect(panAt(p, 11.5)).toBeCloseTo(345);   // next period
  });
});
