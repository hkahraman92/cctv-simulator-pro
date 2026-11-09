import { useEffect, useRef, useState } from "react";
import { DORI, type OpticResult } from "../core/api";
import { fmt } from "./ui";

/**
 * Top view drawn from engine numbers only: HFOV, dead zone, DORI distances.
 * `requirement` adds the bench's target marker (distance + pass/fail); `irRangeM`
 * marks where the night picture ends.
 *
 * The drawing is laid out in the box's own pixels (measured), so it fits the
 * panel at any window size and text stays 12 px instead of scaling with width.
 */
export default function PlanView({ r, requirement, irRangeM, className = "h-[min(58vh,560px)]" }: {
  r: OpticResult;
  requirement?: { distance_m: number; pass: boolean; dayPass?: boolean };
  irRangeM?: number;
  className?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 520, h: 360 });
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      const { width, height } = e.contentRect;
      if (width > 40 && height > 40) setSize({ w: Math.round(width), h: Math.round(height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const { w: W, h: H } = size;
  const cx = W / 2, cy = H - 28;
  const ir = irRangeM && irRangeM > 0 ? irRangeM : 0;
  const dists = DORI.map((d) => r.ground_distances[d.key] ?? r.max_geom_dist_m ?? 0);
  const far = Math.max(...dists.filter((d) => Number.isFinite(d)), r.dead_zone_m + 1,
    requirement ? requirement.distance_m * 1.1 : 0, ir * 1.05, 5);
  const half = (Math.min(r.hfov_deg, 170) / 2) * (Math.PI / 180);
  // fit the far edge vertically and the wedge's widest point horizontally
  const spread = Math.max(Math.sin(Math.min(half, Math.PI / 2)), 0.05);
  const scale = Math.min((cy - 20) / far, (W / 2 - 16) / (far * spread));
  const pt = (rad: number, a: number) => [cx + rad * Math.sin(a), cy - rad * Math.cos(a)] as const;
  const wedge = (r0: number, r1: number) => {
    const p = (rad: number, a: number) => pt(rad, a).join(" ");
    const a = r0 * scale, b = Math.max(r1 * scale, a);
    return `M${p(a, -half)} L${p(b, -half)} A${b} ${b} 0 0 1 ${p(b, half)} L${p(a, half)} A${a} ${a} 0 0 0 ${p(a, -half)}Z`;
  };
  // Far-to-near so the better (nearer) level paints over the weaker one.
  const bands = DORI.map((d, i) => ({ ...d, to: dists[i] })).reverse();
  const step = niceStep(far);
  const rings = Array.from({ length: Math.floor(far / step) }, (_, i) => (i + 1) * step);
  // ring labels just right of the wedge, where they never sit on the coverage
  const labelAt = Math.min(half + 0.12, 1.25);
  const ry = requirement ? cy - requirement.distance_m * scale : 0;
  const reqTone = !requirement ? "" : requirement.pass ? "var(--color-primary)"
    : requirement.dayPass ? "var(--color-warn)" : "var(--color-danger)";
  const reqLabel = requirement ? `hedef ${fmt(requirement.distance_m)} m` : "";
  const reqRight = cx + 70 + reqLabel.length * 7.5 < W;
  const irArc = (() => {
    if (!ir) return "";
    const a = Math.min(half + 0.1, Math.PI * 0.95), rr = ir * scale;
    const [x0, y0] = pt(rr, -a), [x1, y1] = pt(rr, a);
    return `M${x0} ${y0} A${rr} ${rr} 0 ${a > Math.PI / 2 ? 1 : 0} 1 ${x1} ${y1}`;
  })();

  return (
    <div ref={box} className={`w-full ${className}`}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="block rounded bg-bg-2" role="img"
        aria-label={`Kuş bakışı kapsama: yatay FOV ${fmt(r.hfov_deg)} derece, algılama ${fmt(dists[3])} m`}>
        {rings.map((m) => {
          const [lx, ly] = pt(m * scale, labelAt);
          return (
            <g key={m}>
              <circle cx={cx} cy={cy} r={m * scale} fill="none" stroke="var(--color-line-soft)" />
              {lx < W - 44 && ly > 14 && (
                <text x={lx + 4} y={ly - 4} fill="var(--color-text-3)" fontSize="12" fontFamily="var(--font-mono)">{m} m</text>
              )}
            </g>
          );
        })}
        {bands.map((b) => (
          <path key={b.key} d={wedge(r.dead_zone_m, b.to)} fill={`var(--color-${b.tone})`} opacity={0.55} />
        ))}
        {r.dead_zone_m > 0.05 && (
          <path d={wedge(0, r.dead_zone_m)} fill="var(--color-bg-0)" stroke="var(--color-warn)" strokeDasharray="4 3" opacity={0.9} />
        )}
        {ir > 0 && (
          <g>
            <path d={irArc} fill="none" stroke="var(--color-warn)" strokeWidth={1.5} strokeDasharray="3 4" />
            <text x={pt(ir * scale, -Math.min(half + 0.1, 1.25))[0] - 6} y={pt(ir * scale, -Math.min(half + 0.1, 1.25))[1] - 4}
              textAnchor="end" fill="var(--color-warn)" fontSize="12" fontFamily="var(--font-mono)">IR {fmt(ir, 0)} m</text>
          </g>
        )}
        {requirement && (
          <g>
            <line x1={cx - 60} y1={ry} x2={cx + 60} y2={ry} stroke={reqTone} strokeWidth={2} strokeDasharray="6 4" />
            <circle cx={cx} cy={ry} r={5} fill={reqTone} />
            <text x={reqRight ? cx + 66 : cx - 66} y={ry + 4} textAnchor={reqRight ? "start" : "end"}
              fill="var(--color-text)" fontSize="12" fontFamily="var(--font-mono)">{reqLabel}</text>
          </g>
        )}
        <circle cx={cx} cy={cy} r={6} fill="var(--color-primary)" stroke="var(--color-bg-0)" strokeWidth={2} />
        <text x={cx + 10} y={cy + 4} fill="var(--color-text)" fontSize="12" fontFamily="var(--font-mono)">
          {r.camera.name} · {fmt(r.camera.pole_height_m)} m
        </text>
      </svg>
    </div>
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
