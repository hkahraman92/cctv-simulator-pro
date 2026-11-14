/** PTZ patrol speed search: the candidate list and the revisit-limit field, kept pure so they can be tested. */

/** perimeter.ptz_patrol accepts 0.5–400 deg/s per candidate, 1–12 candidates, 1–3600 s revisit limit. */
export const SPEED_MIN = 0.5;
export const SPEED_MAX = 400;
export const REVISIT_MIN = 1;
export const REVISIT_MAX = 3600;
export const SPEED_FACTORS = [0.25, 0.5, 1, 2, 4] as const;

const round3 = (v: number) => Math.round(v * 1000) / 1000;

/** Candidates around the current patrol speed (×0.25 … ×4), clamped to the allowed range and de-duplicated, ascending. */
export function speedCandidates(base: number): number[] {
  const out: number[] = [];
  for (const f of SPEED_FACTORS) {
    const v = round3(Math.min(SPEED_MAX, Math.max(SPEED_MIN, base * f)));
    if (!out.includes(v)) out.push(v);
  }
  return out.sort((a, b) => a - b);
}

/** The optional "En uzun izlenmeyen süre (sn)" field: empty = no limit; a number outside 1–3600 is an error. */
export function parseMaxRevisit(text: string): { value?: number; error?: string } {
  const t = text.trim().replace(",", ".");
  if (t === "") return {};
  const v = Number(t);
  if (!Number.isFinite(v) || v < REVISIT_MIN || v > REVISIT_MAX) return { error: `${REVISIT_MIN}–${REVISIT_MAX} sn arasında bir değer girin.` };
  return { value: v };
}

/** The patrol speed to show for one PTZ: its own searched speed when it has one, else the page's global speed. */
export function ptzSpeed(own: number | null | undefined, global: number): { speed: number; own: boolean } {
  return own != null ? { speed: own, own: true } : { speed: global, own: false };
}
