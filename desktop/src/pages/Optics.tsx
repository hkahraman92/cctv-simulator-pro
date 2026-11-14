import { useEffect, useMemo, useRef, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { isTauri } from "@tauri-apps/api/core";
import { CheckCircle2, ScanLine, XCircle } from "lucide-react";
import type { Catalog, OpticResult } from "../core/api";
import { useCoalesced, useCore } from "../core/CoreProvider";
import { usePageActive } from "../core/pageActive";
import { t, useLang } from "../core/i18n";
import { useProject } from "../core/project";
import { useWeather } from "../core/weather";
import { levelLabel } from "../core/levelLabel";
import { clampTask, seedTask } from "./opticsSeed";
import type { BenchResult, EdgeMeasure } from "../core/benchApi";
import PlanView from "../components/PlanView";
import AnprBench from "../components/AnprBench";
import { Button, Panel, Slider, Tile, fmt } from "../components/ui";

// a function, not a constant: the labels follow the language (callers use it during render)
const enTasks = (): [string, number][] => [
  [t("Teşhis (Identification)"), 250],
  [t("Tanıma (Recognition)"), 125],
  [t("Gözlem (Observation)"), 62.5],
  [t("Algılama (Detection)"), 25],
];

/** EN 62676-4 optics bench: one camera, one task at one distance, a verdict with reasons. */
export default function Optics() {
  const active = usePageActive();
  const { api, bench, terrain, state } = useCore();
  const { site, update, levels, openNonce } = useProject();
  const { lang } = useLang();
  const c = site.camera;
  // The project's own levels (strongest first); EN defaults when the project has none.
  const TASKS = useMemo<[string, number][]>(() => {
    const own = (levels ?? []).filter((l) => l.ppm > 0).sort((a, b) => b.ppm - a.ppm);
    return own.length ? own.map((l) => [levelLabel(l.name, l.ppm, fmt(l.ppm, l.ppm % 1 ? 1 : 0)), l.ppm] as [string, number]) : enTasks();
  }, [levels, lang]);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [weathers, setWeathers] = useState<{ label: string; visibility_km: number }[]>([]);
  const [mode, setMode] = useState<"min" | "max">("min");
  const [taskRaw, setTask] = useState(0);
  const task = clampTask(taskRaw, TASKS.length);
  const touched = useRef(false);
  const seeded = useRef(false);
  const [dist, setDist] = useState(20);
  const [weather, setWeather] = useWeather();
  const [edge, setEdge] = useState<EdgeMeasure | null>(null);
  const [edgeErr, setEdgeErr] = useState("");
  const [view, setView] = useState<"dori" | "anpr">("dori");

  useEffect(() => {
    if (state !== "ready") return;
    api.catalog().then(setCatalog).catch(() => undefined);
    terrain.catalog().then((cat) => setWeathers(cat.weather)).catch(() => undefined);
  }, [api, terrain, state]);

  const taskPpm = TASKS[task][1];
  const input = useMemo(() => ({ c, mode, taskPpm, dist, weather }), [c, mode, taskPpm, dist, weather]);
  const res = useCoalesced(input, async (i) => {
    const [plan, verdict] = await Promise.all([
      api.optics(i.c, { mode: i.mode }),
      bench.bench({ camera: i.c, mode: i.mode, req_ppm: i.taskPpm, distance_m: i.dist, weather: i.weather }),
    ]);
    return { plan, verdict, input: i } as { plan: OpticResult; verdict: BenchResult; input: typeof input };
  }, state === "ready" && active && view === "dori");
  const v = res.value?.verdict;
  // the verdict the seeding may read must belong to the camera / task on screen now (not a previous project's)
  const fresh = res.value?.input === input ? v : undefined;
  const mp = (() => {
    const r = catalog?.resolutions.find((x) => x.name === c.resolution_name);
    return r ? (r.width_px * r.height_px) / 1e6 : 0;
  })();

  // First open: the fixed defaults (wide lens, strongest task at 20 m) fail for most cameras, which
  // read as a broken page. Once, from the core's own DORI reaches, start at the strongest task the
  // camera reaches at the default distance (else the weakest, at 80 % of its reach). Never after
  // the user has touched an input.
  useEffect(() => {
    if (seeded.current || touched.current || !fresh) return;
    seeded.current = true;
    const s = seedTask(fresh, TASKS, dist);
    if (!s) return;
    setTask(s.task);
    if (s.dist !== undefined) setDist(s.dist);
  }, [fresh, TASKS, dist]);
  // another project (opened / new) is a first open again: seed afresh, forget what was touched
  useEffect(() => {
    seeded.current = false; touched.current = false;
    setTask(0); setDist(20);
  }, [openNonce]);

  const measure = async () => {
    setEdgeErr("");
    const path = await open({ title: t("Eğik kenar fotoğrafı (yüksek kontrast, ~5° eğik)"), filters: [{ name: t("Görsel"), extensions: ["png", "jpg", "jpeg", "tif", "tiff", "bmp"] }] });
    if (typeof path !== "string") return;
    try {
      const m = await bench.measureEdge(path, mp || undefined);
      setEdge(m);
      update({ effective_px_ratio: Math.round(m.k * 100) / 100 });
    } catch (e) {
      setEdgeErr(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="flex min-h-full flex-wrap content-start gap-3 p-3">
      <div className="flex w-full gap-1 rounded-md bg-bg-4 p-1 sm:w-auto">
        {([["dori", t("DORI tezgâhı")], ["anpr", t("Plaka okuma (ANPR)")]] as const).map(([id, label]) => (
          <button key={id} type="button" onClick={() => setView(id)}
            className={`h-8 rounded px-4 text-[12px] font-semibold ${view === id ? "bg-bg-6 text-primary" : "text-text-2"}`}>{label}</button>
        ))}
      </div>
      <div className="basis-full" />
      {view === "anpr" ? <AnprBench camera={c} active={active} /> : (<>
      <Panel title={t("Kamera ve montaj")} className="w-[300px] shrink-0">
        <div className="mb-2 truncate text-[12px] font-semibold">{c.name} · <span className="text-text-2">{c.model_name}</span></div>
        <div className="flex flex-col gap-2">
          <Select label={t("Sensör")} value={c.sensor_name} options={(catalog?.sensors ?? []).map((s) => s.name)} onChange={(v) => update({ sensor_name: v })} />
          <Select label={t("Çözünürlük")} value={c.resolution_name} options={(catalog?.resolutions ?? []).map((s) => s.name)} onChange={(v) => update({ resolution_name: v })} />
        </div>
        <div className="mt-3 flex flex-col gap-3">
          <Slider label={t("Odak (geniş)")} unit="mm" value={c.focal_min_mm} min={1} max={Math.max(c.focal_max_mm, 1)} step={0.1} onChange={(f) => update({ focal_min_mm: f })} />
          <Slider label={t("Odak (dar)")} unit="mm" value={c.focal_max_mm} min={c.focal_min_mm} max={Math.max(400, c.focal_max_mm)} step={0.5} onChange={(f) => update({ focal_max_mm: f })} />
          <Slider label={t("Direk")} unit="m" value={c.pole_height_m} min={1} max={30} step={0.5} onChange={(x) => update({ pole_height_m: x })} />
          <Slider label={t("Eğim (aşağı)")} unit="°" value={c.tilt_deg} min={0} max={60} step={0.5} onChange={(x) => update({ tilt_deg: x })} />
          <Slider label={t("Hedef yüksekliği")} unit="m" value={c.target_height_m} min={0.5} max={2.5} step={0.1} onChange={(x) => update({ target_height_m: x })} />
          <Slider label={t("IR menzili")} unit="m" value={c.ir_range_m} min={0} max={500} step={5} onChange={(x) => update({ ir_range_m: x })} />
          <Slider label={t("En düşük ışık")} unit="lux" value={c.min_lux} min={0} max={2} step={0.01} digits={2} onChange={(x) => update({ min_lux: x })} />
        </div>
      </Panel>

      <div className="flex min-w-[420px] flex-[3_1_480px] flex-col gap-3">
        <Panel title={t("Kuş bakışı")} right={
          <div className="flex gap-1">
            {(["min", "max"] as const).map((m) => (
              <button key={m} type="button" onClick={() => { touched.current = true; setMode(m); }}
                className={`h-7 rounded-full px-3 text-[11px] font-semibold ${mode === m ? "bg-secondary text-on-secondary" : "bg-bg-5 text-text-2"}`}>
                {m === "min" ? t("Geniş {f} mm", { f: fmt(c.focal_min_mm) }) : t("Dar {f} mm", { f: fmt(c.focal_max_mm) })}
              </button>
            ))}
          </div>
        }>
          {res.value ? <PlanView r={res.value.plan} irRangeM={res.value.verdict.ir_range_m}
              requirement={{ distance_m: dist, pass: res.value.verdict.pass, dayPass: res.value.verdict.day_pass }} />
            : <div className="flex h-[300px] items-center justify-center text-text-3">{res.error ?? t("Hesaplanıyor…")}</div>}
        </Panel>
        <Panel title={t("Gereksinim")}>
          <div className="grid grid-cols-2 gap-3">
            <Select label={t("Görev (EN 62676-4)")} value={TASKS[task][0]} options={TASKS.map((x) => x[0])} onChange={(v) => { touched.current = true; setTask(Math.max(0, TASKS.findIndex((x) => x[0] === v))); }} />
            <Select label={t("Hava")} value={weather} options={weathers.length ? weathers.map((w) => w.label) : ["Berrak hava"]} onChange={(w) => { touched.current = true; setWeather(w); }} />
          </div>
          <div className="mt-3"><Slider label={t("Hedef mesafesi")} unit="m" value={dist} min={1} max={Math.max(200, Math.ceil((v?.dori_reach_m.detect ?? 0) * 1.2))} step={0.5} onChange={(d) => { touched.current = true; setDist(d); }} /></div>
        </Panel>
      </div>

      <div className="flex w-[320px] shrink-0 flex-col gap-3">
        {v && (
          <>
            <section className={`rounded-md p-3 ${v.pass ? "bg-ok-bg" : v.day_pass ? "bg-warn-bg" : "bg-danger-bg"}`} aria-live="polite">
              <div className={`flex items-center gap-2 text-[16px] font-bold ${v.pass ? "text-primary" : v.day_pass ? "text-warn" : "text-danger"}`}>
                {v.pass ? <CheckCircle2 size={20} /> : <XCircle size={20} />}{v.pass ? t("UYGUN") : v.day_pass ? t("GÜNDÜZ UYGUN · GECE DEĞİL") : t("UYGUN DEĞİL")}
              </div>
              <div className="mt-1 text-[12px] text-text-2">{t("{task} · {d} m · gereken {ppm} px/m", { task: TASKS[task][0].split(" (")[0], d: fmt(dist), ppm: fmt(v.required_ppm, v.required_ppm % 1 ? 1 : 0) })}</div>
              {!v.pass && <ul className={`mt-2 list-disc pl-5 text-[12px] ${v.day_pass ? "text-warn" : "text-danger"}`}>{v.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
            </section>
            <div className="grid grid-cols-2 gap-1.5">
              <Tile label={t("Elde edilen")} value={fmt(v.achieved_ppm, 0)} unit="px/m" tone={v.achieved_ppm >= v.required_ppm ? "text-dori-identify" : "text-danger"} />
              <Tile label={t("Görev menzili")} value={fmt(v.reach_m)} unit="m" />
              <Tile label={t("Yatay FOV")} value={fmt(v.hfov_deg)} unit="°" tone="text-secondary" />
              <Tile label={t("Dikey FOV")} value={fmt(v.vfov_deg)} unit="°" />
              <Tile label={t("Kör nokta")} value={v.dead_zone_m < 0.05 ? t("yok") : fmt(v.dead_zone_m)} unit={v.dead_zone_m < 0.05 ? "" : "m"} tone={v.dead_zone_m > 4 ? "text-warn" : "text-text"} />
              <Tile label={t("Geometrik sınır")} value={v.max_geom_dist_m == null ? t("açık") : fmt(v.max_geom_dist_m)} unit={v.max_geom_dist_m == null ? "" : "m"} />
            </div>
            <Panel title={t("DORI menzilleri (yer)")}>
              {([["identify", t("Teşhis ≥250")], ["recognize", t("Tanıma ≥125")], ["observe", t("Gözlem ≥62,5")], ["detect", t("Algılama ≥25")]] as const).map(([k, l]) => (
                <div key={k} className="flex justify-between font-mono text-[12px]"><span className="text-text-3">{l}</span><span>{fmt(v.dori_reach_m[k])} m</span></div>
              ))}
              {v.atmospheric_limit_m != null && <div className="mt-2 font-mono text-[11px] text-warn">{t("▲ atmosferik menzil {m} m ({weather})", { m: fmt(v.atmospheric_limit_m, 0), weather })}</div>}
            </Panel>
          </>
        )}
        <Panel title={t("Ölçülen kalite (MTF)")}>
          <Slider label={t("Etkin piksel oranı k")} unit="" value={c.effective_px_ratio} min={0.05} max={1} step={0.01} onChange={(k) => update({ effective_px_ratio: k })} />
          <div className="mt-1 font-mono text-[11px] text-text-2">
            {t("etiket {a} MP → etkin {b} MP", { a: fmt(mp, 1), b: fmt(mp * c.effective_px_ratio ** 2, 1) })} · {v ? t("{a} / {b} px", { a: fmt(v.res_width_px, 0), b: fmt(v.nominal_res_width_px, 0) }) : ""}
          </div>
          <Button className="mt-2 w-full" disabled={!isTauri()} onClick={measure}><ScanLine size={14} /> {t("Eğik kenar → k ölç")}</Button>
          {edge && <div className="mt-1 text-[11px] text-text-2">{t("● Ölçüldü: k = {k} ({w}×{h} px)", { k: fmt(edge.k, 2), w: edge.image_size[0], h: edge.image_size[1] })}</div>}
          {edgeErr && <div className="mt-1 text-[11px] text-danger">■ {edgeErr}</div>}
          <p className="mt-2 text-[11px] text-text-3">{t("◆ FOV optiktir, değişmez; k yalnız px/m ve menzili ölçekler.")}</p>
        </Panel>
      </div>
      </>)}
    </div>
  );
}

function Select({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (v: string) => void }) {
  return (
    <label className="block"><span className="label">{label}</span>
      <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" value={value} onChange={(e) => onChange(e.target.value)}>
        {!options.includes(value) && <option value={value}>{value}</option>}
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );
}
