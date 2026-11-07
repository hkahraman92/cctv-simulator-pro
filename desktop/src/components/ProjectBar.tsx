import { useEffect, useState } from "react";
import { open, save } from "@tauri-apps/plugin-dialog";
import { isTauri } from "@tauri-apps/api/core";
import { FolderOpen, Save, SaveAll } from "lucide-react";
import { useCore } from "../core/CoreProvider";
import { useProject } from "../core/project";

const FILTERS = [{ name: "CCTV projesi", extensions: ["json"] }];

/** Project name + saved state + open / save, in the title bar. Same .json schema as the classic app. */
export default function ProjectBar() {
  const { api, state } = useCore();
  const proj = useProject();
  const [msg, setMsg] = useState("");

  const doSave = async (asNew: boolean) => {
    let path = proj.path;
    if (asNew || !path) {
      const p = await save({ title: "Projeyi kaydet", filters: FILTERS, defaultPath: `${proj.name || "proje"}.json` });
      if (!p) return;
      path = p.endsWith(".json") ? p : `${p}.json`;
    }
    const name = proj.name === "Yeni proje" ? (path.split(/[\\/]/).pop() ?? "").replace(/\.json$/, "") : proj.name;
    try {
      await api.saveProject(path, { cameras: proj.sites.map((s) => s.camera), project_name: name });
      proj.markSaved(path, name);
      setMsg("");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const doOpen = async () => {
    if (proj.dirty && !window.confirm("Kaydedilmemiş değişiklikler kaybolacak. Devam edilsin mi?")) return;
    const p = await open({ title: "Proje aç", filters: FILTERS });
    if (typeof p !== "string") return;
    try {
      const r = await api.loadProject(p);
      const sites = r.project.cameras.map((c) => ({ camera: c, focal: r.project.lens_mode === "max" ? c.focal_max_mm : c.focal_min_mm }));
      proj.load(sites, p, r.project.project_name || (p.split(/[\\/]/).pop() ?? "").replace(/\.json$/, ""));
      setMsg("");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };

  // Ctrl+S / Ctrl+O, as in the classic app
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key.toLowerCase() === "s") { e.preventDefault(); void doSave(e.shiftKey); }
      if (e.key.toLowerCase() === "o") { e.preventDefault(); void doOpen(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const can = state === "ready" && isTauri();
  return (
    <div className="flex items-center gap-2">
      <span className="font-mono text-[11px] text-secondary">{proj.name}</span>
      <span className={`font-mono text-[10px] ${proj.dirty ? "text-warn" : "text-text-3"}`}>
        {proj.dirty ? "▲ Kaydedilmedi" : proj.path ? "● Kaydedildi" : ""}
      </span>
      {msg && <span className="max-w-[360px] truncate font-mono text-[10px] text-danger" title={msg}>■ {msg}</span>}
      <button type="button" disabled={!can} onClick={doOpen} title="Proje aç (Ctrl+O)" className="rounded p-1 text-text-2 hover:bg-bg-5 disabled:opacity-40"><FolderOpen size={14} /></button>
      <button type="button" disabled={!can} onClick={() => doSave(false)} title="Kaydet (Ctrl+S)" className="rounded p-1 text-text-2 hover:bg-bg-5 disabled:opacity-40"><Save size={14} /></button>
      <button type="button" disabled={!can} onClick={() => doSave(true)} title="Farklı kaydet (Ctrl+Shift+S)" className="rounded p-1 text-text-2 hover:bg-bg-5 disabled:opacity-40"><SaveAll size={14} /></button>
    </div>
  );
}
