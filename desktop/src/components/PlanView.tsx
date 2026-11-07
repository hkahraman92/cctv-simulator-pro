import { DORI, type OpticResult } from "../core/api";
import { fmt } from "./ui";

/**
 * Top view drawn from engine numbers only: HFOV, dead zone, DORI distances.
 * `requirement` adds the bench's target marker (distance + pass/fail).
 */
export default function PlanView({ r, requirement }: { r: OpticResult; requirement?: { distance_m: number; pass: boolean } }) {
  const W = 520, H = 360, cx = W / 2, cy = H - 24;
  const dists = DORI.map((d) => r.ground_distances[d.key] ?? r.max_geom_dist_m ?? 0);
  const far = Math.max(...dists.filter((d) => Number.isFinite(d)), r.dead_zone_m + 1, requirement ? requirement.distance_m * 1.1 : 0, 5);
  const scale = (H - 48) / far;
  const half = (Math.min(r.hfov_deg, 170) / 2) * (Math.PI / 180);
  const wedge = (r0: number, r1: number) => {
    const p = (rad: number, a: number) => `${cx + rad * Math.sin(a)} ${cy - rad * Math.cos(a)}`;
    const a = r0 * scale, b = Math.max(r1 * scale, a);
    return `M${p(a, -half)} L${p(b, -half)} A${b} ${b} 0 0 1 ${p(b, half)} L${p(a, half)} A${a} ${a} 0 0 0 ${p(a, -half)}Z`;
  };
  // Far-to-near so the better (nearer) level paints over the weaker one.
  const bands = DORI.map((d, i) => ({ ...d, to: dists[i] })).reverse();
  const step = niceStep(far);
  const rings = Array.from({ length: Math.floor(far / step) }, (_, i) => (i + 1) * step);
  const ry = requirement ? cy - requirement.distance_m * scale : 0;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full rounded bg-bg-2" role="img"
      aria-label={`Kuş bakışı kapsama: yatay FOV ${fmt(r.hfov_deg)} derece, algılama ${fmt(dists[3])} m`}>
      {rings.map((m) => (
        <g key={m}>
          <circle cx={cx} cy={cy} r={m * scale} fill="none" stroke="var(--color-line-soft)" />
          <text x={cx - m * scale + 4} y={cy - 4} fill="var(--color-text-3)" fontSize="10" fontFamily="var(--font-mono)">{m} m</text>
        </g>
      ))}
      {bands.map((b) => (
        <path key={b.key} d={wedge(r.dead_zone_m, b.to)} fill={`var(--color-${b.tone})`} opacity={0.55} />
      ))}
      {r.dead_zone_m > 0.05 && (
        <path d={wedge(0, r.dead_zone_m)} fill="var(--color-bg-0)" stroke="var(--color-warn)" strokeDasharray="4 3" opacity={0.9} />
      )}
      {requirement && (
        <g>
          <line x1={cx - 60} y1={ry} x2={cx + 60} y2={ry} stroke={requirement.pass ? "var(--color-primary)" : "var(--color-danger)"} strokeWidth={2} strokeDasharray="6 4" />
          <circle cx={cx} cy={ry} r={5} fill={requirement.pass ? "var(--color-primary)" : "var(--color-danger)"} />
          <text x={cx + 66} y={ry + 4} fill="var(--color-text)" fontSize="11" fontFamily="var(--font-mono)">hedef {fmt(requirement.distance_m)} m</text>
        </g>
      )}
      <circle cx={cx} cy={cy} r={6} fill="var(--color-primary)" stroke="var(--color-bg-0)" strokeWidth={2} />
      <text x={cx + 10} y={cy + 4} fill="var(--color-text)" fontSize="11" fontFamily="var(--font-mono)">
        {r.camera.name} · {fmt(r.camera.pole_height_m)} m
      </text>
    </svg>
  );
}

/** The engine caps a level at the frame's top-edge ground distance (status "Geometrik limit"). */
export function atGeomLimit(r: OpticResult, key: string) {
  const d = r.ground_distances[key];
  return r.max_geom_dist_m != null && d != null && d === r.max_geom_dist_m;
}

function niceStep(far: number) {
  const raw = far / 5;
  const p = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map((k) => k * p).find((s) => s >= raw) ?? p * 10;
}
