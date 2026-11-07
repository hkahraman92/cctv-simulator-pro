import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Camera, Plus, Trash2 } from "lucide-react";
import { DEFAULT_CAMERA, DORI, type CameraConfig, type ModelSummary, type OpticResult } from "../core/api";
import { useCoalesced, useCore } from "../core/CoreProvider";
import { Button, Panel, Slider, Tile, fmt } from "../components/ui";

interface Site {
  camera: CameraConfig;
  focal: number;
}

const newSite = (n: number): Site => ({
  camera: { ...DEFAULT_CAMERA, name: `Kamera ${n}` },
  focal: DEFAULT_CAMERA.focal_min_mm,
});

export default function Workspace({ onStatus }: { onStatus: (s: string) => void }) {
  const { api, state } = useCore();
  const [sites, setSites] = useState<Site[]>([newSite(1)]);
  const [sel, setSel] = useState(0);
  const [models, setModels] = useState<ModelSummary[]>([]);
  const site = sites[Math.min(sel, sites.length - 1)];

  useEffect(() => {
    if (state !== "ready") return;
    api.library().then((r) => setModels(r.models)).catch(() => setModels([]));
  }, [api, state]);

  const input = useMemo(() => ({ camera: site.camera, focal: site.focal }), [site]);
  const res = useCoalesced(input, (i) => api.optics(i.camera, { focal_mm: i.focal }), state === "ready");
  const r = res.value;

  useEffect(() => {
    if (res.ms !== undefined) onStatus(`Hesap ${fmt(res.ms, 0)} ms`);
  }, [res.ms, onStatus]);

  const update = (patch: Partial<CameraConfig>, focal?: number) =>
    setSites((all) =>
      all.map((s, i) => {
        if (i !== sel) return s;
        const camera = { ...s.camera, ...patch };
        const f = focal ?? Math.min(Math.max(s.focal, camera.focal_min_mm), camera.focal_max_mm);
        return { camera, focal: f };
      }),
    );

  const applyModel = async (name: string) => {
    const { camera } = await api.cameraFor(name, site.camera);
    setSites((all) => all.map((s, i) => (i === sel ? { camera, focal: camera.focal_min_mm } : s)));
  };

  const c = site.camera;
  const varifocal = c.focal_max_mm > c.focal_min_mm;
  const identify = r?.ground_distances.Characterize;

  return (
    <div className="flex min-h-full flex-wrap content-start gap-3 p-3">
      <Panel title="Kameralar" icon={<Camera size={16} className="text-secondary" />} className="w-[220px] shrink-0"
        right={
          <button type="button" aria-label="Kamera ekle" className="text-text-2 hover:text-primary"
            onClick={() => { setSites((a) => [...a, newSite(a.length + 1)]); setSel(sites.length); }}>
            <Plus size={16} />
          </button>
        }>
        <ul className="flex flex-col gap-1">
          {sites.map((s, i) => (
            <li key={i}>
              <button type="button" onClick={() => setSel(i)}
                className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left ${i === sel ? "bg-bg-5 text-primary" : "text-text-2 hover:bg-bg-5"}`}>
                <span className="flex-1 truncate">{s.camera.name}</span>
                <span className="font-mono text-[10px] text-text-3">{fmt(s.focal)} mm</span>
              </button>
            </li>
          ))}
        </ul>
        {sites.length > 1 && (
          <Button className="mt-2 w-full" onClick={() => { setSites((a) => a.filter((_, i) => i !== sel)); setSel(0); }}>
            <Trash2 size={14} /> Seçiliyi Sil
          </Button>
        )}
      </Panel>

      <Panel title="Kuş Bakışı Plan" className="min-w-[380px] flex-[3_1_420px]"
        right={<span className="font-mono text-[10px] text-text-3">{res.busy ? "hesaplanıyor…" : r ? `${fmt(r.focal_mm)} mm` : ""}</span>}>
        {r ? <PlanView r={r} /> : <Empty state={state} error={res.error} />}
        <div className="mt-2 grid grid-cols-4 gap-1.5">
          {DORI.map((d) => (
            <div key={d.key} className="rounded bg-bg-1 p-2">
              <div className="flex items-center gap-1.5 font-mono text-[10px] text-text-3">
                <span className="h-2.5 w-2.5 rounded-sm" style={{ background: `var(--color-${d.tone})` }} />
                {d.label} ≥{fmt(d.ppm, d.ppm % 1 ? 1 : 0)}
              </div>
              <div className="font-mono text-[16px] font-bold">{r ? fmt(r.ground_distances[d.key]) : "—"} m</div>
              {r && atGeomLimit(r, d.key) && <div className="font-mono text-[10px] text-warn">▲ geometrik sınır</div>}
            </div>
          ))}
        </div>
        {r && DORI.some((d) => atGeomLimit(r, d.key)) && (
          <p className="mt-2 text-[11px] text-text-2">
            ▲ Kadrajın üst kenarı zemine {fmt(r.max_geom_dist_m)} m'de iniyor; bu seviyeler lensle değil eğimle sınırlı. Daha uzağı görmek için eğimi azaltın.
          </p>
        )}
      </Panel>

      <Panel title="İnceleme" className="flex-[2_1_300px]">
        <label className="mb-3 block">
          <span className="label">Kütüphane modeli</span>
          <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]"
            value={models.some((m) => m.name === c.model_name) ? c.model_name : ""}
            onChange={(e) => e.target.value && void applyModel(e.target.value)} disabled={!models.length}>
            <option value="">{models.length ? "Model seç…" : "Kütüphane yükleniyor…"}</option>
            {models.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
          </select>
        </label>
        <div className="mb-3 font-mono text-[11px] text-text-2">
          {c.sensor_name} · {c.resolution_name} · {fmt(c.focal_min_mm)}{varifocal && `–${fmt(c.focal_max_mm)}`} mm
        </div>
        <div className="flex flex-col gap-3">
          <Slider label="Odak" unit="mm" value={site.focal} min={c.focal_min_mm} max={c.focal_max_mm}
            step={(c.focal_max_mm - c.focal_min_mm) / 200 || 1} disabled={!varifocal} onChange={(f) => update({}, f)} />
          <Slider label="Eğim (aşağı)" unit="°" value={c.tilt_deg} min={0} max={60} step={0.5} onChange={(v) => update({ tilt_deg: v })} />
          <Slider label="Direk yüksekliği" unit="m" value={c.pole_height_m} min={2} max={30} step={0.5} onChange={(v) => update({ pole_height_m: v })} />
          <Slider label="Hedef yüksekliği" unit="m" value={c.target_height_m} min={0.5} max={2.5} step={0.1} onChange={(v) => update({ target_height_m: v })} />
        </div>
        <div className="mt-3 grid grid-cols-2 gap-1.5">
          <Tile label="Yatay FOV" value={r ? fmt(r.hfov_deg) : "—"} unit="°" tone="text-secondary" />
          <Tile label="Dikey FOV" value={r ? fmt(r.vfov_deg) : "—"} unit="°" />
          <Tile label="Kör nokta" value={r ? fmt(r.dead_zone_m) : "—"} unit="m" tone={r && r.dead_zone_m > 3 ? "text-warn" : "text-text"} />
          <Tile label="Teşhis menzili" value={r ? fmt(identify) : "—"} unit="m" tone="text-dori-identify" />
        </div>
        {r && c.ir_range_m > 0 && r.ground_distances.Outline != null && r.ground_distances.Outline > c.ir_range_m && (
          <div className="mt-3 flex gap-2 rounded bg-warn-bg p-2 text-[11px] text-warn">
            <AlertTriangle size={14} className="shrink-0" />
            Gece IR menzili ({fmt(c.ir_range_m, 0)} m) Algılama menzilinin ({fmt(r.ground_distances.Outline)} m) altında; gece kapsaması IR ile sınırlı.
          </div>
        )}
        {r?.recommendations.length ? (
          <ul className="mt-3 list-disc pl-4 text-[11px] text-text-2">
            {r.recommendations.slice(0, 4).map((t) => <li key={t}>{t}</li>)}
          </ul>
        ) : null}
      </Panel>
    </div>
  );
}

function Empty({ state, error }: { state: string; error?: string }) {
  return (
    <div className="flex h-[340px] items-center justify-center rounded bg-bg-2 p-4 text-center text-text-3">
      {error ? <span className="text-danger">{error}</span> : state === "down" ? "Çekirdeğe bağlı değil." : "Hesaplanıyor…"}
    </div>
  );
}

/** Top view drawn from engine numbers only: HFOV, dead zone, DORI distances. */
function PlanView({ r }: { r: OpticResult }) {
  const W = 520, H = 360, cx = W / 2, cy = H - 24;
  const dists = DORI.map((d) => r.ground_distances[d.key] ?? r.max_geom_dist_m ?? 0);
  const far = Math.max(...dists.filter((d) => Number.isFinite(d)), r.dead_zone_m + 1, 5);
  const scale = (H - 48) / far;
  const half = (Math.min(r.hfov_deg, 170) / 2) * (Math.PI / 180);
  const wedge = (r0: number, r1: number) => {
    const p = (rad: number, a: number) => `${cx + rad * Math.sin(a)} ${cy - rad * Math.cos(a)}`;
    const a = r0 * scale, b = Math.max(r1 * scale, a);
    return `M${p(a, -half)} L${p(b, -half)} A${b} ${b} 0 0 1 ${p(b, half)} L${p(a, half)} A${a} ${a} 0 0 0 ${p(a, -half)}Z`;
  };
  // Draw far-to-near so the better (nearer) level paints over the weaker one.
  const bands = DORI.map((d, i) => ({ ...d, to: dists[i] })).reverse();
  const step = niceStep(far);
  const rings = Array.from({ length: Math.floor(far / step) }, (_, i) => (i + 1) * step);

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
      <circle cx={cx} cy={cy} r={6} fill="var(--color-primary)" stroke="var(--color-bg-0)" strokeWidth={2} />
      <text x={cx + 10} y={cy + 4} fill="var(--color-text)" fontSize="11" fontFamily="var(--font-mono)">
        {r.camera.name} · {fmt(r.camera.pole_height_m)} m
      </text>
    </svg>
  );
}

/** The engine caps a level at the frame's top-edge ground distance (status "Geometrik limit"). */
function atGeomLimit(r: OpticResult, key: string) {
  const d = r.ground_distances[key];
  return r.max_geom_dist_m != null && d != null && d === r.max_geom_dist_m;
}

function niceStep(far: number) {
  const raw = far / 5;
  const p = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map((k) => k * p).find((s) => s >= raw) ?? p * 10;
}
