import { useState } from "react";
import { open, save } from "@tauri-apps/plugin-dialog";
import { isTauri } from "@tauri-apps/api/core";
import { ExternalLink, FileCheck2, FileText, FolderOpen, Globe2, Mountain, Package, PenTool, Share2, Table2 } from "lucide-react";
import { useCore } from "../core/CoreProvider";
import { useProject } from "../core/project";
import { useActiveTerrain, useSpecResult } from "../core/activeTerrain";
import { useJob } from "../core/useJob";
import JobBar from "../components/JobBar";
import { Button, Panel } from "../components/ui";

/** Report files from the same writers as the classic app and the headless CLI. */
export default function Reports() {
  const { report, terrain: T } = useCore();
  const { sites, name, levels } = useProject();
  const { active } = useActiveTerrain();
  const specResult = useSpecResult();
  const [realWorld, setRealWorld] = useState(false);
  const job = useJob();
  const [dir, setDir] = useState("");
  const [stem, setStem] = useState("");
  const [mode, setMode] = useState<"min" | "max" | "compare">("compare");
  const [fmts, setFmts] = useState<Record<string, boolean>>({ pdf: true, xlsx: true, csv: false });
  const [engFmts, setEngFmts] = useState<Record<string, boolean>>({ pdf: true, csv: true });
  const [files, setFiles] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const base = stem || name || "cctv-rapor";
  const dupNames = new Set(sites.map((s) => s.camera.name)).size !== sites.length;

  const pickDir = async () => {
    const d = await open({ title: "Raporların yazılacağı klasör", directory: true });
    if (typeof d === "string") setDir(d);
  };

  const done = (r: { files: string[] } | undefined, extra = "") => {
    if (!r) return;
    setFiles((f) => [...r.files, ...f.filter((x) => !r.files.includes(x))]);
    setNote(extra);
  };

  return (
    <div className="flex min-h-full flex-wrap content-start gap-3 p-3">
      <div className="flex w-full flex-col gap-3">
        <JobBar job={job} onCancel={job.cancel} onDismiss={job.clearError} />
        {note && <div className="rounded bg-warn-bg px-3 py-2 text-[12px] text-warn">▲ {note}</div>}
      </div>
      <Panel title="Hedef klasör" icon={<FolderOpen size={16} className="text-secondary" />} className="w-full">
        <div className="flex flex-wrap items-center gap-2">
          <Button disabled={!isTauri()} onClick={pickDir}><FolderOpen size={14} /> Klasör Seç</Button>
          <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-text-2">{dir || "seçilmedi"}</span>
          <label className="flex items-center gap-2 text-[12px]"><span className="label">Dosya adı</span>
            <input className="h-8 w-56 rounded border border-line bg-bg-0 px-2 font-mono text-[12px]" value={stem} placeholder={name} onChange={(e) => setStem(e.target.value)} />
          </label>
        </div>
      </Panel>

      <Panel title="Optik tablo raporu" icon={<Table2 size={16} className="text-primary" />} className="min-w-[340px] flex-1">
        <p className="text-[12px] text-text-2">{sites.length} kamera · DORI mesafeleri, kör nokta, öneriler — klasik uygulama ve başsız CLI ile aynı çıktı.</p>
        {dupNames && <p className="mt-1 text-[11px] text-warn">▲ Kamera adları benzersiz olmalı (CAD Çalışma Alanı'nda yeniden adlandırın).</p>}
        <div className="mt-3 grid grid-cols-3 gap-1">
          {([["min", "Geniş uç"], ["max", "Dar uç"], ["compare", "Karşılaştırma"]] as const).map(([id, l]) => (
            <button key={id} type="button" onClick={() => setMode(id)} className={`h-8 rounded text-[11px] font-semibold ${mode === id ? "bg-bg-6 text-primary" : "bg-bg-5 text-text-2"}`}>{l}</button>
          ))}
        </div>
        <Formats value={fmts} onChange={setFmts} />
        <Button primary className="mt-3 w-full" disabled={!dir || job.running || dupNames || !Object.values(fmts).some(Boolean)}
          onClick={() => job.run("Optik rapor yazılıyor", () => report.optics({
            cameras: sites.map((s) => s.camera), mode, formats: Object.keys(fmts).filter((k) => fmts[k]), out_dir: dir, stem: base,
            ...(levels ? { levels } : {}),
          })).then((r) => done(r))}>
          <FileText size={14} /> Optik Raporu Yaz
        </Button>
      </Panel>

      <Panel title="Arazi mühendislik raporu" icon={<Mountain size={16} className="text-primary" />} className="min-w-[340px] flex-1">
        {active ? (
          <>
            <p className="text-[12px] text-text-2">{active.label} · {active.is_measured ? "● ölçülmüş DEM" : "▲ prosedürel — rapor “ölçüm değil” uyarısı taşır"}</p>
            <ul className="mt-2 text-[12px]">
              {([["viewshed", "Tek kamera görüş alanı"], ["plan", "Çevre çiti planı + direk tablosu"], ["coverage", "Birleşik kapsama"]] as const).map(([k, l]) => (
                <li key={k} className={active.ran[k] ? "text-primary" : "text-text-3"}>{active.ran[k] ? "●" : "○"} {l}</li>
              ))}
            </ul>
            <Formats value={engFmts} onChange={setEngFmts} only={["pdf", "csv"]} />
            <Button primary className="mt-3 w-full" disabled={!dir || job.running || !Object.values(active.ran).some(Boolean)}
              onClick={() => job.run("Mühendislik raporu yazılıyor", () => report.engineering({
                terrain_id: active.terrain_id, use_obstacles: active.use_obstacles, camera: active.camera, weather: active.weather,
                formats: Object.keys(engFmts).filter((k) => engFmts[k]), out_dir: dir, stem: base, project_name: name,
              })).then((r) => done(r, r && !r.measured ? "Arazi ölçülmüş değil; rapor bunu kaynak kutusunda kırmızı uyarıyla belirtir." : ""))}>
              <FileText size={14} /> Mühendislik Raporunu Yaz
            </Button>
          </>
        ) : <p className="text-[12px] text-text-3">Önce "Arazi & Görüş Alanı" sayfasında bir arazi yükleyip analiz çalıştırın.</p>}
      </Panel>

      <Panel title="Teslim dosyaları" icon={<Package size={16} className="text-primary" />} className="w-full">
        {active?.ran.plan ? (
          <Deliverables onFile={(f, extra) => done({ files: [f] }, extra)} job={job} base={base} dir={dir}
            run={{
              site: (path: string) => T.exportSite({ terrain_id: active.terrain_id, use_obstacles: active.use_obstacles, path, real_world: realWorld, name }),
              statement: (path: string) => T.planStatement({ terrain_id: active.terrain_id, use_obstacles: active.use_obstacles, path, project_name: name, goal_frac: active.goal_frac }),
              share: (path: string) => T.exportShare({ terrain_id: active.terrain_id, use_obstacles: active.use_obstacles, path, title: name, scenarios: active.scenarios }),
              pkg: (path: string, o?: Parameters<typeof T.exportPackage>[1]) => T.exportPackage({ terrain_id: active.terrain_id, use_obstacles: active.use_obstacles, path,
                project_name: name, goal_frac: active.goal_frac, scenarios: active.scenarios, ...(specResult ? { spec_result: specResult } : {}) }, o),
            }}
            status={[
              ["Çit planı + direk listesi", true], ["Birleşik kapsama (KMZ katmanı, bant yüzdeleri)", active.ran.coverage],
              ["Ağ planı + maliyet", active.ran.network], ["Gece senaryosu", active.ran.night],
              [`Senaryolar (${active.scenarios.length})`, active.scenarios.length > 0], ["Şartname uygunluk beyanı", !!specResult], ["Plan uygunluk beyanı (EN 62676-4)", active.ran.coverage],
            ]}
            realWorld={realWorld} onRealWorld={setRealWorld} measured={active.is_measured} coverageRan={active.ran.coverage} />
        ) : <p className="text-[12px] text-text-3">Önce "Arazi & Görüş Alanı" sayfasında bir çevre çiti planı oluşturun.</p>}
      </Panel>

      {files.length > 0 && (
        <Panel title="Yazılan dosyalar" className="w-full">
          <ul className="flex flex-col gap-1">
            {files.map((f) => (
              <li key={f} className="flex items-center gap-2 font-mono text-[12px]">
                <span className="min-w-0 flex-1 truncate">{f}</span>
                <Button onClick={() => report.open(f).catch((e) => setNote(String(e.message ?? e)))}><ExternalLink size={14} /> Aç</Button>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}

function Formats({ value, onChange, only = ["pdf", "xlsx", "csv"] }: { value: Record<string, boolean>; onChange: (v: Record<string, boolean>) => void; only?: string[] }) {
  return (
    <div className="mt-3 flex gap-4 text-[12px]">
      {only.map((f) => (
        <label key={f} className="flex items-center gap-1.5">
          <input type="checkbox" checked={!!value[f]} onChange={(e) => onChange({ ...value, [f]: e.target.checked })} /> {f.toUpperCase()}
        </label>
      ))}
    </div>
  );
}

type Job = ReturnType<typeof useJob>;

function Deliverables({ run, status, job, base, dir, onFile, realWorld, onRealWorld, measured, coverageRan }: {
  run: {
    site: (path: string) => Promise<{ path: string; exact_georef: boolean; has_latlon: boolean }>;
    share: (path: string) => Promise<{ path: string }>;
    statement: (path: string) => Promise<{ path: string | null; verdict: string; declared_label: string; measured: boolean }>;
    pkg: (path: string, o?: { signal: AbortSignal; onProgress: (p: { done: number; total: number; message: string }) => void }) => Promise<{ path: string; files: string[]; skipped: string[] }>;
  };
  status: [string, boolean][]; job: Job; base: string; dir: string; onFile: (f: string, note?: string) => void;
  realWorld: boolean; onRealWorld: (v: boolean) => void; measured: boolean; coverageRan: boolean;
}) {
  const ask = async (title: string, ext: string, label: string) => {
    if (!isTauri()) return null;
    const p = await save({ title, filters: [{ name: label, extensions: [ext] }], defaultPath: `${dir ? `${dir}\\` : ""}${base}.${ext}` });
    return typeof p === "string" ? p : null;
  };
  const site = (ext: "kml" | "kmz" | "dxf", label: string) => async () => {
    const p = await ask(`${label} olarak dışa aktar`, ext, label);
    if (p) void job.run(`${label} yazılıyor`, () => run.site(p)).then((r) => r && onFile(r.path,
      ext === "dxf" && realWorld && !r.exact_georef ? "Arazinin projeksiyonlu georeferansı yok; DXF yerel metrede yazıldı." : ""));
  };
  return (
    <div className="flex flex-wrap gap-6">
      <ul className="min-w-[260px] text-[12px]">
        {status.map(([l, ok]) => <li key={l} className={ok ? "text-primary" : "text-text-3"}>{ok ? "●" : "○"} {l}</li>)}
        {!measured && <li className="mt-1 text-warn">▲ arazi ölçülmüş değil — tüm çıktılarda uyarı var</li>}
      </ul>
      <div className="flex min-w-[300px] flex-1 flex-col gap-2">
        <div className="flex flex-wrap gap-1.5">
          <Button disabled={job.running} onClick={site("kml", "KML")}><Globe2 size={14} /> KML</Button>
          <Button disabled={job.running} onClick={site("kmz", "KMZ")}><Globe2 size={14} /> KMZ (+ kapsama)</Button>
          <Button disabled={job.running} onClick={site("dxf", "DXF")}><PenTool size={14} /> DXF</Button>
          <label className="flex items-center gap-1.5 text-[12px]"><input type="checkbox" checked={realWorld} onChange={(e) => onRealWorld(e.target.checked)} /> DXF raster projeksiyonunda (UTM / TM)</label>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Button disabled={job.running} onClick={async () => {
            const p = await ask("Paylaşım sayfası (HTML)", "html", "HTML");
            if (p) void job.run("Paylaşım sayfası", () => run.share(p)).then((r) => r && onFile(r.path, ""));
          }}><Share2 size={14} /> Paylaşım Sayfası (HTML)</Button>
          <Button disabled={job.running || !coverageRan} title={coverageRan ? "" : "Önce Birleşik Kapsamayı hesaplayın"} onClick={async () => {
            const p = await ask("Plan uygunluk beyanı (EN 62676-4)", "pdf", "PDF");
            if (p) void job.run("Plan uygunluk beyanı", () => run.statement(p)).then((r) => r && r.path && onFile(r.path,
              `Plan uygunluk beyanı: ${r.declared_label} — ${r.verdict}${r.measured ? "" : " (arazi ölçüm değil: bağlayıcı değil)"}`));
          }}><FileCheck2 size={14} /> Plan Uygunluk Beyanı (PDF)</Button>
          <Button primary disabled={job.running} onClick={async () => {
            const p = await ask("Teklif paketi (ZIP)", "zip", "ZIP");
            if (p) void job.run("Teklif paketi hazırlanıyor", (o) => run.pkg(p, o)).then((r) => r && onFile(r.path,
              r.skipped.length ? `Pakete eklenmeyenler: ${r.skipped.join(" · ")}` : ""));
          }}><Package size={14} /> Teklif Paketi (ZIP)</Button>
        </div>
        <p className="text-[11px] text-text-3">Paket: metraj + maliyet (CSV / XLSX), direk ve switch listesi, mühendislik raporu, şartname ve plan uygunluk beyanı, KMZ + DXF, paylaşım sayfası, BENİOKU. Paylaşım sayfası betiksiz, internetsiz açılır.</p>
      </div>
    </div>
  );
}
