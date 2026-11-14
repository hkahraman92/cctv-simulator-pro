import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Maximize, Minus, Plus } from "lucide-react";
import type { FenceGap, PlacedCamera } from "../core/terrainApi";
import type { ApproachMarks } from "./ApproachPanel";
import { fmt } from "./ui";
import { toDisplayTilt } from "../pages/terrainTilt";
import { ptzSpeed } from "./patrolSpeeds";

export interface CameraMark {
  x: number;
  y: number;
  pan: number;
  hfov: number;
  range: number;
}

interface Props {
  widthM: number;
  heightM: number;
  base?: string;
  overlay?: string | null;
  camera?: CameraMark | null;
  fence?: [number, number][];
  fenceClosed?: boolean;
  poles?: PlacedCamera[];
  ptz?: PlacedCamera[];
  gaps?: FenceGap[];
  switches?: { index: number; x_m: number; y_m: number; ports_used: number; over_budget: boolean }[];
  /** hidden-approach result: fence sectors by warning time, the intruder's last dash */
  approach?: ApproachMarks | null;
  /** draw each planned camera's FOV wedge / PTZ patrol (else dot + heading tick only) */
  showFov?: boolean;
  /** the page's global patrol speed (deg/s): a PTZ with no speed of its own runs this */
  patrolSpeed?: number;
  /** what a plain click does */
  tool: "camera" | "fence" | "none";
  onPick?: (x: number, y: number) => void;
  /** given: the fence is editable on the map -- drag a corner, double-click to remove it, click a mid-edge box to add one */
  onFenceChange?: (fence: [number, number][]) => void;
  children?: ReactNode; // glass overlays (legend …)
  /** drawn inside the map's SVG, in map user space (x, H - y), above the overlay (live PTZ sweep) */
  inMap?: ReactNode;
}

/**
 * North-up map in metres. The SVG user space is (x, H - y) so the pictures
 * from the core (north-up, exactly width_m x height_m) drop in as <image>s
 * and every marker is placed in world metres — no pixel maths in the app.
 */
export default function MapView(p: Props) {
  const W = p.widthM, H = p.heightM;
  const [vb, setVb] = useState({ x: 0, y: 0, w: W, h: H });
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{ sx: number; sy: number; vb: typeof vb; moved: boolean; vertex?: number; mid?: number } | null>(null);
  const [cursor, setCursor] = useState<[number, number] | null>(null);

  const fit = useCallback(() => setVb({ x: 0, y: 0, w: W, h: H }), [W, H]);
  useEffect(fit, [fit]);

  const toUser = (cx: number, cy: number) => {
    const r = svg.current!.getBoundingClientRect();
    // preserveAspectRatio xMidYMid meet: one scale, centred
    const k = Math.min(r.width / vb.w, r.height / vb.h);
    const ox = (r.width - vb.w * k) / 2, oy = (r.height - vb.h * k) / 2;
    return { u: vb.x + (cx - r.left - ox) / k, v: vb.y + (cy - r.top - oy) / k, k };
  };

  const zoom = (f: number, cx?: number, cy?: number) => {
    setVb((b) => {
      const r = svg.current!.getBoundingClientRect();
      const { u, v } = cx === undefined ? { u: b.x + b.w / 2, v: b.y + b.h / 2 } : toUser(cx, cy!);
      const w = Math.min(Math.max(b.w * f, Math.max(W, H) / 64), Math.max(W, H) * 2);
      const h = (w * b.h) / b.w;
      void r;
      return { x: u - ((u - b.x) * w) / b.w, y: v - ((v - b.y) * h) / b.h, w, h };
    });
  };

  const onWheel = (e: React.WheelEvent) => zoom(e.deltaY > 0 ? 1.2 : 1 / 1.2, e.clientX, e.clientY);
  const onDown = (e: React.PointerEvent) => {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    // a corner or a mid-edge box of an editable fence: this press edits the fence, it does not pan or pick
    const t = e.target as Element;
    const num = (k: string) => (p.onFenceChange && t.hasAttribute?.(k) ? Number(t.getAttribute(k)) : undefined);
    drag.current = { sx: e.clientX, sy: e.clientY, vb, moved: false, vertex: num("data-v"), mid: num("data-m") };
  };
  const onMove = (e: React.PointerEvent) => {
    const { u, v } = toUser(e.clientX, e.clientY);
    setCursor([u, H - v]);
    const d = drag.current;
    if (!d) return;
    if (Math.hypot(e.clientX - d.sx, e.clientY - d.sy) > 3) d.moved = true;
    if (!d.moved) return;
    if (d.vertex !== undefined && p.fence && p.onFenceChange) {
      const x = Math.min(W, Math.max(0, u)), y = Math.min(H, Math.max(0, H - v));
      p.onFenceChange(p.fence.map((q, i) => (i === d.vertex ? [x, y] : q)));
      return;
    }
    if (d.mid !== undefined) return;
    const k = toUser(0, 0).k;
    setVb({ ...d.vb, x: d.vb.x - (e.clientX - d.sx) / k, y: d.vb.y - (e.clientY - d.sy) / k });
  };
  const onUp = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    if (d && (d.vertex !== undefined || d.mid !== undefined)) {
      if (d.mid !== undefined && !d.moved && p.fence && p.onFenceChange) {
        const a = p.fence[d.mid], b = p.fence[(d.mid + 1) % p.fence.length];
        const f = p.fence.slice();
        f.splice(d.mid + 1, 0, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]);
        p.onFenceChange(f);
      }
      return;
    }
    if (d && !d.moved && p.tool !== "none" && e.button === 0) {
      const { u, v } = toUser(e.clientX, e.clientY);
      if (u >= 0 && u <= W && v >= 0 && v <= H) p.onPick?.(u, H - v);
    }
  };

  const px = vb.w / 900; // ~1 screen px in metres, for stroke widths and marker sizes
  // focal labels only when poles are far enough apart on screen to read them
  const labels = (() => {
    const ps = p.poles ?? [];
    if (!ps.length || p.showFov === false) return false;
    let near = Infinity;
    for (let i = 1; i < Math.min(ps.length, 400); i++) {
      const d = Math.hypot(ps[i].x_m - ps[i - 1].x_m, ps[i].y_m - ps[i - 1].y_m);
      if (d > 0.5) near = Math.min(near, d);
    }
    return near / px > 46;
  })();
  const fencePts = (p.fence ?? []).map(([x, y]) => `${x},${H - y}`).join(" ");
  const cam = p.camera;
  const fan = cam ? fanPath(cam, H) : "";

  return (
    <div className="relative h-full min-h-[360px] w-full overflow-hidden rounded-md bg-bg-0">
      <svg
        ref={svg}
        viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
        className={`on-imagery block h-full w-full ${p.tool === "none" ? "cursor-grab" : "cursor-crosshair"}`}
        onWheel={onWheel}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerLeave={() => setCursor(null)}
        role="img"
        aria-label="Arazi haritası"
      >
        {p.base && <image href={p.base} x={0} y={0} width={W} height={H} preserveAspectRatio="none" />}
        {p.overlay && <image href={p.overlay} x={0} y={0} width={W} height={H} preserveAspectRatio="none" />}
        <rect x={0} y={0} width={W} height={H} fill="none" stroke="var(--color-bg-6)" strokeWidth={2 * px} />
        {p.inMap}

        {cam && <path d={fan} fill="none" stroke="var(--color-primary)" strokeWidth={1.5 * px} strokeDasharray={`${6 * px} ${4 * px}`} />}

        {fencePts && (
          p.fenceClosed && (p.fence?.length ?? 0) > 2
            ? <polygon points={fencePts} fill="none" stroke="var(--color-warn)" strokeWidth={2 * px} strokeDasharray={`${8 * px} ${5 * px}`} />
            : <polyline points={fencePts} fill="none" stroke="var(--color-warn)" strokeWidth={2 * px} strokeDasharray={`${8 * px} ${5 * px}`} />
        )}
        {/* mid-edge boxes: a click adds a corner there */}
        {p.onFenceChange && (p.fence ?? []).map(([x, y], i, f) => {
          const last = i === f.length - 1;
          if (f.length < 2 || (last && !(p.fenceClosed && f.length > 2))) return null;
          const [nx, ny] = f[(i + 1) % f.length];
          if (Math.hypot(nx - x, ny - y) < 40 * px) return null;       // too short on screen to hit
          return (
            <rect key={`m${i}`} data-m={i} x={(x + nx) / 2 - 5 * px} y={H - (y + ny) / 2 - 5 * px} width={10 * px} height={10 * px}
              fill="var(--color-bg-0)" stroke="var(--color-warn)" strokeWidth={1.2 * px} className="cursor-copy"><title>Köşe ekle</title></rect>
          );
        })}
        {(p.fence ?? []).map(([x, y], i) => (p.onFenceChange ? (
          <g key={i}>
            <rect x={x - 5.5 * px} y={H - y - 5.5 * px} width={11 * px} height={11 * px} fill="var(--color-warn)" stroke="var(--color-bg-0)" strokeWidth={1 * px} pointerEvents="none" />
            {/* the hit area is larger than the mark */}
            <rect data-v={i} x={x - 11 * px} y={H - y - 11 * px} width={22 * px} height={22 * px} fill="transparent" className="cursor-move"
              onDoubleClick={() => p.onFenceChange?.((p.fence ?? []).filter((_, j) => j !== i))}>
              <title>{`Köşe ${i + 1}: sürükle = taşı · çift tık = sil`}</title>
            </rect>
          </g>
        ) : (
          <rect key={i} x={x - 3 * px} y={H - y - 3 * px} width={6 * px} height={6 * px} fill="var(--color-warn)" />
        )))}
        {(p.approach?.sectors ?? []).map((s, i) => (
          <line key={`sec${i}`} x1={s.x0} y1={H - s.y0} x2={s.x1} y2={H - s.y1}
            stroke={s.tone === "danger" ? "var(--color-danger)" : s.tone === "warn" ? "var(--color-warn)" : "var(--color-primary)"}
            strokeWidth={5 * px} strokeLinecap="round" opacity={0.9} />
        ))}
        {p.approach && p.approach.dash.length > 1 && (
          <polyline points={p.approach.dash.map(([x, y]) => `${x},${H - y}`).join(" ")} fill="none" stroke="var(--color-danger)"
            strokeWidth={2.5 * px} strokeDasharray={`${5 * px} ${3 * px}`} />
        )}
        {p.approach?.breakout && (
          <circle cx={p.approach.breakout[0]} cy={H - p.approach.breakout[1]} r={6 * px} fill="none" stroke="var(--color-danger)" strokeWidth={2.5 * px}>
            <title>İlk görülme noktası</title>
          </circle>
        )}
        {(p.gaps ?? []).map((g, i) => (
          <line key={i} x1={g.start_x} y1={H - g.start_y} x2={g.end_x} y2={H - g.end_y} stroke="var(--color-danger)" strokeWidth={4 * px} />
        ))}
        {p.showFov !== false && (p.ptz ?? []).map((c) => (
          // outline only: dozens of overlapping fills turned the whole map purple
          <path key={`pz${c.pole_id}`} d={patrolPath(c, H)} fill="none" stroke="var(--color-ptz)" strokeOpacity={0.45}
            strokeWidth={1 * px} strokeDasharray={`${4 * px} ${5 * px}`} pointerEvents="none" />
        ))}
        {p.showFov !== false && (p.poles ?? []).map((c, i) => (
          <path key={`fov${i}`} d={fanPath({ x: c.x_m, y: c.y_m, pan: c.pan_deg, hfov: c.hfov_deg, range: c.effective_range_m }, H)}
            fill="var(--color-primary)" fillOpacity={0.07} stroke="var(--color-primary)" strokeOpacity={0.55}
            strokeWidth={0.9 * px} pointerEvents="none" />
        ))}
        {(p.poles ?? []).map((c, i) => (
          <g key={i}>
            <title>{camTitle(c)}</title>
            <line x1={c.x_m} y1={H - c.y_m} x2={c.x_m + Math.sin((c.pan_deg * Math.PI) / 180) * 14 * px}
              y2={H - c.y_m - Math.cos((c.pan_deg * Math.PI) / 180) * 14 * px} stroke="var(--color-primary)" strokeWidth={1.5 * px} />
            <circle cx={c.x_m} cy={H - c.y_m} r={3.5 * px} fill="var(--color-primary)" stroke="var(--color-bg-0)" strokeWidth={1.2 * px} />
            {/* zoom label: the lens each camera was planned with, once the map is close enough to read it */}
            {labels && (
              <text x={c.x_m + Math.sin((c.pan_deg * Math.PI) / 180) * 20 * px} y={H - c.y_m - Math.cos((c.pan_deg * Math.PI) / 180) * 20 * px + 3.5 * px}
                textAnchor="middle" fontSize={10 * px} fontFamily="var(--font-mono)" fill="var(--color-text)" stroke="var(--color-bg-0)"
                strokeWidth={2.5 * px} paintOrder="stroke" pointerEvents="none">{fmt(c.focal_mm, 1)} mm</text>
            )}
          </g>
        ))}
        {(p.switches ?? []).map((s) => (
          <g key={`sw${s.index}`}>
            <rect x={s.x_m - 5 * px} y={H - s.y_m - 5 * px} width={10 * px} height={10 * px} fill={s.over_budget ? "var(--color-danger)" : "var(--color-secondary)"}
              stroke="var(--color-bg-0)" strokeWidth={1.2 * px}><title>{`Switch ${s.index}: ${s.ports_used} port`}</title></rect>
          </g>
        ))}
        {(p.ptz ?? []).map((c) => (
          <circle key={`ptz${c.pole_id}`} cx={c.x_m} cy={H - c.y_m} r={7 * px} fill="none" stroke="var(--color-ptz)" strokeWidth={2 * px}>
            <title>{camTitle(c, p.patrolSpeed)}</title>
          </circle>
        ))}
        {cam && (
          <g>
            <circle cx={cam.x} cy={H - cam.y} r={6 * px} fill="var(--color-primary)" stroke="var(--color-bg-0)" strokeWidth={2 * px} />
          </g>
        )}
      </svg>

      <div className="pointer-events-none absolute top-3 left-3 flex flex-col gap-2">{p.children}</div>

      <div className="absolute top-3 right-3 flex flex-col gap-1">
        <MapButton label="Yakınlaştır" onClick={() => zoom(1 / 1.4)}><Plus size={16} /></MapButton>
        <MapButton label="Uzaklaştır" onClick={() => zoom(1.4)}><Minus size={16} /></MapButton>
        <MapButton label="Tümünü göster" onClick={fit}><Maximize size={15} /></MapButton>
      </div>

      <div className="pointer-events-none absolute right-3 bottom-3 rounded border border-bg-5 bg-bg-1/85 px-2 py-1 font-mono text-[11px] text-text-2 backdrop-blur">
        {cursor ? `x ${fmt(cursor[0], 0)} · y ${fmt(cursor[1], 0)} m` : `${fmt(W / 1000, 2)} × ${fmt(H / 1000, 2)} km`}
        {" · "}
        <ScaleBar metresPerPx={px} />
      </div>
    </div>
  );
}

function MapButton({ label, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" aria-label={label} title={label} onClick={onClick}
      className="flex h-8 w-8 items-center justify-center rounded bg-bg-5/90 text-text hover:bg-bg-6">
      {children}
    </button>
  );
}

function ScaleBar({ metresPerPx }: { metresPerPx: number }) {
  const target = metresPerPx * 90;
  const p = 10 ** Math.floor(Math.log10(target));
  const step = [1, 2, 5, 10].map((k) => k * p).find((s) => s >= target) ?? p * 10;
  return <span>ölçek {step >= 1000 ? `${fmt(step / 1000, step % 1000 ? 1 : 0)} km` : `${fmt(step, 0)} m`}</span>;
}

/** Hover text for a planned camera: the numbers the plan placed it with. */
function camTitle(c: PlacedCamera, patrolSpeed?: number) {
  const ptz = c.patrol_span_deg > 0;
  // a PTZ shows its own searched speed when it has one, else the page's global patrol speed
  const sp = ptz ? ptzSpeed(c.speed_deg_s, patrolSpeed ?? NaN) : null;
  const speed = sp && Number.isFinite(sp.speed) ? ` · hız ${fmt(sp.speed, sp.speed % 1 ? 1 : 0)}°/sn${sp.own ? " (aranan)" : ""}` : "";
  return [
    `Direk ${c.pole_id}${ptz ? " · PTZ" : ""} — ${c.camera_model}`,
    ptz ? `devriye ${fmt(c.patrol_span_deg, 0)}° · merkez ${fmt(c.pan_deg, 0)}° · teşhis erişimi ${fmt(c.effective_range_m, 0)} m${speed}`
      : `odak ${fmt(c.focal_mm, 1)} mm · yatay FOV ${fmt(c.hfov_deg, 1)}° · yön ${fmt(c.pan_deg, 0)}°`,
    ptz ? `geniş uç ${fmt(c.focal_mm, 1)} mm · eğim (aşağı) ${fmt(toDisplayTilt(c.tilt_deg), 1)}°`
      : `menzil ${fmt(c.effective_range_m, 0)} m · eğim (aşağı) ${fmt(toDisplayTilt(c.tilt_deg), 1)}° · kör nokta ${fmt(c.dead_zone_m, 1)} m`,
    `direk ${fmt(c.mast_height_m, 1)} m`,
  ].join("\n");
}

/** PTZ patrol: the swept sector (or full circle) out to its identification reach. */
function patrolPath(c: PlacedCamera, H: number) {
  const r = c.effective_range_m;
  if (c.patrol_span_deg >= 359.9) {
    return `M${c.x_m - r} ${H - c.y_m} a${r} ${r} 0 1 0 ${2 * r} 0 a${r} ${r} 0 1 0 ${-2 * r} 0Z`;
  }
  return fanPath({ x: c.x_m, y: c.y_m, pan: c.pan_deg, hfov: c.patrol_span_deg, range: r }, H);
}

/** FOV wedge outline (compass pan: 0 = north, clockwise). */
function fanPath(c: CameraMark, H: number) {
  const rad = (d: number) => (d * Math.PI) / 180;
  const a0 = rad(c.pan - c.hfov / 2), a1 = rad(c.pan + c.hfov / 2);
  const pt = (a: number) => `${c.x + c.range * Math.sin(a)} ${H - c.y - c.range * Math.cos(a)}`;
  const large = c.hfov > 180 ? 1 : 0;
  return `M${c.x} ${H - c.y} L${pt(a0)} A${c.range} ${c.range} 0 ${large} 1 ${pt(a1)} Z`;
}
