import { useEffect, useState } from "react";
import { X } from "lucide-react";
import type { SystemInfo } from "../core/api";
import { useCore } from "../core/CoreProvider";
import { setPref, usePrefs } from "../core/prefs";
import { Button } from "./ui";

const SHORTCUTS: [string, string][] = [
  ["Ctrl+N", "Yeni proje"], ["Ctrl+O", "Proje aç"], ["Ctrl+S", "Kaydet"], ["Ctrl+Shift+S", "Farklı kaydet"],
  ["Ctrl+Z", "Geri al (Arazi'de çit, diğer sayfalarda proje)"], ["Ctrl+Y", "Yinele"],
  ["Ctrl+1 … Ctrl+7", "Modüller (yan çubuk sırası)"],
  ["Kaydırıcı değeri", "Tıkla, yaz, Enter"], ["Haritada çit köşesi", "Sürükle: taşı · çift tık: sil"],
];

/** Preferences, shortcuts and about, in one place. */
export default function SettingsDialog({ onClose }: { onClose: () => void }) {
  const prefs = usePrefs();
  const { api, state } = useCore();
  const [info, setInfo] = useState<SystemInfo | null>(null);
  useEffect(() => { if (state === "ready") api.info().then(setInfo).catch(() => undefined); }, [api, state]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/55" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-label="Ayarlar" className="max-h-[86vh] w-[520px] max-w-[92vw] overflow-auto rounded-md border border-bg-6 bg-bg-2 p-4 shadow-xl">
        <div className="flex items-center justify-between">
          <h2 className="text-[14px] font-semibold">Ayarlar</h2>
          <button type="button" aria-label="Kapat" onClick={onClose} className="rounded p-1 text-text-2 hover:bg-bg-5"><X size={15} /></button>
        </div>

        <div className="label mt-4">Görünüm</div>
        <div className="mt-1 grid grid-cols-2 gap-1">
          {([["dark", "Konsol (koyu)"], ["light", "Ofis (açık)"]] as const).map(([k, l]) => (
            <button key={k} type="button" onClick={() => setPref("theme", k)} aria-pressed={prefs.theme === k}
              className={`h-8 rounded text-[12px] font-semibold ${prefs.theme === k ? "bg-secondary text-on-secondary" : "bg-bg-5 text-text-2"}`}>{l}</button>
          ))}
        </div>
        <p className="mt-1 text-[11px] text-text-3">Harita, kapsama katmanları ve 3B görüntü çekirdekten gelir; tema onları değiştirmez.</p>

        <div className="label mt-4">Rapor klasörü</div>
        <div className="mt-1 flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-text-2">{prefs.reportDir || "seçilmedi (Raporlar sayfasında seçilir ve hatırlanır)"}</span>
          {prefs.reportDir && <Button onClick={() => setPref("reportDir", "")}>Unut</Button>}
        </div>

        <div className="label mt-4">Kısayollar</div>
        <div className="mt-1 grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-[12px]">
          {SHORTCUTS.map(([k, l]) => (<div key={k} className="contents"><span className="font-mono text-text-2">{k}</span><span>{l}</span></div>))}
        </div>

        <div className="label mt-4">Hakkında</div>
        <div className="mt-1 font-mono text-[11px] text-text-2">
          CCTV Simülatör Pro · EN 62676-4:2015 DORI · Johnson (termal)
          <br />
          {info ? `API v${info.api_version} · Python ${info.python} · ${info.platform}${info.frozen ? " · paketli çekirdek" : " · geliştirme çekirdeği"}` : "çekirdek bilgisi alınamadı"}
        </div>
        <p className="mt-2 text-[11px] text-text-3">Sayılar uygulamanın optik ve görüş alanı motorundan gelir; arayüz fizik türetmez. Ölçülmemiş arazide sonuçlar bağlayıcı değildir.</p>
      </div>
    </div>
  );
}
