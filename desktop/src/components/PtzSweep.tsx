import { useEffect, useRef } from "react";
import type { PlaybackPtz } from "../core/terrainApi";

/**
 * Live PTZ sweep on the map: every PTZ turns at once, at the focal and tilt
 * the plan gave it. The core sends each PTZ's pan-independent pictures once
 * (DORI where the line of sight is clear, shadow where terrain / buildings
 * hide the ground); here the current cone, a fading trail and a faint memory
 * of what has been swept are clip paths over those pictures, moved every
 * animation frame through refs (no React render per frame, no round trip).
 *
 * Pan motion is the core's (`ptz_animation.pan_at`): constant speed, a 360°
 * rotation turns clockwise, a sector goes start -> end, dwells, comes back.
 */

const TRAIL = [0.5, 0.3, 0.15];            // trailing wedges behind the cone, each half a FOV wide

export function panAt(p: PlaybackPtz, t: number) {
  if (p.mode === "rotation") return (p.start_pan_deg + p.speed_deg_s * t) % 360;
  const w = Math.max(p.speed_deg_s, 1e-6), leg = p.span_deg / w, d = p.end_dwell_s;
  const u = ((t % p.period_s) + p.period_s) % p.period_s;
  const off = u < leg ? w * u : u < leg + d ? p.span_deg : u < 2 * leg + d ? p.span_deg - w * (u - leg - d) : 0;
  return (p.start_pan_deg + off) % 360;
}

/** Wedge path (compass degrees, clockwise from a0 to a1) in map user space (x, H - y). */
function wedge(cx: number, cy: number, r: number, a0: number, a1: number) {
  const span = a1 - a0;
  if (span >= 359.9) return `M${cx - r} ${cy} a${r} ${r} 0 1 0 ${2 * r} 0 a${r} ${r} 0 1 0 ${-2 * r} 0Z`;
  if (span <= 0) return "";
  const pt = (a: number) => `${cx + r * Math.sin((a * Math.PI) / 180)} ${cy - r * Math.cos((a * Math.PI) / 180)}`;
  return `M${cx} ${cy} L${pt(a0)} A${r} ${r} 0 ${span > 180 ? 1 : 0} 1 ${pt(a1)}Z`;
}

export default function PtzSweep({ ptz, heightM, playing, rate, onTime, idPrefix = "sw" }: {
  ptz: PlaybackPtz[];
  heightM: number;
  playing: boolean;
  /** playback speed multiplier (1 = real time) */
  rate: number;
  onTime?: (t: number) => void;
  idPrefix?: string;
}) {
  const H = heightM;
  const t = useRef(0);
  const refs = useRef<(SVGPathElement | null)[]>([]);
  const rateRef = useRef(rate);
  rateRef.current = rate;
  const onTimeRef = useRef(onTime);
  onTimeRef.current = onTime;

  // a new set of PTZs starts from t = 0
  useEffect(() => { t.current = 0; }, [ptz]);

  useEffect(() => {
    const per = 3 + TRAIL.length;           // refs per PTZ: memory, cone, outline, trail...
    const draw = () => {
      ptz.forEach((p, i) => {
        const cx = p.x_m, cy = H - p.y_m, r = p.reach_m, half = p.hfov_deg / 2;
        const pan = panAt(p, t.current);
        // memory: what the patrol has swept since playback started
        let mem: string;
        if (p.mode === "rotation") mem = wedge(cx, cy, r, p.start_pan_deg - half, p.start_pan_deg + Math.min(360 - p.hfov_deg, p.speed_deg_s * t.current) + half);
        else mem = wedge(cx, cy, r, p.start_pan_deg - half, p.start_pan_deg + Math.min(p.span_deg, p.speed_deg_s * t.current) + half);
        const cone = wedge(cx, cy, r, pan - half, pan + half);
        const set = (k: number, d: string) => refs.current[i * per + k]?.setAttribute("d", d || "M0 0");
        set(0, mem);
        set(1, cone);
        set(2, cone);
        // trail behind the direction of motion (none while dwelling)
        const dir = Math.sign(((panAt(p, t.current) - panAt(p, Math.max(t.current - 0.05, 0)) + 540) % 360) - 180);
        TRAIL.forEach((_, k) => {
          const a = dir >= 0 ? pan - half - (k + 1) * half : pan + half + k * half;
          set(3 + k, dir === 0 ? "" : wedge(cx, cy, r, a, a + half));
        });
      });
    };
    draw();
    if (!playing) return;
    let raf = 0, last = performance.now(), lastReport = 0;
    const tick = (now: number) => {
      t.current += ((now - last) / 1000) * rateRef.current;
      last = now;
      draw();
      if (now - lastReport > 250) { lastReport = now; onTimeRef.current?.(t.current); }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [ptz, H, playing]);

  const per = 3 + TRAIL.length;
  return (
    <g pointerEvents="none">
      <defs>
        {ptz.map((_, i) => (
          <g key={i}>
            <clipPath id={`${idPrefix}-m${i}`}><path ref={(el) => { refs.current[i * per] = el; }} /></clipPath>
            <clipPath id={`${idPrefix}-c${i}`}><path ref={(el) => { refs.current[i * per + 1] = el; }} /></clipPath>
            {TRAIL.map((_, k) => (
              <clipPath key={k} id={`${idPrefix}-t${i}-${k}`}><path ref={(el) => { refs.current[i * per + 3 + k] = el; }} /></clipPath>
            ))}
          </g>
        ))}
      </defs>
      {ptz.map((p, i) => {
        const [x0, y0, x1, y1] = p.box_m;
        const img = (href: string, clip: string, opacity: number) => (
          <image href={href} x={x0} y={H - y1} width={x1 - x0} height={y1 - y0} preserveAspectRatio="none"
            clipPath={`url(#${clip})`} opacity={opacity} />
        );
        return (
          <g key={i}>
            {img(p.lit.data, `${idPrefix}-m${i}`, 0.22)}
            {TRAIL.map((o, k) => <g key={k}>{img(p.lit.data, `${idPrefix}-t${i}-${k}`, o)}</g>)}
            {img(p.lit.data, `${idPrefix}-c${i}`, 1)}
            {img(p.shadow.data, `${idPrefix}-c${i}`, 1)}
            <path ref={(el) => { refs.current[i * per + 2] = el; }} fill="none" stroke="var(--color-ptz)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
          </g>
        );
      })}
    </g>
  );
}
