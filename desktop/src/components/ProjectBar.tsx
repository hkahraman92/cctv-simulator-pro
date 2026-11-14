import { useEffect, useState } from "react";
import { open, save } from "@tauri-apps/plugin-dialog";
import { isTauri } from "@tauri-apps/api/core";
import { FilePlus2, FolderOpen, Redo2, Save, SaveAll, Undo2 } from "lucide-react";
import { useCore } from "../core/CoreProvider";
import { undoScope, useProject } from "../core/project";
import { confirmDialog, promptDialog } from "./dialogs";
import type { PPMLevel } from "../core/api";

// 28x28 hit area, icon stays 14 px; focus ring comes from the global :focus-visible rule
const ICON_BTN = "flex h-7 w-7 items-center justify-center rounded text-text-2 hover:bg-bg-5 disabled:opacity-40";

const FILTERS = [{ name: "CCTV projesi", extensions: ["json"] }];

/** Project name + saved state + open / save, in the title bar. Same .json schema as the classic app. */
export default function ProjectBar() {
  const { api, state } = useCore();
  const proj = useProject();
  const [msg, setMsg] = useState("");

  const doSave = async (asNew: boolean, forcedPath?: string) => {
    let path = forcedPath ?? proj.path;
    if (!forcedPath && (asNew || !path)) {
      const p = await save({ title: "Projeyi kaydet", filters: FILTERS, defaultPath: `${proj.name || "proje"}.json` });
      if (!p) return;
      path = p.endsWith(".json") ? p : `${p}.json`;
    }
    const name = proj.name === "Yeni proje" ? (path.split(/[\\/]/).pop() ?? "").replace(/\.json$/, "") : proj.name;
    try {
      await api.saveProject(path, {
        cameras: proj.sites.map((s) => s.camera), project_name: name,
        plan_path: proj.plan.plan_path, plan_width_m: proj.plan.plan_width_m,
        target_point: { active: proj.plan.target.active, name: "Kontrol Noktası", x_m: proj.plan.target.x_m,
          y_m: proj.plan.target.y_m, required_level_key: proj.plan.level_key },
        // the terrain page may never have opened this session: keep the block read from the file
        ...(proj.terrainBlock ?? proj.loadedTerrain?.block ?? {}),
        ...(proj.levels ? { ppm_levels: proj.levels } : {}),
      });
      proj.markSaved(path, name);
      setMsg("");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const doOpen = async (forcedPath?: string) => {
    if (!forcedPath && proj.dirty && !(await confirmDialog("Kaydedilmemiş değişiklikler kaybolacak. Devam edilsin mi?"))) return;
    const p = forcedPath ?? (await open({ title: "Proje aç", filters: FILTERS }));
    if (typeof p !== "string") return;
    try {
      const r = await api.loadProject(p);
      const sites = r.project.cameras.map((c) => ({ camera: c, focal: r.project.lens_mode === "max" ? c.focal_max_mm : c.focal_min_mm }));
      const tp = (r.project.target_point ?? {}) as { active?: boolean; x_m?: number; y_m?: number; required_level_key?: string };
      proj.load(sites, p, r.project.project_name || (p.split(/[\\/]/).pop() ?? "").replace(/\.json$/, ""), {
        plan_path: String(r.project.plan_path ?? ""), plan_width_m: Number(r.project.plan_width_m ?? 45) || 45,
        target: { active: !!tp.active, x_m: tp.x_m ?? 12, y_m: tp.y_m ?? 0 }, level_key: tp.required_level_key || "Characterize",
      }, Object.fromEntries(Object.entries(r.project).filter(([k]) =>
        k.startsWith("terrain_") || ["viewshed_range_m", "weather", "placements", "ptz"].includes(k))),
        (r.project.ppm_levels as PPMLevel[] | undefined) ?? null);
      setMsg("");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const doNew = async () => {
    if (proj.dirty && !(await confirmDialog("Kaydedilmemiş değişiklikler kaybolacak. Yeni proje açılsın mı?", { okLabel: "Yeni proje" }))) return;
    proj.reset();
    setMsg("");
  };
  // the active page first (terrain: fence edits), then the project's own history
  const undo = () => { if (!undoScope.current?.undo()) proj.undo(); };
  const redo = () => { if (!undoScope.current?.redo()) proj.redo(); };

  // Development builds only: lets the UI automation open / save without the native dialog.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    (window as unknown as { __cctvTest?: object }).__cctvTest = {
      open: (path: string) => doOpen(path), save: (path: string) => doSave(false, path), reset: () => proj.reset(),
    };
  });

  // Ctrl+S / Ctrl+O, as in the classic app
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      const k = e.key.toLowerCase();
      // in a text field Ctrl+Z / Ctrl+Y belong to the field
      const typing = /^(input|textarea|select)$/i.test((e.target as Element)?.tagName ?? "");
      if (k === "n") { e.preventDefault(); void doNew(); }
      if (!typing && k === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
      if (!typing && (k === "y" || (k === "z" && e.shiftKey))) { e.preventDefault(); redo(); }
      if (e.key.toLowerCase() === "s") { e.preventDefault(); void doSave(e.shiftKey); }
      if (e.key.toLowerCase() === "o") { e.preventDefault(); void doOpen(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const can = state === "ready" && isTauri();
  return (
    <div className="flex items-center gap-2">
      <button type="button" title={`Proje adını değiştir: ${proj.name}`} aria-label={`Proje adını değiştir: ${proj.name}`} className="inline-flex h-7 min-w-0 max-w-[260px] items-center rounded px-1.5 font-mono text-[11px] text-secondary hover:bg-bg-5"
        onClick={async () => { const n = await promptDialog("Proje adı:", { initial: proj.name }); if (n) proj.rename(n); }}><span className="truncate">{proj.name}</span></button>
      <span className={`shrink-0 whitespace-nowrap font-mono text-[11px] ${proj.dirty ? "text-warn" : "text-text-3"}`}>
        {proj.dirty ? "▲ Kaydedilmedi" : proj.path ? "● Kaydedildi" : ""}
      </span>
      {msg && <span className="max-w-[360px] truncate font-mono text-[11px] text-danger" title={msg}>■ {msg}</span>}
      <button type="button" onClick={undo} title="Geri al (Ctrl+Z)" aria-label="Geri al (Ctrl+Z)" className={ICON_BTN}><Undo2 size={14} /></button>
      <button type="button" onClick={redo} title="Yinele (Ctrl+Y)" aria-label="Yinele (Ctrl+Y)" className={ICON_BTN}><Redo2 size={14} /></button>
      <button type="button" onClick={() => void doNew()} title="Yeni proje (Ctrl+N)" aria-label="Yeni proje (Ctrl+N)" className={ICON_BTN}><FilePlus2 size={14} /></button>
      <button type="button" disabled={!can} onClick={() => void doOpen()} title="Proje aç (Ctrl+O)" aria-label="Proje aç (Ctrl+O)" className={ICON_BTN}><FolderOpen size={14} /></button>
      <button type="button" disabled={!can} onClick={() => doSave(false)} title="Kaydet (Ctrl+S)" aria-label="Kaydet (Ctrl+S)" className={ICON_BTN}><Save size={14} /></button>
      <button type="button" disabled={!can} onClick={() => doSave(true)} title="Farklı kaydet (Ctrl+Shift+S)" aria-label="Farklı kaydet (Ctrl+Shift+S)" className={ICON_BTN}><SaveAll size={14} /></button>
    </div>
  );
}
