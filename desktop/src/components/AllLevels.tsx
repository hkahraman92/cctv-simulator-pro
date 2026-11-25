import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { CameraConfig, OpticResult, PPMLevel } from "../core/api";
import { useCoalesced, useCore } from "../core/CoreProvider";
import { t } from "../core/i18n";
import { fmt } from "./ui";
import { nameStatesPpm } from "../core/levelLabel";
import { GEOM, buildLevelRows, isThermalCamera } from "../pages/allLevels";

const KEY = "cctv.ui.allLevelsOpen";
const readOpen = () => { try { return localStorage.getItem(KEY) === "1"; } catch { return false; } };
const writeOpen = (v: boolean) => { try { localStorage.setItem(KEY, v ? "1" : "0"); } catch { /* per-viewer convenience only */ } };

/** The px/m column already states the number; drop a trailing "(250 px/m)" from the shown name (the tooltip keeps it). */
const shortName = (name: string, ppm: number) => (nameStatesPpm(name, ppm) ? name.replace(/\s*\([^()]*\)\s*$/, "") || name : name);

const dist = (v: number | null | undefined) => (v == null ? "—" : fmt(v));

/** Every level of the project list at the selected focal: one collapsed row, a compact table when opened. */
export default function AllLevels({ camera, levels, r, active }: {
  camera: CameraConfig; levels: PPMLevel[]; r: OpticResult | undefined; active: boolean;
}) {
  const { api, state } = useCore();
  const [open, setOpen] = useState(readOpen);
  const toggle = () => setOpen((o) => { writeOpen(!o); return !o; });
  const thermal = isThermalCamera(camera);
  const varifocal = camera.focal_max_mm > camera.focal_min_mm;

  // wide / tele ends: only fetched while the list is open (the focal slider does not change them)
  const ends = useMemo(() => ({ camera, levels }), [camera, levels]);
  const endsRes = useCoalesced(ends, async (i) => {
    const lv = i.levels.length ? { levels: i.levels } : {};
    const [wide, tele] = await Promise.all([
      api.optics(i.camera, { focal_mm: i.camera.focal_min_mm, ...lv }),
      api.optics(i.camera, { focal_mm: i.camera.focal_max_mm, ...lv }),
    ]);
    return { wide, tele };
  }, state === "ready" && active && open && varifocal);

  const table = useMemo(
    () => buildLevelRows(levels, r, thermal, varifocal ? endsRes.value?.wide : undefined, varifocal ? endsRes.value?.tele : undefined),
    [levels, r, thermal, varifocal, endsRes.value],
  );
  // wide panel: one line per level in a 5-column grid; narrow panel (container query): name on its own line, values under it
  const cols = varifocal
    ? "grid-cols-[10px_minmax(0,1fr)] @min-[380px]:grid-cols-[10px_minmax(0,1fr)_44px_56px_92px]"
    : "grid-cols-[10px_minmax(0,1fr)] @min-[380px]:grid-cols-[10px_minmax(0,1fr)_44px_56px]";

  return (
    <div className="mt-2">
      <button type="button" onClick={toggle} aria-expanded={open}
        className="flex w-full items-center gap-1.5 rounded px-1 py-1 text-left font-mono text-[11px] text-text-2 hover:text-text">
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {t("Tüm seviyeler ({n})", { n: table.rows.length })}
      </button>
      {open && (
        <div className="@container mt-1 rounded bg-bg-1 p-2">
          <div className={`hidden ${cols.replace("grid-cols-[10px_minmax(0,1fr)] ", "")} items-center gap-x-2 pb-1 font-mono text-[10px] uppercase text-text-3 @min-[380px]:grid`}>
            <span /><span>{t("Seviye")}</span><span className="text-right">px/m</span>
            <span className="text-right">{t("Yer (m)")}</span>
            {varifocal && <span className="text-right" title={t("Geniş uç – dar uç, yer mesafesi (m)")}>{t("geniş–dar")}</span>}
          </div>
          <div>
            {table.rows.map((l) => {
              const reason = l.ground == null ? (l.status === GEOM ? t("▲ geometrik sınır") : t("Kör noktada")) : "";
              const tip = `${l.name}
${l.optical != null ? `${t("Optik mesafe")}: ${fmt(l.optical)} m` : ""}${reason ? `
${reason}` : ""}`;
              return (
                <div key={l.key} title={tip} className={`grid ${cols} items-start gap-x-2 border-t border-line/40 py-1 font-mono text-[11px]`}>
                  <span className="mt-1 h-2.5 w-2.5 rounded-sm" style={{ background: l.color }} />
                  <span className="min-w-0 [overflow-wrap:anywhere] text-text-2">{shortName(l.name, l.ppm)}</span>
                  <div className="col-start-2 grid grid-cols-2 gap-x-3 @min-[380px]:contents">
                    <span className="text-text-3 @min-[380px]:text-right">{fmt(l.ppm, l.ppm % 1 ? 1 : 0)}<span className="@min-[380px]:hidden"> px/m</span></span>
                    <span className="text-right font-bold">
                      {dist(l.ground)}{l.geomLimit && <span className="text-warn"> ▲</span>}<span className="font-normal text-text-3 @min-[380px]:hidden"> m</span>
                    </span>
                    {varifocal && (
                      <span className="col-span-2 text-text-3 @min-[380px]:col-span-1 @min-[380px]:text-right">
                        <span className="@min-[380px]:hidden">{t("geniş–dar")} </span>
                        {l.wide === undefined ? "…" : `${dist(l.wide)}${l.wideGeom ? "▲" : ""} → ${dist(l.tele)}${l.teleGeom ? "▲" : ""}`}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          {table.hidden > 0 && (
            <p className="mt-1.5 text-[11px] text-text-3">
              {thermal
                ? t("Görünür bant seviyeleri bu kamera için gizli: {n}", { n: table.hidden })
                : t("Termal (Johnson) seviyeleri bu kamera için gizli: {n}", { n: table.hidden })}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
