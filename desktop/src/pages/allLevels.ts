import type { AnalysisRow, OpticResult, PPMLevel } from "../core/api";

/** level_type values the engine ships (models.DEFAULT_LEVELS); anything else is a user level. */
const BUILT_IN_VISIBLE = new Set(["Standart", "Algoritma"]);
const JOHNSON = "Termal";

/** Same test as viewshed_3d.is_thermal_camera, so the list agrees with the engine. */
export function isThermalCamera(c: { sensor_name: string; model_name: string }): boolean {
  const s = c.sensor_name.toUpperCase();
  return s.includes("LWIR") || s.includes("MWIR") || c.model_name.toUpperCase().includes("TERMAL");
}

/** Does this level apply to the camera band? Visible: everything but Johnson. Thermal: Johnson + user levels. */
export function levelApplies(l: PPMLevel, thermal: boolean): boolean {
  if (thermal) return l.level_type === JOHNSON || !BUILT_IN_VISIBLE.has(l.level_type);
  return l.level_type !== JOHNSON;
}

export interface LevelRow {
  key: string;
  name: string;
  ppm: number;
  color: string;
  /** ground distance at the selected focal; null = not reachable */
  ground: number | null;
  /** engine status when ground is null or capped ("Kör noktada" / "Geometrik limit") */
  status: string;
  geomLimit: boolean;
  optical: number | null;
  wide: number | null | undefined;
  tele: number | null | undefined;
  /** the wide / tele value is the frame-edge cap, not the lens */
  wideGeom: boolean;
  teleGeom: boolean;
}

export interface LevelTable { rows: LevelRow[]; hidden: number }

export const GEOM = "Geometrik limit"; // i18n-ignore: engine status value
const DEAD = "Kör noktada"; // i18n-ignore: engine status value (calculations.py)

/** The engine's own verdict for one level: its row (name + px/m) decides "in the dead zone", not the raw distance. */
function rowOf(r: OpticResult | undefined, l: PPMLevel): AnalysisRow | undefined {
  return r?.rows.find((x) => x.level === l.name && x.ppm === l.ppm);
}

const capped = (r: OpticResult | undefined, d: number | null) =>
  !!r && d != null && r.max_geom_dist_m != null && d === r.max_geom_dist_m;

const reach = (r: OpticResult | undefined, l: PPMLevel): number | null => {
  if (rowOf(r, l)?.status === DEAD) return null;
  const d = r?.ground_distances[l.key];
  return d == null || !Number.isFinite(d) || d <= 0 ? null : d;
};

/**
 * One row per level for the camera, ppm descending. Distances come straight from the core result
 * (`ground_distances`, `rows`); the wide / tele results are optional (varifocal only).
 */
export function buildLevelRows(
  levels: PPMLevel[], r: OpticResult | undefined, thermal: boolean, wide?: OpticResult, tele?: OpticResult,
): LevelTable {
  const shown = levels.filter((l) => levelApplies(l, thermal));
  const rows = shown.map((l): LevelRow => {
    const row = rowOf(r, l);
    const ground = reach(r, l);
    const geomLimit = capped(r, ground);
    const w = wide ? reach(wide, l) : undefined, te = tele ? reach(tele, l) : undefined;
    return {
      key: l.key, name: l.name, ppm: l.ppm, color: l.color, ground,
      status: ground == null ? (row?.status ?? "") : geomLimit ? GEOM : (row?.status ?? ""),
      geomLimit, optical: row ? row.optical_distance_m : null,
      wide: w, tele: te, wideGeom: capped(wide, w ?? null), teleGeom: capped(tele, te ?? null),
    };
  });
  rows.sort((a, b) => b.ppm - a.ppm);
  return { rows, hidden: levels.length - shown.length };
}
