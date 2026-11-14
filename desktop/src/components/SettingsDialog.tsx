import { useEffect, useState } from "react";
import { X } from "lucide-react";
import type { SystemInfo } from "../core/api";
import { useCore } from "../core/CoreProvider";
import { t, useLang } from "../core/i18n";
import { setPref, usePrefs } from "../core/prefs";
import { Button } from "./ui";

// language names are shown in their own language, never translated
const LANGS = [["tr", "Türkçe"], ["en", "English"]] as const; // i18n-ignore

// built at render time (not at module load) so the text follows the language
const shortcuts = (): [string, string][] => [
  ["Ctrl+N", t("Yeni proje")], ["Ctrl+O", t("Proje aç")], ["Ctrl+S", t("Kaydet")], ["Ctrl+Shift+S", t("Farklı kaydet")],
  ["Ctrl+Z", t("Geri al (Arazi'de çit, diğer sayfalarda proje)")], ["Ctrl+Y", t("Yinele")],
  ["Ctrl+1 … Ctrl+7", t("Modüller (yan çubuk sırası)")],
  [t("Kaydırıcı değeri"), t("Tıkla, yaz, Enter")], [t("Haritada çit köşesi"), t("Sürükle: taşı · çift tık: sil")],
];

/** Preferences, shortcuts and about, in one place. */
export default function SettingsDialog({ onClose }: { onClose: () => void }) {
  const prefs = usePrefs();
  const { lang, setLang } = useLang();
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
      <div role="dialog" aria-modal="true" aria-label={t("Ayarlar")} className="max-h-[86vh] w-[520px] max-w-[92vw] overflow-auto rounded-md border border-bg-6 bg-bg-2 p-4 shadow-xl">
        <div className="flex items-center justify-between">
          <h2 className="text-[14px] font-semibold">{t("Ayarlar")}</h2>
          <button type="button" aria-label={t("Kapat")} onClick={onClose} className="rounded p-1 text-text-2 hover:bg-bg-5"><X size={15} /></button>
        </div>

        <div className="label mt-4">Dil / Language</div>
        <div className="mt-1 grid grid-cols-2 gap-1">
          {LANGS.map(([k, l]) => (
            <button key={k} type="button" lang={k} onClick={() => setLang(k)} aria-pressed={lang === k}
              className={`h-8 rounded text-[12px] font-semibold ${lang === k ? "bg-secondary text-on-secondary" : "bg-bg-5 text-text-2"}`}>{l}</button>
          ))}
        </div>
        {lang === "en" && (
          <p className="mt-1 text-[11px] text-text-3">{t("Analiz notları, uyarılar ve üretilen raporlar şimdilik Türkçe.")}</p>
        )}

        <div className="label mt-4">{t("Görünüm")}</div>
        <div className="mt-1 grid grid-cols-2 gap-1">
          {([["dark", t("Konsol (koyu)")], ["light", t("Ofis (açık)")]] as const).map(([k, l]) => (
            <button key={k} type="button" onClick={() => setPref("theme", k)} aria-pressed={prefs.theme === k}
              className={`h-8 rounded text-[12px] font-semibold ${prefs.theme === k ? "bg-secondary text-on-secondary" : "bg-bg-5 text-text-2"}`}>{l}</button>
          ))}
        </div>
        <p className="mt-1 text-[11px] text-text-3">{t("Harita, kapsama katmanları ve 3B görüntü çekirdekten gelir; tema onları değiştirmez.")}</p>

        <div className="label mt-4">{t("Rapor klasörü")}</div>
        <div className="mt-1 flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-text-2">{prefs.reportDir || t("seçilmedi (Raporlar sayfasında seçilir ve hatırlanır)")}</span>
          {prefs.reportDir && <Button onClick={() => setPref("reportDir", "")}>{t("Unut")}</Button>}
        </div>

        <div className="label mt-4">{t("Kısayollar")}</div>
        <div className="mt-1 grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-[12px]">
          {shortcuts().map(([k, l]) => (<div key={k} className="contents"><span className="font-mono text-text-2">{k}</span><span>{l}</span></div>))}
        </div>

        <div className="label mt-4">{t("Hakkında")}</div>
        <div className="mt-1 font-mono text-[11px] text-text-2">
          {t("CCTV Simülatör Pro")} · EN 62676-4:2015 DORI · Johnson ({t("termal")})
          <br />
          {info ? t("API v{v} · Python {py} · {platform} · {kind}", { v: info.api_version, py: info.python, platform: info.platform, kind: info.frozen ? t("paketli çekirdek") : t("geliştirme çekirdeği") }) : t("çekirdek bilgisi alınamadı")}
        </div>
        <p className="mt-2 text-[11px] text-text-3">{t("Sayılar uygulamanın optik ve görüş alanı motorundan gelir; arayüz fizik türetmez. Ölçülmemiş arazide sonuçlar bağlayıcı değildir.")}</p>
      </div>
    </div>
  );
}
