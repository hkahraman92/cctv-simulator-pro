/**
 * Settings of the analysis panels (assist, night, hidden approach, network, PTZ, city, "Tam analiz"), kept pure so the
 * project file mapping can be tested without React.
 *
 * They travel in `terrain_extra.analysis_settings` (the core's project_io passes unknown keys through untouched, so the
 * headless CLI keeps them on a load + save). A block is `null` while the user never touched that panel: the panel shows
 * its defaults and the "Tam analiz" dialog does not pre-tick the step. Reading is forgiving: an absent block, an absent
 * field or a value of the wrong type / out of range falls back to the default, so an old project opens unchanged.
 * Results are never persisted; only what the user chose.
 */
import type { TerrainLevel } from "../core/terrainApi";

export type PtzMode = "rotation" | "sector" | "cue" | "tour";
export interface PresetRow { name: string; pan_deg: number; tilt_deg: number; focal_mm?: number; dwell_s: number }

/** PTZ-assisted target: the PTZ library model is kept by NAME (resolved from the library when the project opens). */
export interface AssistSettings { model: string; level: string; maxS: number }
/** `when` null = "today 23:00" (computed when the panel shows); `ir` / `minLux` null = the camera's own values. */
export interface NightSettings { when: string | null; lat: number; lon: number; cloud: number; glow: number; irOn: boolean; white: number; ir: number | null; minLux: number | null }
export interface ApproachSettings { depth: number; response: number; threat: string }
export interface NetworkSettings { switch_ports: number; poe_budget_w: number; max_copper_m: number; retention_days: number; autonomy_h: number; night_factor: number; ring: boolean }
/** Single-camera PTZ panel: mode, tour presets, patrol / cue settings. `target` null = the camera's own identification level. */
export interface PtzSettings { mode: PtzMode; presets: PresetRow[]; speed: number; span: number; dwell: number; target: number | null; accel: number }
/** Mixed panel choices beyond the model / density / patrol that terrain_extra already keeps. */
export interface MixedPanelSettings { searchSpeed: boolean; revisitText: string; layer: "fused" | "cue" }
export interface CitySettings { level: TerrainLevel }
export type FullStep = "mixed" | "night" | "approach" | "network" | "sensitivity";
/** "Tam analiz" dialog: which optional steps are ticked (absent = derived from the panels) and the output folder. */
export interface FullSettings { include: Partial<Record<FullStep, boolean>>; outDir: string }

export interface AnalysisSettings {
  assist: AssistSettings | null; night: NightSettings | null; approach: ApproachSettings | null; network: NetworkSettings | null;
  ptz: PtzSettings | null; mixedPanel: MixedPanelSettings | null; city: CitySettings | null; full: FullSettings | null;
}

export const EMPTY_ANALYSIS: AnalysisSettings = { assist: null, night: null, approach: null, network: null, ptz: null, mixedPanel: null, city: null, full: null };

export const DEFAULT_ASSIST: AssistSettings = { model: "", level: "recognize", maxS: 10 };
export const DEFAULT_NIGHT: NightSettings = { when: null, lat: 39.92, lon: 32.85, cloud: 0, glow: 0, irOn: true, white: 0, ir: null, minLux: null };
export const DEFAULT_APPROACH: ApproachSettings = { depth: 400, response: 60, threat: "Koşarak" }; // i18n-ignore
export const DEFAULT_NETWORK: NetworkSettings = { switch_ports: 8, poe_budget_w: 120, max_copper_m: 90, retention_days: 30, autonomy_h: 1, night_factor: 2, ring: false };
export const DEFAULT_PTZ: PtzSettings = { mode: "rotation", presets: [], speed: 20, span: 120, dwell: 2, target: null, accel: 0 };
export const DEFAULT_MIXED_PANEL: MixedPanelSettings = { searchSpeed: false, revisitText: "", layer: "fused" };
export const DEFAULT_CITY: CitySettings = { level: "observe" };
export const DEFAULT_FULL: FullSettings = { include: {}, outDir: "" };

/** Intruder-speed keys the core indexes by (stored data, same as ApproachPanel.SPEEDS; a test keeps them equal). */
export const THREAT_KEYS: readonly string[] = ["Yürüyerek", "Koşarak", "Araçla (40 km/sa)"]; // i18n-ignore
const LEVELS: readonly string[] = ["monitor", "detect", "observe", "recognize", "identify"];
const PTZ_MODES: readonly string[] = ["rotation", "sector", "cue", "tour"];
const MAX_PRESETS = 64;

const isObj = (v: unknown): boolean => !!v && typeof v === "object" && !Array.isArray(v);
const rec = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const num = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const bool = (v: unknown): boolean | undefined => (typeof v === "boolean" ? v : undefined);
/** A finite number inside [lo, hi], else the fallback. */
const within = (v: unknown, lo: number, hi: number, d: number): number => { const n = num(v); return n !== undefined && n >= lo && n <= hi ? n : d; };
/** Same, but null stays null (= "the camera's own / the core's default"). */
const withinOrNull = (v: unknown, lo: number, hi: number, d: number | null): number | null => (v === null ? null : (() => { const n = num(v); return n !== undefined && n >= lo && n <= hi ? n : d; })());

/** `YYYY-MM-DDTHH:MM` as a datetime-local input holds it (the core wants a local time of that shape). */
export const isLocalDateTime = (v: unknown): v is string => typeof v === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(v) && !Number.isNaN(Date.parse(v));

export function readPresets(raw: unknown): PresetRow[] {
  if (!Array.isArray(raw)) return [];
  const out: PresetRow[] = [];
  for (const p of raw.slice(0, MAX_PRESETS)) {
    const r = rec(p);
    const pan = num(r.pan_deg), tilt = num(r.tilt_deg), dwell = num(r.dwell_s);
    if (typeof r.name !== "string" || pan === undefined || tilt === undefined || dwell === undefined || dwell < 0) continue;
    const focal = num(r.focal_mm);
    out.push({ name: r.name, pan_deg: pan, tilt_deg: tilt, dwell_s: dwell, ...(focal !== undefined && focal > 0 ? { focal_mm: focal } : {}) });
  }
  return out;
}

export function readAssist(raw: unknown): AssistSettings | null {
  if (!isObj(raw)) return null;
  const r = rec(raw), d = DEFAULT_ASSIST;
  return { model: typeof r.model === "string" ? r.model : d.model, level: typeof r.level === "string" && LEVELS.includes(r.level) ? r.level : d.level,
    maxS: within(r.max_s, 1, 600, d.maxS) };
}
export function readNight(raw: unknown): NightSettings | null {
  if (!isObj(raw)) return null;
  const r = rec(raw), d = DEFAULT_NIGHT;
  return { when: isLocalDateTime(r.when) ? r.when : d.when, lat: within(r.lat, -90, 90, d.lat), lon: within(r.lon, -180, 180, d.lon),
    cloud: within(r.cloud, 0, 100, d.cloud), glow: within(r.glow, 0, 50, d.glow), irOn: bool(r.ir_on) ?? d.irOn, white: within(r.white, 0, 2000, d.white),
    ir: withinOrNull(r.ir, 0, 10000, d.ir), minLux: withinOrNull(r.min_lux, 0, 100000, d.minLux) };
}
export function readApproach(raw: unknown): ApproachSettings | null {
  if (!isObj(raw)) return null;
  const r = rec(raw), d = DEFAULT_APPROACH;
  return { depth: within(r.depth_m, 50, 5000, d.depth), response: within(r.response_s, 0, 3600, d.response),
    threat: typeof r.threat === "string" && THREAT_KEYS.includes(r.threat) ? r.threat : d.threat };
}
export function readNetwork(raw: unknown): NetworkSettings | null {
  if (!isObj(raw)) return null;
  const r = rec(raw), d = DEFAULT_NETWORK;
  return { switch_ports: Math.round(within(r.switch_ports, 1, 48, d.switch_ports)), poe_budget_w: within(r.poe_budget_w, 10, 2000, d.poe_budget_w),
    max_copper_m: within(r.max_copper_m, 10, 100, d.max_copper_m), retention_days: Math.round(within(r.retention_days, 1, 3650, d.retention_days)),
    autonomy_h: within(r.autonomy_h, 0, 72, d.autonomy_h), night_factor: within(r.night_factor, 1, 5, d.night_factor), ring: bool(r.fibre_ring) ?? d.ring };
}
export function readPtz(raw: unknown): PtzSettings | null {
  if (!isObj(raw)) return null;
  const r = rec(raw), d = DEFAULT_PTZ;
  return { mode: typeof r.mode === "string" && PTZ_MODES.includes(r.mode) ? (r.mode as PtzMode) : d.mode, presets: readPresets(r.presets),
    speed: within(r.speed_deg_s, 0.5, 400, d.speed), span: within(r.span_deg, 10, 360, d.span), dwell: within(r.end_dwell_s, 0, 600, d.dwell),
    target: withinOrNull(r.target_ppm, 1, 2000, d.target), accel: within(r.accel_deg_s2, 0, 100000, d.accel) };
}
export function readMixedPanel(raw: unknown): MixedPanelSettings | null {
  if (!isObj(raw)) return null;
  const r = rec(raw), d = DEFAULT_MIXED_PANEL;
  return { searchSpeed: bool(r.search_speed) ?? d.searchSpeed, revisitText: typeof r.revisit_text === "string" && r.revisit_text.length <= 12 ? r.revisit_text : d.revisitText,
    layer: r.layer === "cue" ? "cue" : "fused" };
}
export function readCity(raw: unknown): CitySettings | null {
  if (!isObj(raw)) return null;
  const r = rec(raw);
  return { level: typeof r.level === "string" && LEVELS.includes(r.level) ? (r.level as TerrainLevel) : DEFAULT_CITY.level };
}
export function readFull(raw: unknown): FullSettings | null {
  if (!isObj(raw)) return null;
  const r = rec(raw), inc = rec(r.include);
  const include: FullSettings["include"] = {};
  for (const k of ["mixed", "night", "approach", "network", "sensitivity"] as const) { const b = bool(inc[k]); if (b !== undefined) include[k] = b; }
  return { include, outDir: typeof r.out_dir === "string" ? r.out_dir : "" };
}

/** Project file -> settings. Anything absent or invalid reads as "never touched" (null) or the field's default. */
export function readAnalysisSettings(raw: unknown): AnalysisSettings {
  const x = rec(raw);
  return { assist: readAssist(x.assist), night: readNight(x.night), approach: readApproach(x.approach), network: readNetwork(x.network),
    ptz: readPtz(x.ptz), mixedPanel: readMixedPanel(x.mixed_panel), city: readCity(x.city), full: readFull(x.full) };
}

/** Settings -> the `analysis_settings` object of terrain_extra (untouched blocks are left out). */
export function buildAnalysisSettings(s: AnalysisSettings): Record<string, unknown> {
  const o: Record<string, unknown> = {};
  if (s.assist) o.assist = { model: s.assist.model, level: s.assist.level, max_s: s.assist.maxS };
  if (s.night) o.night = { when: s.night.when, lat: s.night.lat, lon: s.night.lon, cloud: s.night.cloud, glow: s.night.glow, ir_on: s.night.irOn, white: s.night.white, ir: s.night.ir, min_lux: s.night.minLux };
  if (s.approach) o.approach = { depth_m: s.approach.depth, response_s: s.approach.response, threat: s.approach.threat };
  if (s.network) o.network = { ...s.network, ring: undefined, fibre_ring: s.network.ring };
  if (s.ptz) o.ptz = { mode: s.ptz.mode, presets: s.ptz.presets, speed_deg_s: s.ptz.speed, span_deg: s.ptz.span, end_dwell_s: s.ptz.dwell, target_ppm: s.ptz.target, accel_deg_s2: s.ptz.accel };
  if (s.mixedPanel) o.mixed_panel = { search_speed: s.mixedPanel.searchSpeed, revisit_text: s.mixedPanel.revisitText, layer: s.mixedPanel.layer };
  if (s.city) o.city = { level: s.city.level };
  if (s.full) o.full = { include: s.full.include, out_dir: s.full.outDir };
  return JSON.parse(JSON.stringify(o)) as Record<string, unknown>;   // drops the undefined helper keys
}

// ── ANPR (Optics lab) ─────────────────────────────────────────────────────────────────────────────────────────

/** The Optics lab ANPR setup (anpr.AnprSetup) + the chosen focal; `focal` null = the lens' long end. */
export interface AnprSaved { setup: Record<string, number | boolean | null>; focal: number | null }

const ANPR_NUM: Record<string, [number, number]> = {
  height_m: [0.5, 50], pole_x_m: [-50, 50], lanes: [1, 6], lane_width_m: [2, 5], aim_y_m: [1, 500], plate_height_m: [0.1, 3], target_ppm: [10, 1000],
  max_h_deg: [1, 89], max_v_deg: [1, 89], speed_kmh: [1, 400], fps: [1, 120], min_frames: [1, 30], exposure_s: [1e-5, 1], blur_px: [0.1, 50], max_y_m: [10, 1000],
};

/** A saved ANPR block: every setup field read on its own (invalid / absent = left out, the lab's default stays). */
export function readAnpr(raw: unknown): AnprSaved | null {
  if (!isObj(raw)) return null;
  const r = rec(raw), st = rec(r.setup);
  const setup: AnprSaved["setup"] = {};
  for (const [k, [lo, hi]] of Object.entries(ANPR_NUM)) { const n = num(st[k]); if (n !== undefined && n >= lo && n <= hi) setup[k] = n; }
  if (st.aim_x_m === null) setup.aim_x_m = null; else { const n = num(st.aim_x_m); if (n !== undefined && Math.abs(n) <= 100) setup.aim_x_m = n; }
  const ir = bool(st.night_ir); if (ir !== undefined) setup.night_ir = ir;
  const focal = num(r.focal);
  return { setup, focal: focal !== undefined && focal > 0 && focal <= 5000 ? focal : null };
}
export function buildAnpr(setup: Record<string, number | boolean | null>, focal: number | null): AnprSaved {
  return { setup: { ...setup }, focal };
}

// ── request builders: the parameter objects the panels send, shared with "Tam analiz" ─────────────────────────

/** Local `YYYY-MM-DDTHH:00` of today at hour `h` (the night panel's default: today 23:00, the computer's clock). */
export const localNow = (h = 23): string => {
  const d = new Date();
  d.setHours(h, 0, 0, 0);
  const z = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}T${z(h)}:00`;
};

/** What night.coverage / night.timeline get besides the terrain and band: light, IR, the edited camera, a typed position when the terrain has none. */
export function nightCommon<C extends { ir_range_m: number; min_lux: number }>(s: NightSettings, camera: C, hasLatLon: boolean) {
  const cam = { ...camera, ir_range_m: s.ir ?? camera.ir_range_m, min_lux: s.minLux ?? camera.min_lux };
  return { cloud_cover: s.cloud / 100, sky_glow_lux: s.glow, ir_on: s.irOn, white_light_range_m: s.white, camera: cam, ...(hasLatLon ? {} : { lat: s.lat, lon: s.lon }) };
}
/** The approach analysis request body (without terrain): `speeds` are the UI's intruder speeds (m/s). */
export const approachRequest = (s: ApproachSettings, speeds: Record<string, number>) => ({ depth_m: s.depth, response_s: s.response, speeds, max_px: 1200 });
/** The network request body (without terrain / prices). */
export const networkRequest = (s: NetworkSettings) => ({ switch_ports: s.switch_ports, poe_budget_w: s.poe_budget_w, max_copper_m: s.max_copper_m,
  retention_days: s.retention_days, autonomy_h: s.autonomy_h, night_factor: s.night_factor, fibre_ring: s.ring });
