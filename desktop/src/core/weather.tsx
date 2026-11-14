import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

/** Core label of the clear-air preset (terrain.catalog().weather[0]). */
export const CLEAR_AIR = "Berrak hava";

const Ctx = createContext<{ weather: string; setWeather: (w: string) => void } | null>(null);

/** One "Hava" choice for the whole session, shared by every page that computes with atmosphere. */
export function WeatherProvider({ children }: { children: ReactNode }) {
  const [weather, setWeather] = useState(CLEAR_AIR);
  const v = useMemo(() => ({ weather, setWeather }), [weather]);
  return <Ctx.Provider value={v}>{children}</Ctx.Provider>;
}

/** [weather, setWeather] — same shape as useState, so a page swaps one line. */
export function useWeather(): [string, (w: string) => void] {
  const v = useContext(Ctx);
  if (!v) throw new Error("useWeather outside WeatherProvider");
  return [v.weather, v.setWeather];
}
