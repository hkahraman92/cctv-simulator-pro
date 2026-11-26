import { useEffect, useMemo, useState } from "react";
import { Gauge, X } from "lucide-react";
import { useCore } from "../core/CoreProvider";
import { useProject } from "../core/project";
import { Button, Panel, fmt } from "../components/ui";
import { t } from "../core/i18n";
import type { BitrateResult, Codec, RecordMode, Scene } from "../core/bitrateApi";
import type { ModelSummary } from "../core/api";
import { DEFAULT_BITRATE, buildRequest, type BitrateDefaults, type RowOverrides, type RowSource } from "./bitrateRows";

const CODECS: [Codec, () => string][] = [
  ["h264", () => "H.264"], ["h265", () => "H.265"], ["smart_h265", () => "Smart H.265+"], ["mjpeg", () => "MJPEG"],
];
const SCENES: [Scene, () => string][] = [
  ["low", () => t("Düşük hareket")], ["medium", () => t("Orta hareket")], ["high", () => t("Yüksek hareket")],
];
const MODES: [RecordMode, () => string][] = [
  ["continuous", () => t("Sürekli")], ["motion", () => t("Hareketle")], ["mixed", () => t("Karma")],
];

const SELECT = "h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]";
const COMPACT = "h-7 w-full min-w-[84px] rounded border border-line bg-bg-0 px-1 text-[11px]";

export default function Bitrate() {
  const { api, bitrate, state } = useCore();
  const { sites } = useProject();
  const [d, setD] = useState<BitrateDefaults>(DEFAULT_BITRATE);
  const [added, setAdded] = useState<RowSource[]>([]);
  const [over, setOver] = useState<Record<string, RowOverrides>>({});
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [pick, setPick] = useState("");
  const [result, setResult] = useState<BitrateResult | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (state !== "ready") return;
    api.library().then((r) => setModels(r.models)).catch(() => undefined);
  }, [api, state]);

  const rows: RowSource[] = useMemo(
    () => [...sites.map((s, i) => ({ id: `site-${i}`, label: s.camera.name, camera: s.camera })), ...added],
    [sites, added],
  );
  const request = useMemo(() => buildRequest(rows, over, d), [rows, over, d]);

  // debounced recompute; a stale response is dropped
  useEffect(() => {
    if (state !== "ready" || !request.cameras.length) { setResult(null); return; }
    let live = true;
    const timer = setTimeout(() => {
      bitrate.estimate(request)
        .then((r) => { if (live) { setResult(r); setError(""); } })
        .catch((e) => { if (live) setError(String((e as Error).message ?? e)); });
    }, 250);
    return () => { live = false; clearTimeout(timer); };
  }, [bitrate, state, request]);

  const num = (key: keyof BitrateDefaults, label: string, unit: string, step = 1) => (
    <label className="block">
      <span className="label">{label}</span>
      <div className="mt-1 flex items-center gap-1.5">
        <input type="number" min={0} step={step} value={Number.isFinite(d[key] as number) ? (d[key] as number) : ""}
          onChange={(e) => setD({ ...d, [key]: e.target.value === "" ? NaN : Number(e.target.value) })}
          className="h-8 w-full rounded border border-line bg-bg-0 px-2 font-mono text-[12px]" />
        <span className="font-mono text-[11px] text-text-3">{unit}</span>
      </div>
    </label>
  );
  const pickSelect = <T extends string>(label: string, value: T, list: [T, () => string][], on: (v: T) => void) => (
    <label className="block">
      <span className="label">{label}</span>
      <select className={`mt-1 ${SELECT}`} value={value} onChange={(e) => on(e.target.value as T)}>
        {list.map(([id, name]) => <option key={id} value={id}>{name()}</option>)}
      </select>
    </label>
  );

  const addModel = () => {
    if (!pick) return;
    setAdded((a) => [...a, { id: `lib-${Date.now()}-${a.length}`, label: pick, model: pick }]);
    setPick("");
  };
  const setOverride = (id: string, patch: RowOverrides) => setOver((o) => ({ ...o, [id]: { ...o[id], ...patch } }));
  const dash = "—";

  return (
    <div className="flex min-h-full flex-col gap-3 p-3">
      <Panel title={t("Varsayılanlar")} icon={<Gauge size={18} className="text-primary" />}>
        <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
          {pickSelect(t("Codec"), d.codec, CODECS, (v) => setD({ ...d, codec: v }))}
          {num("fps", t("Kare hızı"), "fps")}
          {pickSelect(t("Sahne"), d.scene, SCENES, (v) => setD({ ...d, scene: v }))}
          {num("gop_s", t("GOP"), "s", 0.5)}
          {pickSelect(t("Kayıt düzeni"), d.mode, MODES, (v) => setD({ ...d, mode: v }))}
          {d.mode !== "continuous" && num("motion_pct", t("Hareket payı"), "%")}
          {num("retention_days", t("Kayıt günü"), t("gün"))}
          {num("raid_pct", t("RAID payı"), "%")}
        </div>
      </Panel>

      <Panel title={t("Kameralar")}
        right={
          <span className="flex items-center gap-1.5">
            <select className="h-8 max-w-[260px] rounded border border-line bg-bg-0 px-2 text-[12px]" value={pick}
              aria-label={t("Kütüphaneden ekle")} onChange={(e) => setPick(e.target.value)}>
              <option value="">{t("+ Kütüphaneden ekle")}</option>
              {models.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
            </select>
            <Button onClick={addModel} disabled={!pick}>{t("Ekle")}</Button>
          </span>
        }>
        {error && <p className="mb-2 text-[12px] text-danger">{error}</p>}
        <div className="overflow-auto">
          <table className="w-full min-w-[980px] border-collapse text-[12px]">
            <thead>
              <tr>
                {[t("Kamera"), t("Codec"), t("fps"), t("Sahne"), t("Gündüz"), t("Gece tepe"), t("Ortalama kayıt"), t("Önerilen CBR"),
                  t("VBR üst"), t("Depolama ({days} gün)", { days: fmt(result?.totals.retention_days ?? d.retention_days, 0) }), ""].map((h, i) => (
                  <th key={i} className="label bg-bg-5 px-2 py-2 text-left whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const res = result?.rows[i];
                const o = over[r.id] ?? {};
                const warn = res ? [...res.warnings, ...(res.fps_clamped ? res.notes.filter((n) => n.startsWith("fps")) : [])] : [];
                return (
                  <tr key={r.id} className="border-b border-line-soft">
                    <td className="max-w-[220px] px-2 py-1.5">
                      <div className="truncate font-semibold" title={r.label}>
                        {warn.length > 0 && <span className="mr-1 text-warning" title={warn.join("\n")} aria-label={warn.join(" ")}>▲</span>}
                        {r.label}
                      </div>
                      <div className="truncate font-mono text-[11px] text-text-3" title={res?.model}>{res?.model ?? r.model ?? ""}</div>
                    </td>
                    <td className="px-2">
                      <select className={COMPACT} value={o.codec ?? ""} aria-label={t("Codec")}
                        onChange={(e) => setOverride(r.id, { codec: (e.target.value || undefined) as Codec | undefined })}>
                        <option value="">{t("varsayılan")}</option>
                        {CODECS.map(([id, name]) => <option key={id} value={id}>{name()}</option>)}
                      </select>
                    </td>
                    <td className="px-2">
                      <input type="number" min={1} className={`${COMPACT} font-mono`} value={o.fps ?? ""} placeholder={res ? fmt(res.fps_used, 0) : t("varsayılan")}
                        aria-label={t("Kare hızı")}
                        onChange={(e) => setOverride(r.id, { fps: e.target.value === "" ? undefined : Number(e.target.value) })} />
                    </td>
                    <td className="px-2">
                      <select className={COMPACT} value={o.scene ?? ""} aria-label={t("Sahne")}
                        onChange={(e) => setOverride(r.id, { scene: (e.target.value || undefined) as Scene | undefined })}>
                        <option value="">{t("varsayılan")}</option>
                        {SCENES.map(([id, name]) => <option key={id} value={id}>{name()}</option>)}
                      </select>
                    </td>
                    <td className="px-2 font-mono whitespace-nowrap">{res ? `${fmt(res.day_mbps)} Mbps` : dash}</td>
                    <td className="px-2 font-mono whitespace-nowrap">{res ? `${fmt(res.peak_mbps)} Mbps` : dash}</td>
                    <td className="px-2 font-mono whitespace-nowrap">{res ? `${fmt(res.avg_record_mbps)} Mbps` : dash}</td>
                    <td className="px-2 font-mono whitespace-nowrap">{res ? `${fmt(res.recommended_cbr_mbps)} Mbps` : dash}</td>
                    <td className="px-2 font-mono whitespace-nowrap">{res ? `${fmt(res.recommended_vbr_max_mbps)} Mbps` : dash}</td>
                    <td className="px-2 font-mono whitespace-nowrap">{res ? `${fmt(res.storage_tb, 2)} TB` : dash}</td>
                    <td className="px-2">
                      {r.id.startsWith("lib-") && (
                        <button type="button" title={t("Satırı kaldır")} aria-label={t("Satırı kaldır")}
                          onClick={() => setAdded((a) => a.filter((x) => x.id !== r.id))} className="rounded p-1 text-text-2 hover:bg-bg-5">
                          <X size={14} />
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {result && result.rows.some((x) => x.notes.length > 0) && (
          <ul className="mt-2 space-y-0.5 text-[11px] text-text-3">
            {result.rows.flatMap((x, i) => x.notes.map((n) => <li key={`${i}-${n}`}>◆ {x.label}: {n}</li>))}
          </ul>
        )}
      </Panel>

      <Panel>
        {result ? (
          <div className="flex flex-wrap gap-x-8 gap-y-2">
            <Total label={t("Ortalama kayıt")} value={`${fmt(result.totals.avg_record_mbps)} Mbps`} />
            <Total label={t("NVR girişi (tepe)")} value={`${fmt(result.totals.peak_mbps)} Mbps`} />
            <Total label={t("Toplam depolama ({days} gün)", { days: fmt(result.totals.retention_days, 0) })} value={`${fmt(result.totals.storage_tb, 2)} TB`} />
          </div>
        ) : <span className="text-[12px] text-text-3">{dash}</span>}
        <p className="mt-2 text-[11px] text-text-3">
          {t("Tüm değerler tahmindir; ölçüm değildir.")} {result?.assumptions.join(" ")}
        </p>
      </Panel>
    </div>
  );
}

function Total({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className="font-mono text-[17px] font-bold">{value}</div>
    </div>
  );
}
