/** Optics page: which task (and distance) the page starts at, kept pure so it can be tested. */

/** The four EN levels the core reports a ground reach for. */
const REACH_KEY: Record<number, "identify" | "recognize" | "observe" | "detect"> = { 250: "identify", 125: "recognize", 62.5: "observe", 25: "detect" };

export interface SeedVerdict { pass: boolean; dori_reach_m: Record<"identify" | "recognize" | "observe" | "detect", number> }

/** A task index always points at a task of the CURRENT list (the project's levels can shrink on open). */
export const clampTask = (raw: number, count: number): number => Math.max(0, Math.min(Math.floor(raw) || 0, count - 1));

/**
 * First-open choice from the core's own DORI reaches: the strongest task the camera reaches at `dist`,
 * else the weakest one at 80 % of its reach. null = keep the page as it is (the verdict already passes,
 * or nothing is reachable).
 */
export function seedTask(v: SeedVerdict, tasks: [string, number][], dist: number): { task: number; dist?: number } | null {
  if (v.pass) return null;
  const reach = (ppm: number) => (REACH_KEY[ppm] ? v.dori_reach_m[REACH_KEY[ppm]] : undefined);
  const ok = tasks.findIndex(([, ppm]) => (reach(ppm) ?? 0) >= dist);
  if (ok >= 0) return { task: ok };
  let weakest = -1;
  tasks.forEach(([, ppm], i) => { if (reach(ppm) != null && (weakest < 0 || ppm < tasks[weakest][1])) weakest = i; });
  const r = weakest >= 0 ? reach(tasks[weakest][1]) ?? 0 : 0;
  return weakest >= 0 && r >= 2 ? { task: weakest, dist: Math.max(1, Math.floor(r * 0.8)) } : null;
}
