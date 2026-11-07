import { useEffect, useMemo, useState } from "react";
import { Database, Search } from "lucide-react";
import { DORI, type ModelSummary, type OpticResult } from "../core/api";
import { useCore } from "../core/CoreProvider";
import { Panel, fmt } from "../components/ui";
import LibraryEditor from "../components/LibraryEditor";

type Filter = "all" | "fixed" | "ptz" | "thermal";

const isThermal = (m: ModelSummary) => /lwir|termal|thermal/i.test(`${m.sensor_name} ${m.camera_type} ${m.name}`);

export default function Library() {
  const { api, state } = useCore();
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sel, setSel] = useState<string>("");
  const [ends, setEnds] = useState<{ wide: OpticResult; tele: OpticResult } | null>(null);

  const [rev, setRev] = useState(0);     // bumped after an edit: reload the list and the selected model
  useEffect(() => {
    if (state !== "ready") return;
    api.library().then((r) => { setModels(r.models); setError(""); }).catch((e) => setError(String(e.message ?? e)));
  }, [api, state, rev]);

  const shown = useMemo(() => {
    const q = query.trim().toLocaleLowerCase("tr-TR");
    return models.filter((m) => {
      if (filter === "ptz" && !m.is_ptz) return false;
      if (filter === "fixed" && (m.is_ptz || isThermal(m))) return false;
      if (filter === "thermal" && !isThermal(m)) return false;
      return !q || `${m.name} ${m.sensor_name} ${m.resolution_name}`.toLocaleLowerCase("tr-TR").includes(q);
    });
  }, [models, query, filter]);

  useEffect(() => {
    if (!sel) return;
    let live = true;
    setEnds(null);
    (async () => {
      const { camera } = await api.cameraFor(sel, undefined, true);
      const [wide, tele] = await Promise.all([api.optics(camera, { mode: "min" }), api.optics(camera, { mode: "max" })]);
      if (live) setEnds({ wide, tele });
    })().catch((e) => live && setError(String(e.message ?? e)));
    return () => { live = false; };
  }, [api, sel, rev]);

  const counts: Record<Filter, number> = {
    all: models.length,
    fixed: models.filter((m) => !m.is_ptz && !isThermal(m)).length,
    ptz: models.filter((m) => m.is_ptz).length,
    thermal: models.filter(isThermal).length,
  };
  const chips: [Filter, string][] = [["all", "Tümü"], ["fixed", "Sabit"], ["ptz", "PTZ"], ["thermal", "Termal"]];
  const current = models.find((m) => m.name === sel);

  return (
    <div className="flex min-h-full flex-wrap content-start gap-3 p-3">
      <div className="flex min-w-0 flex-[7_1_560px] flex-col gap-3">
        <Panel title="Kamera Kütüphanesi" icon={<Database size={18} className="text-primary" />}
          right={<span className="font-mono text-[10px] text-text-3">{models.length} model</span>}>
          <label className="relative block">
            <Search size={16} className="absolute top-2.5 left-2.5 text-text-3" aria-hidden />
            <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Model, sensör, çözünürlük ara…"
              aria-label="Kamera ara" className="h-9 w-full rounded border border-line bg-bg-0 pr-3 pl-9 text-[13px]" />
          </label>
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {chips.map(([id, label]) => (
              <button key={id} type="button" onClick={() => setFilter(id)}
                className={`h-7 rounded-full px-3 text-[11px] font-semibold ${filter === id ? "bg-secondary text-on-secondary" : "bg-bg-5 text-text-2"}`}>
                {label} <span className="font-mono text-[10px] opacity-70">{counts[id]}</span>
              </button>
            ))}
          </div>
        </Panel>

        <section className="overflow-hidden rounded-md bg-bg-4">
          <div className="max-h-[440px] overflow-auto">
            <table className="w-full min-w-[720px] border-collapse [&_td:not(:first-child)]:whitespace-nowrap">
              <thead>
                <tr>
                  {["Model", "Tip", "Sensör", "Çözünürlük", "Odak", "IR"].map((h) => (
                    <th key={h} className="label sticky top-0 bg-bg-5 px-2 py-2 text-left">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shown.map((m) => (
                  <tr key={m.name} onClick={() => setSel(m.name)} aria-selected={m.name === sel}
                    className={`cursor-pointer border-b border-line-soft text-[12px] ${m.name === sel ? "bg-bg-5 shadow-[inset_3px_0_0_var(--color-primary)]" : "hover:bg-bg-3"}`}>
                    <td className="min-w-[240px] px-2 py-2 font-semibold">{m.name}</td>
                    <td className="px-2"><Kind m={m} /></td>
                    <td className="px-2 font-mono text-text-2">{m.sensor_name}</td>
                    <td className="px-2 font-mono">{m.resolution_name}</td>
                    <td className="px-2 font-mono">{fmt(m.focal_min_mm)}{m.focal_max_mm !== m.focal_min_mm && `–${fmt(m.focal_max_mm)}`} mm</td>
                    <td className="px-2 font-mono">{m.ir_range_m ? `${fmt(m.ir_range_m, 0)} m` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!models.length && <div className="p-6 text-center text-text-3">{error || (state === "ready" ? "Yükleniyor…" : "Çekirdeğe bağlı değil.")}</div>}
          </div>
        </section>
      </div>

      <div className="flex min-w-0 flex-[5_1_360px] flex-col gap-3 self-start">
      <Panel title={current ? current.name : "Model seçin"}>
        {!current && <p className="text-text-3">Ayrıntı ve DORI menzilleri için tablodan bir model seçin.</p>}
        {current && !ends && <p className="text-text-3">{error || "Hesaplanıyor…"}</p>}
        {current && ends && (
          <>
            <div className="mb-3 font-mono text-[11px] text-text-2">
              {current.sensor_name} · {current.resolution_name} · yatay FOV {fmt(ends.wide.hfov_deg)}°{ends.tele.focal_mm !== ends.wide.focal_mm && ` → ${fmt(ends.tele.hfov_deg)}°`}
            </div>
            <div className="grid grid-cols-2 gap-1.5 font-mono">
              {DORI.map((d) => (
                <div key={d.key} className="rounded bg-bg-1 p-2">
                  <div className="text-[10px] text-text-3">{d.label} ≥{fmt(d.ppm, d.ppm % 1 ? 1 : 0)} px/m</div>
                  <div className="text-[16px] font-bold">
                    {fmt(ends.wide.ground_distances[d.key])}
                    {ends.tele.focal_mm !== ends.wide.focal_mm && <> <span className="text-[10px] text-text-3">→</span> {fmt(ends.tele.ground_distances[d.key])}</>} m
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-3 text-[11px] text-text-3">
              ◆ Yer mesafesi, modelin kütüphanedeki direk ve eğim değerleriyle optik motordan. {isThermal(current) && "Termal kamerada EN 62676-4 px/m tabloları görünür bant içindir; Johnson ölçütleri ayrıca geçerlidir."}
            </p>
          </>
        )}
      </Panel>
      <LibraryEditor selected={sel} onSaved={(n) => { setSel(n); setRev((x) => x + 1); }} />
      </div>
    </div>
  );
}

function Kind({ m }: { m: ModelSummary }) {
  const [cls, label] = isThermal(m)
    ? ["bg-info-bg text-dori-identify", m.is_ptz ? "PTZ Termal" : "Termal"]   // warm colours are for alarms only
    : m.is_ptz ? ["bg-bg-6 text-ptz", "PTZ"] : ["bg-bg-5 text-text-2", "Sabit"];
  return <span className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${cls}`}>{label}</span>;
}
