import { useEffect, useMemo, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { isTauri } from "@tauri-apps/api/core";
import { AlertTriangle, Camera, Crosshair, ImagePlus, Move, Plus, Trash2, X } from "lucide-react";
import { DORI, type ModelSummary, type PPMLevel } from "../core/api";
import LevelEditor from "../components/LevelEditor";
import { useProject } from "../core/project";
import { useCoalesced, useCore } from "../core/CoreProvider";
import { usePageActive } from "../core/pageActive";
import { Button, Panel, Slider, Tile, fmt } from "../components/ui";
import PlanView, { atGeomLimit } from "../components/PlanView";
import SitePlan from "../components/SitePlan";

export default function Workspace({ onStatus }: { onStatus: (s: string) => void }) {
  const active = usePageActive();
  const { api, bench, state } = useCore();
  const { sites, selected: sel, site, select: setSel, add, remove, update, updateAt, replaceCamera, plan, setPlan, levels, setLevels } = useProject();
  const [defaults, setDefaults] = useState<PPMLevel[]>([]);
  const effLevels = levels ?? defaults;
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [view, setView] = useState<"site" | "single">("site");
  const [tool, setTool] = useState<"move" | "target">("move");
  const [planImg, setPlanImg] = useState<{ data: string; aspect: number } | null>(null);
  const [planErr, setPlanErr] = useState("");

  // background picture follows the project's plan_path
  useEffect(() => {
    if (!plan.plan_path || state !== "ready") { setPlanImg(null); return; }
    bench.planImage(plan.plan_path).then((r) => { setPlanImg({ data: r.data, aspect: r.aspect }); setPlanErr(""); })
      .catch((e) => { setPlanImg(null); setPlanErr(String(e.message ?? e)); });
  }, [bench, plan.plan_path, state]);

  // every camera at once: DORI wedges, dead-zone overlap, check point
  const projInput = useMemo(() => ({
    cameras: sites.map((x) => x.camera), focals: sites.map((x) => x.focal),
    target: plan.target.active ? { x_m: plan.target.x_m, y_m: plan.target.y_m } : undefined, level_key: plan.level_key,
    ...(levels ? { levels } : {}),
  }), [sites, plan.target, plan.level_key, levels]);
  const dupNames = new Set(sites.map((x) => x.camera.name)).size !== sites.length;
  const proj = useCoalesced(projInput, (i) => bench.project(i), state === "ready" && active && !dupNames);

  useEffect(() => {
    if (state !== "ready") return;
    api.library().then((r) => setModels(r.models)).catch(() => setModels([]));
    api.catalog().then((c) => setDefaults(c.levels)).catch(() => undefined);
  }, [api, state]);

  const input = useMemo(() => ({ camera: site.camera, focal: site.focal, levels }), [site, levels]);
  const res = useCoalesced(input, (i) => api.optics(i.camera, { focal_mm: i.focal, ...(i.levels ? { levels: i.levels } : {}) }), state === "ready" && active);
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
  const planW = Math.max(plan.plan_width_m, 5);
  const planH = planImg ? planW * planImg.aspect : planW * 0.62;
  const mine = proj.value?.cameras.find((x) => x.name === c.name);

  const pickPlan = async () => {
    const f = await open({ title: "Vaziyet planı resmi", filters: [{ name: "Resim", extensions: ["png", "jpg", "jpeg", "bmp", "tif", "tiff"] }] });
    if (typeof f === "string") setPlan({ plan_path: f });
  };
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
        <div className="label mt-4 mb-1">Plan</div>
        <div className="flex gap-1.5">
          <Button className="flex-1" disabled={!isTauri()} onClick={pickPlan}><ImagePlus size={14} /> Resim</Button>
          {plan.plan_path && <Button title="Plan resmini kaldır" onClick={() => setPlan({ plan_path: "" })}><X size={14} /></Button>}
        </div>
        {plan.plan_path && <div className="mt-1 truncate font-mono text-[10px] text-text-3" title={plan.plan_path}>{plan.plan_path.split(/[\\/]/).pop()}</div>}
        {planErr && <div className="mt-1 text-[11px] text-danger">■ {planErr}</div>}
        <label className="mt-2 block"><span className="label">Plan genişliği (m)</span>
          <input type="number" min={5} step={1} value={plan.plan_width_m} onChange={(e) => { const v = Number(e.target.value); if (v >= 5) setPlan({ plan_width_m: v }); }}
            className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 font-mono text-[12px]" /></label>
        <div className="label mt-4 mb-1">Kontrol noktası</div>
        <label className="flex items-center gap-2 text-[12px]">
          <input type="checkbox" checked={plan.target.active} onChange={(e) => setPlan({ target: { ...plan.target, active: e.target.checked } })} /> Etkin
        </label>
        {plan.target.active && (
          <>
            <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-1 text-[12px]" value={plan.level_key} aria-label="Gereken seviye"
              onChange={(e) => setPlan({ level_key: e.target.value })}>
              {[...effLevels].sort((a, b) => b.ppm - a.ppm).map((lv) => <option key={lv.key} value={lv.key}>{lv.name} · {fmt(lv.ppm, lv.ppm % 1 ? 1 : 0)}</option>)}
            </select>
            <div className="mt-1 font-mono text-[10px] text-text-3">({fmt(plan.target.x_m)}, {fmt(plan.target.y_m)}) m · ⌖ ile haritaya tıklayın</div>
            <ul className="mt-2 flex flex-col gap-1 text-[11px]">
              {proj.value?.cameras.map((pc) => pc.target && (
                <li key={pc.name} className={pc.target.status === "uygun" ? "text-primary" : "text-text-2"}>
                  {pc.target.status === "uygun" ? "●" : "○"} {pc.name}: {fmt(pc.target.ppm, 0)} px/m · {fmt(pc.target.distance)} m{pc.target.status !== "uygun" ? ` — ${pc.target.status}` : ""}
                </li>
              ))}
            </ul>
          </>
        )}
      </Panel>

      <Panel title={view === "site" ? "Vaziyet Planı" : "Seçili kamera"} className="min-w-[380px] flex-[3_1_420px]"
        right={
          <div className="flex items-center gap-1">
            {view === "site" && (
              <>
                <button type="button" title="Taşı / döndür" onClick={() => setTool("move")} className={`rounded p-1 ${tool === "move" ? "bg-bg-6 text-primary" : "text-text-2"}`}><Move size={15} /></button>
                <button type="button" title="Kontrol noktası koy" onClick={() => { setTool("target"); setPlan({ target: { ...plan.target, active: true } }); }}
                  className={`rounded p-1 ${tool === "target" ? "bg-bg-6 text-secondary" : "text-text-2"}`}><Crosshair size={15} /></button>
              </>
            )}
            {([["site", "Plan"], ["single", "Tek kamera"]] as const).map(([id, l]) => (
              <button key={id} type="button" onClick={() => setView(id)}
                className={`h-7 rounded-full px-3 text-[11px] font-semibold ${view === id ? "bg-secondary text-on-secondary" : "bg-bg-5 text-text-2"}`}>{l}</button>
            ))}
          </div>
        }>
        {view === "site" ? (
          <div className="h-[420px]">
            {dupNames ? <Empty state={state} error="Kamera adları benzersiz olmalı — İnceleme'den yeniden adlandırın." /> : (
              <SitePlan sites={sites} results={proj.value?.cameras} selected={sel} widthM={planW} heightM={planH} image={planImg?.data}
                target={plan.target} tool={tool} onSelect={setSel}
                onMove={(i, x, y) => updateAt(i, { pos_x_m: x, pos_y_m: y })}
                onHeading={(i, h) => updateAt(i, { heading_deg: h })}
                onTarget={(x, y) => setPlan({ target: { x_m: x, y_m: y, active: true } })}
                onDragEnd={() => undefined} />
            )}
          </div>
        ) : r ? <PlanView r={r} /> : <Empty state={state} error={res.error} />}
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

      <LevelEditor levels={effLevels} defaults={defaults} onChange={(l) => setLevels(l)} onReset={() => setLevels(null)} custom={!!levels} />

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
          <Slider label="Yön (0° = doğu, saat tersi)" unit="°" value={c.heading_deg} min={0} max={359} step={1} onChange={(v) => update({ heading_deg: v })} />
          <div className="grid grid-cols-2 gap-2">
            <label><span className="label">X (m)</span><input type="number" step={0.5} value={c.pos_x_m} onChange={(e) => update({ pos_x_m: Number(e.target.value) || 0 })}
              className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 font-mono text-[12px]" /></label>
            <label><span className="label">Y (m)</span><input type="number" step={0.5} value={c.pos_y_m} onChange={(e) => update({ pos_y_m: Number(e.target.value) || 0 })}
              className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 font-mono text-[12px]" /></label>
          </div>
        </div>
        {mine && mine.dead_zone_m > 0.5 && sites.length > 1 && (
          <div className={`mt-3 rounded p-2 text-[11px] ${mine.dead_zone_covered_by.length ? "bg-ok-bg text-primary" : "bg-warn-bg text-warn"}`}>
            {mine.dead_zone_covered_by.length
              ? `● Kör nokta (${fmt(mine.dead_zone_m)} m) şu kameralarca kapatılıyor: ${mine.dead_zone_covered_by.join(", ")}`
              : `▲ Kör nokta (${fmt(mine.dead_zone_m)} m) başka hiçbir kamerayla kapatılmıyor.`}
          </div>
        )}
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
