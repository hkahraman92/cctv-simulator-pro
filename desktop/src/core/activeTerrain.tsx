import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type { CameraConfig } from "./api";

/** The terrain the Arazi page is working on, for pages that report on it. */
export interface ActiveTerrain {
  terrain_id: string;
  label: string;
  is_measured: boolean;
  use_obstacles: boolean;
  weather: string;
  camera: CameraConfig;
  ran: { viewshed: boolean; plan: boolean; coverage: boolean };
}

const Ctx = createContext<{ active: ActiveTerrain | null; setActive: (a: ActiveTerrain | null) => void } | null>(null);

export function ActiveTerrainProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState<ActiveTerrain | null>(null);
  const v = useMemo(() => ({ active, setActive }), [active]);
  return <Ctx.Provider value={v}>{children}</Ctx.Provider>;
}

export function useActiveTerrain() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useActiveTerrain outside ActiveTerrainProvider");
  return v;
}
