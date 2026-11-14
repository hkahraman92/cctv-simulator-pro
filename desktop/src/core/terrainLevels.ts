import type { TerrainLevel } from "./terrainApi";

/** Every level selector of the Terrain page reads these two lists, so no selector can drift from another. */
export const LEVELS = [
  ["monitor", "İzleme ≥12,5"],
  ["detect", "Algılama ≥25"],
  ["observe", "Gözlem ≥62,5"],
  ["recognize", "Tanıma ≥125"],
  ["identify", "Teşhis ≥250"],
] as const;

// thermal views: the same zone codes on Johnson thresholds (viewshed_3d.zone_codes)
export const THERMAL_LEVELS = [
  ["detect", "Johnson algılama ≥1,3"],
  ["observe", "Johnson tanıma ≥6,7"],
  ["recognize", "Johnson teşhis ≥13,3"],
] as const;

/** One list for every level selector: a thermal has three levels and none of them is a face.
 *  Monitoring (12.5 px/m) exists for visible cameras only: Johnson has no such level. */
export const levelsFor = (thermal: boolean): readonly (readonly [TerrainLevel, string])[] => (thermal ? THERMAL_LEVELS : LEVELS);

/** "Algılama ≥25" -> "Algılama": the label without its threshold. */
export const levelName = (k: string, thermal: boolean) => {
  const l = levelsFor(thermal).find(([x]) => x === k)?.[1] ?? "";
  return l.slice(0, l.lastIndexOf(" "));
};
