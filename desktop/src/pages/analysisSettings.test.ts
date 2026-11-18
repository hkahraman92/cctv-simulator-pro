import { describe, expect, it } from "vitest";
import { SPEEDS } from "../components/ApproachPanel";
import { buildFullParams, defaultInclude, partialToShow, stepLines, STEP_MARK, STEP_ORDER, type FullInputs } from "./fullAnalysis";
import {
  DEFAULT_APPROACH, DEFAULT_NETWORK, DEFAULT_NIGHT, EMPTY_ANALYSIS, THREAT_KEYS, approachRequest, buildAnalysisSettings, buildAnpr, isLocalDateTime,
  networkRequest, nightCommon, nightRunBody, readAnalysisSettings, readAnpr, readNight, readPresets, type AnalysisSettings,
} from "./analysisSettings";
import { SENSITIVITY_DEFAULTS, clampRuns, buildTerrainExtra, readTerrainExtra } from "./terrainPersist";
import { mergeExtras } from "../components/ProjectBar";
import { DEFAULT_CAMERA } from "../core/api";

const full: AnalysisSettings = {
  assist: { model: "PTZ Y", level: "identify", maxS: 7 },
  night: { when: "2026-01-11T23:00", lat: 40.1, lon: 29.5, cloud: 30, glow: 0.01, irOn: false, white: 25, ir: 60, minLux: 0.002 },
  approach: { depth: 650, response: 90, threat: "Araçla (40 km/sa)" },
  network: { switch_ports: 16, poe_budget_w: 240, max_copper_m: 80, retention_days: 45, autonomy_h: 2, night_factor: 3, ring: true },
  ptz: { mode: "tour", presets: [{ name: "A", pan_deg: 10, tilt_deg: -5, dwell_s: 3, focal_mm: 20 }, { name: "B", pan_deg: 200, tilt_deg: -8, dwell_s: 2 }], speed: 35, span: 200, dwell: 4, target: 125, accel: 120 },
  mixedPanel: { searchSpeed: true, revisitText: "30", layer: "cue" },
  city: { level: "recognize" },
  full: { include: { night: true, approach: false, sensitivity: true }, outDir: "C:\\cikti" },
};

describe("analysis panel settings <-> project file", () => {
  it("round-trips every block through JSON", () => {
    const back = readAnalysisSettings(JSON.parse(JSON.stringify(buildAnalysisSettings(full))));
    expect(back).toEqual(full);
  });

  it("an old file (no analysis_settings) reads as nothing touched", () => {
    expect(readAnalysisSettings(undefined)).toEqual(EMPTY_ANALYSIS);
    expect(readAnalysisSettings({})).toEqual(EMPTY_ANALYSIS);
    expect(readTerrainExtra({ line_mode: "facility" }).analysis).toEqual(EMPTY_ANALYSIS);
    expect(buildAnalysisSettings(EMPTY_ANALYSIS)).toEqual({});
  });

  it("a block with missing fields fills the defaults; garbage values are rejected to defaults", () => {
    const r = readAnalysisSettings({
      night: { cloud: 20 }, approach: { depth_m: "far", response_s: -5, threat: "Uçarak" }, network: { switch_ports: 0, fibre_ring: "yes", retention_days: 1e9 },
      ptz: { mode: "fly", speed_deg_s: NaN, presets: [{ name: 1 }, { name: "ok", pan_deg: 1, tilt_deg: 2, dwell_s: -1 }, 5] },
      assist: { model: 5, level: "face", max_s: 9999 }, city: { level: "x" }, mixed_panel: { layer: "other", revisit_text: "x".repeat(40) }, full: { include: { night: "yes", network: false }, out_dir: 7 },
    });
    expect(r.night).toEqual({ ...DEFAULT_NIGHT, cloud: 20 });
    expect(r.approach).toEqual(DEFAULT_APPROACH);
    expect(r.network).toEqual(DEFAULT_NETWORK);
    expect(r.ptz).toMatchObject({ mode: "rotation", speed: 20, presets: [] });
    expect(r.assist).toEqual({ model: "", level: "recognize", maxS: 10 });
    expect(r.city).toEqual({ level: "observe" });
    expect(r.mixedPanel).toEqual({ searchSpeed: false, revisitText: "", layer: "fused" });
    expect(r.full).toEqual({ include: { network: false }, outDir: "" });
    for (const v of [5, "x", [1], true]) expect(readAnalysisSettings({ night: v, approach: v, network: v, ptz: v, assist: v, city: v, mixed_panel: v, full: v })).toEqual(EMPTY_ANALYSIS);
  });

  it("night: only an ISO local date-time is kept; ir / min lux null means the camera's own", () => {
    expect(isLocalDateTime("2026-01-11T23:00")).toBe(true);
    for (const v of ["2026-01-11", "yesterday", "2026-13-40T99:99", 5, null]) expect(isLocalDateTime(v)).toBe(false);
    expect(readNight({ when: "soon", ir: null, min_lux: null })).toMatchObject({ when: null, ir: null, minLux: null });
    expect(readNight({ lat: 200, lon: -999 })).toMatchObject({ lat: DEFAULT_NIGHT.lat, lon: DEFAULT_NIGHT.lon });
    const cam = { ir_range_m: 30, min_lux: 0.05 };
    expect(nightCommon(DEFAULT_NIGHT, cam, true).camera).toEqual(cam);
    expect(nightCommon({ ...DEFAULT_NIGHT, ir: 80 }, cam, false)).toMatchObject({ camera: { ir_range_m: 80, min_lux: 0.05 }, lat: 39.92, lon: 32.85 });
    expect(nightCommon(DEFAULT_NIGHT, cam, true)).not.toHaveProperty("lat");
  });

  it("presets: invalid rows are dropped, the list is capped", () => {
    expect(readPresets([{ name: "a", pan_deg: 1, tilt_deg: 2, dwell_s: 1 }, { pan_deg: 1 }, null])).toHaveLength(1);
    expect(readPresets("x")).toEqual([]);
    expect(readPresets(Array.from({ length: 200 }, (_, i) => ({ name: `p${i}`, pan_deg: i, tilt_deg: 0, dwell_s: 1 })))).toHaveLength(64);
  });

  it("the threat keys equal the approach panel's speed table; request builders send the panel's objects", () => {
    expect([...THREAT_KEYS].sort()).toEqual(Object.keys(SPEEDS).sort());
    expect(approachRequest(full.approach!, SPEEDS)).toEqual({ depth_m: 650, response_s: 90, speeds: SPEEDS, max_px: 1200 });
    expect(networkRequest(full.network!)).toEqual({ switch_ports: 16, poe_budget_w: 240, max_copper_m: 80, retention_days: 45, autonomy_h: 2, night_factor: 3, fibre_ring: true });
  });

  it("terrain_extra carries the analysis_settings block and an old project without it opens unchanged", () => {
    const state = { camera: { ...DEFAULT_CAMERA }, tab: "perimeter" as const, fence: [], plan: { closed: true, line_mode: "facility" as const, watch_side: "right" as const, target_ppm: 40, mast_m: 6, lens_mode: "min" as const, back_to_back: false },
      band: { out: 40, in: 40 }, targetH: 1.8, goal: 95, reqLevel: "detect" as const, area: { interior: true, perPole: 2, streetsOnly: false, maxHole: 0, minCams: 1, maxCams: 80 },
      mixedPtz: { camera: null, everyN: 4, span: 360 }, prices: {}, scenarios: [], mapMode: "hybrid", useObstacles: true, focal: undefined, patrol: { speed: 20, dwell: 2 },
      ptzReach: false, slide: true, panSearch: false, covLayer: "target" as const, covLevel: "plan" as const, sweepLevel: "recognize" as const, refine: 1, sensitivity: SENSITIVITY_DEFAULTS };
    const built = buildTerrainExtra({ ...state, analysis: full });
    expect(readTerrainExtra(JSON.parse(JSON.stringify(built))).analysis).toEqual(full);
    const noKey = { ...built };
    delete (noKey as Record<string, unknown>).analysis_settings;
    expect(readTerrainExtra(noKey).analysis).toEqual(EMPTY_ANALYSIS);
    expect(buildTerrainExtra(state).analysis_settings).toEqual({});
  });

  it("ANPR: each setup field is read on its own, junk is left out, the focal is validated", () => {
    const saved = buildAnpr({ lanes: 3, speed_kmh: 120, aim_x_m: null, night_ir: true, exposure_s: 0.002 }, 12.5);
    expect(readAnpr(JSON.parse(JSON.stringify(saved)))).toEqual({ setup: { lanes: 3, speed_kmh: 120, exposure_s: 0.002, aim_x_m: null, night_ir: true }, focal: 12.5 });
    expect(readAnpr({ setup: { lanes: 99, fps: "fast", aim_x_m: 1e6, night_ir: "y" }, focal: -3 })).toEqual({ setup: {}, focal: null });
    for (const v of [undefined, null, 5, "x"]) expect(readAnpr(v)).toBeNull();
  });

  it("the other pages' settings merge into the terrain block's terrain_extra at save time", () => {
    const block = { terrain_source: "procedural", terrain_extra: { fence_points: [[1, 2]], anpr: { stale: true } } };
    expect(mergeExtras(block, {})).toBe(block);
    const m = mergeExtras(block, { anpr: { setup: { lanes: 2 }, focal: null } }) as { terrain_extra: Record<string, unknown> };
    expect(m.terrain_extra.fence_points).toEqual([[1, 2]]);
    expect(m.terrain_extra.anpr).toEqual({ setup: { lanes: 2 }, focal: null });
    expect(mergeExtras({}, { anpr: 1 })).toEqual({ terrain_extra: { anpr: 1 } });
  });
});

describe("Tam analiz request + step list", () => {
  const base: FullInputs = {
    terrainId: "t1", useObstacles: true, targetH: 1.8, band: { band_m: 40 }, camera: { ...DEFAULT_CAMERA }, hasLatLon: false, goal: 90, projectName: "P",
    covLayer: "target", covLevel: "plan", patrol: { speed: 20, dwell: 2 }, ptzReach: false, settings: EMPTY_ANALYSIS, sens: SENSITIVITY_DEFAULTS, prices: { camera: 1 },
    include: { mixed: false, night: false, approach: false, network: true, sensitivity: false }, outDir: "", speeds: SPEEDS,
  };

  it("ticks derive from the panels: untouched panels are off, network on, PTZ only when the plan has one; a stored tick wins", () => {
    expect(defaultInclude(EMPTY_ANALYSIS, SENSITIVITY_DEFAULTS, false)).toEqual({ mixed: false, night: false, approach: false, network: true, sensitivity: false });
    expect(defaultInclude(full, { nRuns: 20, sigma: null, corr: null }, true)).toEqual({ mixed: true, night: true, approach: false, network: true, sensitivity: true });
    expect(defaultInclude(full, SENSITIVITY_DEFAULTS, false).mixed).toBe(false);
  });

  it("builds the request with each panel's own parameter object; an unticked step is false", () => {
    const p = buildFullParams(base) as Record<string, unknown> & { steps: Record<string, unknown> };
    expect(p).toMatchObject({ terrain_id: "t1", band_m: 40, target_height_m: 1.8, project_name: "P" });
    expect(p).not.toHaveProperty("out_dir");
    expect(p.steps).toMatchObject({ mixed: false, night: false, approach: false, sensitivity: false, coverage: { layer: "target", level: "plan", ptz_speed_deg_s: 20 } });
    expect(p.steps.network as Record<string, unknown>).toMatchObject({ switch_ports: 8, prices: { camera: 1 }, fibre_ring: false });
    const q = buildFullParams({ ...base, settings: full, include: { mixed: true, night: true, approach: true, network: false, sensitivity: true }, outDir: "C:\\o", sens: { nRuns: 20, sigma: 2, corr: null } }) as typeof p;
    expect(q.out_dir).toBe("C:\\o");
    expect(q.steps).toMatchObject({ network: false, mixed: { layer: "cue" }, sensitivity: { n_runs: 20, dem_sigma_m: 2, dem_corr_m: null },
      night: { datetime: "2026-01-11T23:00", lat: 40.1, cloud_cover: 0.3, ir_on: false }, approach: { depth_m: 650, response_s: 90 } });
  });

  it("step order equals the core's and the marks are the project's allowed symbols (no emoji)", () => {
    expect([...STEP_ORDER]).toEqual(["coverage", "mixed", "night", "approach", "network", "sensitivity", "statement", "acceptance", "engineering", "package"]);
    expect(STEP_MARK).toEqual({ ok: "●", skipped: "–", failed: "■", cancelled: "▲", not_run: "–" });
    const row = { id: "coverage", title: "x", status: "ok" as const, elapsed_s: 1, files: [], numbers: { target_label: "40 px/m", target_held: 0.912, fence_line_held: 0.97, band_share: { detect: 0.99, recognize: 0.5 } } };
    expect(stepLines(row).join("|")).toContain("91,2");
    expect(stepLines({ ...row, id: "approach", numbers: { breach: true } })[0]).toContain("İhlal");
    expect(stepLines({ ...row, id: "package", numbers: {} })).toEqual([]);
  });
});

describe("one night request, one network line, one run count", () => {
  const camera = { ...DEFAULT_CAMERA, ir_range_m: 200, min_lux: 0.02 };
  const inputs = (night: AnalysisSettings["night"]): FullInputs => ({
    terrainId: "t1", useObstacles: true, targetH: 1.8, band: {}, camera, hasLatLon: true, goal: 90, projectName: "P",
    covLayer: "target", covLevel: "plan", patrol: { speed: 20, dwell: 2 }, ptzReach: false, settings: { ...EMPTY_ANALYSIS, night },
    sens: { nRuns: 60, sigma: null, corr: null }, prices: {}, include: { mixed: false, night: true, approach: false, network: true, sensitivity: true },
    outDir: "", speeds: SPEEDS,
  });
  const when = "2026-01-11T23:00";

  it("the panel's night body equals the full run's night step for null / explicit IR and min lux", () => {
    for (const ir of [null, 80]) for (const minLux of [null, 0.5]) {
      const n = { ...DEFAULT_NIGHT, when, ir, minLux };
      const step = (buildFullParams(inputs(n)).steps as Record<string, unknown>).night;
      expect(step).toEqual(nightRunBody(n, camera, true, when));
      expect((step as { camera: { ir_range_m: number; min_lux: number } }).camera).toMatchObject({ ir_range_m: ir ?? 200, min_lux: minLux ?? 0.02 });
    }
  });
  it("a priced-nothing network step says so instead of 0", () => {
    const row = { id: "network", title: "x", status: "ok" as const, elapsed_s: 0, files: [], numbers: { switches: 6, cost_total: 0, currency: "TL", unpriced: 5 } };
    expect(stepLines(row)[0]).toContain("birim fiyatı girilmedi");
    expect(stepLines(row)[0]).not.toContain("0 TL");
  });
  it("the run count is clamped to the core's 4..32 in the request", () => {
    expect(clampRuns(60)).toBe(32); expect(clampRuns(1)).toBe(4); expect(clampRuns("x", 12)).toBe(12);
    expect((buildFullParams(inputs(null)).steps as { sensitivity: { n_runs: number } }).sensitivity.n_runs).toBe(32);
  });
  it("only an unfinished stored run replaces the list", () => {
    const run = { steps: [], written: [], out_dir: null, measured: true, refine_factor: 1, target_height_m: 0, total_s: 0, results: {}, counts: { ok: 0, skipped: 0, failed: 0 } };
    expect(partialToShow({ ...run, state: "cancelled" })).not.toBeNull();
    expect(partialToShow({ ...run, state: "done" })).toBeNull();
    expect(partialToShow(null)).toBeNull();
  });
});
