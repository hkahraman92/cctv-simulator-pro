import { useState } from "react";
import { Gauge, Plus, RotateCcw, X } from "lucide-react";
import type { PPMLevel } from "../core/api";
import { Button, Panel, fmt } from "./ui";

/**
 * The project's PPM levels (classic app's level table). Engine defaults stay;
 * custom levels (e.g. a customer's "face 400 px/m") are added on top and saved
 * in the project file as ppm_levels.
 */
export default function LevelEditor({ levels, defaults, custom, onChange, onReset }: {
  levels: PPMLevel[];
  defaults: PPMLevel[];
  custom: boolean;
  onChange: (l: PPMLevel[]) => void;
  onReset: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [ppm, setPpm] = useState("");
  const [type, setType] = useState("Özel");
  const builtin = new Set(defaults.map((d) => d.key));
  const sorted = [...levels].sort((a, b) => b.ppm - a.ppm);
  const value = Number(ppm.replace(",", "."));
  const valid = name.trim() && Number.isFinite(value) && value > 0;

  const add = () => {
    const key = `Ozel_${name.trim().replace(/[^\p{L}\p{N}]+/gu, "_")}_${Math.round(value)}`;
    if (levels.some((l) => l.key === key)) return;
    onChange([...levels, { key, name: name.trim(), ppm: value, color: "#acedff", level_type: type || "Özel" }]);
    setName(""); setPpm("");
  };

  return (
    <Panel title="PPM seviyeleri" icon={<Gauge size={16} className="text-secondary" />} className="w-[260px] shrink-0 self-start"
      right={<button type="button" className="text-[11px] text-text-2 underline" onClick={() => setOpen((o) => !o)}>{open ? "kapat" : `${levels.length} seviye`}</button>}>
      {!open ? (
        <p className="text-[11px] text-text-3">{custom ? "● Projeye özel seviyeler var." : "Motorun varsayılan seviyeleri (EN 62676-4 DORI, Johnson, plaka…)."} Mesafe tablosu, kontrol noktası ve raporlar bu listeyi kullanır.</p>
      ) : (
        <>
          <ul className="flex max-h-[300px] flex-col gap-0.5 overflow-auto font-mono text-[11px]">
            {sorted.map((l) => (
              <li key={l.key} className="flex items-center gap-1.5 rounded px-1 py-0.5 hover:bg-bg-5">
                <span className="w-12 shrink-0 text-right">{fmt(l.ppm, l.ppm % 1 ? 1 : 0)}</span>
                <span className="min-w-0 flex-1 truncate font-sans" title={l.name}>{l.name}</span>
                {!builtin.has(l.key) && (
                  <button type="button" aria-label={`${l.name} sil`} className="text-text-3 hover:text-danger"
                    onClick={() => onChange(levels.filter((x) => x.key !== l.key))}><X size={12} /></button>
                )}
              </li>
            ))}
          </ul>
          <div className="mt-2 grid grid-cols-[1fr_64px] gap-1.5">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Ad (ör. Firma: Yüz)" aria-label="Seviye adı"
              className="h-8 rounded border border-line bg-bg-0 px-2 text-[12px]" />
            <input value={ppm} onChange={(e) => setPpm(e.target.value)} placeholder="px/m" inputMode="decimal" aria-label="px/m"
              className="h-8 rounded border border-line bg-bg-0 px-2 font-mono text-[12px]" />
          </div>
          <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Seviye türü" className="mt-1.5 h-8 w-full rounded border border-line bg-bg-0 px-1 text-[12px]">
            {["Özel", "Standart", "Algoritma", "Termal"].map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <div className="mt-1.5 flex gap-1.5">
            <Button className="flex-1" disabled={!valid} onClick={add}><Plus size={14} /> Ekle</Button>
            {custom && <Button title="Varsayılan seviyelere dön" onClick={onReset}><RotateCcw size={14} /></Button>}
          </div>
        </>
      )}
    </Panel>
  );
}
