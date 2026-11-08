import { createContext, useContext, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";
import type { CameraConfig } from "./api";
import type { Scenario } from "./terrainApi";

/** The terrain the Arazi page is working on, for pages that report on it. */
export interface ActiveTerrain {
  terrain_id: string;
  label: string;
  is_measured: boolean;
  use_obstacles: boolean;
  weather: string;
  camera: CameraConfig;
  ran: { viewshed: boolean; plan: boolean; coverage: boolean; network: boolean; night: boolean };
  scenarios: Scenario[];
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

// Last compliance analysis of the Şartname page, for the offer package. A
// plain store, not context: the publishing page must not re-render on it.
let specResult: object | null = null;
const listeners = new Set<() => void>();
export function publishSpecResult(r: object | null) {
  if (r === specResult) return;
  specResult = r;
  listeners.forEach((l) => l());
}
export function useSpecResult(): object | null {
  return useSyncExternalStore((l) => { listeners.add(l); return () => { listeners.delete(l); }; }, () => specResult);
}
