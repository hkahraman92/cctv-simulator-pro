import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Camera, Plus, Trash2 } from "lucide-react";
import { DORI, type ModelSummary } from "../core/api";
import { useProject } from "../core/project";
import { useCoalesced, useCore } from "../core/CoreProvider";
import { usePageActive } from "../core/pageActive";
import { Button, Panel, Slider, Tile, fmt } from "../components/ui";
import PlanView, { atGeomLimit } from "../components/PlanView";

export default function Workspace({ onStatus }: { onStatus: (s: string) => void }) {
  const active = usePageActive();
  const { api, state } = useCore();
  const { sites, selected: sel, site, select: setSel, add, remove, update, replaceCamera } = useProject();
  const [models, setModels] = useState<ModelSummary[]>([]);

  useEffect(() => {
    if (state !== "ready") return;
    api.library().then((r) => setModels(r.models)).catch(() => setModels([]));
  }, [api, state]);

  const input = useMemo(() => ({ camera: site.camera, focal: site.focal }), [site]);
  const res = useCoalesced(input, (i) => api.optics(i.camera, { focal_mm: i.focal }), state === "ready" && active);
  const r = res.value;

  useEffect(() => {
    if (res.ms !== undefined) onStatus(`Hesap ${fmt(res.ms, 0)} ms`);
  }, [res.ms, onStatus]);

  const applyModel = async (name: string) => {
    const { camera } = await api.cameraFor(name, site.camera);
    replaceCamera(camera);
  };

  const c = site.camera;
  const varifocal = c.focal_max_mm > c.focal_min_mm;
  const identify = r?.ground_distances.Characterize;

  return (
    <div className="flex min-h-full flex-wrap content-start gap-3 p-3">
      <Panel title="Kameralar" icon={<Camera size={16} className="text-secondary" />} className="w-[220px] shrink-0"
        right={
          <button type="button" aria-label="Kamera ekle" className="text-text-2 hover:text-primary"
            onClick={add}>
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
          <Button className="mt-2 w-full" onClick={() => remove(sel)}>
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
          <span className="label">Kamera adı</span>
          <input className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" value={c.name}
            onChange={(e) => update({ name: e.target.value })} aria-label="Kamera adı" />
          {sites.filter((s) => s.camera.name === c.name).length > 1 && <span className="text-[11px] text-warn">▲ Bu ad başka bir kamerada da var</span>}
        </label>
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
