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
import { getPrefs, setPref } from "../core/prefs";
import { t, tn } from "../core/i18n";

/** Report files from the same writers as the classic app and the headless CLI. */
export default function Reports() {
  const { report, terrain: T } = useCore();
  const { sites, name, levels } = useProject();
  const { active } = useActiveTerrain();
  const specResult = useSpecResult();
  const [realWorld, setRealWorld] = useState(false);
  const job = useJob();
  const [dir, setDir] = useState(() => getPrefs().reportDir);
  const [stem, setStem] = useState("");
  const [mode, setMode] = useState<"min" | "max" | "compare">("compare");
  const [fmts, setFmts] = useState<Record<string, boolean>>({ pdf: true, xlsx: true, csv: false });
  const [engFmts, setEngFmts] = useState<Record<string, boolean>>({ pdf: true, csv: true });
  const [files, setFiles] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const base = stem || name || "cctv-rapor";
  const dupNames = new Set(sites.map((s) => s.camera.name)).size !== sites.length;

  const pickDir = async () => {
    const d = await open({ title: t("Raporların yazılacağı klasör"), directory: true });
    if (typeof d === "string") { setDir(d); setPref("reportDir", d); }
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
      <Panel title={t("Hedef klasör")} icon={<FolderOpen size={16} className="text-secondary" />} className="w-full">
        <div className="flex flex-wrap items-center gap-2">
          <Button disabled={!isTauri()} onClick={pickDir}><FolderOpen size={14} /> {t("Klasör Seç")}</Button>
          <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-text-2">{dir || t("seçilmedi")}</span>
          <label className="flex items-center gap-2 text-[12px]"><span className="label">{t("Dosya adı")}</span>
            <input className="h-8 w-56 rounded border border-line bg-bg-0 px-2 font-mono text-[12px]" value={stem} placeholder={name} onChange={(e) => setStem(e.target.value)} />
          </label>
        </div>
      </Panel>

      <Panel title={t("Optik tablo raporu")} icon={<Table2 size={16} className="text-primary" />} className="min-w-[340px] flex-1">
        <p className="text-[12px] text-text-2">{tn(sites.length, "{n} kamera · DORI mesafeleri, kör nokta, öneriler — klasik uygulama ve başsız CLI ile aynı çıktı.")}</p>
        {dupNames && <p className="mt-1 text-[11px] text-warn">{t("▲ Kamera adları benzersiz olmalı (CAD Çalışma Alanı'nda yeniden adlandırın).")}</p>}
        <div className="mt-3 grid grid-cols-3 gap-1">
          {([["min", t("Geniş uç")], ["max", t("Dar uç")], ["compare", t("Karşılaştırma")]] as const).map(([id, l]) => (
            <button key={id} type="button" onClick={() => setMode(id)} className={`h-8 rounded text-[11px] font-semibold ${mode === id ? "bg-bg-6 text-primary" : "bg-bg-5 text-text-2"}`}>{l}</button>
          ))}
        </div>
        <Formats value={fmts} onChange={setFmts} />
        <Button primary className="mt-3 w-full" disabled={!dir || job.running || dupNames || !Object.values(fmts).some(Boolean)}
          onClick={() => job.run(t("Optik rapor yazılıyor"), () => report.optics({
            cameras: sites.map((s) => s.camera), mode, formats: Object.keys(fmts).filter((k) => fmts[k]), out_dir: dir, stem: base,
            ...(levels ? { levels } : {}),
          })).then((r) => done(r))}>
          <FileText size={14} /> {t("Optik Raporu Yaz")}
        </Button>
        {!dir && <p className="mt-1 text-[11px] text-warn">{t("▲ Önce yukarıdan hedef klasörü seçin.")}</p>}
        {dupNames && <p className="mt-1 text-[11px] text-warn">{t("▲ Aynı adı taşıyan kameralar var; rapor adlara göre yazılır.")}</p>}
      </Panel>

      <Panel title={t("Arazi mühendislik raporu")} icon={<Mountain size={16} className="text-primary" />} className="min-w-[340px] flex-1">
        {active ? (
          <>
            <p className="text-[12px] text-text-2">{active.label} · {active.is_measured ? t("● ölçülmüş DEM") : t("▲ prosedürel — rapor “ölçüm değil” uyarısı taşır")}</p>
            <ul className="mt-2 text-[12px]">
              {([["viewshed", t("Tek kamera görüş alanı")], ["plan", t("Çevre çiti planı + direk tablosu")], ["coverage", t("Birleşik kapsama")]] as const).map(([k, l]) => (
                <li key={k} className={active.ran[k] ? "text-primary" : "text-text-3"}>{active.ran[k] ? "●" : "○"} {l}</li>
              ))}
            </ul>
            <Formats value={engFmts} onChange={setEngFmts} only={["pdf", "csv"]} />
            <Button primary className="mt-3 w-full" disabled={!dir || job.running || !Object.values(active.ran).some(Boolean)}
              onClick={() => job.run(t("Mühendislik raporu yazılıyor"), () => report.engineering({
                terrain_id: active.terrain_id, use_obstacles: active.use_obstacles, camera: active.camera, weather: active.weather,
                formats: Object.keys(engFmts).filter((k) => engFmts[k]), out_dir: dir, stem: base, project_name: name,
              })).then((r) => done(r, r && !r.measured ? t("Arazi ölçülmüş değil; rapor bunu kaynak kutusunda kırmızı uyarıyla belirtir.") : ""))}>
              <FileText size={14} /> {t("Mühendislik Raporunu Yaz")}
            </Button>
          </>
        ) : <p className="text-[12px] text-text-3">{t('Önce "Arazi & Görüş Alanı" sayfasında bir arazi yükleyip analiz çalıştırın.')}</p>}
      </Panel>

      <Panel title={t("Teslim dosyaları")} icon={<Package size={16} className="text-primary" />} className="w-full">
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
              [t("Çit planı + direk listesi"), true], [t("Birleşik kapsama (KMZ katmanı, bant yüzdeleri)"), active.ran.coverage],
              [t("Ağ planı + maliyet"), active.ran.network], [t("Gece senaryosu"), active.ran.night],
              [t("Senaryolar ({n})", { n: active.scenarios.length }), active.scenarios.length > 0], [t("Şartname uygunluk beyanı"), !!specResult], [t("Plan uygunluk beyanı (EN 62676-4)"), active.ran.coverage],
            ]}
            realWorld={realWorld} onRealWorld={setRealWorld} measured={active.is_measured} coverageRan={active.ran.coverage} />
        ) : <p className="text-[12px] text-text-3">{t('Önce "Arazi & Görüş Alanı" sayfasında bir çevre çiti planı oluşturun.')}</p>}
      </Panel>

      {files.length > 0 && (
        <Panel title={t("Yazılan dosyalar")} className="w-full">
          <ul className="flex flex-col gap-1">
            {files.map((f) => (
              <li key={f} className="flex items-center gap-2 font-mono text-[12px]">
                <span className="min-w-0 flex-1 truncate">{f}</span>
                <Button onClick={() => report.open(f).catch((e) => setNote(String(e.message ?? e)))}><ExternalLink size={14} /> {t("Aç")}</Button>
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
    site: (path: string) => Promise<{ path: string; exact_georef: boolean; has_latlon: boolean; privacy_masks: number }>;
    share: (path: string) => Promise<{ path: string }>;
    statement: (path: string) => Promise<{ path: string | null; verdict: string; declared_label: string; measured: boolean; assist: { verdict: string; max_s: number } | null }>;
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
    const p = await ask(t("{label} olarak dışa aktar", { label }), ext, label);
    if (p) void job.run(t("{label} yazılıyor", { label }), () => run.site(p)).then((r) => r && onFile(r.path,
      [ext === "dxf" && realWorld && !r.exact_georef ? t("Arazinin projeksiyonlu georeferansı yok; DXF yerel metrede yazıldı.") : "",
        r.privacy_masks > 0 ? tn(r.privacy_masks, "{n} KVKK gizlilik maskesi poligonu eklendi (şehir analizinden).") : ""].filter(Boolean).join(" ")));
  };
  return (
    <div className="flex flex-wrap gap-6">
      <ul className="min-w-[260px] text-[12px]">
        {status.map(([l, ok]) => <li key={l} className={ok ? "text-primary" : "text-text-3"}>{ok ? "●" : "○"} {l}</li>)}
        {!measured && <li className="mt-1 text-warn">{t("▲ arazi ölçülmüş değil — tüm çıktılarda uyarı var")}</li>}
      </ul>
      <div className="flex min-w-[300px] flex-1 flex-col gap-2">
        <div className="flex flex-wrap gap-1.5">
          <Button disabled={job.running} onClick={site("kml", "KML")}><Globe2 size={14} /> KML</Button>
          <Button disabled={job.running} onClick={site("kmz", "KMZ")}><Globe2 size={14} /> {t("KMZ (+ kapsama)")}</Button>
          <Button disabled={job.running} onClick={site("dxf", "DXF")}><PenTool size={14} /> DXF</Button>
          <label className="flex items-center gap-1.5 text-[12px]"><input type="checkbox" checked={realWorld} onChange={(e) => onRealWorld(e.target.checked)} /> {t("DXF raster projeksiyonunda (UTM / TM)")}</label>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Button disabled={job.running} onClick={async () => {
            const p = await ask(t("Paylaşım sayfası (HTML)"), "html", "HTML");
            if (p) void job.run(t("Paylaşım sayfası"), () => run.share(p)).then((r) => r && onFile(r.path, ""));
          }}><Share2 size={14} /> {t("Paylaşım Sayfası (HTML)")}</Button>
          <Button disabled={job.running || !coverageRan} title={coverageRan ? "" : t("Önce Birleşik Kapsamayı hesaplayın")} onClick={async () => {
            const p = await ask(t("Plan uygunluk beyanı (EN 62676-4)"), "pdf", "PDF");
            if (p) void job.run(t("Plan uygunluk beyanı"), () => run.statement(p)).then((r) => r && r.path && onFile(r.path,
              t("Plan uygunluk beyanı: {label} — {verdict}", { label: r.declared_label, verdict: r.verdict })
              + (r.assist ? " · " + t("PTZ destekli hedef (≤ {s} sn) — {verdict}", { s: r.assist.max_s, verdict: r.assist.verdict }) : "")
              + (r.measured ? "" : " " + t("(arazi ölçüm değil: bağlayıcı değil)"))));
          }}><FileCheck2 size={14} /> {t("Plan Uygunluk Beyanı (PDF)")}</Button>
          <Button primary disabled={job.running} onClick={async () => {
            const p = await ask(t("Teklif paketi (ZIP)"), "zip", "ZIP");
            if (p) void job.run(t("Teklif paketi hazırlanıyor"), (o) => run.pkg(p, o)).then((r) => r && onFile(r.path,
              r.skipped.length ? t("Pakete eklenmeyenler: {list}", { list: r.skipped.join(" · ") }) : ""));
          }}><Package size={14} /> {t("Teklif Paketi (ZIP)")}</Button>
        </div>
        <p className="text-[11px] text-text-3">{t("Paket: metraj + maliyet (CSV / XLSX), direk ve switch listesi, mühendislik raporu, şartname ve plan uygunluk beyanı, KMZ + DXF (şehir analizi çalıştıysa KVKK maske poligonlarıyla), paylaşım sayfası, BENİOKU. Paylaşım sayfası betiksiz, internetsiz açılır.")}</p>
      </div>
    </div>
  );
}
