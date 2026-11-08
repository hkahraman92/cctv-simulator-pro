import { useEffect, useState } from "react";
import { Building, Download, Play } from "lucide-react";
import type { CityAnalysis, CityInfo, DoriLevel, TerrainApi } from "../core/terrainApi";
import type { useJob } from "../core/useJob";
import type { Layer } from "./PtzPanels";
import { Button, Panel, fmt } from "./ui";

type Job = ReturnType<typeof useJob>;
const pct = (f: number) => `%${fmt(f * 100, f < 0.1 ? 1 : 0)}`;
const LEVEL_TEXT: Record<DoriLevel, string> = { detect: "Algılama", observe: "Gözlem", recognize: "Tanıma", identify: "Teşhis" };

/** City surveillance: OSM streets / homes / places, then street coverage, face-capable streets and privacy (KVKK). */
export function CityPanel({ T, job, terrainId, useObstacles, hasLatLon, hasPlan, city, onCity, onLayer, resetKey }: {
  T: TerrainApi; job: Job; terrainId: string; useObstacles: boolean; hasLatLon: boolean; hasPlan: boolean; resetKey: number;
  city: CityInfo | null; onCity: (c: CityInfo | null) => void; onLayer: (l: Layer | null) => void;
}) {
  const [level, setLevel] = useState<DoriLevel>("observe");
  const [res, setRes] = useState<CityAnalysis | null>(null);
  useEffect(() => { setRes(null); }, [terrainId, resetKey]);

  const load = () => void job.run("Sokak, konut ve yerler (OpenStreetMap)", (o) => T.cityOsm(terrainId, o)).then((c) => {
    if (!c) return;
    onCity(c);
    onLayer({ overlay: c.overlay.data, legend: c.legend, title: "Sokaklar" });
  });
  const run = () => void job.run("Şehir analizi", (o) => T.cityAnalyse({ terrain_id: terrainId, use_obstacles: useObstacles, level, max_px: 1200 }, o))
    .then((r) => {
      if (!r) return;
      setRes(r);
      onLayer({ overlay: r.overlay.data, legend: r.legend, title: "Şehir" });
    });
  const flagged = res?.privacy.filter((c) => c.flagged) ?? [];

  return (
    <Panel title="Şehir güvenliği" icon={<Building size={16} className="text-secondary" />}>
      {!hasLatLon ? (
        <p className="text-[11px] text-text-3">Sokak verisi yalnız konumu bilinen arazide (indirilen / kayıtlı bölge) alınabilir.</p>
      ) : !city ? (
        <>
          <p className="mb-2 text-[11px] text-text-3">Sokak ağı, konut binaları ve öncelikli yerler (okul, hastane, durak, meydan) OpenStreetMap'ten alınır.</p>
          <Button className="w-full" disabled={job.running} onClick={load}><Download size={14} /> Sokakları ve Konutları İndir (OSM)</Button>
        </>
      ) : (
        <>
          <div className="text-[11px] text-text-2">{city.streets} yol, {city.residential} konut binası, {city.pois.length} öncelikli yer</div>
          <div className="mt-1 font-mono text-[11px]">
            {Object.entries(city.street_area_m2).filter(([, a]) => a > 0).map(([k, a]) => (
              <div key={k} className="flex justify-between gap-2"><span className="font-sans text-text-3">{k}</span><span>{fmt(a / 1e4, 2)} ha</span></div>
            ))}
          </div>
          <div className="mt-2 flex gap-1.5">
            <Button className="flex-1" onClick={() => onLayer({ overlay: city.overlay.data, legend: city.legend, title: "Sokaklar" })}>Sokakları göster</Button>
            <Button disabled={job.running} onClick={load} title="Yeniden indir"><Download size={14} /></Button>
          </div>
          {hasPlan ? (
            <>
              <label className="mt-3 block"><span className="label">Sokak hedef seviyesi</span>
                <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" value={level} aria-label="Sokak hedef seviyesi"
                  onChange={(e) => setLevel(e.target.value as DoriLevel)}>
                  {(Object.keys(LEVEL_TEXT) as DoriLevel[]).map((k) => <option key={k} value={k}>{LEVEL_TEXT[k]}</option>)}
                </select></label>
              <Button className="mt-2 w-full" disabled={job.running} onClick={run}><Play size={14} /> Planı Şehirde Değerlendir</Button>
            </>
          ) : <p className="mt-2 text-[11px] text-text-3">Değerlendirme için önce bir plan kurun (alan modunda "Yalnız sokaklar" ile direkler sokağa dizilir).</p>}
        </>
      )}
      {res && (
        <div className="mt-3 text-[12px]">
          <div className={res.share_by_level[res.level] >= 0.9 ? "text-primary" : "text-warn"}>
            {res.share_by_level[res.level] >= 0.9 ? "●" : "▲"} Sokaklarda {LEVEL_TEXT[res.level]} payı <b>{pct(res.share_by_level[res.level])}</b> ({fmt(res.street_area_m2 / 1e4, 2)} ha)
          </div>
          <table className="mt-1 w-full font-mono text-[11px]">
            <tbody>
              {res.by_class.map((c) => (
                <tr key={c.label}><td className="font-sans text-text-3">{c.label}</td><td className="text-right">{pct(c.share)}</td></tr>
              ))}
              <tr><td className="font-sans text-text-3">Yüz yakalanan sokak</td><td className="text-right">{pct(res.face_share)}</td></tr>
            </tbody>
          </table>
          {res.pois.length > 0 && (
            <>
              <div className="mt-2 label">Öncelikli yerler</div>
              <div className="max-h-[132px] overflow-y-auto">
                {res.pois.map((p, i) => (
                  <div key={`${p.name}${i}`} className={`flex justify-between gap-2 text-[11px] ${p.best ? "" : "text-danger"}`}>
                    <span className="truncate">{p.name}{p.name !== p.kind ? <span className="text-text-3"> · {p.kind}</span> : null}</span>
                    <span className="shrink-0 font-mono">{p.best ? LEVEL_TEXT[p.best] : "görülmüyor"}{p.face ? " · yüz" : ""}</span>
                  </div>
                ))}
              </div>
            </>
          )}
          <div className="mt-2 label">Gizlilik (KVKK)</div>
          {flagged.length ? (
            <div className="text-[11px] text-warn">
              ▲ {flagged.length} kamerada görüşün konut binasına düşen payı %10 sınırını aşıyor:
              <div className="max-h-[96px] overflow-y-auto font-mono">
                {flagged.map((c) => <div key={c.label}>{c.label} · {pct(c.share)}</div>)}
              </div>
            </div>
          ) : <div className="text-[11px] text-primary">● Hiçbir kamera ağırlıklı olarak konut binasına bakmıyor.</div>}
          {res.notes.map((n) => <div key={n} className="mt-1 text-[11px] text-text-3">◆ {n}</div>)}
          <div className="mt-1 text-[10px] text-text-3">Yüz: Teşhis (≥250 px/m) ve yüze en çok 15° yukarıdan bakış. Konut: OSM bina etiketi (residential, apartments, house…). {city?.attribution}</div>
        </div>
      )}
    </Panel>
  );
}
