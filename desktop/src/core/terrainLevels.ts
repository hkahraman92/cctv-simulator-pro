import type { TerrainLevel } from "./terrainApi";
import { t } from "./i18n";

/** Every level selector of the Terrain page reads these two lists, so no selector can drift from another.
 *  Functions, not constants: the labels follow the interface language at render time. The value (first
 *  element) is what goes to the core; the label must stay "<name> ≥<threshold>" (threshold last, one
 *  space before it) because `levelName` cuts it off at the last space. */
const levels = () => [
  ["monitor", t("İzleme ≥12,5")],
  ["detect", t("Algılama ≥25")],
  ["observe", t("Gözlem ≥62,5")],
  ["recognize", t("Tanıma ≥125")],
  ["identify", t("Teşhis ≥250")],
] as const;

// thermal views: the same zone codes on Johnson thresholds (viewshed_3d.zone_codes)
const thermalLevels = () => [
  ["detect", t("Johnson algılama ≥1,3")],
  ["observe", t("Johnson tanıma ≥6,7")],
  ["recognize", t("Johnson teşhis ≥13,3")],
] as const;

/** One list for every level selector: a thermal has three levels and none of them is a face.
 *  Monitoring (12.5 px/m) exists for visible cameras only: Johnson has no such level. */
export const levelsFor = (thermal: boolean): readonly (readonly [TerrainLevel, string])[] => (thermal ? thermalLevels() : levels());

/** "Algılama ≥25" -> "Algılama": the label without its threshold. */
export const levelName = (k: string, thermal: boolean) => {
  const l = levelsFor(thermal).find(([x]) => x === k)?.[1] ?? "";
  return l.slice(0, l.lastIndexOf(" "));
};
