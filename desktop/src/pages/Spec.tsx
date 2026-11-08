import { useEffect, useMemo, useState } from "react";
import { open, save } from "@tauri-apps/plugin-dialog";
import { isTauri } from "@tauri-apps/api/core";
import { BrainCircuit, ClipboardCheck, FileDown, FolderOpen, Play, Save, Trash2 } from "lucide-react";
import { useCore } from "../core/CoreProvider";
import { useProject } from "../core/project";
import { useJob } from "../core/useJob";
import { publishSpecResult } from "../core/activeTerrain";
import type { Analysis, MatrixRow, Status } from "../core/specApi";
import JobBar from "../components/JobBar";
import { Button, Panel, fmt } from "../components/ui";

const STATUSES: Status[] = ["Uyumlu", "Kısmi", "Uyumsuz", "Bulunamadı"];
const STATUS_STYLE: Record<Status, string> = {
  Uyumlu: "bg-ok-bg text-primary",
  Kısmi: "bg-warn-bg text-warn",
  Uyumsuz: "bg-danger-bg text-danger",
  Bulunamadı: "bg-bg-5 text-text-2",
};
const MARK: Record<Status, string> = { Uyumlu: "●", Kısmi: "▲", Uyumsuz: "■", Bulunamadı: "◆" };
const SOURCE: Record<Analysis["source"], string> = {
  rule: "Kurallı + optik motor", ollama: "Yerel model (Ollama)", gemini: "Gemini", template: "İster şablonu",
};

export default function Spec() {
  const { spec, state } = useCore();
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
    spec.templates().then((t) => setTemplates(t.templates)).catch(() => undefined);
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
    setNote(a.fallback_reason ? `${a.fallback_reason} Kurallı + fizik analizine dönüldü.` : "");
  };

  const openFile = async () => {
    const p = await open({ title: "Şartname dosyası", filters: [{ name: "Şartname", extensions: ["pdf", "txt", "md"] }] });
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
    void job.run(engine === "rule" ? "Kurallı analiz" : `${engine === "ollama" ? "Yerel model" : "Gemini"} analizi`, (o) =>
      spec.analyze({ text: src, engine, model: model || undefined, api_key: apiKey || undefined, pdf_path: pdfPath || undefined }, o),
    ).then((a) => adopt(a, src));
  };

  const overrideRow = async (row: MatrixRow, status: Status) => {
    if (!analysis) return;
    const reason = window.prompt(`"${row.requirement}" → ${status}. Gerekçe (eğitim verisine yazılır):`, "") ?? null;
    if (reason === null) return;
    await spec.override(analysedText, row, status, reason);
    setAnalysis({
      ...analysis,
      result: {
        ...analysis.result,
        matrix: analysis.result.matrix.map((r) => (r === row ? { ...r, user_status: status, user_note: reason || "elle geçersiz kılındı" } : r)),
      },
    });
  };

  const makeStatement = async (pdf: boolean) => {
    if (!result) return;
    let path: string | undefined;
    if (pdf) {
      const p = await save({ title: "Uygunluk beyanı (PDF)", filters: [{ name: "PDF", extensions: ["pdf"] }], defaultPath: "uygunluk-beyani.pdf" });
      if (!p) return;
      path = p;
    }
    const r = await spec.statement(result, projectName, path);
    setStatement(r.markdown);
    if (r.pdf_path) setNote(`PDF yazıldı: ${r.pdf_path}`);
  };

  return (
    <div className="flex min-h-full flex-wrap content-start gap-3 p-3">
      <div className="flex w-[340px] shrink-0 flex-col gap-3">
        <Panel title="Şartname" icon={<ClipboardCheck size={16} className="text-secondary" />}
          right={<Button disabled={!isTauri()} onClick={openFile}><FolderOpen size={14} /> Dosya</Button>}>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={14} aria-label="Şartname metni"
            placeholder="Şartname metnini yapıştırın ya da PDF / metin dosyası açın…"
            className="w-full resize-y rounded border border-line bg-bg-0 p-2 font-mono text-[11px] leading-relaxed" />
          <div className="mt-1 font-mono text-[10px] text-text-3">{fmt(text.length, 0)} karakter{pdfPath ? " · PDF Gemini'ye de gönderilebilir" : ""}</div>
        </Panel>
        <Panel title="Analiz yolu">
          <div className="grid grid-cols-3 gap-1">
            {([["rule", "Kurallı"], ["ollama", "Yerel"], ["gemini", "Gemini"]] as const).map(([id, l]) => (
              <button key={id} type="button" onClick={() => setEngine(id)}
                className={`h-8 rounded text-[11px] font-semibold ${engine === id ? "bg-bg-6 text-primary" : "bg-bg-5 text-text-2"}`}>{l}</button>
            ))}
          </div>
          {engine === "rule" && <p className="mt-2 text-[11px] text-text-3">Çevrimdışı. DORI/menzil isterleri optik motorla, diğerleri broşür alanlarıyla denetlenir.</p>}
          {engine === "ollama" && (
            <div className="mt-2 text-[12px]">
              <div className={engines?.ollama ? "text-primary" : "text-warn"}>{engines?.ollama ? "● Ollama çalışıyor (localhost:11434)" : "▲ Ollama yanıt vermiyor — kurallı analize dönülür"}</div>
              <select className="mt-2 h-8 w-full rounded border border-line bg-bg-0 px-2" value={model} onChange={(e) => setModel(e.target.value)} aria-label="Yerel model">
                {(engines?.ollama_models ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
          )}
          {engine === "gemini" && (
            <div className="mt-2 flex flex-col gap-2 text-[12px]">
              <select className="h-8 w-full rounded border border-line bg-bg-0 px-2" value={model} onChange={(e) => setModel(e.target.value)} aria-label="Gemini modeli">
                {(engines?.gemini_models ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
              <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="off"
                placeholder={engines?.gemini_key_in_env ? "GEMINI_API_KEY ortamdan okunacak" : "API anahtarı (kaydedilmez)"}
                className="h-8 rounded border border-line bg-bg-0 px-2" aria-label="Gemini API anahtarı" />
              <p className="text-[11px] text-text-3">◆ Şartname metni (ve PDF) Google'a gönderilir. Gizli belgeler için Kurallı ya da Yerel yolu kullanın.</p>
            </div>
          )}
          <Button primary className="mt-3 w-full" disabled={job.running || !text.trim()} onClick={run}><Play size={14} /> Analiz Et</Button>
        </Panel>
        <Panel title="İster şablonları">
          <div className="flex gap-1.5">
            <select className="h-8 min-w-0 flex-1 rounded border border-line bg-bg-0 px-2 text-[12px]" value={tplName} onChange={(e) => setTplName(e.target.value)} aria-label="Şablon">
              <option value="">{templates.length ? "Şablon seç…" : "Kayıtlı şablon yok"}</option>
              {templates.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <Button disabled={!tplName || job.running} onClick={() => job.run("Şablon değerlendiriliyor", () => spec.runTemplate(tplName)).then((a) => adopt(a, ""))}><Play size={14} /></Button>
            <Button disabled={!tplName} title="Şablonu sil" onClick={() => { if (window.confirm(`"${tplName}" silinsin mi?`)) void spec.deleteTemplate(tplName).then(() => spec.templates()).then((t) => { setTemplates(t.templates); setTplName(""); }); }}><Trash2 size={14} /></Button>
          </div>
          <Button className="mt-2 w-full" disabled={!result?.requirements.length} onClick={async () => {
            const n = window.prompt("Şablon adı (ör. firma adı):", "");
            if (!n) return;
            await spec.saveTemplate(n, result!.requirements);
            setTemplates((await spec.templates()).templates);
          }}><Save size={14} /> Bu isterleri şablon olarak kaydet</Button>
        </Panel>
      </div>

      <div className="flex min-w-[400px] flex-[3_1_420px] flex-col gap-3">
        <JobBar job={job} onCancel={job.cancel} onDismiss={job.clearError} />
        {note && <div className="rounded bg-warn-bg px-3 py-2 text-[12px] text-warn">▲ {note}</div>}
        {!result && <div className="flex min-h-[300px] items-center justify-center rounded-md bg-bg-4 text-text-3">Şartnameyi girip "Analiz Et" deyin.</div>}
        {result && (
          <section className="overflow-hidden rounded-md bg-bg-4">
            <div className="flex flex-wrap items-center gap-2 p-3">
              <h2 className="text-[13px] font-semibold">Uyum matrisi</h2>
              <span className="font-mono text-[10px] text-text-3">{SOURCE[analysis!.source]} · {result.requirements.length} ister</span>
              <div className="flex-1" />
              <select className="h-8 max-w-[360px] rounded border border-line bg-bg-0 px-2 text-[12px]" value={shownCamera} onChange={(e) => setCamera(e.target.value)} aria-label="Kamera">
                {cameras.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div className="max-h-[560px] overflow-auto">
              <table className="w-full border-collapse text-[12px]">
                <thead><tr>{["Madde", "İster", "Durum", "Kanıt"].map((h) => <th key={h} className="label sticky top-0 bg-bg-5 px-2 py-2 text-left">{h}</th>)}</tr></thead>
                <tbody>
                  {rows.map((r, i) => {
                    const st = r.user_status ?? r.status;
                    return (
                      <tr key={`${r.requirement_id}-${i}`} className="border-b border-line-soft align-top">
                        <td className="px-2 py-2 font-mono text-[10px] whitespace-nowrap text-text-3">{r.requirement_id}<br />{r.standard_clause}</td>
                        <td className="px-2 py-2">{r.requirement}{r.spec_quote ? <div className="mt-0.5 text-[10px] text-text-3">“{r.spec_quote}”</div> : null}</td>
                        <td className="px-2 py-2 whitespace-nowrap">
                          <select value={st} onChange={(e) => void overrideRow(r, e.target.value as Status)} aria-label="Durum (elle düzelt)"
                            className={`h-7 rounded px-1.5 font-mono text-[11px] font-semibold ${STATUS_STYLE[st] ?? ""}`}>
                            {STATUSES.map((s) => <option key={s} value={s}>{MARK[s]} {s}</option>)}
                          </select>
                          {typeof r.confidence === "number" && <div className="mt-0.5 font-mono text-[10px] text-text-3">güven %{fmt(r.confidence * 100, 0)}</div>}
                          {r.user_status && <div className="mt-0.5 text-[10px] text-secondary">elle: {r.user_note}</div>}
                        </td>
                        <td className="px-2 py-2 text-text-2">{r.evidence}{r.evidence_kind && <span className="ml-1 font-mono text-[10px] text-text-3">({r.evidence_kind})</span>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        )}
        {statement && (
          <Panel title="EN 62676-4 uygunluk beyanı (önizleme)">
            <pre className="max-h-[360px] overflow-auto whitespace-pre-wrap font-mono text-[11px] text-text-2">{statement}</pre>
          </Panel>
        )}
      </div>

      {result && (
        <div className="flex w-[320px] shrink-0 flex-col gap-3">
          <Panel title="Kamera puanları">
            {result.camera_scores.slice(0, 8).map((s) => (
              <button key={s.profile_name + s.camera_model} type="button" onClick={() => setCamera(s.camera_model)}
                className={`mb-1 flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] ${s.camera_model === shownCamera ? "bg-bg-6" : "hover:bg-bg-5"}`}>
                <span className={`font-mono text-[12px] font-bold ${s.score >= 82 ? "text-primary" : s.score >= 50 ? "text-warn" : "text-danger"}`}>{s.score}</span>
                <span className="min-w-0 flex-1 truncate" title={s.camera_model}>{s.camera_model}</span>
              </button>
            ))}
            <p className="mt-2 text-[11px] text-text-2">{result.recommendation}</p>
          </Panel>
          {result.clarification_questions.length > 0 && (
            <Panel title={`Netleştirilecek (${result.clarification_questions.length})`}>
              <ul className="list-disc pl-4 text-[11px] text-warn">{result.clarification_questions.map((q) => <li key={q} className="mb-1">{q}</li>)}</ul>
            </Panel>
          )}
          <Panel title="Çıktı">
            <div className="flex flex-col gap-1.5">
              <Button onClick={() => void makeStatement(false)}><FileDown size={14} /> Beyanı Önizle</Button>
              <Button primary disabled={!isTauri()} onClick={() => void makeStatement(true)}><FileDown size={14} /> Uygunluk Beyanı (PDF)</Button>
              <Button disabled={!isTauri()} onClick={async () => {
                const p = await save({ title: "Eğitim veri seti", filters: [{ name: "JSON Lines", extensions: ["jsonl"] }], defaultPath: "cctv_compliance_train.jsonl" });
                if (!p) return;
                try { const r = await spec.trainingExport(p); setNote(`${r.written} örnek yazıldı (${r.analyses} analiz · ${r.overrides} elle düzeltme).`); }
                catch (e) { setNote(e instanceof Error ? e.message : String(e)); }
              }}><BrainCircuit size={14} /> Eğitim Verisi Dışa Aktar</Button>
            </div>
          </Panel>
        </div>
      )}
    </div>
  );
}
