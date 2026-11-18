/**
 * "Tam analiz": the pure part. Builds the `analysis.full` request from the panels' settings (the same parameter objects each
 * panel sends to its own method), decides which optional steps are ticked, and words a step row for the list.
 * The core runs the steps and stores each result as if the panel had run it; nothing is derived here.
 */
import type { CameraConfig } from "../core/api";
import { t } from "../core/i18n";
import type { CoverageLayer, TerrainLevel } from "../core/terrainApi";
import {
  DEFAULT_APPROACH, DEFAULT_ASSIST, DEFAULT_NETWORK, DEFAULT_NIGHT, approachRequest, localNow, networkRequest, nightCommon,
  type AnalysisSettings, type FullStep,
} from "./analysisSettings";
import type { SensitivitySettings } from "./terrainPersist";
import { SENSITIVITY_DEFAULTS } from "./terrainPersist";

/** The steps in the order the core runs them (kept equal to `full_methods.STEP_ORDER`). */
export const STEP_ORDER = ["coverage", "mixed", "night", "approach", "network", "sensitivity", "statement", "acceptance", "engineering", "package"] as const;
export type StepId = (typeof STEP_ORDER)[number];
export const OPTIONAL_STEPS: readonly FullStep[] = ["mixed", "night", "approach", "network", "sensitivity"];

export interface FullStepRow {
  id: string; title: string; status: "ok" | "skipped" | "failed"; reason?: string; error?: string; elapsed_s: number;
  numbers: Record<string, unknown>; files: string[]; kept_previous?: boolean;
}
export interface FullAnalysisResult {
  steps: FullStepRow[]; written: string[]; out_dir: string | null; measured: boolean; refine_factor: number; target_height_m: number;
  total_s: number; counts: { ok: number; skipped: number; failed: number };
  /** each ok step's own method result (what the panel would hold), keyed by step id */
  results: Record<string, unknown>;
  summary_path?: string; summary_error?: string;
}
/** A finished run handed to the panels: they show their step's result when `nonce` changes. */
export interface FullRun { nonce: number; results: Record<string, unknown> }

/** Which optional steps the dialog starts with: the stored tick, else "the panel has been used" (network: on). */
export function defaultInclude(s: AnalysisSettings, sens: SensitivitySettings, hasPtz: boolean): Record<FullStep, boolean> {
  const touchedSens = sens.nRuns !== SENSITIVITY_DEFAULTS.nRuns || sens.sigma !== null || sens.corr !== null;
  const base: Record<FullStep, boolean> = { mixed: hasPtz, night: s.night !== null, approach: s.approach !== null, network: true, sensitivity: touchedSens };
  const stored = s.full?.include ?? {};
  return { mixed: hasPtz && (stored.mixed ?? base.mixed), night: stored.night ?? base.night, approach: stored.approach ?? base.approach,
    network: stored.network ?? base.network, sensitivity: stored.sensitivity ?? base.sensitivity };
}

export interface FullInputs {
  terrainId: string; useObstacles: boolean; targetH: number; band: { band_m?: number; band_in_m?: number };
  camera: CameraConfig; hasLatLon: boolean; goal: number; projectName: string;
  covLayer: CoverageLayer; covLevel: "plan" | TerrainLevel; patrol: { speed: number; dwell: number }; ptzReach: boolean;
  settings: AnalysisSettings; sens: SensitivitySettings; prices: object;
  include: Record<FullStep, boolean>; outDir: string;
  /** intruder speeds (m/s) the approach panel owns */
  speeds: Record<string, number>;
}

/** The `analysis.full` parameters. A step the user did not tick is sent as `false` (the core says why it was skipped). */
export function buildFullParams(i: FullInputs): Record<string, unknown> {
  const night = i.settings.night ?? DEFAULT_NIGHT;
  const approach = i.settings.approach ?? DEFAULT_APPROACH;
  const network = i.settings.network ?? DEFAULT_NETWORK;
  const assist = i.settings.assist ?? DEFAULT_ASSIST;
  return {
    terrain_id: i.terrainId, use_obstacles: i.useObstacles, target_height_m: i.targetH, ...i.band, project_name: i.projectName,
    camera: i.camera, ...(i.outDir ? { out_dir: i.outDir } : {}),
    steps: {
      coverage: { layer: i.covLayer, level: i.covLevel, max_px: 1200, ptz_speed_deg_s: i.patrol.speed, ptz_end_dwell_s: i.patrol.dwell, ptz_reach: i.ptzReach },
      mixed: i.include.mixed ? { speed_deg_s: i.patrol.speed, end_dwell_s: i.patrol.dwell, layer: i.settings.mixedPanel?.layer ?? "fused", max_px: 1200 } : false,
      night: i.include.night ? { datetime: night.when ?? localNow(), max_px: 1200, ...nightCommon(night, i.camera, i.hasLatLon) } : false,
      approach: i.include.approach ? approachRequest(approach, i.speeds) : false,
      network: i.include.network ? { ...networkRequest(network), prices: i.prices } : false,
      sensitivity: i.include.sensitivity ? { n_runs: i.sens.nRuns, dem_sigma_m: i.sens.sigma, dem_corr_m: i.sens.corr, max_px: 1200 } : false,
      statement: { goal_frac: i.goal / 100, assist_max_s: assist.maxS },
    },
  };
}

/** Status marks use the project's allowed symbols (no emoji): ● done, – skipped, ■ failed. */
export const STEP_MARK: Record<FullStepRow["status"], string> = { ok: "●", skipped: "–", failed: "■" };

const pct = (v: unknown, nd = 1): string => (typeof v === "number" && Number.isFinite(v) ? t("%{n}", { n: (v * 100).toFixed(nd).replace(".", ",") }) : "—");
const n1 = (v: unknown, nd = 1): string => (typeof v === "number" && Number.isFinite(v) ? v.toFixed(nd).replace(".", ",") : "—");

/** Titles come from the core as Turkish; they are looked up so the English interface shows English (the key set is fixed). */
export const stepTitle = (id: string, fallback: string): string => ({
  coverage: t("Birleşik kapsama"), mixed: t("Sabit + PTZ değerlendirmesi"), night: t("Gece kapsaması"), approach: t("Gizli yaklaşma"),
  network: t("Ağ planı ve maliyet"), sensitivity: t("Veri hassasiyeti"), statement: t("Plan uygunluk beyanı"), acceptance: t("Kabul föyü"),
  engineering: t("Mühendislik raporu"), package: t("Teklif paketi (ZIP)"),
} as Record<string, string>)[id] ?? fallback;

/** The key numbers of an ok step, as short display lines. */
export function stepLines(r: FullStepRow): string[] {
  const n = r.numbers;
  const o = n as Record<string, number | undefined>;
  switch (r.id) {
    case "coverage": {
      const bs = (n.band_share ?? {}) as Record<string, number>;
      const lines = [t("Hedef ({label}) sabit kameralarla tutulan pay: {v}", { label: String(n.target_label ?? ""), v: pct(n.target_held) })];
      if (typeof n.fence_line_held === "number") lines.push(t("Çit çizgisi payı: {v}", { v: pct(n.fence_line_held) }));
      if (bs.detect !== undefined) lines.push(t("Bant: algılama {d} · tanıma {r}", { d: pct(bs.detect), r: pct(bs.recognize) }));
      return lines;
    }
    case "mixed": return [t("Teşhis edilebilir pay: %{v} · tespit → teşhis ortanca {s} sn", { v: n1(o.identifiable_pct), s: n1(o.median_d2i_s) })];
    case "night": {
      const ns = (n.night_share ?? {}) as Record<string, number>;
      return [t("Gece bant payı: algılama {d} · tanıma {r}", { d: pct(ns.detect), r: pct(ns.recognize) })];
    }
    case "approach": return [n.breach ? t("İhlal var: hedefe görünmeden ulaşılıyor") : t("Uyarı mesafesi {m} m", { m: n1(o.warning_m, 0) })];
    case "network": return [t("{n} switch · maliyet {c} {cur}", { n: String(n.switches ?? "—"), c: n1(o.cost_total, 0), cur: String(n.currency ?? "") })];
    case "sensitivity": return [t("Kesin {c} · belirsiz {u} · hiç {z}", { c: pct(n.certain), u: pct(n.uncertain), z: pct(n.never) })];
    case "statement": return [t("Beyan: {v}", { v: String(n.verdict ?? "—") })];
    case "acceptance": return [t("{p} kontrol noktası · hedef noktası olmayan kamera: {c}", { p: String(n.points ?? "—"), c: String(n.cameras_without_target_point ?? "—") })];
    default: return [];
  }
}
