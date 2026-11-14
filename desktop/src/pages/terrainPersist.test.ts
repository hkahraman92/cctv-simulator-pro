import { describe, expect, it } from "vitest";
import { DEFAULT_CAMERA } from "../core/api";
import { isHatched } from "../components/LegendSwatch";
import {
  buildTerrainExtra, changedKeys, projectCameraToTerrain, readPlacement, readTerrainExtra, terrainToProjectCamera, type ExtraState,
} from "./terrainPersist";

const cam = { ...DEFAULT_CAMERA, name: "KAM-01", model_name: "Model X", focal_min_mm: 2.8, focal_max_mm: 12 };
const state: ExtraState = {
  camera: cam, tab: "perimeter", fence: [[10, 20], [30, 40], [50, 10]],
  plan: { closed: false, line_mode: "border", watch_side: "left", target_ppm: 62.5, mast_m: 9, lens_mode: "max", back_to_back: true },
  band: { out: 150, in: 30 }, targetH: 1.6, goal: 90, reqLevel: "observe",
  area: { interior: false, perPole: 3, streetsOnly: true, maxHole: 100, minCams: 2, maxCams: 80 },
  mixedPtz: { camera: { ...cam, model_name: "PTZ Y" }, everyN: 5, span: 180 }, prices: { camera: 100 }, scenarios: [],
  mapMode: "satellite", useObstacles: false, focal: 7.5, patrol: { speed: 30, dwell: 4 }, ptzReach: true, slide: false, panSearch: true,
  covLayer: "dori", covLevel: "recognize", sweepLevel: "observe",
};

describe("terrain_extra save / load mapping", () => {
  it("round-trips every persisted choice", () => {
    const r = readTerrainExtra(JSON.parse(JSON.stringify(buildTerrainExtra(state))));
    expect(r.fence).toEqual(state.fence);
    expect(r).toMatchObject({
      closed: false, lineMode: "border", watchSide: "left", targetPpm: 62.5, mast: 9, lensMode: "max", backToBack: true,
      bandOut: 150, bandIn: 30, targetH: 1.6, goal: 90, plannerMode: "perimeter", cameraModel: "Model X",
      mapMode: "satellite", useObstacles: false, focal: 7.5, patrol: { speed: 30, dwell: 4 }, ptzReach: true, slide: false,
      panSearch: true, covLayer: "dori", covLevel: "recognize", sweepLevel: "observe",
    });
    expect(r.area).toEqual({ interior: false, perPole: 3, streetsOnly: true, maxHole: 100, minCams: 2, maxCams: 80 });
  });

  it("an old file without the new keys loads with defaults (nothing set)", () => {
    const old = {
      camera_name: "KAM-01", camera_model: "Model X", planner_mode: "single", fence_points: [[1, 2], [3, 4]], fence_closed: true,
      line_mode: "facility", watch_side: "right", target_ppm: 40, perimeter_mast_m: 6, lens_mode: "min", back_to_back: false,
      band_out_m: 40, band_in_m: 40, target_height_m: 1.8, requirement: { target_ppm: 40, goal_pct: 95 },
      area_goal: { level: "detect", goal_pct: 95, interior: true, per_pole: 2 }, scenarios: [{ x: 1 }],
    };
    const r = readTerrainExtra(old);
    for (const k of ["mapMode", "useObstacles", "focal", "patrol", "ptzReach", "slide", "panSearch", "covLayer", "covLevel", "sweepLevel", "plannerMode"] as const)
      expect(r[k]).toBeUndefined();
    expect(r).toMatchObject({ closed: true, targetPpm: 40, goal: 95, lensMode: "min", backToBack: false, bandOut: 40, targetH: 1.8 });
    expect(r.scenarios).toHaveLength(1);
    expect(r.area.maxHole).toBeUndefined();
  });

  it("handles a missing or malformed block like an empty one, and ignores wrong types", () => {
    expect(readTerrainExtra(undefined).scenarios).toEqual([]);
    expect(readTerrainExtra(null).lensMode).toBe("min");
    const r = readTerrainExtra({ map_mode: "weird", ptz_reach: "yes", focal_mm: "7", patrol: { speed_deg_s: 10 }, coverage_view: { layer: "x", level: "nope" }, sweep_level: "face" });
    for (const k of ["mapMode", "ptzReach", "focal", "patrol", "covLayer", "covLevel", "sweepLevel"] as const) expect(r[k]).toBeUndefined();
  });

  it("an older area project that stored a level, not a px/m, still maps to its px/m", () => {
    const r = readTerrainExtra({ line_mode: "area", area_goal: { level: "recognize", goal_pct: 80 } });
    expect(r.targetPpm).toBe(125);
    expect(r.goal).toBe(80);
  });

  it("a saved null focal means the lens end (undefined)", () => {
    expect(readTerrainExtra(buildTerrainExtra({ ...state, focal: undefined })).focal).toBeUndefined();
  });

  it("reads the single placement incl. the lens end", () => {
    expect(readPlacement({ placements: [{ x_m: 1, y_m: 2, mast_m: 8, pan_deg: 10, tilt_deg: -5, range_m: 600, lens_mode: "min" }] }))
      .toEqual({ x: 1, y: 2, mast: 8, pan: 10, tilt: -5, range: 600, lens: "min" });
    expect(readPlacement({})).toBeNull();
  });
});

describe("terrain page and project camera", () => {
  it("writes the CAD tilt (positive down) from the core tilt (negative down), mast and focal", () => {
    const w = terrainToProjectCamera({ camera: cam, mast: 12, tilt: -18, focal: 6, lens: "max" });
    expect(w.patch).toMatchObject({ model_name: "Model X", focal_min_mm: 2.8, focal_max_mm: 12, pole_height_m: 12, tilt_deg: 18 });
    expect(w.focal).toBe(6);
  });

  it("uses the lens end when no focal is set, and clamps focal and tilt into the CAD ranges", () => {
    expect(terrainToProjectCamera({ camera: cam, mast: 8, tilt: -5, focal: undefined, lens: "max" }).focal).toBe(12);
    expect(terrainToProjectCamera({ camera: cam, mast: 8, tilt: -5, focal: undefined, lens: "min" }).focal).toBe(2.8);
    expect(terrainToProjectCamera({ camera: cam, mast: 8, tilt: -5, focal: 99, lens: "min" }).focal).toBe(12);
    expect(terrainToProjectCamera({ camera: cam, mast: 8, tilt: 7, focal: 5, lens: "min" }).patch.tilt_deg).toBe(0);
    expect(terrainToProjectCamera({ camera: cam, mast: 8, tilt: -90, focal: 5, lens: "min" }).patch.tilt_deg).toBe(60);
    expect(Object.is(terrainToProjectCamera({ camera: cam, mast: 8, tilt: 0, focal: 5, lens: "min" }).patch.tilt_deg, 0)).toBe(true);
  });

  it("round-trips: terrain to project camera and back gives the same mast, tilt and focal", () => {
    for (const tilt of [-45, -17.5, -5, -0.5, 0]) {
      const w = terrainToProjectCamera({ camera: cam, mast: 11.5, tilt, focal: 4.25, lens: "min" });
      const back = projectCameraToTerrain({ ...cam, ...w.patch }, w.focal);
      expect(back).toEqual({ mast: 11.5, tilt, focal: 4.25 });
    }
  });

  it("and project camera to terrain and back", () => {
    const pc = { ...cam, pole_height_m: 7, tilt_deg: 22 };
    const t = projectCameraToTerrain(pc, 3.5);
    const w = terrainToProjectCamera({ camera: pc, mast: t.mast, tilt: t.tilt, focal: t.focal, lens: "min" });
    expect(w.patch.tilt_deg).toBe(22);
    expect(w.patch.pole_height_m).toBe(7);
    expect(changedKeys(pc, 3.5, w)).toEqual([]);
  });

  it("reports what would change", () => {
    const w = terrainToProjectCamera({ camera: cam, mast: 12, tilt: -18, focal: 6, lens: "min" });
    expect(changedKeys({ ...cam, pole_height_m: 8, tilt_deg: 18 }, 6, w)).toEqual(["pole_height_m"]);
    expect(changedKeys({ ...cam, pole_height_m: 12, tilt_deg: 18 }, 3, w)).toEqual(["focal"]);
  });
});

describe("legend swatch", () => {
  it("hatches only the level key the core sends, never a label", () => {
    expect(isHatched("monitor")).toBe(true);
    expect(isHatched("detect")).toBe(false);
    expect(isHatched(undefined)).toBe(false);
  });
});
