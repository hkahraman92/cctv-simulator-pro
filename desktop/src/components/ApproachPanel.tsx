import { useEffect, useState } from "react";
import { Footprints, Play } from "lucide-react";
import type { ApproachResult, TerrainApi } from "../core/terrainApi";
import type { useJob } from "../core/useJob";
import type { Layer } from "./PtzPanels";
import { Button, Panel, Slider, fmt } from "./ui";

type Job = ReturnType<typeof useJob>;

/** Intruder speeds (m/s). The UI owns them so the sector colours and the table use the same numbers. */
export const SPEEDS: Record<string, number> = { "Yürüyerek": 1.4, "Koşarak": 4.0, "Araçla (40 km/sa)": 11.1 };

export type Tone = "danger" | "warn" | "ok";
export interface ApproachMarks {
  sectors: { x0: number; y0: number; x1: number; y1: number; tone: Tone }[];
  dash: [number, number][];
  breakout: [number, number] | null;
}

const secs = (s: number | null) => (s === null ? "—" : s >= 600 ? `${fmt(s / 60, 0)} dk` : `${fmt(s, 0)} sn`);

/** Hidden approach: how close an intruder gets unseen, and how many seconds the response force gets. */
export function ApproachPanel({ T, job, terrainId, useObstacles, onLayer, onMarks, resetKey }: {
  T: TerrainApi; job: Job; terrainId: string; useObstacles: boolean; resetKey: number;
  onLayer: (l: Layer | null) => void; onMarks: (m: ApproachMarks | null) => void;
}) {
  const [depth, setDepth] = useState(400);
  const [response, setResponse] = useState(60);
  const [threat, setThreat] = useState("Koşarak");
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

  const run = () => void job.run("Gizli yaklaşma analizi", (o) => T.approach({
    terrain_id: terrainId, use_obstacles: useObstacles, depth_m: depth, response_s: response, speeds: SPEEDS, max_px: 1200 }, o))
    .then((r) => {
      if (!r) return;
      setRes(r);
      onLayer({ overlay: r.overlay.data, legend: r.legend, title: "Gizli yaklaşma" });
    });

  const weak = res ? res.sectors.filter((s) => s.warning_m !== null && s.warning_m / SPEEDS[threat] < response).length : 0;
  const reached = res ? res.sectors.filter((s) => s.warning_m !== null).length : 0;
  return (
    <Panel title="Gizli yaklaşma" icon={<Footprints size={16} className="text-secondary" />}>
      <p className="mb-2 text-[11px] text-text-3">Sızan biri kameraların görmediği yerlerden (dere yatağı, sırt arkası, bina gölgesi) ne kadar yaklaşabilir; görüldüğü anda müdahale ekibine kaç saniye kalır?</p>
      <div className="flex flex-col gap-3">
        <Slider label="Yaklaşma derinliği" unit="m" value={depth} min={100} max={1500} step={50} onChange={setDepth} />
        <Slider label="Müdahale süresi" unit="sn" value={response} min={10} max={600} step={5} onChange={setResponse} />
        <label><span className="label">Tehdit hızı (kesim renkleri)</span>
          <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" value={threat} onChange={(e) => setThreat(e.target.value)} aria-label="Tehdit hızı">
            {Object.entries(SPEEDS).map(([k, v]) => <option key={k} value={k}>{k} · {fmt(v, 1)} m/sn</option>)}
          </select></label>
      </div>
      <Button className="mt-3 w-full" disabled={job.running} onClick={run}><Play size={14} /> Yaklaşmayı Analiz Et</Button>
      {res && (
        <div className="mt-2 text-[12px]">
          {res.breach ? (
            <div className="text-danger">■ Hedefe görünmeden ulaşılabiliyor ({res.breach_points.length ? "haritada kırmızı yol" : ""}). Uyarı süresi: 0 sn.</div>
          ) : res.warning_m === null ? (
            <div className="text-text-2">◆ Yaklaşma tarafından hedefe yürünebilen yol yok.</div>
          ) : (
            <div className={res.warning_s[threat] !== null && (res.warning_s[threat] as number) < response ? "text-warn" : "text-primary"}>
              {res.warning_s[threat] !== null && (res.warning_s[threat] as number) < response ? "▲" : "●"} İlk görülme hedefe <b>{fmt(res.warning_m, 0)} m</b> kala
            </div>
          )}
          <table className="mt-2 w-full font-mono text-[11px]">
            <thead><tr className="text-text-3"><th className="text-left font-normal">Hız</th><th className="text-right font-normal">Uyarı</th><th className="text-right font-normal">{fmt(response, 0)} sn'ye</th></tr></thead>
            <tbody>
              {Object.keys(SPEEDS).map((k) => (
                <tr key={k}>
                  <td className="font-sans">{k}</td>
                  <td className="text-right">{secs(res.warning_s[k] ?? null)}</td>
                  <td className={`text-right ${res.sufficient[k] ? "text-primary" : "text-danger"}`}>{res.sufficient[k] ? "yeterli" : "yetersiz"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {reached > 0 && (
            <div className="mt-2 text-[11px] text-text-2">
              {weak ? <span className="text-warn">▲ {weak}/{reached} kesimde {threat.toLocaleLowerCase("tr")} uyarı süresi müdahaleden kısa</span>
                : <span className="text-primary">● Her kesimde uyarı süresi müdahaleye yetiyor</span>} (haritada kesim renkleri, 50 m).
            </div>
          )}
          <div className="mt-1 text-[11px] text-text-3">Görünmeden ilerlenebilen alan: {fmt(res.covert_area_m2 / 1e4, 1)} ha (±{fmt(res.depth_m, 0)} m)</div>
          {res.notes.map((n) => <div key={n} className="mt-1 text-[11px] text-text-3">◆ {n}</div>)}
          <div className="mt-1 text-[11px] text-text-3">Yalnız sabit kameralar, geometrik algılama (≥25 px/m); kamuflaj ve algılama olasılığı modellenmez.{res.measured ? "" : " Prosedürel arazi — ölçüm değil."}</div>
        </div>
      )}
    </Panel>
  );
}
