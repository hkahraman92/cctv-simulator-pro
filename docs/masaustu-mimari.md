# Masaüstü mimarisi (yeni arayüz)

Klasik Tk arayüzü çalışmaya devam eder. Yeni arayüz ayrı bir kabuktur ve aynı
optik motoru kullanır; hiçbir fizik hesabı arayüze kopyalanmaz (Kural 1).

```
┌────────────────────────── CCTV Simülatör Pro.exe (Tauri 2) ──────────────────────────┐
│  WebView2  ── React + TypeScript + Tailwind (desktop/src)                            │
│     │   invoke("core_send", line)          ▲ olay: core://frame / core://log / exit  │
│     ▼                                      │                                         │
│  Rust kabuk (desktop/src-tauri/src/lib.rs) — süreci başlatır, satır taşır, kapatır   │
└─────┬───────────────────────────────────────────────────────────────────────────────┘
      │ stdin / stdout, satır başına bir JSON-RPC 2.0 nesnesi
┌─────▼───────────────────────────────────────────────────────────────────────────────┐
│  Python çekirdek: python -m cctv_simulator.service  (yayında: cctv-core.exe)         │
│  service/protocol.py  çerçeveleme, iş havuzu, $/cancelRequest, $/progress            │
│  service/methods.py   API v1 — calculations / database / project_io'ya ince köprü    │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

## Sözleşme

- **API v1** (`cctv_simulator/service/__init__.py: API_VERSION`). Yöntem
  eklemek / isteğe bağlı alan eklemek küçük sürüm; yeniden adlandırma ya da
  silme büyük sürüm. `system.describe` güncel listeyi verir.
- Yöntemler: `system.info|ping|describe`, `catalog.get`, `library.list|get|camera`,
  `optics.calculate`, `project.load|save`.
- TypeScript tipleri `desktop/src/core/api.ts` — Python dataclass alanlarıyla
  bire bir. Sonsuz değerler (`max_geom_dist_m` vb.) JSON'da `null`.
- Hatalar JSON-RPC kodlarıyla: −32602 geçersiz parametre (alan adıyla),
  −32000 alan hatası (ör. kütüphanede model yok), −32603 iç hata (traceback
  `data` içinde, stderr'e de yazılır), −32800 iptal.
- stdout yalnız protokol kanalıdır: sunucu çalışırken `sys.stdout` stderr'e
  yönlendirilir; motor içindeki bir `print` çerçeveyi bozamaz.

## Arayüz kuralları

- Sürekli girdiler (kaydırıcı) `useCoalesced` ile: aynı anda tek istek, arada
  gelenlerden yalnız sonuncusu gönderilir (Kural 2'nin karşılığı).
- Renkler `desktop/src/index.css` `@theme` tokenlarından; DORI soğuk rampa,
  sıcak renk yalnız uyarı/alarm.
- Yazı tipleri (Inter, JetBrains Mono) ve simgeler (Lucide) pakete gömülü;
  çalışırken ağdan hiçbir şey indirilmez. CSP yalnız `self` + IPC.
- Henüz taşınmamış modüller "taşınmadı" der; uydurma veri gösterilmez.

## Geliştirme

```
cd desktop
npm install
npm run tauri dev      # Vite + Rust kabuk + py -3.13 -m cctv_simulator.service
npm test               # RPC istemcisi (vitest)
npm run typecheck
py -3.13 -m pytest tests/test_service.py
```

Çekirdek komutu sırası: `CCTV_CORE` ortam değişkeni (tam komut satırı) →
yayın derlemesinde kurulumun `core\cctv-core.exe` kaynağı, geliştirme
derlemesinde kaynak ağacındaki `py -3.13 -m cctv_simulator.service` (yani
`tauri dev` her zaman düzenlenen kodu koşturur). Başlatma hatası Rust'ta
saklanır (`core_status`), durum çubuğu "ÇEKİRDEK YOK: <neden>" yazar.

## Yayın paketi

`desktop\packaging\build_release.bat` → çekirdek (`cctv_core.spec`: Tk'siz,
konsol alt sistemi, onedir → `src-tauri\core\`) → tip denetimi + testler →
`tauri build` → `src-tauri\target\release\bundle\nsis\CCTV Simülatör Pro_<sürüm>_x64-setup.exe`.

- `core/` kurulumda bir kaynak klasördür (`bundle.resources`), onefile değil:
  her açılışta açma yok, kurulum dosya ağacını kendisi yazdığı için zip
  kopyasında dosya kaybı (klasik arıza 5) yok.
- Konsol alt sistemi bilerek: kabuk `CREATE_NO_WINDOW` ile başlatır; pencere
  modlu önyükleyici Python'a kullanılabilir stdin/stdout vermeyebilir.
- `collect_submodules` alt yorumlayıcıda çalışır ve `pathex`'i değil
  `PYTHONPATH`'i görür; spec kökü ikisine de ekler ve 30'dan az modül
  bulursa derlemeyi durdurur (ilk denemede sessizce 0 modül toplamıştı).
- Spec `desktop/packaging` altında — kökte yalnız klasik iki spec kalır.
- Ölçüldü: kurulum 26,9 MB, çekirdek açık hâlde 72 MB; sessiz kurulum
  (`/S /D=<klasör>`) → çalıştır → çekirdek bağlandı, değerler geliştirme
  sürümüyle aynı → pencere kapanınca `cctv-core.exe` de kapandı → kaldırma
  temiz. Norton bu derlemede `cctv-core.exe`'ye dokunmadı.
- Simge: `packaging\make_icon.py` (Lucide "cctv" + DORI yelpazesi) →
  `npx tauri icon packaging/app-icon.png`.

## Sıradaki adımlar

1. Arazi / viewshed API'si: uzun işler `$/progress` + iptal; raster sonuçları
   ikili (PNG/npz) dosya yoluyla, JSON'da değil.
3. Optik laboratuvarı, şartname, raporlar ekranları.
4. İmza: sertifika gelince `signCommand`.
