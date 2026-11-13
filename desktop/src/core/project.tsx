import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { DEFAULT_CAMERA, type CameraConfig, type PPMLevel } from "./api";

/** One camera on the plan plus the focal it is being looked at with. */
export interface Site {
  camera: CameraConfig;
  focal: number;
}

/** Site plan settings, saved in the project file (same fields as the classic app). */
export interface PlanSettings {
  plan_path: string;       // "" = no background picture
  plan_width_m: number;    // real width the picture spans
  target: { x_m: number; y_m: number; active: boolean };
  level_key: string;       // required level at the check point (models.DEFAULT_LEVELS key)
}

/** project_io terrain fields (flat, as project.save/load carry them). */
export type TerrainBlock = Record<string, unknown>;

interface ProjectCtx {
  sites: Site[];
  /** the project's PPM levels; null = the engine defaults (models.DEFAULT_LEVELS) */
  levels: PPMLevel[] | null;
  setLevels: (l: PPMLevel[] | null) => void;
  /** what the terrain page last published (saved with the project) */
  terrainBlock: TerrainBlock | null;
  setTerrainBlock: (b: TerrainBlock | null, opts?: { touch?: boolean }) => void;
  /** terrain block of the project file just opened; nonce changes on every open */
  loadedTerrain: { block: TerrainBlock; nonce: number } | null;
  selected: number;
  site: Site;
  plan: PlanSettings;
  path: string;            // "" = never saved
  name: string;
  dirty: boolean;
  select: (i: number) => void;
  add: () => void;
  remove: (i: number) => void;
  /** patch the selected camera; focal is clamped into the (new) lens range */
  update: (patch: Partial<CameraConfig>, focal?: number) => void;
  /** patch any camera by index (dragging on the plan) */
  updateAt: (i: number, patch: Partial<CameraConfig>) => void;
  replaceCamera: (camera: CameraConfig) => void;
  setPlan: (patch: Partial<PlanSettings>) => void;
  load: (sites: Site[], path: string, name: string, plan?: Partial<PlanSettings>, terrain?: TerrainBlock, levels?: PPMLevel[] | null) => void;
  markSaved: (path: string, name: string) => void;
  /** a blank project; resetNonce changes so pages holding their own state (terrain) start over */
  reset: () => void;
  resetNonce: number;
  /** undo / redo of the project's cameras, plan settings and levels */
  undo: () => void;
  redo: () => void;
  canUndo: boolean;
  canRedo: boolean;
}

/** The active page may take Ctrl+Z / Ctrl+Y first (the terrain page: fence edits); a handler
 *  returns false when it had nothing to undo and the project history gets the key. */
export const undoScope: { current: null | { undo: () => boolean; redo: () => boolean } } = { current: null };

const Ctx = createContext<ProjectCtx | null>(null);

export const DEFAULT_PLAN: PlanSettings = {
  plan_path: "", plan_width_m: 45, target: { x_m: 20, y_m: 14, active: false }, level_key: "Characterize",
};

/** Where a new project's first camera stands: left side, halfway up the default
 *  45 m plan (height 0.62 x width), looking east into it -- (0, 0) is the plan's
 *  corner, so the camera started on the edge with half its view clipped. */
const FIRST_AT = { x: 8, y: 14 };

export const newSite = (n: number, at?: { x: number; y: number }): Site => ({
  camera: { ...DEFAULT_CAMERA, name: `Kamera ${n}`, pos_x_m: (at ?? FIRST_AT).x, pos_y_m: (at ?? FIRST_AT).y },
  focal: DEFAULT_CAMERA.focal_min_mm,
});

const clamp = (f: number, c: CameraConfig) => Math.min(Math.max(f, c.focal_min_mm), c.focal_max_mm);

/** First "Kamera N" name not taken (names key the reports and the overlap analysis). */
const freeName = (sites: Site[]) => {
  const taken = new Set(sites.map((s) => s.camera.name));
  let n = sites.length + 1;
  while (taken.has(`Kamera ${n}`)) n++;
  return n;
};

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [sites, setSites] = useState<Site[]>([newSite(1)]);
  const [selected, setSelected] = useState(0);
  const [plan, setPlanState] = useState<PlanSettings>(DEFAULT_PLAN);
  const [path, setPath] = useState("");
  const [name, setName] = useState("Yeni proje");
  const [dirty, setDirty] = useState(false);
  const [levels, setLevelsState] = useState<PPMLevel[] | null>(null);
  const [terrainBlock, setTerrainBlockState] = useState<TerrainBlock | null>(null);
  const [loadedTerrain, setLoadedTerrain] = useState<{ block: TerrainBlock; nonce: number } | null>(null);
  const sel = Math.min(selected, sites.length - 1);
  const [resetNonce, setResetNonce] = useState(0);

  // History: snapshots of what the project file holds. Changes closer than 500 ms are one
  // step, so a drag on the plan or a slider sweep undoes as a whole.
  type Snap = { sites: Site[]; plan: PlanSettings; levels: PPMLevel[] | null; selected: number };
  const snap = useRef<Snap>({ sites, plan, levels, selected: sel });
  snap.current = { sites, plan, levels, selected: sel };
  const hist = useRef<{ past: Snap[]; future: Snap[]; last: number }>({ past: [], future: [], last: 0 });
  const [, setHistVer] = useState(0);
  const record = useCallback(() => {
    const h = hist.current, now = Date.now();
    if (now - h.last > 500) {
      h.past.push(snap.current);
      if (h.past.length > 100) h.past.shift();
      h.future = [];
      setHistVer((v) => v + 1);
    }
    h.last = now;
  }, []);
  const restore = useCallback((s: Snap) => {
    setSites(s.sites); setPlanState(s.plan); setLevelsState(s.levels); setSelected(s.selected);
    hist.current.last = 0; setDirty(true); setHistVer((v) => v + 1);
  }, []);
  const clearHistory = () => { hist.current = { past: [], future: [], last: 0 }; setHistVer((v) => v + 1); };

  const touch = useCallback((fn: (s: Site[]) => Site[]) => { record(); setSites(fn); setDirty(true); }, [record]);
  // Stable, and a no-op for an equal block: the terrain page publishes from an
  // effect that depends on it, so a fresh identity / fresh state per call looped.
  const setTerrainBlock = useCallback((b: TerrainBlock | null, opts?: { touch?: boolean }) => {
    setTerrainBlockState((prev) => {
      if (JSON.stringify(prev) === JSON.stringify(b)) return prev;
      if (opts?.touch) setDirty(true);
      return b;
    });
  }, []);

  const value = useMemo<ProjectCtx>(() => ({
    sites, selected: sel, site: sites[sel], plan, path, name, dirty, terrainBlock, loadedTerrain, levels,
    setLevels: (l) => { record(); setLevelsState(l); setDirty(true); },
    setTerrainBlock,
    select: setSelected,
    add: () => {
      const n = freeName(sites);
      const last = sites[sites.length - 1]?.camera;
      touch((a) => [...a, newSite(n, last ? { x: last.pos_x_m + 5, y: last.pos_y_m } : undefined)]);
      setSelected(sites.length);
    },
    remove: (i) => { touch((a) => (a.length > 1 ? a.filter((_, j) => j !== i) : a)); setSelected(0); },
    update: (patch, focal) => touch((a) => a.map((s, i) => {
      if (i !== sel) return s;
      const camera = { ...s.camera, ...patch };
      return { camera, focal: clamp(focal ?? s.focal, camera) };
    })),
    updateAt: (idx, patch) => touch((a) => a.map((s, i) => (i === idx ? { ...s, camera: { ...s.camera, ...patch } } : s))),
    replaceCamera: (camera) => touch((a) => a.map((s, i) => (i === sel ? { camera, focal: camera.focal_min_mm } : s))),
    setPlan: (patch) => { record(); setPlanState((p) => ({ ...p, ...patch })); setDirty(true); },
    load: (next, p, n, pl, terrain, lv) => {
      clearHistory();
      setLevelsState(lv ?? null);
      setSites(next.length ? next : [newSite(1)]); setSelected(0); setPath(p); setName(n);
      setPlanState({ ...DEFAULT_PLAN, ...pl }); setDirty(false);
      if (terrain) setLoadedTerrain((t) => ({ block: terrain, nonce: (t?.nonce ?? 0) + 1 }));
    },
    markSaved: (p, n) => { setPath(p); setName(n); setDirty(false); },
    reset: () => {
      clearHistory();
      setSites([newSite(1)]); setSelected(0); setPlanState(DEFAULT_PLAN); setLevelsState(null);
      setPath(""); setName("Yeni proje"); setTerrainBlockState(null); setLoadedTerrain(null); setDirty(false);
      setResetNonce((n) => n + 1);
    },
    resetNonce,
    undo: () => { const h = hist.current, s = h.past.pop(); if (!s) return; h.future.push(snap.current); restore(s); },
    redo: () => { const h = hist.current, s = h.future.pop(); if (!s) return; h.past.push(snap.current); restore(s); },
    canUndo: hist.current.past.length > 0, canRedo: hist.current.future.length > 0,
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [sites, sel, plan, path, name, dirty, touch, setTerrainBlock, terrainBlock, loadedTerrain, levels, resetNonce, record, restore,
    hist.current.past.length, hist.current.future.length]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useProject(): ProjectCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useProject outside ProjectProvider");
  return v;
}
