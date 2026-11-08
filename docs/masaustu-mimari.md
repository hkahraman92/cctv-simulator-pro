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
- Yöntemler (70; modül başına):
  - `methods.py`: `system.info|ping|describe`, `catalog.get`,
    `library.list|get|camera`, `optics.calculate`, `project.load|save`
  - `library_methods.py`: `library.fields|record|auth|save|delete|set_password`
    (her yazım yönetici şifresini denetler)
  - `terrain_methods.py`: `terrain.catalog|procedural|geotiff|download|image`,
    `regions.list|open|save|delete`, `surface.osm|raster`, `viewshed.single`,
    `perimeter.plan|coverage|minimize_poles|optimize`, `area.plan` (alan hedefi:
    poligonda en az kamera, motorla doğrulanır), `focal.sweep`
  - `ptz_methods.py`: `ptz.sweep|cue|tour`, `perimeter.mixed`
  - `bench_methods.py`: `optics.project|bench`, `plan.image`, `iq.measure`, `scene.render`,
    `anpr.analyse|optimize` (düz yolda plaka okuma: şerit başına bölge, kare, bulanıklık)
  - `spec_methods.py`: `spec.read_file|engines|analyze|override|templates|
    template_save|template_run|template_delete|statement`, `training.export`
  - `report_methods.py`: `report.optics|engineering|open`
  - `planning_methods.py`: `network.plan` (switch / kablo / PoE / NVR + maliyet),
    `approach.analyse` (gizli yaklaşma + uyarı süresi),
    `perimeter.compare_models|compare_use`
  - `delivery_methods.py`: `export.site` (KML / KMZ / DXF), `export.share_html`,
    `export.package` (teklif ZIP), `night.condition|timeline|coverage`,
    `scenario.snapshot|compare`
- Plandan türetilmiş sonuçlar (`multi`, `band_share`, `network`, `cost`,
  `night`, `mixed`, `approach`) yeni bir plan kurulunca düşer (`TerrainEntry.set_plan`):
  senaryo, KMZ ya da teklif paketi B planını A'nın kapsaması / maliyetiyle
  eşleştiremez. Arayüz aynı kuralı izler (`replacePlan`).
- Izgaralar çekirdek oturumunda (`session.py`, en fazla 4 arazi) kalır; kabuğa
  özet + base64 resim gider. Resimler kuzey-yukarı, tam `width_m × height_m`.
- Motor modülleri tkinter almaz (`test_every_engine_module_imports_without_tk`):
  `cctv-core.exe` Tk'siz paketlenir, üst düzey bir `import tkinter` paketli
  çekirdeği o yöntemde düşürür.
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
- Uzun işler `useJob` + `JobBar`: ilerleme (`$/progress`) ve İptal.
- Sayfalar ilk ziyarette kurulur, sonra gizlenerek canlı tutulur; gizli sayfa
  canlı önizleme istemez (`usePageActive`).
- Ortak durum: `ProjectProvider` (kameralar + seçim + kayıt durumu),
  `ActiveTerrainProvider` (Arazi'de çalışan analizler → Raporlar).
- Uydurma veri gösterilmez; ölçülmemiş arazi her yerde uyarılır.

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
- Duman testi (`smoke_core.py`) paketli çekirdekte 20 ağır yolu koşar —
  ağ planı, gece kapsaması, teklif paketi ve döşemeli + sıkıştırılmış (kayan
  nokta öngörücülü) bir GeoTIFF dahil. `imagecodecs`'in yalnız GeoTIFF
  codec'leri pakete girer (tam tekerlek ~46 MB; AVIF tek başına 16 MB).
- Ölçüldü (2026-11-08): kurulum 29,7 MB, çekirdek klasörü 78 MB. Önceki
  ölçüm: kurulum 26,9 MB, çekirdek açık hâlde 72 MB; sessiz kurulum
  (`/S /D=<klasör>`) → çalıştır → çekirdek bağlandı, değerler geliştirme
  sürümüyle aynı → pencere kapanınca `cctv-core.exe` de kapandı → kaldırma
  temiz. Norton bu derlemede `cctv-core.exe`'ye dokunmadı.
- Simge: `packaging\make_icon.py` (Lucide "cctv" + DORI yelpazesi) →
  `npx tauri icon packaging/app-icon.png`.

## Sıradaki adımlar

- İmza: sertifika gelince `bundle.windows.signCommand` (+ `cctv-core.exe`'yi de
  imzala; Norton imzasız PyInstaller önyükleyicisini engelleyebilir).
- Otomatik güncelleme (Tauri updater, imzalı sürüm + yayın sunucusu gerektirir).
- İngilizce arayüz (yeni kabuk yalnız Türkçe).
- Yönetici şifresini karma (hash) olarak saklamak — klasikle birlikte;
  şimdi düz metin `admin_settings.json` (her iki arayüz aynı dosyayı okur).

## Proje dosyası

Klasik şemanın aynısı (`project_io`): kameralar, `plan_path`, `plan_width_m`,
`target_point`, `ppm_levels` (özel seviye varsa) ve `terrain` bloğu. Arazi
bloğunun GUI-only anahtarları (`fence_points`, `line_mode`, `mixed_ptz`,
…) `terrain_extra` olarak aynen korunur; klasik harita penceresi ve yeni
kabuk aynı dosyayı açar. Arazi sayfası hiç açılmadan kaydedilen proje
dosyadaki bloğu korur; proje açılınca arazi + çit geri kurulur.

## Kurulum yolu sınırı

Çekirdek klasöründeki en derin dosya `core\` altında ~102 karakter. NSIS,
Windows'un 260 karakter sınırını aşan dosyaları **hatasız atlar**; çok derin bir
klasöre kurulursa numpy DLL'i eksik kalır ve çekirdek açılmaz (ölçüldü: 190
karakterlik kökte 126 dosyanın 101'i kuruldu). Kabuk başlatmadan önce yolu
ölçer ve "Kurulum klasörü çok derin" der; varsayılan kurulum yolu
(`%LOCALAPPDATA%\CCTV Simülatör Pro`) güvenlidir. Çekirdek kapanırsa durum
çubuğu son günlük satırını da gösterir.
