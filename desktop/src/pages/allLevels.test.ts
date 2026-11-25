import { describe, expect, it } from "vitest";
import type { AnalysisRow, OpticResult, PPMLevel } from "../core/api";
import { buildLevelRows, isThermalCamera } from "./allLevels";

const lv = (key: string, ppm: number, level_type: string, name = key): PPMLevel => ({ key, name, ppm, color: "#fff", level_type });
const LEVELS = [lv("J", 6.67, "Termal"), lv("Face", 794, "Algoritma"), lv("Id", 250, "Standart"), lv("Test", 400, "Özel"), lv("Far", 5000, "Standart")];

function result(d: Record<string, number>, geom: number | null = null, dead: string[] = []): OpticResult {
  const rows: AnalysisRow[] = LEVELS.map((l) => ({
    camera: "c", mode: "m", level: l.name, level_type: l.level_type, ppm: l.ppm, optical_distance_m: (d[l.key] ?? 0) + 1,
    ground_distance_m: d[l.key] ?? 0, status: (d[l.key] ?? 0) <= 0 || dead.includes(l.key) ? "Kör noktada" : "Aktif",
  }));
  return { ground_distances: d, rows, max_geom_dist_m: geom } as unknown as OpticResult;
}

describe("buildLevelRows", () => {
  const r = result({ J: 400, Face: 40, Id: 120, Test: 75, Far: 0 });
  it("visible camera hides Johnson rows and counts them", () => {
    const t = buildLevelRows(LEVELS, r, false);
    expect(t.rows.map((x) => x.key)).not.toContain("J");
    expect(t.hidden).toBe(1);
  });
  it("thermal shows Johnson + custom, hides the visible-band ones", () => {
    const t = buildLevelRows(LEVELS, r, true);
    expect(t.rows.map((x) => x.key).sort()).toEqual(["J", "Test"]);
    expect(t.hidden).toBe(3);
  });
  it("sorts by px/m descending and keeps custom levels", () => {
    const t = buildLevelRows(LEVELS, r, false);
    expect(t.rows.map((x) => x.key)).toEqual(["Far", "Face", "Test", "Id"]);
  });
  it("unreachable level has no distance and keeps the engine reason", () => {
    const far = buildLevelRows(LEVELS, r, false).rows[0];
    expect(far.ground).toBeNull();
    expect(far.status).toBe("Kör noktada");
  });
  it("a level inside the dead zone is unreachable even when the raw distance is positive", () => {
    const x = buildLevelRows(LEVELS, result({ J: 1, Face: 1.3, Id: 120, Test: 75, Far: 0 }, null, ["Face"]), false);
    const f = x.rows.find((q) => q.key === "Face")!;
    expect(f.ground).toBeNull();
    expect(f.status).toBe("Kör noktada");
  });
  it("flags the geometric limit", () => {
    const g = buildLevelRows(LEVELS, result({ J: 1, Face: 40, Id: 90, Test: 90, Far: 0 }, 90), false);
    expect(g.rows.find((x) => x.key === "Id")?.geomLimit).toBe(true);
    expect(g.rows.find((x) => x.key === "Face")?.geomLimit).toBe(false);
  });
  it("merges wide / tele only when given", () => {
    expect(buildLevelRows(LEVELS, r, false).rows[0].wide).toBeUndefined();
    const m = buildLevelRows(LEVELS, r, false, result({ Face: 10 }), result({ Face: 90 }));
    const f = m.rows.find((x) => x.key === "Face")!;
    expect([f.wide, f.tele]).toEqual([10, 90]);
    expect(m.rows.find((x) => x.key === "Id")!.wide).toBeNull();
  });
});

describe("isThermalCamera", () => {
  it("follows the engine rule", () => {
    expect(isThermalCamera({ sensor_name: "LWIR 640", model_name: "x" })).toBe(true);
    expect(isThermalCamera({ sensor_name: "1/2.8", model_name: "Termal Kule" })).toBe(true);
    expect(isThermalCamera({ sensor_name: "1/2.8", model_name: "Dome" })).toBe(false);
  });
});
