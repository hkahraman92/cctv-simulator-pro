import { useEffect, useRef, useState } from "react";
import { DORI } from "../core/api";
import type { ProjectCamera } from "../core/benchApi";
import type { Site } from "../core/project";
import { fmt } from "./ui";

interface Props {
  sites: Site[];
  results: ProjectCamera[] | undefined;
  selected: number;
  widthM: number;
  heightM: number;
  image?: string;
  target: { x_m: number; y_m: number; active: boolean };
  tool: "move" | "target";
  onSelect: (i: number) => void;
  onMove: (i: number, x: number, y: number) => void;
  onHeading: (i: number, deg: number) => void;
  onTarget: (x: number, y: number) => void;
  onDragEnd: () => void;
}

/**
 * Top-down site plan in metres (x east, y north; heading = maths angle, 0 = east,
 * counter-clockwise — calculations.polar_point). Drawn only from engine
 * numbers: HFOV, DORI ground distances, dead zone and who covers it.
 * Drag a camera to move it, drag its handle to turn it.
 */
export default function SitePlan(p: Props) {
  const svg = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<{ kind: "move" | "turn"; i: number } | null>(null);
  const pad = Math.max(p.widthM, p.heightM) * 0.04;
  const W = p.widthM + 2 * pad, H = p.heightM + 2 * pad;
  const X = (x: number) => x + pad;
  const Y = (y: number) => p.heightM - y + pad;              // y up in the world, down in SVG
  // one screen pixel in plan metres, from the drawn size (markers / text stay legible at any plan width)
  const [shown, setShown] = useState({ w: 600, h: 400 });
  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setShown({ w: e.contentRect.width || 600, h: e.contentRect.height || 400 }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const px = 1 / Math.min(shown.w / W, shown.h / H);

  const world = (e: React.PointerEvent) => {
    const r = svg.current!.getBoundingClientRect();
    const k = Math.min(r.width / W, r.height / H);
    const ox = (r.width - W * k) / 2, oy = (r.height - H * k) / 2;
    const u = (e.clientX - r.left - ox) / k, v = (e.clientY - r.top - oy) / k;
    return { x: u - pad, y: p.heightM - (v - pad) };
  };

  const onMove = (e: React.PointerEvent) => {
    if (!drag) return;
    const w = world(e);
    if (drag.kind === "move") p.onMove(drag.i, round(w.x), round(w.y));
    else {
      const c = p.sites[drag.i].camera;
      p.onHeading(drag.i, Math.round(((Math.atan2(w.y - c.pos_y_m, w.x - c.pos_x_m) * 180) / Math.PI + 360) % 360));
    }
  };
  const end = () => { if (drag) { setDrag(null); p.onDragEnd(); } };

  const step = niceStep(p.widthM);
  const gridX = Array.from({ length: Math.floor(p.widthM / step) + 1 }, (_, i) => i * step);
  const gridY = Array.from({ length: Math.floor(p.heightM / step) + 1 }, (_, i) => i * step);

  return (
    <svg ref={svg} viewBox={`0 0 ${W} ${H}`} className="block h-full w-full touch-none rounded bg-bg-2"
      onPointerMove={onMove} onPointerUp={end} onPointerLeave={end} role="img"
      aria-label={`Vaziyet planı, ${p.sites.length} kamera`}
      onPointerDown={(e) => { if (p.tool === "target" && e.target === e.currentTarget) { const w = world(e); p.onTarget(round(w.x), round(w.y)); } }}>
      <defs><clipPath id="plan-area"><rect x={pad} y={pad} width={p.widthM} height={p.heightM} /></clipPath></defs>
      {p.image && <image href={p.image} x={pad} y={pad} width={p.widthM} height={p.heightM} preserveAspectRatio="none" opacity={0.85} />}
      {gridX.map((g) => <line key={`x${g}`} x1={X(g)} y1={pad} x2={X(g)} y2={pad + p.heightM} stroke="var(--color-line-soft)" strokeWidth={px} pointerEvents="none" />)}
      {gridY.map((g) => <line key={`y${g}`} x1={pad} y1={Y(g)} x2={pad + p.widthM} y2={Y(g)} stroke="var(--color-line-soft)" strokeWidth={px} pointerEvents="none" />)}
      {gridX.filter((_, i) => i % 2 === 0).map((g) => (
        <text key={`t${g}`} x={X(g) + 2 * px} y={pad + p.heightM + 11 * px} fontSize={10 * px} fill="var(--color-text-3)" fontFamily="var(--font-mono)" pointerEvents="none">{g} m</text>
      ))}

      {p.sites.map((s, i) => {
        const r = p.results?.find((x) => x.name === s.camera.name);
        if (!r) return null;
        const c = s.camera;
        const dists = DORI.map((d) => r.ground_distances[d.key] ?? r.max_geom_dist_m ?? 0);
        const bands = DORI.map((d, k) => ({ ...d, to: dists[k] })).reverse();
        const covered = r.dead_zone_covered_by.length > 0;
        return (
          <g key={i} opacity={i === p.selected ? 1 : 0.75} pointerEvents="none" clipPath="url(#plan-area)">
            {bands.map((b) => <path key={b.key} d={wedge(c, r.dead_zone_m, b.to, r.hfov_deg, X, Y)} fill={`var(--color-${b.tone})`} opacity={0.42} />)}
            {r.dead_zone_m > 0.05 && (
              <path d={wedge(c, 0, r.dead_zone_m, r.hfov_deg, X, Y)} fill={covered ? "var(--color-ok-bg)" : "var(--color-bg-0)"}
                stroke={covered ? "var(--color-primary)" : "var(--color-warn)"} strokeWidth={px * 1.2} strokeDasharray={`${4 * px} ${3 * px}`} opacity={0.9} />
            )}
          </g>
        );
      })}

      {p.target.active && (
        <g pointerEvents="none">
          <circle cx={X(p.target.x_m)} cy={Y(p.target.y_m)} r={6 * px} fill="none" stroke="var(--color-secondary)" strokeWidth={2 * px} />
          <line x1={X(p.target.x_m) - 9 * px} y1={Y(p.target.y_m)} x2={X(p.target.x_m) + 9 * px} y2={Y(p.target.y_m)} stroke="var(--color-secondary)" strokeWidth={1.5 * px} />
          <line x1={X(p.target.x_m)} y1={Y(p.target.y_m) - 9 * px} x2={X(p.target.x_m)} y2={Y(p.target.y_m) + 9 * px} stroke="var(--color-secondary)" strokeWidth={1.5 * px} />
        </g>
      )}

      {p.sites.map((s, i) => {
        const c = s.camera;
        const hx = c.pos_x_m + Math.cos((c.heading_deg * Math.PI) / 180) * 26 * px;
        const hy = c.pos_y_m + Math.sin((c.heading_deg * Math.PI) / 180) * 26 * px;
        const sel = i === p.selected;
        return (
          <g key={`c${i}`}>
            <line x1={X(c.pos_x_m)} y1={Y(c.pos_y_m)} x2={X(hx)} y2={Y(hy)} stroke="var(--color-primary)" strokeWidth={1.5 * px} pointerEvents="none" />
            <circle cx={X(hx)} cy={Y(hy)} r={5 * px} fill="var(--color-bg-1)" stroke="var(--color-primary)" strokeWidth={1.5 * px}
              className="cursor-grab" onPointerDown={(e) => { e.stopPropagation(); p.onSelect(i); setDrag({ kind: "turn", i }); }}>
              <title>{`${c.name}: yön ${fmt(c.heading_deg, 0)}° (sürükle)`}</title>
            </circle>
            <circle cx={X(c.pos_x_m)} cy={Y(c.pos_y_m)} r={(sel ? 8 : 6.5) * px} fill="var(--color-primary)" stroke={sel ? "var(--color-text)" : "var(--color-bg-0)"}
              strokeWidth={2 * px} className="cursor-move" onPointerDown={(e) => { e.stopPropagation(); p.onSelect(i); setDrag({ kind: "move", i }); }}>
              <title>{`${c.name} (${fmt(c.pos_x_m)}, ${fmt(c.pos_y_m)}) m — sürükle`}</title>
            </circle>
            <text x={X(c.pos_x_m) + 10 * px} y={Y(c.pos_y_m) - 8 * px} fontSize={11 * px} fill="var(--color-text)" fontFamily="var(--font-mono)"
              stroke="var(--color-bg-0)" strokeWidth={3 * px} paintOrder="stroke" pointerEvents="none">{c.name}</text>
          </g>
        );
      })}
    </svg>
  );
}

const round = (v: number) => Math.round(v * 10) / 10;

function wedge(c: { pos_x_m: number; pos_y_m: number; heading_deg: number }, r0: number, r1: number, hfov: number,
  X: (x: number) => number, Y: (y: number) => number) {
  const half = (Math.min(hfov, 179) / 2) * (Math.PI / 180);
  const h = (c.heading_deg * Math.PI) / 180;
  const pt = (rad: number, a: number) => `${X(c.pos_x_m + rad * Math.cos(a))} ${Y(c.pos_y_m + rad * Math.sin(a))}`;
  const b = Math.max(r1, r0);
  // world counter-clockwise (y up) = clockwise on the y-down screen = SVG sweep flag 1
  return `M${pt(r0, h - half)} L${pt(b, h - half)} A${b} ${b} 0 0 1 ${pt(b, h + half)} L${pt(r0, h + half)} A${r0} ${r0} 0 0 0 ${pt(r0, h - half)}Z`;
}

function niceStep(span: number) {
  const raw = span / 9;
  const p = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map((k) => k * p).find((s) => s >= raw) ?? p * 10;
}
