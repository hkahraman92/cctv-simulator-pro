import { useEffect, useState } from "react";
import { Footprints, Play } from "lucide-react";
import type { ApproachResult, TerrainApi } from "../core/terrainApi";
import type { useJob } from "../core/useJob";
import type { Layer } from "./PtzPanels";
import { numberLocale, t } from "../core/i18n";
import { Button, Panel, Slider, fmt } from "./ui";

type Job = ReturnType<typeof useJob>;

/** Intruder speeds (m/s). The UI owns them so the sector colours and the table use the same numbers. */
// the keys go to the core and index its result: values, not UI text (shown through speedLabel)
export const SPEEDS: Record<string, number> = { "Yürüyerek": 1.4, "Koşarak": 4.0, "Araçla (40 km/sa)": 11.1 }; // i18n-ignore
const speedLabel = (k: string) => t(k);

export type Tone = "danger" | "warn" | "ok";
export interface ApproachMarks {
  sectors: { x0: number; y0: number; x1: number; y1: number; tone: Tone }[];
  dash: [number, number][];
  breakout: [number, number] | null;
}

const secs = (s: number | null) => (s === null ? "—" : s >= 600 ? `${fmt(s / 60, 0)} ${t("dk")}` : `${fmt(s, 0)} ${t("sn")}`);

/** Hidden approach: how close an intruder gets unseen, and how many seconds the response force gets. */
export function ApproachPanel({ T, job, terrainId, useObstacles, onLayer, onMarks, resetKey }: {
  T: TerrainApi; job: Job; terrainId: string; useObstacles: boolean; resetKey: number;
  onLayer: (l: Layer | null) => void; onMarks: (m: ApproachMarks | null) => void;
}) {
  const [depth, setDepth] = useState(400);
  const [response, setResponse] = useState(60);
  const [threat, setThreat] = useState("Koşarak"); // i18n-ignore
  const [res, setRes] = useState<ApproachResult | null>(null);
  useEffect(() => { setRes(null); onMarks(null); }, [terrainId, resetKey, onMarks]);

  // sector colours follow the chosen threat speed against the response time
  useEffect(() => {
    if (!res) return;
    const v = SPEEDS[threat];
    const tone = (w: number | null): Tone | null => {
      if (w === null) return null;
      const s = w / v;
      return s < response ? "danger" : s < 2 * response ? "warn" : "ok";
    };
    onMarks({
      sectors: res.sectors.flatMap((s) => { const t = tone(s.warning_m); return t ? [{ ...s, tone: t }] : []; }),
      dash: res.dash_path, breakout: res.breakout_xy,
    });
  }, [res, threat, response, onMarks]);

  const run = () => void job.run(t("Gizli yaklaşma analizi"), (o) => T.approach({
    terrain_id: terrainId, use_obstacles: useObstacles, depth_m: depth, response_s: response, speeds: SPEEDS, max_px: 1200 }, o))
    .then((r) => {
      if (!r) return;
      setRes(r);
      onLayer({ overlay: r.overlay.data, legend: r.legend, title: "Gizli yaklaşma" }); // i18n-ignore: Terrain.tsx matches the layer by this exact title
    });

  const weak = res ? res.sectors.filter((s) => s.warning_m !== null && s.warning_m / SPEEDS[threat] < response).length : 0;
  const reached = res ? res.sectors.filter((s) => s.warning_m !== null).length : 0;
  return (
    <Panel title={t("Gizli yaklaşma")} icon={<Footprints size={16} className="text-secondary" />}>
      <p className="mb-2 text-[11px] text-text-3">{t("Sızan biri kameraların görmediği yerlerden (dere yatağı, sırt arkası, bina gölgesi) ne kadar yaklaşabilir; görüldüğü anda müdahale ekibine kaç saniye kalır?")}</p>
      <div className="flex flex-col gap-3">
        <Slider label={t("Yaklaşma derinliği")} unit={t("m")} value={depth} min={100} max={1500} step={50} onChange={setDepth} />
        <Slider label={t("Müdahale süresi")} unit={t("sn")} value={response} min={10} max={600} step={5} onChange={setResponse} />
        <label><span className="label">{t("Tehdit hızı (kesim renkleri)")}</span>
          <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" value={threat} onChange={(e) => setThreat(e.target.value)} aria-label={t("Tehdit hızı")}>
            {Object.entries(SPEEDS).map(([k, v]) => <option key={k} value={k}>{speedLabel(k)} · {fmt(v, 1)} {t("m/sn")}</option>)}
          </select></label>
      </div>
      <Button className="mt-3 w-full" disabled={job.running} onClick={run}><Play size={14} /> {t("Yaklaşmayı Analiz Et")}</Button>
      {res && (
        <div className="mt-2 text-[12px]">
          {res.breach ? (
            <div className="text-danger">{t("■ Hedefe görünmeden ulaşılabiliyor ({path}). Uyarı süresi: 0 sn.", { path: res.breach_points.length ? t("haritada kırmızı yol") : "" })}</div>
          ) : res.warning_m === null ? (
            <div className="text-text-2">{t("◆ Yaklaşma tarafından hedefe yürünebilen yol yok.")}</div>
          ) : (
            <div className={res.warning_s[threat] !== null && (res.warning_s[threat] as number) < response ? "text-warn" : "text-primary"}>
              {res.warning_s[threat] !== null && (res.warning_s[threat] as number) < response ? "▲" : "●"} {t("İlk görülme hedefe")} <b>{fmt(res.warning_m, 0)} m</b> {t("kala")}
            </div>
          )}
          <table className="mt-2 w-full font-mono text-[11px]">
            <thead><tr className="text-text-3"><th className="text-left font-normal">{t("Hız")}</th><th className="text-right font-normal">{t("Uyarı")}</th><th className="text-right font-normal">{t("{n} sn'ye", { n: fmt(response, 0) })}</th></tr></thead>
            <tbody>
              {Object.keys(SPEEDS).map((k) => (
                <tr key={k}>
                  <td className="font-sans">{speedLabel(k)}</td>
                  <td className="text-right">{secs(res.warning_s[k] ?? null)}</td>
                  <td className={`text-right ${res.sufficient[k] ? "text-primary" : "text-danger"}`}>{res.sufficient[k] ? t("yeterli") : t("yetersiz")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {reached > 0 && (
            <div className="mt-2 text-[11px] text-text-2">
              {weak ? <span className="text-warn">{t("▲ {weak}/{reached} kesimde {speed} uyarı süresi müdahaleden kısa", { weak, reached, speed: speedLabel(threat).toLocaleLowerCase(numberLocale()) })}</span>
                : <span className="text-primary">{t("● Her kesimde uyarı süresi müdahaleye yetiyor")}</span>} {t("(haritada kesim renkleri, 50 m).")}
            </div>
          )}
          <div className="mt-1 text-[11px] text-text-3">{t("Görünmeden ilerlenebilen alan: {a} ha (±{d} m)", { a: fmt(res.covert_area_m2 / 1e4, 1), d: fmt(res.depth_m, 0) })}</div>
          {res.notes.map((n) => <div key={n} className="mt-1 text-[11px] text-text-3">◆ {n}</div>)}
          <div className="mt-1 text-[11px] text-text-3">{t("Yalnız sabit kameralar, geometrik algılama (≥25 px/m); kamuflaj ve algılama olasılığı modellenmez.")}{res.measured ? "" : " " + t("Prosedürel arazi — ölçüm değil.")}</div>
        </div>
      )}
    </Panel>
  );
}
