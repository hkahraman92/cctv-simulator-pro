import { useEffect, useMemo, useState } from "react";
import { open, save } from "@tauri-apps/plugin-dialog";
import { isTauri } from "@tauri-apps/api/core";
import { BrainCircuit, ClipboardCheck, FileDown, FolderOpen, Play, Save, Trash2 } from "lucide-react";
import { useCore } from "../core/CoreProvider";
import { useProject } from "../core/project";
import { useJob } from "../core/useJob";
import { uniqueQuestions } from "../core/clarify";
import { publishSpecResult } from "../core/activeTerrain";
import type { Analysis, ComplianceResult, MatrixRow, Status } from "../core/specApi";
import JobBar from "../components/JobBar";
import { Button, Panel, fmt } from "../components/ui";
import { confirmDialog, promptDialog } from "../components/dialogs";
import { t, tn } from "../core/i18n";

// Status strings are API values compared in code and sent to the core; only their display is translated.
const STATUSES: Status[] = ["Uyumlu", "Kısmi", "Uyumsuz", "Bulunamadı"]; // i18n-ignore
const STATUS_STYLE: Record<Status, string> = {
  Uyumlu: "bg-ok-bg text-primary",
  Kısmi: "bg-warn-bg text-warn", // i18n-ignore
  Uyumsuz: "bg-danger-bg text-danger",
  Bulunamadı: "bg-bg-5 text-text-2", // i18n-ignore
};
const MARK: Record<Status, string> = { Uyumlu: "●", Kısmi: "▲", Uyumsuz: "■", Bulunamadı: "◆" }; // i18n-ignore
const sourceLabel = (k: Analysis["source"]): string => ({
  rule: t("Kurallı + optik motor"), ollama: t("Yerel model (Ollama)"), gemini: "Gemini", template: t("İster şablonu"),
})[k];

function csvCell(v: unknown): string {
  const s = v == null ? "" : String(v);
  return /[;"\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Compliance matrix as Turkish-Excel CSV: BOM, `;`, CRLF; one status column per camera model. */
export function buildMatrixCsv(result: ComplianceResult): string {
  const cams = [...new Set(result.matrix.map((r) => r.camera_model))];
  const category = new Map(result.requirements.map((q) => [q.id, q.category]));
  const order: string[] = [];
  const byReq = new Map<string, MatrixRow[]>();
  for (const r of result.matrix) {
    if (!byReq.has(r.requirement_id)) { byReq.set(r.requirement_id, []); order.push(r.requirement_id); }
    byReq.get(r.requirement_id)!.push(r);
  }
  const lines = [[t("Madde"), t("İster"), t("Kategori"), t("Kanıt türü"), t("Standart maddesi"), ...cams]];
  for (const id of order) {
    const rs = byReq.get(id)!;
    const first = rs[0];
    lines.push([
      id, first.requirement, category.get(id) ?? "",
      rs.find((r) => r.evidence_kind)?.evidence_kind ?? "",
      rs.find((r) => r.standard_clause)?.standard_clause ?? "",
      ...cams.map((c) => { const r = rs.find((x) => x.camera_model === c); return r ? (r.user_status ?? r.status) : ""; }),
    ]);
  }
  return "\uFEFF" + lines.map((l) => l.map(csvCell).join(";")).join("\r\n") + "\r\n";
}

export default function Spec() {
  const { spec, report, state } = useCore();
  const { name: projectName } = useProject();
  const job = useJob();
  const [text, setText] = useState("");
  const [pdfPath, setPdfPath] = useState("");
  const [engine, setEngine] = useState<"rule" | "ollama" | "gemini">("rule");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [engines, setEngines] = useState<{ ollama: boolean; ollama_models: string[]; gemini_key_in_env: boolean; gemini_models: string[] } | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [analysedText, setAnalysedText] = useState("");
  const [camera, setCamera] = useState("");
  const [templates, setTemplates] = useState<string[]>([]);
  const [tplName, setTplName] = useState("");
  const [note, setNote] = useState("");
  const [statement, setStatement] = useState("");

  useEffect(() => {
    if (state !== "ready") return;
    spec.engines().then(setEngines).catch(() => undefined);
    spec.templates().then((tl) => setTemplates(tl.templates)).catch(() => undefined);
  }, [spec, state]);

  const result = analysis?.result;
  useEffect(() => { publishSpecResult(result ?? null); }, [result]);   // offer package (Raporlar)
  const cameras = useMemo(() => [...new Set((result?.matrix ?? []).map((r) => r.camera_model))], [result]);
  const best = result?.camera_scores[0]?.camera_model ?? "";
  const shownCamera = camera || best;
  const rows = (result?.matrix ?? []).filter((r) => !shownCamera || r.camera_model === shownCamera);

  const adopt = (a: Analysis | undefined, src: string) => {
    if (!a) return;
    setAnalysis(a);
    setAnalysedText(src);
    setCamera("");
    setStatement("");
    setNote(a.fallback_reason ? t("{reason} Kurallı + fizik analizine dönüldü.", { reason: a.fallback_reason }) : "");
  };

  const openFile = async () => {
    const p = await open({ title: t("Şartname dosyası"), filters: [{ name: t("Şartname"), extensions: ["pdf", "txt", "md"] }] });
    if (typeof p !== "string") return;
    try {
      const f = await spec.readFile(p);
      setText(f.text);
      setPdfPath(f.pdf ? p : "");
      setNote("");
    } catch (e: unknown) {
      const err = e as { message?: string; data?: { pdf_only?: boolean } };
      setPdfPath(err.data?.pdf_only ? p : "");
      setNote(err.message ?? String(e));
    }
  };

  const run = () => {
    const src = text.trim();
    void job.run(engine === "rule" ? t("Kurallı analiz") : engine === "ollama" ? t("Yerel model analizi") : t("Gemini analizi"), (o) =>
      spec.analyze({ text: src, engine, model: model || undefined, api_key: apiKey || undefined, pdf_path: pdfPath || undefined }, o),
    ).then((a) => adopt(a, src));
  };

  const overrideRow = async (row: MatrixRow, status: Status) => {
    if (!analysis) return;
    const reason = await promptDialog(t('"{req}" → {status}. Gerekçe (eğitim verisine yazılır):', { req: row.requirement, status: t(status) }));
    if (reason === null) return;
    await spec.override(analysedText, row, status, reason);
    setAnalysis({
      ...analysis,
      result: {
        ...analysis.result,
        matrix: analysis.result.matrix.map((r) => (r === row ? { ...r, user_status: status, user_note: reason || t("elle geçersiz kılındı") } : r)),
      },
    });
  };

  // A question that an ambiguity entry already shows is not listed a second time.
  const extraQuestions = useMemo(() => (result ? uniqueQuestions(result.ambiguities, result.clarification_questions) : []), [result]);

  const makeStatement = async (pdf: boolean) => {
    if (!result) return;
    let path: string | undefined;
    if (pdf) {
      const p = await save({ title: t("Uygunluk beyanı (PDF)"), filters: [{ name: "PDF", extensions: ["pdf"] }], defaultPath: "uygunluk-beyani.pdf" });
      if (!p) return;
      path = p;
    }
    const r = await spec.statement(result, projectName, path);
    setStatement(r.markdown);
    if (r.pdf_path) setNote(t("PDF yazıldı: {path}", { path: r.pdf_path }));
  };

  const saveMatrixCsv = async () => {
    if (!result) return;
    const p = await save({ title: t("Uygunluk matrisi (CSV)"), filters: [{ name: "CSV", extensions: ["csv"] }], defaultPath: "uygunluk-matrisi.csv" });
    if (!p) return;
    try {
      const r = await report.writeFile({ path: p, text: buildMatrixCsv(result) });
      setNote(t("CSV yazıldı: {path}", { path: r.path }));
    } catch (e) { setNote(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <div className="flex min-h-full flex-wrap content-start gap-3 p-3">
      <div className="flex w-[340px] shrink-0 flex-col gap-3">
        <Panel title={t("Şartname")} icon={<ClipboardCheck size={16} className="text-secondary" />}
          right={<Button disabled={!isTauri()} onClick={openFile}><FolderOpen size={14} /> {t("Dosya")}</Button>}>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={14} aria-label={t("Şartname metni")}
            placeholder={t("Şartname metnini yapıştırın ya da PDF / metin dosyası açın…")}
            className="w-full resize-y rounded border border-line bg-bg-0 p-2 font-mono text-[11px] leading-relaxed" />
          <div className="mt-1 font-mono text-[11px] text-text-3">{t("{n} karakter", { n: fmt(text.length, 0) })}{pdfPath ? " " + t("· PDF Gemini'ye de gönderilebilir") : ""}</div>
        </Panel>
        <Panel title={t("Analiz yolu")}>
          <div className="grid grid-cols-3 gap-1">
            {([["rule", t("Kurallı")], ["ollama", t("Yerel")], ["gemini", "Gemini"]] as const).map(([id, l]) => (
              <button key={id} type="button" onClick={() => setEngine(id)}
                className={`h-8 rounded text-[11px] font-semibold ${engine === id ? "bg-bg-6 text-primary" : "bg-bg-5 text-text-2"}`}>{l}</button>
            ))}
          </div>
          {engine === "rule" && <p className="mt-2 text-[11px] text-text-3">{t("Çevrimdışı. DORI/menzil isterleri optik motorla, diğerleri broşür alanlarıyla denetlenir.")}</p>}
          {engine === "ollama" && (
            <div className="mt-2 text-[12px]">
              <div className={engines?.ollama ? "text-primary" : "text-warn"}>{engines?.ollama ? t("● Ollama çalışıyor (localhost:11434)") : t("▲ Ollama yanıt vermiyor — kurallı analize dönülür")}</div>
              <select className="mt-2 h-8 w-full rounded border border-line bg-bg-0 px-2" value={model} onChange={(e) => setModel(e.target.value)} aria-label={t("Yerel model")}>
                {(engines?.ollama_models ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
          )}
          {engine === "gemini" && (
            <div className="mt-2 flex flex-col gap-2 text-[12px]">
              <select className="h-8 w-full rounded border border-line bg-bg-0 px-2" value={model} onChange={(e) => setModel(e.target.value)} aria-label={t("Gemini modeli")}>
                {(engines?.gemini_models ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
              <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="off"
                placeholder={engines?.gemini_key_in_env ? t("GEMINI_API_KEY ortamdan okunacak") : t("API anahtarı (kaydedilmez)")}
                className="h-8 rounded border border-line bg-bg-0 px-2" aria-label={t("Gemini API anahtarı")} />
              <p className="text-[11px] text-text-3">{t("◆ Şartname metni (ve PDF) Google'a gönderilir. Gizli belgeler için Kurallı ya da Yerel yolu kullanın.")}</p>
            </div>
          )}
          <Button primary className="mt-3 w-full" disabled={job.running || !text.trim()} onClick={run}><Play size={14} /> {t("Analiz Et")}</Button>
        </Panel>
        <Panel title={t("İster şablonları")}>
          <div className="flex gap-1.5">
            <select className="h-8 min-w-0 flex-1 rounded border border-line bg-bg-0 px-2 text-[12px]" value={tplName} onChange={(e) => setTplName(e.target.value)} aria-label={t("Şablon")}>
              <option value="">{templates.length ? t("Şablon seç…") : t("Kayıtlı şablon yok")}</option>
              {templates.map((tl) => <option key={tl} value={tl}>{tl}</option>)}
            </select>
            <Button disabled={!tplName || job.running} onClick={() => job.run(t("Şablon değerlendiriliyor"), () => spec.runTemplate(tplName)).then((a) => adopt(a, ""))} title={t("Şablonu çalıştır")} aria-label={t("Şablonu çalıştır")}><Play size={14} /></Button>
            <Button disabled={!tplName} title={t("Şablonu sil")} onClick={async () => { if (await confirmDialog(t('"{name}" silinsin mi?', { name: tplName }), { okLabel: t("Sil"), danger: true })) void spec.deleteTemplate(tplName).then(() => spec.templates()).then((tl) => { setTemplates(tl.templates); setTplName(""); }); }}><Trash2 size={14} /></Button>
          </div>
          <Button className="mt-2 w-full" disabled={!result?.requirements.length} onClick={async () => {
            const n = await promptDialog(t("Şablon adı (ör. firma adı):"));
            if (!n) return;
            await spec.saveTemplate(n, result!.requirements);
            setTemplates((await spec.templates()).templates);
          }}><Save size={14} /> {t("Bu isterleri şablon olarak kaydet")}</Button>
        </Panel>
      </div>

      <div className="flex min-w-[400px] flex-[3_1_420px] flex-col gap-3">
        <JobBar job={job} onCancel={job.cancel} onDismiss={job.clearError} />
        {note && <div className="rounded bg-warn-bg px-3 py-2 text-[12px] text-warn">▲ {note}</div>}
        {!result && <div className="flex min-h-[300px] items-center justify-center rounded-md bg-bg-4 text-text-3">{t('Şartnameyi girip "Analiz Et" deyin.')}</div>}
        {result && (
          <section className="overflow-hidden rounded-md bg-bg-4">
            <div className="flex flex-wrap items-center gap-2 p-3">
              <h2 className="text-[13px] font-semibold">{t("Uyum matrisi")}</h2>
              <span className="font-mono text-[11px] text-text-3">{sourceLabel(analysis!.source)} · {tn(result.requirements.length, "{n} ister")}</span>
              <div className="flex-1" />
              <select className="h-8 max-w-[360px] rounded border border-line bg-bg-0 px-2 text-[12px]" value={shownCamera} onChange={(e) => setCamera(e.target.value)} aria-label={t("Kamera")}>
                {cameras.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div className="max-h-[560px] overflow-auto">
              <table className="w-full border-collapse text-[12px]">
                <thead><tr>{[t("Madde"), t("İster"), t("Durum"), t("Kanıt")].map((h) => <th key={h} className="label sticky top-0 bg-bg-5 px-2 py-2 text-left">{h}</th>)}</tr></thead>
                <tbody>
                  {rows.map((r, i) => {
                    const st = r.user_status ?? r.status;
                    return (
                      <tr key={`${r.requirement_id}-${i}`} className="border-b border-line-soft align-top">
                        <td className="px-2 py-2 font-mono text-[11px] whitespace-nowrap text-text-3">{r.requirement_id}<br />{r.standard_clause}</td>
                        <td className="px-2 py-2">{r.requirement}{r.spec_quote ? <div className="mt-0.5 text-[11px] text-text-3">“{r.spec_quote}”</div> : null}</td>
                        <td className="px-2 py-2 whitespace-nowrap">
                          <select value={st} onChange={(e) => void overrideRow(r, e.target.value as Status)} aria-label={t("Durum (elle düzelt)")}
                            className={`h-7 rounded px-1.5 font-mono text-[11px] font-semibold ${STATUS_STYLE[st] ?? ""}`}>
                            {STATUSES.map((s) => <option key={s} value={s}>{MARK[s]} {t(s)}</option>)}
                          </select>
                          {typeof r.confidence === "number" && <div className="mt-0.5 font-mono text-[11px] text-text-3">{t("güven %{n}", { n: fmt(r.confidence * 100, 0) })}</div>}
                          {r.user_status && <div className="mt-0.5 text-[11px] text-secondary">{t("elle: {note}", { note: r.user_note ?? "" })}</div>}
                        </td>
                        <td className="px-2 py-2 text-text-2">{r.evidence}{r.evidence_kind && <span className="ml-1 font-mono text-[11px] text-text-3">({r.evidence_kind})</span>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        )}
        {statement && (
          <Panel title={t("EN 62676-4 uygunluk beyanı (önizleme)")}>
            <pre className="max-h-[360px] overflow-auto whitespace-pre-wrap font-mono text-[11px] text-text-2">{statement}</pre>
          </Panel>
        )}
      </div>

      {result && (
        <div className="flex w-[320px] shrink-0 flex-col gap-3">
          <Panel title={t("Kamera puanları")}>
            {result.camera_scores.slice(0, 8).map((s) => (
              <button key={s.profile_name + s.camera_model} type="button" onClick={() => setCamera(s.camera_model)}
                className={`mb-1 flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] ${s.camera_model === shownCamera ? "bg-bg-6" : "hover:bg-bg-5"}`}>
                <span className={`font-mono text-[12px] font-bold ${s.score >= 82 ? "text-primary" : s.score >= 50 ? "text-warn" : "text-danger"}`}>{s.score}</span>
                <span className="min-w-0 flex-1 truncate" title={s.camera_model}>{s.camera_model}</span>
              </button>
            ))}
            <p className="mt-2 text-[11px] text-text-2">{result.recommendation}</p>
          </Panel>
          {(result.ambiguities.length > 0 || extraQuestions.length > 0) && (
            <details open className="rounded-md bg-bg-4">
              <summary className="cursor-pointer select-none px-3 py-2 text-[13px] font-semibold">
                {t("Belirsiz ifadeler")} <span className="font-mono text-[11px] text-warn">({result.ambiguities.length})</span>
              </summary>
              <div className="px-3 pb-3">
                {result.ambiguities.map((a, i) => (
                  <div key={`${a.term}-${i}`} className="mb-2 text-[11px]">
                    <div className="text-text-3">“{a.quote}”</div>
                    <div className="text-warn"><span className="font-semibold">{a.term}</span> — {a.clarification}</div>
                  </div>
                ))}
                {extraQuestions.length > 0 && (
                  <>
                    <div className="label mt-2 mb-1">{t("Netleştirme soruları ({n})", { n: extraQuestions.length })}</div>
                    <ul className="list-disc pl-4 text-[11px] text-text-2">{extraQuestions.map((q) => <li key={q} className="mb-1">{q}</li>)}</ul>
                  </>
                )}
              </div>
            </details>
          )}
          <Panel title={t("Çıktı")}>
            <div className="flex flex-col gap-1.5">
              <Button onClick={() => void makeStatement(false)}><FileDown size={14} /> {t("Beyanı Önizle")}</Button>
              <Button primary disabled={!isTauri()} onClick={() => void makeStatement(true)}><FileDown size={14} /> {t("Uygunluk Beyanı (PDF)")}</Button>
              <Button disabled={!isTauri()} onClick={() => void saveMatrixCsv()}><FileDown size={14} /> {t("Matrisi CSV olarak kaydet")}</Button>
              <Button disabled={!isTauri()} onClick={async () => {
                const p = await save({ title: t("Eğitim veri seti"), filters: [{ name: "JSON Lines", extensions: ["jsonl"] }], defaultPath: "cctv_compliance_train.jsonl" });
                if (!p) return;
                try { const r = await spec.trainingExport(p); setNote(t("Yazılan örnek: {written} (analiz: {analyses} · elle düzeltme: {overrides})", { written: r.written ?? 0, analyses: r.analyses, overrides: r.overrides })); }
                catch (e) { setNote(e instanceof Error ? e.message : String(e)); }
              }}><BrainCircuit size={14} /> {t("Eğitim Verisi Dışa Aktar")}</Button>
            </div>
          </Panel>
        </div>
      )}
    </div>
  );
}
