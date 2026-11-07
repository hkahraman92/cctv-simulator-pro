import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { DEFAULT_CAMERA, type CameraConfig } from "./api";

/** One camera on the plan plus the focal it is being looked at with. */
export interface Site {
  camera: CameraConfig;
  focal: number;
}

interface ProjectCtx {
  sites: Site[];
  selected: number;
  site: Site;
  path: string;            // "" = never saved
  name: string;
  dirty: boolean;
  select: (i: number) => void;
  add: () => void;
  remove: (i: number) => void;
  /** patch the selected camera; focal is clamped into the (new) lens range */
  update: (patch: Partial<CameraConfig>, focal?: number) => void;
  replaceCamera: (camera: CameraConfig) => void;
  load: (sites: Site[], path: string, name: string) => void;
  markSaved: (path: string, name: string) => void;
}

const Ctx = createContext<ProjectCtx | null>(null);

export const newSite = (n: number): Site => ({
  camera: { ...DEFAULT_CAMERA, name: `Kamera ${n}` },
  focal: DEFAULT_CAMERA.focal_min_mm,
});

const clamp = (f: number, c: CameraConfig) => Math.min(Math.max(f, c.focal_min_mm), c.focal_max_mm);

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [sites, setSites] = useState<Site[]>([newSite(1)]);
  const [selected, setSelected] = useState(0);
  const [path, setPath] = useState("");
  const [name, setName] = useState("Yeni proje");
  const [dirty, setDirty] = useState(false);
  const sel = Math.min(selected, sites.length - 1);

  const touch = useCallback((fn: (s: Site[]) => Site[]) => { setSites(fn); setDirty(true); }, []);

  const value = useMemo<ProjectCtx>(() => ({
    sites, selected: sel, site: sites[sel], path, name, dirty,
    select: setSelected,
    add: () => { touch((a) => [...a, newSite(a.length + 1)]); setSelected(sites.length); },
    remove: (i) => { touch((a) => (a.length > 1 ? a.filter((_, j) => j !== i) : a)); setSelected(0); },
    update: (patch, focal) => touch((a) => a.map((s, i) => {
      if (i !== sel) return s;
      const camera = { ...s.camera, ...patch };
      return { camera, focal: clamp(focal ?? s.focal, camera) };
    })),
    replaceCamera: (camera) => touch((a) => a.map((s, i) => (i === sel ? { camera, focal: camera.focal_min_mm } : s))),
    load: (next, p, n) => { setSites(next.length ? next : [newSite(1)]); setSelected(0); setPath(p); setName(n); setDirty(false); },
    markSaved: (p, n) => { setPath(p); setName(n); setDirty(false); },
  }), [sites, sel, path, name, dirty, touch]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useProject(): ProjectCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useProject outside ProjectProvider");
  return v;
}
