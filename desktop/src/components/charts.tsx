import type { FocalChoice, SingleViewshed } from "../core/terrainApi";
import { fmt } from "./ui";

/** Sight-line cross-section along the camera axis (engine profile arrays). */
export function ProfileChart({ profile }: { profile: SingleViewshed["profile"] }) {
  const n = profile.dist_m.length;
  if (n < 2) return null;
  const W = 520, Hh = 150, pad = { l: 40, r: 8, t: 8, b: 20 };
  const maxD = profile.dist_m[n - 1] || 1;
  const zs = [...profile.terrain_m, ...profile.ray_m.filter((v) => Number.isFinite(v))];
  const zMin = Math.min(...profile.terrain_m), zMax = Math.max(...zs);
  const zr = Math.max(zMax - zMin, 1);
  const X = (d: number) => pad.l + (d / maxD) * (W - pad.l - pad.r);
  const Y = (z: number) => Hh - pad.b - ((z - zMin) / zr) * (Hh - pad.t - pad.b);
  const ground = profile.dist_m.map((d, i) => `${X(d)},${Y(profile.terrain_m[i])}`).join(" ");
  const visSegs: string[] = [];
  let cur: string[] = [];
  profile.dist_m.forEach((d, i) => {
    if (profile.visible[i]) cur.push(`${X(d)},${Y(profile.terrain_m[i])}`);
    else if (cur.length) { visSegs.push(cur.join(" ")); cur = []; }
  });
  if (cur.length) visSegs.push(cur.join(" "));
  return (
    <svg viewBox={`0 0 ${W} ${Hh}`} className="block h-auto w-full" role="img" aria-label="Görüş hattı kesiti">
      <polygon points={`${X(0)},${Hh - pad.b} ${ground} ${X(maxD)},${Hh - pad.b}`} fill="var(--color-bg-5)" />
      <polyline points={ground} fill="none" stroke="var(--color-text-3)" strokeWidth={1} />
      {visSegs.map((s, i) => <polyline key={i} points={s} fill="none" stroke="var(--color-dori-recognize)" strokeWidth={3} />)}
      {/* the optical axis from the lens, and the lens itself on its mast */}
      <polyline points={profile.dist_m.map((d, i) => [d, profile.ray_m[i]] as const).filter(([, z]) => Number.isFinite(z) && z >= zMin && z <= zMax).map(([d, z]) => `${X(d)},${Y(z)}`).join(" ")}
        fill="none" stroke="var(--color-primary)" strokeWidth={1} strokeDasharray="5 4" />
      {Number.isFinite(profile.ray_m[0]) && (
        <>
          <line x1={X(0)} y1={Y(profile.terrain_m[0])} x2={X(0)} y2={Y(profile.ray_m[0])} stroke="var(--color-text-2)" strokeWidth={1.5} />
          <circle cx={X(0)} cy={Y(profile.ray_m[0])} r={3.5} fill="var(--color-primary)" />
        </>
      )}
      <g fontSize="10" fontFamily="var(--font-mono)" fill="var(--color-text-3)">
        <line x1={W - 250} y1={12} x2={W - 232} y2={12} stroke="var(--color-dori-recognize)" strokeWidth={3} /><text x={W - 228} y={15}>görülen zemin</text>
        <line x1={W - 140} y1={12} x2={W - 122} y2={12} stroke="var(--color-primary)" strokeDasharray="5 4" /><text x={W - 118} y={15}>optik eksen</text>
        <text x={W - 44} y={15}>kot: m</text>
      </g>
      <text x={pad.l - 4} y={Y(zMax) + 4} textAnchor="end" fontSize="10" fill="var(--color-text-3)" fontFamily="var(--font-mono)">{fmt(zMax, 0)}</text>
      <text x={pad.l - 4} y={Y(zMin)} textAnchor="end" fontSize="10" fill="var(--color-text-3)" fontFamily="var(--font-mono)">{fmt(zMin, 0)}</text>
      <text x={W - pad.r} y={Hh - 5} textAnchor="end" fontSize="10" fill="var(--color-text-3)" fontFamily="var(--font-mono)">{fmt(maxD, 0)} m</text>
      <text x={pad.l} y={Hh - 5} fontSize="10" fill="var(--color-text-3)" fontFamily="var(--font-mono)">kamera</text>
    </svg>
  );
}

/** Area at the target level per focal step (engine sweep). */
export function FocalCurve({ curve, best, level }: { curve: FocalChoice[]; best: FocalChoice; level: string }) {
  const W = 520, Hh = 150, pad = { l: 46, r: 10, t: 10, b: 22 };
  const key = level as keyof FocalChoice["areas_m2"];
  const ys = curve.map((c) => (c.areas_m2[key] ?? 0) / 1e4);
  const yMax = Math.max(...ys, 0.01);
  const lf = curve.map((c) => Math.log(c.focal_mm));
  const l0 = lf[0], l1 = lf[lf.length - 1] === l0 ? l0 + 1 : lf[lf.length - 1];
  const X = (f: number) => pad.l + ((Math.log(f) - l0) / (l1 - l0)) * (W - pad.l - pad.r);
  const Y = (v: number) => Hh - pad.b - (v / yMax) * (Hh - pad.t - pad.b);
  const pts = curve.map((c, i) => `${X(c.focal_mm)},${Y(ys[i])}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${Hh}`} className="block h-auto w-full" role="img" aria-label="Odak uzunluğuna göre kapsama alanı">
      <line x1={pad.l} y1={Hh - pad.b} x2={W - pad.r} y2={Hh - pad.b} stroke="var(--color-bg-6)" />
      <polyline points={pts} fill="none" stroke="var(--color-secondary)" strokeWidth={2} />
      {curve.map((c, i) => <circle key={i} cx={X(c.focal_mm)} cy={Y(ys[i])} r={3} fill="var(--color-secondary)" />)}
      <circle cx={X(best.focal_mm)} cy={Y((best.areas_m2[key] ?? 0) / 1e4)} r={6} fill="none" stroke="var(--color-primary)" strokeWidth={2} />
      <text x={pad.l - 4} y={Y(yMax) + 4} textAnchor="end" fontSize="10" fill="var(--color-text-3)" fontFamily="var(--font-mono)">{fmt(yMax, 2)} ha</text>
      <text x={pad.l} y={Hh - 6} fontSize="10" fill="var(--color-text-3)" fontFamily="var(--font-mono)">{fmt(curve[0].focal_mm)} mm</text>
      <text x={W - pad.r} y={Hh - 6} textAnchor="end" fontSize="10" fill="var(--color-text-3)" fontFamily="var(--font-mono)">{fmt(curve[curve.length - 1].focal_mm)} mm</text>
    </svg>
  );
}
