import { useEffect, useMemo, useRef, useState } from "react";
import { useCoalesced, useCore } from "../core/CoreProvider";
import { usePageActive } from "../core/pageActive";
import { useProject } from "../core/project";
import { Panel, Slider, fmt } from "../components/ui";

const PALETTES = [
  ["auto", "Otomatik"], ["day", "Gündüz"], ["ir", "Gece IR"],
  ["thermal_white", "Termal (beyaz sıcak)"], ["thermal_black", "Termal (siyah sıcak)"], ["thermal_ironbow", "Termal (ironbow)"],
] as const;
const TARGETS = [["human", "İnsan (1,8 m)"], ["vehicle", "Araç (2,3 m)"], ["object", "Nesne (1,0 m)"]] as const;

/** What the camera itself would show: real FOV, sensor and MTF loss, palette, weather. */
export default function View3D() {
  const active = usePageActive();
  const { bench, terrain, state } = useCore();
  const { site, update } = useProject();
  const c = site.camera;
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 960, h: 540 });
  const [weathers, setWeathers] = useState<string[]>(["Berrak hava"]);
  const [p, setP] = useState({ target: "human", dist: 15, lateral: 0, palette: "auto", weather: "Berrak hava", dori: true, grid: true });

  useEffect(() => {
    if (state === "ready") terrain.catalog().then((t) => setWeathers(t.weather.map((w) => w.label))).catch(() => undefined);
  }, [terrain, state]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      const w = Math.max(320, Math.round(e.contentRect.width));
      setSize({ w, h: Math.round(w * 9 / 16) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const input = useMemo(() => ({ c, focal: site.focal, ...p, ...size }), [c, site.focal, p, size]);
  const res = useCoalesced(input, (i) => bench.scene({
    camera: i.c, focal_mm: i.focal, width: i.w, height: i.h, target: i.target, target_dist_m: i.dist,
    lateral_m: i.lateral, palette: i.palette, weather: i.weather, show_dori: i.dori, max_dist_m: Math.max(100, i.dist * 1.5),
  }), state === "ready" && active);
  const f = res.value;
  const atmAlarm = f && f.visibility_km < 20 && f.transmittance < 0.3;
  const varifocal = c.focal_max_mm > c.focal_min_mm;

  return (
    <div className="flex min-h-full flex-wrap content-start gap-3 p-3">
      <div className="flex min-w-[480px] flex-[3_1_640px] flex-col gap-2">
        <div ref={box} className="relative w-full overflow-hidden rounded-md bg-bg-0" style={{ aspectRatio: "16 / 9" }}>
          {f && <img src={f.frame.data} alt={`${c.name} kamera görüntüsü, hedef ${fmt(p.dist)} m`} className="absolute inset-0 h-full w-full" />}
          {f && (
            <svg viewBox={`0 0 ${f.width} ${f.height}`} className="absolute inset-0 h-full w-full" aria-hidden>
              {p.grid && f.grid.map((g, i) => (
                <line key={i} x1={g.u1} y1={g.v1} x2={g.u2} y2={g.v2} stroke={g.center ? "rgba(120,140,150,.8)" : "rgba(176,190,197,.35)"} strokeWidth={g.major || g.center ? 1.5 : 0.8} />
              ))}
              {p.grid && f.grid.filter((g) => g.label && g.lu != null && g.lv != null && g.lu >= 0 && g.lu <= f.width).map((g, i) => (
                <text key={i} x={g.lu! + 14} y={g.lv!} fontSize="11" fill="#eceff1" fontFamily="var(--font-mono)" stroke="rgba(0,0,0,.5)" strokeWidth={2.5} paintOrder="stroke">{g.label}</text>
              ))}
              {f.horizon_v > 0 && f.horizon_v < f.height && (
                <g><line x1={0} y1={f.horizon_v} x2={f.width} y2={f.horizon_v} stroke="#b0bec5" strokeDasharray="4 4" />
                  <text x={f.width - 54} y={Math.max(f.horizon_v - 8, 14)} fontSize="10" fill="#cfd8dc" fontFamily="var(--font-mono)">UFUK</text></g>
              )}
              {f.target_head.visible && (
                <g>
                  <rect x={f.target_head.u - 52} y={Math.max(f.target_head.v - 30, 2)} width={104} height={18} rx={3} fill="rgba(15,19,28,.82)" stroke="rgba(255,255,255,.18)" />
                  <text x={f.target_head.u} y={Math.max(f.target_head.v - 30, 2) + 13} textAnchor="middle" fontSize="11" fill="#dfe2ee" fontFamily="var(--font-mono)">
                    {fmt(p.dist, 0)} m · {fmt(f.target_px, 0)} px
                  </text>
                </g>
              )}
            </svg>
          )}
          {!f && <div className="absolute inset-0 flex items-center justify-center text-text-3">{res.error ?? "Kare üretiliyor…"}</div>}
          {res.busy && <div className="absolute top-2 right-2 rounded bg-bg-1/80 px-2 py-0.5 font-mono text-[10px] text-text-3">işleniyor…</div>}
        </div>
        {f && (
          <div className="flex flex-wrap items-center gap-3 rounded-md bg-bg-4 px-3 py-2 font-mono text-[12px]">
            <span className={`rounded px-2 py-0.5 font-bold ${atmAlarm ? "bg-danger-bg text-danger" : "bg-info-bg text-dori-identify"}`}>
              {atmAlarm ? `▲ ATMOSFER · ${f.dori_name}` : f.dori_name}
            </span>
            <span>{f.is_thermal ? `${fmt(f.target_px, 1)} px (%${fmt(f.target_pct_h, 2)}) · ${fmt(f.ppm, 2)} PPM` : `${fmt(f.ppm, 1)} PPM`}</span>
            <span className="text-text-3">HFOV {fmt(f.hfov_deg)}° · lens {fmt(site.focal)} mm · direk {fmt(c.pole_height_m)} m</span>
            {f.visibility_km < 20 && <span className={f.transmittance < 0.5 ? "text-warn" : "text-text-3"}>hava: {f.weather} · iletim %{fmt(f.transmittance * 100, 0)}</span>}
            {f.is_thermal && <span className={f.target_pct_h >= 1.5 ? "text-primary" : "text-warn"}>{f.target_pct_h >= 1.5 ? "● Algoritma %1,5 eşiği OK" : "▲ Algoritma %1,5 eşiği altında"}</span>}
          </div>
        )}
      </div>

      <div className="flex w-[320px] shrink-0 flex-col gap-3">
        <Panel title="Kamera">
          <div className="mb-2 truncate text-[12px] font-semibold">{c.name} · <span className="text-text-2">{c.model_name}</span></div>
          <div className="flex flex-col gap-3">
            <Slider label="Odak" unit="mm" value={site.focal} min={c.focal_min_mm} max={c.focal_max_mm} step={(c.focal_max_mm - c.focal_min_mm) / 200 || 1}
              disabled={!varifocal} onChange={(v) => update({}, v)} />
            <Slider label="Eğim (aşağı)" unit="°" value={c.tilt_deg} min={0} max={60} step={0.5} onChange={(v) => update({ tilt_deg: v })} />
            <Slider label="Direk" unit="m" value={c.pole_height_m} min={1} max={30} step={0.5} onChange={(v) => update({ pole_height_m: v })} />
          </div>
        </Panel>
        <Panel title="Hedef">
          <div className="mb-3 grid grid-cols-3 gap-1">
            {TARGETS.map(([id, l]) => (
              <button key={id} type="button" onClick={() => setP((s) => ({ ...s, target: id }))}
                className={`h-8 rounded text-[11px] font-semibold ${p.target === id ? "bg-bg-6 text-primary" : "bg-bg-5 text-text-2"}`}>{l.split(" (")[0]}</button>
            ))}
          </div>
          <div className="flex flex-col gap-3">
            <Slider label="Mesafe" unit="m" value={p.dist} min={1} max={300} step={0.5} onChange={(v) => setP((s) => ({ ...s, dist: v }))} />
            <Slider label="Yanal kayma" unit="m" value={p.lateral} min={-30} max={30} step={0.5} onChange={(v) => setP((s) => ({ ...s, lateral: v }))} />
          </div>
        </Panel>
        <Panel title="Görüntü">
          <label className="block"><span className="label">Palet</span>
            <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" value={p.palette} onChange={(e) => setP((s) => ({ ...s, palette: e.target.value }))}>
              {PALETTES.map(([id, l]) => <option key={id} value={id}>{l}</option>)}
            </select></label>
          <label className="mt-2 block"><span className="label">Hava</span>
            <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" value={p.weather} onChange={(e) => setP((s) => ({ ...s, weather: e.target.value }))}>
              {weathers.map((w) => <option key={w} value={w}>{w}</option>)}
            </select></label>
          <label className="mt-2 flex items-center gap-2 text-[12px]"><input type="checkbox" checked={p.dori} onChange={(e) => setP((s) => ({ ...s, dori: e.target.checked }))} /> DORI bantları zeminde</label>
          <label className="mt-1 flex items-center gap-2 text-[12px]"><input type="checkbox" checked={p.grid} onChange={(e) => setP((s) => ({ ...s, grid: e.target.checked }))} /> Mesafe ızgarası</label>
          <p className="mt-2 text-[11px] text-text-3">◆ Kare gerçek FOV ve sensör çözünürlüğüyle üretilir: uzaktaki hedef ekranda da gerçekten az pikseldir. Sağ alttaki pencere hedefe dijital yakınlaştırmadır.</p>
        </Panel>
      </div>
    </div>
  );
}
