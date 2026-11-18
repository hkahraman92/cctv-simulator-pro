import { useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { isTauri } from "@tauri-apps/api/core";
import { ExternalLink, FolderOpen, ListChecks, Play, X } from "lucide-react";
import { useCore } from "../core/CoreProvider";
import type { TerrainApi } from "../core/terrainApi";
import type { useJob } from "../core/useJob";
import { t } from "../core/i18n";
import type { FullSettings, FullStep } from "../pages/analysisSettings";
import {
  OPTIONAL_STEPS, STEP_MARK, buildFullParams, stepLines, stepTitle, type FullAnalysisResult, type FullInputs, type FullStepRow,
} from "../pages/fullAnalysis";
import { Button, Panel, fmt } from "./ui";

type Job = ReturnType<typeof useJob>;

const optionLabel = (k: FullStep): string => ({
  mixed: t("Sabit + PTZ değerlendirmesi"), night: t("Gece kapsaması"), approach: t("Gizli yaklaşma"), network: t("Ağ planı ve maliyet"),
  sensitivity: t("Veri hassasiyeti"),
} as Record<FullStep, string>)[k];

const statusText = (r: FullStepRow): string => (r.status === "ok" ? t("tamam") : r.status === "skipped" ? t("atlandı") : t("başarısız"));

/**
 * "Tam analiz": one run of every analysis of the plan with the settings the panels hold, plus (with an output folder) the
 * deliverables. It sits at the top of the Doğrula step: that is where the plan is verified and where the night, approach and
 * sensitivity panels whose settings it uses live. A run does not replace any panel: each shows its result afterwards.
 */
export function FullAnalysisPanel({ T, job, base, settings, onSettings, defaults, hasPtz, onDone }: {
  T: TerrainApi; job: Job;
  /** everything the request needs except the ticks and the folder */
  base: Omit<FullInputs, "include" | "outDir">;
  settings: FullSettings | null; onSettings: (s: FullSettings) => void;
  /** the optional steps' ticks: the stored choice, else derived from the panels' persisted settings */
  defaults: Record<FullStep, boolean>;
  hasPtz: boolean;
  onDone: (r: FullAnalysisResult) => void;
}) {
  const { report } = useCore();
  const outDir = settings?.outDir ?? "";
  const [res, setRes] = useState<FullAnalysisResult | null>(null);
  const [msg, setMsg] = useState("");
  const setTick = (k: FullStep, v: boolean) => onSettings({ include: { ...(settings?.include ?? {}), [k]: v }, outDir });
  const pickDir = async () => {
    const d = await open({ title: t("Çıktıların yazılacağı klasör"), directory: true });
    if (typeof d === "string") onSettings({ include: settings?.include ?? {}, outDir: d });
  };
  const run = () => void job.run(t("Tam analiz"), (o) => T.fullAnalysis(buildFullParams({ ...base, include: defaults, outDir }), o))
    .then((r) => { if (r) { setRes(r); setMsg(""); onDone(r); } });
  const openFile = (p: string) => report.open(p).catch((e) => setMsg(String(e.message ?? e)));
  const p = job.running && job.label === t("Tam analiz") ? job.progress : undefined;

  return (
    <Panel title={t("Tam analiz")} icon={<ListChecks size={16} className="text-primary" />}>
      <p className="text-[11px] text-text-3">{t("Birleşik kapsamayı ve işaretli adımları panellerdeki ayarlarla tek seferde çalıştırır; klasör seçilirse beyan, kabul föyü, mühendislik raporu, teklif paketi ve analiz özeti yazılır. Sonuçlar ilgili panellerde de görünür.")}</p>
      <div className="mt-2 flex flex-col gap-1 text-[12px]">
        {OPTIONAL_STEPS.map((k) => {
          const off = k === "mixed" && !hasPtz;
          return (
            <label key={k} className={`flex items-center gap-2 ${off ? "text-text-3" : ""}`}>
              <input type="checkbox" disabled={off} checked={defaults[k]} onChange={(e) => setTick(k, e.target.checked)} />
              {optionLabel(k)}{off ? ` — ${t("plan PTZ içermiyor")}` : ""}
            </label>
          );
        })}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button disabled={!isTauri()} onClick={pickDir}><FolderOpen size={14} /> {t("Klasör Seç")}</Button>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-text-2" title={outDir}>{outDir || t("klasör seçilmedi (yalnız hesaplanır, dosya yazılmaz)")}</span>
        {outDir && <button type="button" aria-label={t("Klasörü temizle")} title={t("Klasörü temizle")} onClick={() => onSettings({ include: settings?.include ?? {}, outDir: "" })}><X size={14} /></button>}
      </div>
      <Button primary className="mt-2 w-full" disabled={job.running} onClick={run}><Play size={14} /> {t("Tam Analizi Çalıştır")}</Button>
      {p && <div className="mt-1 font-mono text-[11px] text-text-3">{p.message}</div>}
      {msg && <div className="mt-1 text-[11px] text-danger">■ {msg}</div>}
      {res && (
        <div className="mt-2 text-[12px]">
          <div className="flex flex-wrap gap-x-3 font-mono text-[11px] text-text-2">
            <span>● {t("{n} tamam", { n: res.counts.ok })}</span><span>– {t("{n} atlandı", { n: res.counts.skipped })}</span>
            <span className={res.counts.failed ? "text-danger" : ""}>■ {t("{n} başarısız", { n: res.counts.failed })}</span>
            <span>{t("{s} sn", { s: fmt(res.total_s, 1) })}</span>
          </div>
          <ul className="mt-1 flex flex-col gap-1">
            {res.steps.map((r) => (
              <li key={r.id} className="rounded bg-bg-3 px-2 py-1">
                <div className="flex items-baseline gap-2">
                  <span className={`font-mono ${r.status === "ok" ? "text-primary" : r.status === "failed" ? "text-danger" : "text-text-3"}`}>{STEP_MARK[r.status]}</span>
                  <span className="font-semibold">{stepTitle(r.id, r.title)}</span>
                  <span className="text-text-3">{statusText(r)}</span>
                  {r.status === "ok" && <span className="ml-auto font-mono text-[11px] text-text-3">{t("{s} sn", { s: fmt(r.elapsed_s, 1) })}</span>}
                </div>
                {r.status === "skipped" && <div className="ml-5 text-[11px] text-text-3">{r.reason}{r.kept_previous ? ` — ${t("önceki sonuç korunuyor")}` : ""}</div>}
                {r.status === "failed" && <div className="ml-5 text-[11px] text-danger">{r.error}</div>}
                {r.status === "ok" && stepLines(r).map((l) => <div key={l} className="ml-5 font-mono text-[11px] text-text-2">{l}</div>)}
              </li>
            ))}
          </ul>
          {res.written.length > 0 && (
            <div className="mt-2">
              <div className="label mb-1">{t("Yazılan dosyalar")}</div>
              <ul className="flex flex-col gap-0.5">
                {res.written.map((f) => (
                  <li key={f} className="flex items-center gap-2 font-mono text-[11px]">
                    <span className="min-w-0 flex-1 truncate" title={f}>{f.split(/[\\/]/).pop()}</span>
                    <button type="button" aria-label={t("Aç")} title={t("Aç")} onClick={() => void openFile(f)}><ExternalLink size={13} /></button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {res.summary_error && <div className="mt-1 text-[11px] text-danger">■ {res.summary_error}</div>}
          <div className="mt-1 text-[11px] text-text-3">
            {res.measured ? t("● Ölçülmüş yükseklik modeli") : t("▲ Temsili arazi: ölçüm değil")} · {t("analiz ızgarası {f}×", { f: res.refine_factor })}
          </div>
        </div>
      )}
    </Panel>
  );
}
