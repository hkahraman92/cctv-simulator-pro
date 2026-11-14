import { useMemo, useState } from "react";
import { CheckCircle2, Sparkles, XCircle } from "lucide-react";
import type { CameraConfig } from "../core/api";
import type { AnprLane, AnprResult, AnprSetup } from "../core/benchApi";
import { useCoalesced, useCore } from "../core/CoreProvider";
import { useJob } from "../core/useJob";
import JobBar from "./JobBar";
import { Button, Panel, Slider, Tile, fmt } from "./ui";

const SHUTTERS = [250, 500, 1000, 2000, 4000];
const DEFAULT: AnprSetup = {
  height_m: 6, pole_x_m: -1.5, lanes: 2, lane_width_m: 3.5, aim_y_m: 25, aim_x_m: null, plate_height_m: 0.5,
  target_ppm: 143, max_h_deg: 30, max_v_deg: 30, speed_kmh: 90, fps: 25, min_frames: 3, exposure_s: 1 / 1000,
  blur_px: 2, max_y_m: 200, night_ir: false,
};

/** Plate reading on a straight road: per-lane readable zone, frames at speed, motion blur, angles. */
export default function AnprBench({ camera, active }: { camera: CameraConfig; active: boolean }) {
  const { bench, state } = useCore();
  const job = useJob();
  const [s, setS] = useState<AnprSetup>(DEFAULT);
  const [focal, setFocal] = useState<number | null>(null);
  const f = Math.min(Math.max(focal ?? camera.focal_max_mm, camera.focal_min_mm), Math.max(camera.focal_max_mm, camera.focal_min_mm));
  const set = (p: Partial<AnprSetup>) => setS((x) => ({ ...x, ...p }));
  const roadW = s.lanes * s.lane_width_m;

  const input = useMemo(() => ({ camera, focal_mm: f, setup: s }), [camera, f, s]);
  const res = useCoalesced(input, (i) => bench.anpr(i), state === "ready" && active);
  const r = res.value;

  const auto = () => void job.run("ANPR odak ve nişan araması", (o) => bench.anprOptimize({ camera, setup: s }, o)).then((b) => {
    if (!b) return;
    setFocal(b.focal_mm);
    set({ aim_y_m: b.setup.aim_y_m, aim_x_m: b.setup.aim_x_m });
  });
  const aimLabel = s.aim_x_m === null || Math.abs(s.aim_x_m - roadW / 2) < 0.01 ? "mid"
    : String(Math.round(s.aim_x_m / s.lane_width_m - 0.5) + 1);

  return (
    <>
      <Panel title="Yol ve montaj" className="w-[300px] shrink-0">
        <div className="mb-2 truncate text-[12px] font-semibold">{camera.name} · <span className="text-text-2">{camera.model_name}</span></div>
        <div className="flex flex-col gap-3">
          <Slider label="Şerit sayısı" unit="" digits={0} value={s.lanes} min={1} max={6} step={1} onChange={(v) => set({ lanes: v, aim_x_m: null })} />
          <Slider label="Şerit genişliği" unit="m" value={s.lane_width_m} min={2.5} max={4.5} step={0.1} onChange={(v) => set({ lane_width_m: v, aim_x_m: null })} />
          <Slider label="Direğin yeri (yol kenarı = 0)" unit="m" value={s.pole_x_m} min={-15} max={roadW} step={0.25} onChange={(v) => set({ pole_x_m: v })} />
          <Slider label="Kamera yüksekliği" unit="m" value={s.height_m} min={1} max={15} step={0.25} onChange={(v) => set({ height_m: v })} />
          <Slider label="Plaka yüksekliği" unit="m" value={s.plate_height_m} min={0.3} max={1.5} step={0.05} onChange={(v) => set({ plate_height_m: v })} />
          <Slider label="Nişan mesafesi" unit="m" value={s.aim_y_m} min={3} max={150} step={1} onChange={(v) => set({ aim_y_m: v })} />
          <label><span className="label">Nişan şeridi</span>
            <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" value={aimLabel} aria-label="Nişan şeridi"
              onChange={(e) => set({ aim_x_m: e.target.value === "mid" ? null : (Number(e.target.value) - 0.5) * s.lane_width_m })}>
              <option value="mid">Yolun ortası</option>
              {Array.from({ length: s.lanes }, (_, i) => <option key={i} value={String(i + 1)}>{i + 1}. şerit</option>)}
            </select></label>
          {camera.focal_max_mm > camera.focal_min_mm && (
            <Slider label="Odak" unit="mm" value={f} min={camera.focal_min_mm} max={camera.focal_max_mm} step={(camera.focal_max_mm - camera.focal_min_mm) / 100} onChange={setFocal} />
          )}
        </div>
      </Panel>

      <div className="flex min-w-[420px] flex-[3_1_480px] flex-col gap-3">
        <Panel title="Yol (kuş bakışı)" right={<span className="font-mono text-[11px] text-text-3">{r ? `yatay ${fmt(r.pan_deg)}° · aşağı ${fmt(r.tilt_deg)}° · FOV ${fmt(r.hfov_deg)}°` : ""}</span>}>
          {r ? <RoadView r={r} /> : <div className="flex h-[220px] items-center justify-center text-text-3">{res.error ?? "Hesaplanıyor…"}</div>}
        </Panel>
        {r && (
          <Panel title="Şeritler">
            <table className="w-full font-mono text-[11px]">
              <thead><tr className="text-text-3">
                {["Şerit", "Okuma bölgesi", "Kare", "En yüksek hız", "Plaka px", "Açı (y/d)", "Bulanıklık"].map((h) => <th key={h} className="pb-1 text-right font-normal first:text-left">{h}</th>)}
              </tr></thead>
              <tbody>
                {r.lanes.map((ln) => (
                  <tr key={ln.lane} className={ln.ok ? "" : "text-warn"}>
                    <td className="font-sans">{ln.ok ? "●" : "▲"} {ln.lane}.</td>
                    <td className="text-right">{ln.zone_y0_m === null ? "—" : `${fmt(ln.zone_y0_m, 0)}–${fmt(ln.zone_y1_m, 0)} m`}</td>
                    <td className="text-right">{ln.zone_len_m ? fmt(ln.frames, 1) : "—"}</td>
                    <td className="text-right">{ln.zone_len_m ? `${fmt(ln.max_speed_kmh, 0)} km/sa` : "—"}</td>
                    <td className="text-right">{ln.zone_len_m ? fmt(ln.plate_px_min, 0) : "—"}</td>
                    <td className="text-right">{ln.zone_len_m ? `${fmt(ln.h_angle_max_deg, 0)}°/${fmt(ln.v_angle_max_deg, 0)}°` : "—"}</td>
                    <td className="text-right">{ln.zone_len_m ? `${fmt(ln.blur_px_max, 1)} px` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {r.lanes.flatMap((ln) => ln.reasons.map((m) => <div key={`${ln.lane}${m}`} className="mt-1 text-[11px] text-warn">▲ {ln.lane}. şerit: {m}</div>))}
            <p className="mt-2 text-[11px] text-text-3">◆ Düz yol, düz zemin. Plaka {fmt(s.target_ppm, 0)} px/m = plaka genişliğinde {fmt(s.target_ppm * 0.52, 0)} px. Yan şeritteki aracın plakayı örtmesi hesaba katılmaz.</p>
          </Panel>
        )}
      </div>

      <div className="flex w-[320px] shrink-0 flex-col gap-3">
        {r && <Verdict r={r} />}
        <JobBar job={job} onCancel={job.cancel} onDismiss={job.clearError} />
        <Button primary className="w-full" disabled={job.running || state !== "ready"} onClick={auto}><Sparkles size={14} /> Odak ve nişanı otomatik ayarla</Button>
        <Panel title="Araç ve kamera">
          <div className="flex flex-col gap-3">
            <Slider label="Araç hızı" unit="km/sa" digits={0} value={s.speed_kmh} min={10} max={200} step={5} onChange={(v) => set({ speed_kmh: v })} />
            <Slider label="Kare hızı" unit="fps" digits={0} value={s.fps} min={5} max={60} step={1} onChange={(v) => set({ fps: v })} />
            <Slider label="En az kare (okuma için)" unit="" digits={0} value={s.min_frames} min={1} max={10} step={1} onChange={(v) => set({ min_frames: v })} />
            <label><span className="label">Enstantane (pozlama)</span>
              <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" aria-label="Enstantane"
                value={String(Math.round(1 / s.exposure_s))} onChange={(e) => set({ exposure_s: 1 / Number(e.target.value) })}>
                {SHUTTERS.map((d) => <option key={d} value={String(d)}>1/{d} sn</option>)}
              </select></label>
            <Slider label="Bulanıklık sınırı" unit="px" value={s.blur_px} min={0.5} max={5} step={0.5} onChange={(v) => set({ blur_px: v })} />
            <Slider label="Plaka hedefi" unit="px/m" digits={0} value={s.target_ppm} min={80} max={400} step={1} onChange={(v) => set({ target_ppm: v })} />
            <Slider label="En büyük yatay açı" unit="°" value={s.max_h_deg} min={10} max={60} step={1} onChange={(v) => set({ max_h_deg: v })} />
            <Slider label="En büyük dikey açı" unit="°" value={s.max_v_deg} min={10} max={60} step={1} onChange={(v) => set({ max_v_deg: v })} />
            <label className="flex items-center gap-2 text-[12px]"><input type="checkbox" checked={s.night_ir} onChange={(e) => set({ night_ir: e.target.checked })} />
              Gece: plaka IR menzili içinde olsun ({fmt(camera.ir_range_m, 0)} m)</label>
          </div>
        </Panel>
      </div>
    </>
  );
}

function Verdict({ r }: { r: AnprResult }) {
  const all = r.lanes_ok === r.lanes.length;
  const worst = r.lanes.reduce<AnprLane | null>((w, ln) => (w === null || ln.max_speed_kmh < w.max_speed_kmh ? ln : w), null);
  const exp = r.lanes.map((ln) => ln.max_exposure_s).filter((x): x is number => x !== null && x > 0);
  return (
    <>
      <section className={`rounded-md p-3 ${all ? "bg-ok-bg" : "bg-danger-bg"}`} aria-live="polite">
        <div className={`flex items-center gap-2 text-[16px] font-bold ${all ? "text-primary" : "text-danger"}`}>
          {all ? <CheckCircle2 size={20} /> : <XCircle size={20} />}{r.lanes_ok}/{r.lanes.length} şerit okunur
        </div>
        <div className="mt-1 text-[12px] text-text-2">{fmt(r.setup.speed_kmh, 0)} km/sa · {fmt(r.setup.fps, 0)} fps · 1/{fmt(1 / r.setup.exposure_s, 0)} sn · odak {fmt(r.focal_mm)} mm</div>
      </section>
      <div className="grid grid-cols-2 gap-1.5">
        <Tile label="En zayıf şeridin hız sınırı" value={worst && worst.zone_len_m ? fmt(worst.max_speed_kmh, 0) : "—"} unit="km/sa"
          tone={worst && worst.max_speed_kmh >= r.setup.speed_kmh ? "text-primary" : "text-warn"} />
        <Tile label="En uzun enstantane" value={exp.length ? `1/${fmt(Math.ceil(1 / Math.min(...exp)), 0)}` : "—"} unit="sn"
          tone={exp.length && Math.min(...exp) >= r.setup.exposure_s ? "text-primary" : "text-warn"} />
      </div>
    </>
  );
}

/** Top-down road: travel along x (away from the pole to the right), lanes stacked; readable zones per lane. */
function RoadView({ r }: { r: AnprResult }) {
  const s = r.setup;
  const roadW = s.lanes * s.lane_width_m;
  const far = Math.max(s.aim_y_m * 1.4, ...r.lanes.map((ln) => (ln.zone_y1_m ?? 0) + 8), 30);
  const x0 = -6, x1 = far;
  const yLo = Math.min(s.pole_x_m, 0) - 3, yHi = Math.max(s.pole_x_m, roadW) + 3;
  const W = 900, H = Math.max(160, Math.min(320, ((yHi - yLo) / (x1 - x0)) * W * 3));
  const sx = (y: number) => ((y - x0) / (x1 - x0)) * W;
  const sy = (x: number) => H - ((x - yLo) / (yHi - yLo)) * H;     // lane 1 (x small) at the bottom
  const rad = (d: number) => (d * Math.PI) / 180;
  const ray = (a: number) => {
    // ground ray from the pole at horizontal angle a (deg from the road axis, + towards larger x)
    const L = far * 1.5;
    return `${sx(L * Math.cos(rad(a)))},${sy(s.pole_x_m + L * Math.sin(rad(a)))}`;
  };
  const ticks = [];
  for (let d = 0; d <= far; d += far > 120 ? 20 : 10) ticks.push(d);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="block w-full" role="img" aria-label="Yol şeması">
      <rect x={sx(x0)} y={sy(roadW)} width={sx(x1) - sx(x0)} height={sy(0) - sy(roadW)} fill="var(--color-bg-1)" />
      {Array.from({ length: s.lanes + 1 }, (_, i) => (
        <line key={i} x1={0} x2={W} y1={sy(i * s.lane_width_m)} y2={sy(i * s.lane_width_m)} stroke="var(--color-text-3)"
          strokeWidth={i === 0 || i === s.lanes ? 1.5 : 1} strokeDasharray={i === 0 || i === s.lanes ? "" : "10 8"} />
      ))}
      <clipPath id="anpr-clip"><rect x={0} y={0} width={W} height={H} /></clipPath>
      <polygon clipPath="url(#anpr-clip)" points={`${sx(0)},${sy(s.pole_x_m)} ${ray(r.pan_deg - r.hfov_deg / 2)} ${ray(r.pan_deg + r.hfov_deg / 2)}`}
        fill="var(--color-secondary)" opacity={0.12} />
      {r.lanes.map((ln) => ln.zone_y0_m !== null && (
        <rect key={ln.lane} x={sx(ln.zone_y0_m)} width={Math.max(2, sx(ln.zone_y1_m ?? ln.zone_y0_m) - sx(ln.zone_y0_m))}
          y={sy(ln.x_m + s.lane_width_m * 0.32)} height={sy(ln.x_m - s.lane_width_m * 0.32) - sy(ln.x_m + s.lane_width_m * 0.32)}
          fill={ln.ok ? "var(--color-primary)" : "var(--color-warn)"} opacity={0.75} rx={3}>
          <title>{`${ln.lane}. şerit: ${fmt(ln.zone_y0_m, 0)}–${fmt(ln.zone_y1_m, 0)} m`}</title>
        </rect>
      ))}
      <line x1={sx(s.aim_y_m)} x2={sx(s.aim_y_m)} y1={sy(roadW) - 4} y2={sy(0) + 4} stroke="var(--color-secondary)" strokeDasharray="3 3" />
      {r.lanes.map((ln) => (
        <text key={`t${ln.lane}`} x={6} y={sy(ln.x_m) + 4} fontSize={11} fill="var(--color-text-3)">{ln.lane}</text>
      ))}
      <circle cx={sx(0)} cy={sy(s.pole_x_m)} r={6} fill="var(--color-primary)" stroke="var(--color-bg-0)" strokeWidth={2} />
      {ticks.map((d) => (
        <text key={d} x={sx(d)} y={H - 4} fontSize={11} textAnchor="middle" fill="var(--color-text-3)">{d} m</text>
      ))}
    </svg>
  );
}
