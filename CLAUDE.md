# CCTV Dual-View Simulator

Python/Tkinter masaüstü aracı. Kameralar bir vaziyet planına yerleştirilir; uygulama
optiği (FOV, kör nokta, geometrik menzil, mesafeye göre piksel/metre) EN 62676-4 DORI
ve Johnson/NATO termal eşiklerine karşı hesaplar; yan kesit, kuş bakışı plan ve 3B
kamera görüşü çizer; arazi üzerinde görüş alanı (viewshed) analizi ve çok kameralı
çevre çiti planlaması yapar; şartname uygunluğunu denetler; CSV/XLSX/PDF/PNG üretir.

## Ortam

- Windows, PowerShell **5.1** — `&&` çalışmaz, `;` veya ayrı satır kullan.
- Derleme yorumlayıcısı `py -3.13`. Anaconda ile derleme yapma (Tcl/Tk düşüyor).
- Sanal ortam: `.venv-build\`.
- Türkçe arayüz, Türkçe hata mesajları. Kod ve yorumlar İngilizce.

## Harita

```
cctv_dual_view_simulator.py          giriş noktası
  theme.py                           ttkbootstrap pencere fabrikası + Tcl yol kurulumu
  design_tokens.py                   tasarım tokenları: renk (koyu/açık), tuval rolleri, gömülü yazı tipleri
  errors.py                          global hata yakalama, log, guarded_build
  calculations.py                    OPTİK MOTOR - tek doğruluk kaynağı
  perspective_3d.py                  3B izdüşüm
  terrain_loader.py                  TerrainData, prosedürel arazi, GeoTIFF/DEM
  viewshed_3d.py                     vektörel ışın yürütme, dünya eğriliği, atmosfer
  perimeter_planner.py               çevre çiti kamera dizilimi, BOM
  online_map_loader.py               karo indirme, ortofoto mozaik, GERÇEK DEM
  region_library.py                  kayıtlı bölgeler: DEM + ortofoto diskte, çevrimdışı aç
  surface_obstacles.py               OSM bina/ağaç → yüzey modeli (DEM + engel), Overpass; yerel nDSM/DSM
  geotiff_reader.py                  rasterio'suz GeoTIFF: etiketlerden georeferans, UTM/TUREF TM/WebMerc; yerel DEM → ölçülmüş arazi
  geo_export.py                      KML / KMZ (+ kapsama katmanı) ve DXF R12 yerleşim çıktısı
  night.py                           gece senaryosu: güneş + ay → ortam lux, IR / beyaz ışık menzil sınırı
  network_plan.py                    switch / bakır / fiber / PoE / NVR planı + maliyet
  model_selection.py                 kütüphane modellerini aynı çit hedefinde karşılaştırma
  offer.py                           senaryo özeti + karşılaştırma, paylaşım HTML'i, teklif paketi (ZIP)
  ptz_animation.py                   PTZ görsel tarama: anlık bakış + iz + gölge, kesit
  focal_optimizer.py                 varifokal/PTZ: tüm odak taraması, zarf, sabit+PTZ dizilim optimizasyonu
  pole_optimizer.py                  çit hedefini tutan en az direk: aralık araması + direk başına odak
  raster_smooth.py                   sınıf bindirmelerini yalnız görüntüde yumuşatır, FOV kamasına kırpar
  compliance.py                      Gemini/Ollama + kural tabanlı şartname analizi
  compliance_optics.py               şartname DORI/menzil isteri → optik motorla doğrula
  compliance_standards.py            EN 62676-4 madde/DORI bilgi tabanı + belirsizlik tarama
  compliance_report.py               EN 62676-4 uygunluk beyanı (markdown/PDF)
  requirement_library.py             firma ister şablonları (JSON, kullanıcı dizini)
  training_log.py                    şartname analiz + override logu → instruction JSONL
  spec_pdf.py                        PDF şartname → metin (pypdf, opsiyonel)
  exporters.py                       CSV / XLSX / PDF / PNG
  cctv_iq.py                         eğik kenar MTF, k oranı, başsız (numpy)
  atmosphere.py                      sis/yağmur zayıflaması (Koschmieder + bant faktörü)
  solar.py                           güneş konumu (NOAA) + kamera parlama/arkadan ışık
  scene_render.py                    3B kamera karesi (PIL): FOV + çözünürlük kaybı + palet
  project_io.py                      .json proje şeması, Tk'siz load/save
  __main__.py                        başsız CLI: proje -> optik -> export/JSON
  database.py / config.py / models.py
  status_summary.py                  durum paneli: gösterge kutuları + önem sıralı alarmlar (Tk'siz)
  ui/main_window.py                  DualViewCCTVDesignApp - klasik arayüz
    ui/components.py                 tasarım sistemi widget'ları: KpiTile, AlarmList, StatusChip, BarButton, glass_under
    ui/icons.py                      Lucide simge seti (gömülü TTF) → PhotoImage; emoji → simge / ● ▲ ■
    ui/canvas_drawer.py              yan kesit + kuş bakışı plan
    ui/view_3d_window.py             3B kamera görüşü
    ui/modern_window.py              EN 62676-4 optik tezgâhı (customtkinter)
    ui/map_3d_window.py              3B arazi, uydu, viewshed, çevre planlayıcı
    ui/spec_assistant.py             uygunluk matrisi
    ui/camera_db_window.py           kamera veritabanı (yönetici parolası)
cctv_optics_workbench.py             optik tezgâhı için bağımsız başlatıcı
  service/                           çekirdek servis: stdio JSON-RPC 2.0, API v1 (yeni masaüstü kabuğu için)
desktop/                             yeni arayüz: Tauri 2 (Rust) + React/TS/Tailwind — docs/masaustu-mimari.md
```

**Yeni masaüstü kabuğu.** `desktop/` Python çekirdeği (`python -m
cctv_simulator.service`, yayında `cctv-core.exe`) alt süreç olarak başlatır,
satır başına bir JSON-RPC nesnesi taşır (API v1, 66 yöntem; liste ve
sözleşme `docs/masaustu-mimari.md`). Ekran fizik türetmez; her sayı bir API
yönteminden gelir. Yeni özellik için: önce `service/*_methods.py`'ye yöntem +
`tests/test_service_*.py`'ye motorla birebir eşlik testi, sonra
`desktop/src/core/*Api.ts`'ye tip. Klasik Tk arayüzü değişmeden çalışır;
iki arayüzün paylaştığı mantık Tk'siz modüllerde (`map_render`,
`optics_bench`, `focal_optimizer.apply_layout/band_area`,
`compliance.call_gemini/evaluate_template`, `database.save_library_model`).
- **Motor modülleri tkinter almaz** (`test_every_engine_module_imports_without_tk`):
  çekirdek Tk'siz paketlenir; üst düzey `import tkinter` paketli çekirdeği o
  yöntemde düşürür (compliance.py ve exporters.py düşürüyordu).
- stdout yalnız protokol kanalı (sunucu `sys.stdout`'u stderr'e çevirir,
  stderr'i UTF-8'e zorlar).
- Yayın: `desktop\packaging\build_release.bat` → `cctv-core.exe` + duman
  testi (`smoke_core.py`, paketli çekirdekte 14 ağır yol) → NSIS kurulum.
  Kurulum yolu çok derinse (NSIS MAX_PATH'i aşan dosyaları sessizce atlar)
  kabuk çekirdeği başlatmadan açık hata verir.
- `tauri dev` her zaman kaynak ağacındaki Python'u koşturur; geliştirme
  derlemesinde kütüphane / şifre dosyaları depo kökündedir — uygulamada
  kaydetme denemesi yaparken buna dikkat (testler geçici dizine yönlendirir).

## Kurallar

**1. Optik motor tek doğruluk kaynağıdır.**
`calculations.calculate_for_camera()` ve `ppm_at_distance()` fiziği sahiplenir.
Arayüz çizer, fizik türetmez. Kamera optik sabitleri (sensör, çözünürlüğe göre
sensör yüksekliği, `k`'lı etkin çözünürlük, FOV) **yalnız**
`calculations.camera_optics(camera, focal_mm)` → `CameraOptics`'ten okunur
(`ppm_at(mesafe)`, `distance_for_ppm(ppm)`), gereken odak `focal_for_ppm`'den.
Viewshed, kapsama ızgarası, çevre planlayıcı, 3B görünüm, harita ve lens
önerileri bunu kullanır; `SENSOR_DIMS_MM`/`RESOLUTIONS`'tan elle türetme
(her modül kendi geri-düşüş değerini seçiyordu, lens önerisi `k`'yı
atlıyordu). Bilinmeyen sensör/çözünürlük adı: `strict=True` KeyError, aksi
halde `CameraConfig` varsayılanları. Bir görünüm bir PPM eşiği için menzil istiyorsa,
motorun kendi bağıntısını hazır `OpticResult` üzerinden yeniden ifade et
(`modern_window.ground_distance_for_ppm` örnek), ki ikisi ayrışamasın.

**2. Hareket olayından doğrudan `calculate()` çağırma.**
`calculate()` fan-in 24; tüm boru hattını çalıştırır. `<B1-Motion>`'a doğrudan
bağlıyken saniyede 60-120 kez koşuyordu, Tk ise kare başına bir kez boyuyordu —
işin neredeyse tamamı görünmeden çöpe gidiyordu. Ölçüldü: **18.93 ms → 0.69 ms
(27x)**.

Desen (`main_window.py:844-935`, `:1341-1400`):

- `_set_selected_camera_position/heading` ve `_set_target_point` sürüklerken
  `calculate()` değil `schedule_calculate()` çağırır. `_calc_job` doluysa yeni
  istek yutulur; boşsa `root.after(_DRAG_INTERVAL_MS=33, _run_scheduled_calculate)`
  kurulur (~30 fps tavan).
- `<ButtonRelease-1>` ve `<ButtonRelease-3>` → `flush_calculate()`: bekleyen light
  geçiş iptal, bir tam geçiş.

`light=True` **yalnız şunları atlar** (`:914`, `:926`): `_populate_table`,
`_populate_recommendations`, `update_lens_suggestion`, `update_alternative_models`
ve 3B pencere güncellemesi, durum panelinin alarm listesi + sistem sağlığı + kayıt
durumu (alarm listesi widget yeniden kurar). **`analyze_dead_zone_coverage`, ölü bölge
paneli, hedef analizi, durum panelinin gösterge kutuları ve `_draw_canvas` her light
geçişte yine koşar** — ölü bölge analizi 2.66x
hızlandırıldığı için light yolda bırakıldı. "Sadece geometri + tuval" deme; yanıltıcı.

Sürekli olan her şey (sürükleme, kaydırıcı, `<Configure>`) debouncer'dan geçer.
Kesikli olanlar (buton, combobox) doğrudan çağırabilir.
`view_3d_window.schedule_render()` ve `map_3d_window._render_job` aynı deseni kullanır.

**3. `ppm_levels` değişince cache'leri geçersizle.**
`_selected_design_level()` ve `_levels_desc()` sonuç cache'ler.
`_refresh_level_tree()` → `_invalidate_level_cache()`, her `ppm_levels` mutasyonundan
sonra (ekle, sil, proje yükle). Bu değişmezi koru.

**4. Türkçe metin, ASCII eşleme yapma.**
`"yuz" in name.lower()` hiçbir zaman `"Optik: Yüz Tespit"` ile eşleşmedi; uyarı ölü
koddu. Gerçek karakterlerle eşle veya iki tarafı da normalize et.

**5. `ImageTk.PhotoImage(img, master=widget)` — `master=` zorunlu.**
Yoksa PIL görüntüyü `tkinter._default_root`'a bağlar; donmuş derlemede bu, tuvalin
ait olduğu yorumlayıcı olmak zorunda değil → `image "pyimageNN" doesn't exist`.

**6. Optik tezgâh penceresi `tk.Toplevel`, `CTkToplevel` değil.**
`ttkbootstrap.Window` ebeveyniyle Windows'ta `CTkToplevel` grid geometrisini yanlış
hesaplayıp tuvali 1x1 bırakıyor. `_WorkbenchBody` mixin'i hem bağımsız `ctk.CTk`
köküne hem gömülü `tk.Toplevel`'a hizmet eder. Alt pencerelere `lift()` +
`focus_force()` gerekir. `view_3d_window` `after(60, render_3d_view)` ister; tuval
kuruluş anında hâlâ 100 px altındadır.

**7. `__init__.py`'ler tembeldir. Böyle kalsınlar.**
PEP 562 `__getattr__`. Eager import 362 ms / 654 modüldü; şimdi 2.1 ms.
Eksik bir opsiyonel bağımlılık artık tüm uygulamayı değil, sadece onu isteyen
pencereyi durduruyor.

**8. Uzun form/satır sığmazsa kaydır ya da sar — sessizce kaybolmasın.**
`pack` yer kalmayınca son widget'ları 0 genişlik/yüksekliğe indirip **haritadan
düşürür** (hata yok, görünmez olur): 900 px'te ana pencerenin 3 başlık düğmesinin
2'si, 3B görünümün Lens/Palet/Hava/Snapshot kontrolleri böyle kayboluyordu.
`theme.py` yardımcıları:
- `make_scrollable(parent)` → `(outer, inner)`: uzun sekme/form; kaydırma
  çubuğu yalnız gerektiğinde, tekerlek imlecin altındaki widget'a göre
  yönlendirilir (eski `<Enter>/<Leave>` + `bind_all` deseni alan üzerine
  gelince tekerleği düşürüyordu). Notebook'a `outer` eklenir — sekme seçimi
  `outer` ile yapılır (`map_3d_window._page_perimeter`).
- `flow_layout(container, items, justify=, expand=)`: düğme/araç satırı dar
  pencerede alt satıra sarar; `(etiket, kontrol)` grupları birlikte kalır.
- `wrap_label(label)`: uzun/dinamik metin etiketi genişliğe göre sarılır.
Ana pencerede tablo/öneriler/tuval dikey `PanedWindow`'da; tuval yüksekliğin
≥%45'ini korur (`_fit_right_panes`) — `calculate()` tuval <120 px ise hiç
çizmez. Tezgâh (`modern_window`) boyutu **düzen birimi × DPI ölçeği**: CTk
widget'ları %150'de 1,5× çizilir, gömülü `tk.Toplevel` ise ham pikseldir.
Canvas üstü yazılarda sabit piksel ofset kullanma, `Font.measure` ile ölç.

## Arazi ve çevrimiçi harita

**Uydurma rölyefi ölçüm gibi sunma.** Viewshed, arazi engellemesinden ibarettir;
uydurma sırt, güven veren yanlış bir kapsama raporu üretir. `TerrainData.is_measured`
yalnız gerçek DEM'de `True` (GeoTIFF, Terrarium karoları, yükselti API'si).
Prosedürel önayarlar, gri tonlamalı heightmap ve son çare arazi `False` ve arayüz
`showinfo` yerine `showwarning` açar. Bu bayrağı dışa aktarımlara da taşı.

**Yön.** `TerrainData.z_grid` satır 0 = **güney** (artan +Y kuzey). Mozaik satır 0 =
kuzey. Render `np.flipud` yapar. DEM'i `z_grid`'e yazarken `flipud` şart, yoksa arazi
kuzey-güney aynalanır. `terrain_loader._terrain_from_rasterio` da yapar: kuzey-yukarı
GeoTIFF (`transform.e < 0`) `flipud`'lanır, `origin_x/y = 0` (yerel metre çerçevesi,
indirilen DEM'lerle aynı), nodata `nanmean` ile doldurulur. Coğrafi (derece) grid
metreye çevrilirken boylam merkez enlem kosinüsüyle ölçeklenir.

**Başarısız indirme yüksek sesle başarısız olur.** Sessizce düşen karolar düz gri bir
mozaik bırakıyor, arayüz de bunu "başarıyla indirildi" diye raporluyordu.
`_download_mosaic` %70 altına düşerse `RuntimeError` atar.

**NaN merkezîdir.** `TerrainData.__post_init__` grid'deki her NaN/inf'i sonlu
hücrelerin ortalamasıyla doldurur (dtype korunur). Nodata bilinear
interpolasyondan geçip viewshed/kapsama sayılarına sızıyordu; artık hiçbir alt
katman NaN'a karşı savunma yapmak zorunda değil.

**Karo sunucusu nezaketi.** Gerçek User-Agent (tarayıcı taklidi OSM politikasına
aykırı ve IP bloklatır), OSM/OpenTopo için `max_workers=2`, disk önbelleği
(`%APPDATA%\<uygulama>\tile-cache`), `MAX_TILES=400` bütçesi, kaynağa göre atıf.
Esri uç noktası `{z}/{y}/{x}`; OSM ve OpenTopoMap `{z}/{x}/{y}` — karıştırma.

**Kayıtlı bölgeler (`region_library.py`).** Karo önbelleği yalnız tek tek
karoları tutar (mozaik yeniden kurulur, yükselti API yedeği hiç önbelleklenmez);
bölge **bitmiş ürünü** saklar: `user_data_dir()/regions/<id>/` → `region.json`
(ad, merkez, kapsam, kaynak, zoom, `is_measured`, istenen lat/lon/genişlik
`extra`'da), `dem.npz` (z_grid **bit-aynı** + hücre + orijin), `ortho.jpg`
(Esri fotoğrafı) / `ortho.png` (OSM/topo: yazı, kayıpsız). Yazım geçici
`.tmp-<id>` dizinine, sonra yeniden adlandırma — yarım kayıt listelenmez.
`region_id` yol bileşeni olduğu için `^[a-z0-9-]+$` ile doğrulanır (`..`,
ayraç, sürücü harfi geçemez). İndirici "3. Çevrimdışı Kayıt" grubu (varsayılan
açık, kayıt adı konumu izler) bölgeyi worker thread'de kaydeder — kayıt hatası
indirmeyi düşürmez, sonuçta yazılır; aynı istek (`find_region`: merkez ≤25 m,
genişlik, kaynak) zaten kayıtlıysa "Kayıtlı Olanı İndirmeden Aç". "📂 Kayıtlı
Bölgeler" penceresi: listele / aç / sil / mevcut haritayı kaydet. İndirme ve
kayıtlı bölge **aynı** `_apply_area` yolundan uygulanır. Proje dosyası
`terrain.source = "region"` + `region_id` yazar; GUI açarken bölgeyi çit
noktalarından **önce** yükler (yoksa uyarır), başsız CLI `load_region` ile
aynı grid'i kurar (yoksa hata — sessiz prosedürel yedek yok). Ölçülmemiş
yükseltili bölge açılırken de `showwarning`.

**Bina / ağaç yüzey modeli (`surface_obstacles.py`).** DEM çıplak zemindir;
viewshed binaların ve ormanın içinden görüyordu. Harita penceresi "Bina & Ağaç
Yüksekliklerini İndir (OSM)" → Overpass (`out body geom`; bina, building:part,
natural=tree/tree_row/wood, landuse=forest; iki ayna, `osm-cache` disk
önbelleği, ≤36 km², hepsi başarısızsa `RuntimeError`) → `rasterize` arazi
ızgarasına (PIL "F" çokgen, kısa önce çizilir → örtüşmede uzun kazanır, iç
halka temizlenir, dış hat da boyanır → hücreden küçük bina kaybolmaz) →
`with_obstacles` **yeni** TerrainData: `z_grid` = yüzey (zemin + engel),
`ground_z_grid` = çıplak zemin, `obstacle_grid` / `obstacle_kind`. Bütün
motorlar (viewshed, mount raster, PTZ, çit LOS) görüş hattını yüzeye karşı
test eder; **direk zeminde durur**: `TerrainData.ground_elevation_at`
(viewshed_3d, mount_visibility, perimeter_planner `_place`). Engel yokken
`ground_elevation_at == get_elevation_at` → optik golden bit-aynı.
Yükseklik sırası: `height` etiketi → `building:levels` × 3 m → varsayılan
(bina 6, ağaç 8, orman 12 m); `ObstacleLayer.counts` kaynakları sayar,
varsayılan payı > %50 ise `showwarning` — tahmini yüzeyi ölçüm gibi sunma.
Rapor/CSV kaynak satırı `_surface_line`. Kayıtlı bölge `dem.npz`'yi **her
zaman çıplak** yazar, engelleri `obstacles.npz`'de tutar, açılışta yeniden
uygular. Arayüz iki sürümü (`_bare_terrain` / `_surface_terrain`) tutar,
"Binalar / ağaçlar görüşü engellesin" kutusu aralarında geçer;
`_sync_obstacle_state` yeni bir arazi gelince eski binaları düşürür. Direk
hücresindeki engel lensten yüksekse (`mount_obstruction_m`) telemetri uyarır.
Harita modları: **Hibrit** = fotoğraf × yüzey kabartma gölgesi (sırtlar ve
bina hacmi fotoğrafın üstünde okunur), sonra kaydırıcıyla yükselti rengi
karışır (eskiden düz fotoğraf/rampa karışımıydı, %80'de "Uydu" ile aynı
görünüyordu). Fotoğraflı modlarda bina hafif dolgu + ayak izi kenarı
(çatı görünür kalır); Yükselti modunda dolu. Yükselti renk rampası çıplak
zeminden ölçeklenir (76 m'lik bir kule rampayı germiş, zemini tek renge
çevirmişti).
Yerel frame: güneybatı 0,0, `calculate_bbox` ile aynı doğrusal enlem/boylam.
Ölçüldü (Kızılay 1 km², 5 m hücre): 956 özellik, Overpass ~38 sn; 12 m direkte
görünür alan 11 375 → 175 m².

**Yerel yüzey modeli (LiDAR nDSM / DSM GeoTIFF).** "Yerel Yüzey Modeli (nDSM /
DSM)" → `geotiff_reader.read_geotiff` (Pillow + GeoTIFF etiketleri 33550/33922/
34264/34735/42113; PixelIsPoint yarım piksel kaydırılır) → `surface_obstacles.
layer_from_raster`: her ızgara düğümü hücresi içindeki ≤6×6 alt örneğin
**en yükseğini** alır (ince LiDAR'daki dar bina kaba hücrede kaybolmasın),
yerel metre → enlem/boylam (`_Projector`, `calculate_bbox` ile aynı doğrusal
çerçeve) → raster CRS. Desteklenen EPSG: 4326/4258/5252, 3857, UTM 326xx/
327xx/258xx, TUREF TM27–TM45 (5253–5259, k0 = 1, Türkiye'nin 3° dilimleri);
Transverse Mercator kapalı formül (Snyder), WGS84 ≈ GRS80 ≈ ETRS89 ≈ ITRF96.
**ED50 reddedilir** (~100 m kayık; sessizce kaydırma yok). DSM'de DEM
çıkarılır, dikey referans farkı = kapsanan alanda (DSM − DEM)'in %10'luk
dilimi (açık zemin) → çıkarılır ve not edilir; tür otomatik tahmin edilemezse
arayüz sorar. <1 m gürültü atılır. Veri olan yerde OSM katmanının yerine
geçer (`KIND_SURFACE`), dışında OSM kalır. rasterio/pyproj/GDAL yok; Pillow
okuyamazsa (döşemeli / deflate / LZW / zstd / kayan nokta öngörücü — COG)
`tifffile` + `imagecodecs` okur; ikisi de yoksa hata şeritli yazmayı önerir.

**Yerel DEM GeoTIFF (`geotiff_reader.terrain_from_geotiff`).** rasterio yokken
(paketli çekirdekte yok) `load_geotiff_or_dem` georeferanslı bir DEM'i gri
tonlamalı heightmap yoluna düşürüyordu: ölçülmemiş, hücre boyu uydurma
(2000 m / genişlik), konum yok. Artık etiketlerden okunur: `is_measured=True`,
kuzey-yukarı raster `flipud`, kare hücre (projeksiyonlu: piksel boyu; coğrafi:
merkez enlemde metreye yeniden örnekleme; > 1200 hücre ortalamayla küçültme),
merkez enlem/boylam **ters projeksiyonla** (`Projection.inverse`, Snyder TM),
gerçek konum `TerrainData.georef`'te (`epsg`, güneybatı köşe `x0/y0`, piksel,
`resampled`). Yerel çerçeve yine 0,0 (indirilen DEM'lerle aynı). Georeferansı
hiç olmayan TIFF heightmap olarak kalır (ölçülmemiş). Başsız CLI göreli arazi
dosyasını proje dosyasının yanında arar.

**Teslim çıktıları ve gece.** `geo_export`: KML/KMZ direk, FOV kaması (planın
kendi menzili), PTZ devriye yayı, çit, boşluk, switch; KMZ'de birleşik DORI
`GroundOverlay` (projeksiyonlu georef'te `gx:LatLonQuad`). Konumu olmayan
(prosedürel) arazide KML açık hatayla reddedilir, DXF yerel metrede yazılır;
`real_world=True` DXF'i raster CRS'inde yazar. Açılar pusula (0 = kuzey,
saat yönü) — planlayıcı ve viewshed ile aynı. `night`: güneş (NOAA) + ay
(düşük hassasiyetli kuram, ~0,3°) → yatay lux tablosu; kamera `min_lux`'ü
tutmuyorsa menzil IR (`ir_range_m`) ya da ek beyaz ışıkla sınırlanır; sınır
motorun kendi `CameraPlacement.max_range_m`'i üzerinden uygulanır
(`band_area(transform=)`), fizik yeniden türetilmez. `offer`: senaryo özeti
(hesaplanmamış alan `None` → "—", tahmin yok), betiksiz/internetsiz paylaşım
HTML'i (renkler `design_tokens`'tan), teklif ZIP'i (her parça bağımsız; eksik
olan BENİOKU'da adıyla yazılır).

**Kırpma global Mercator piksel uzayında.** Enlemde doğrusal interpolasyon yanlış;
Mercator Y = `asinh(tan(lat))`.

**Kesit profili ↔ harita fare bağlantısı.** `map_3d_window` alt tuval (`profile_canvas`)
üzerinde gezinince karşılık gelen nokta haritada sarı artı + kutulu etiketle
gösterilir. `_render_profile_canvas` sonunda düzeni `self._profile_plot` sözlüğüne
stash'ler (mode: `perimeter` | `single`, pad'ler, `total_len` / `max_d`);
`_on_profile_hover` fare-x'i bu düzenle mesafeye çevirir. Perimetre modunda mesafe
`perimeter_planner.point_along_polyline(fence_points, dist)` ile dünya noktasına,
tekil modda kamera + pan ekseni boyunca. Overlay item'ları `"phover"` etiketli;
gerçek yeniden çizimde `delete("all")` ile silinir, sonraki fare hareketinde
yeniden çizilir — tam render tetikleme. Direk numaraları hem profilde hem haritada
etiketli, kalabalıkta seyreltilir (`label_step`), seçili/hover'daki her zaman.

## Tk iş parçacığı

Ağ işi arka planda, sonuç `after(0, ...)` ile ana döngüye. İki tuzak, ikisi de
gerçekten yaşandı:

- `except Exception as exc:` bloğu bitince `exc` **silinir**. Ona kapanan bir lambda
  `after(0)` ile sonra çalıştığında `NameError` atar — hata diyaloğu hiç açılmaz.
  `err = str(exc)` diye yerel bir isme al.
- Diyalog kapandıktan sonra callback'ler yok edilmiş widget'lara dokunur. Ölçüldü:
  iptal sonrası **108 Tk callback hatası** ve iptal edilmiş indirme yine de uygulandı.
  `_post()` sarmalayıcısı (`winfo_exists` + `TclError`/`RuntimeError` → özel
  `_DownloadCancelled`) worker'ı sessizce çıkarır. Yeni arka plan işlerinde aynı deseni
  kullan.

## Windows exe derlemesi

`build_exe.bat` → `cctv_simulator.spec`, `runtime_hook_tcltk.py` ile birlikte kök
dizinde. **Varsayılan artık `onefile`** (tek `dist\CCTV Simulator.exe`); eski
hızlı-başlangıç `_internal\` klasör düzeni için `build_exe.bat onedir`. Sorun
çıkarsa önce `cctv_simulator_minimal.spec` (sıfır Tcl/Tk müdahalesi,
`console=True`) dene — PyInstaller sağlıklı bir yorumlayıcıyla tkinter'ı kendi
başına halleder. Kökte `cctv_simulator.spec` ve `cctv_simulator_minimal.spec`
dışında başka `.spec` bırakma — PyInstaller'ın `pyinstaller betik.py` ile
ürettiği çıplak varsayılan spec hiçbir Tcl/Tk önlemi almaz ve madde 5'teki
arızayı üretir. Beş arıza da gerçekten yaşandı:

1. **`No module named 'tkinter'`** — derleyen yorumlayıcıda/venv'de tkinter yok.
2. **`Can't find a usable init.tcl`** — `TCL_LIBRARY` kurulmamış. PyInstaller'ın kendi
   tkinter runtime hook'u yalnız `tkinter` modül grafiğindeyse devreye girer.
3. **`invalid command name "::msgcat::mcmset"`** — `msgcat` bir Tcl Modülü:
   `tcl8/<sürüm>/msgcat-*.tm`. **Linux'ta `tcl8/` dizini `tcl8.6/` içinde, Windows'ta
   kardeşi.** Dört tur boyunca Linux'ta yeniden üretilememesinin sebebi buydu.
   Kaynaktan çalışırken çözümü: `configure_tk_paths()` `theme.py`'nin **en üstünde**,
   `ttkbootstrap` import edilmeden önce.

Her derleme `build/tkinter-diagnostic.txt` yazar. Tahmin etmeden önce onu oku.

4. **`PermissionError: ...build\cctv_simulator\CCTV Simulator.exe` (copyfile 20 deneme
   başarısız) VEYA `FileNotFoundError` `set_exe_build_timestamp` adımında** — Tcl/Tk
   ile ilgisi yok. Bu makinede aktif AV **Norton 360** (Avast/Gen Digital motoru:
   `aswidsagent.exe`), Defender pasif. Norton PyInstaller bootloader'ını
   (`runw.exe` türevi, imzasız) tehdit sayıp:
   - `build\cctv_simulator\CCTV Simulator.exe` yolunu kalıcı bloke ediyor
     (yeni klasör açsan bile `Access Denied`; ACL temiz, `Test-Path` false),
   - yeni yollarda dosya yazılıyor ama Norton ~100 ms içinde siliyor.
   Yol/isim değiştirmek 2. katmanı aşmaz. **Tek çözüm Norton istisnası:**
   Norton → Ayarlar → Antivirüs → Taramalar ve Riskler → *hem* "Taramalardan Hariç
   Tut" *hem* "Auto-Protect / SONAR / Download Intelligence'tan Hariç Tut" →
   proje kökünü ekle. Sonra Norton → Güvenlik Geçmişi → "Çözülen Güvenlik Riskleri"
   / "Karantina" → her `CCTV Simulator.exe` → **Geri Yükle ve Hariç Tut**.
   Alternatif: temiz runner'da CI derlemesi (Norton yok).

5. **`No module named 'tkinter'`, ama build makinesinde `.exe` düzgün açılıyor,
   başka bir PC'de açılmıyor** — derleme bozuk değildi, `onedir` çıktısı
   (`CCTV Simulator\` + yüzlerce dosyalı `_internal\`) başka PC'ye kopyalanırken
   bazı dosyalar sessizce kayboldu. Windows'un yerleşik zip açıcısı, hedef yol +
   dosya yolu 260 karakteri (`MAX_PATH`) aşan girdileri **hatasız atlar** — bu
   projenin kendi yolu zaten uzun olduğu için (`...\outputs\dist\CCTV
   Simulator\_internal\tkinter\...`), hedefte de biraz derin bir klasöre
   (Masaüstü\İndirilenler\alt klasör, OneDrive senkron yolu) açılırsa
   `_internal\tkinter\__init__.py` gibi tek bir dosya kaybolması bile bu hatayı
   üretir. Kalıcı çözüm: **`build_exe.bat` varsayılanı `onefile`'a çevrildi**
   (kopyalanacak/kaybolacak ayrı bir dosya ağacı yok, tek `.exe`); `onedir`
   artık yalnız `build_exe.bat onedir` ile açık istekle. Ayrıca kökte
   `pyinstaller cctv_dual_view_simulator.py` gibi düz bir komutun ürettiği,
   hiçbir Tcl/Tk önlemi almayan iki çıplak `.spec` dosyası (`CCTV Dual View
   Simulator.spec`, `cctv_dual_view_simulator.spec`) bulunup silindi — bu
   projenin ortamında PyInstaller'ın kendi otomatik tkinter kancası 0 modül
   topluyor (`tkinter-diagnostic.txt`'teki `a.pure içindekiler: 0 modül` bunu
   kanıtlıyor), yani o iki spec'le alınan HERHANGİ bir derleme, hangi PC'de
   çalıştırılırsa çalıştırılsın, aynı hatayı verir — `cctv_simulator.spec`'in
   elle eklediği `Tree(...)` yedekleri olmadan.

## Şartname incelemesi

`spec_assistant` penceresi → üç yol: **Yapay Zeka** (Gemini API key ile; "Yerel
model (Ollama)" kutusu işaretliyse önce `localhost:11434` denenir,
`analyze_with_ollama` aynı JSON sözleşmesi), **Kurallı + Fizik** (offline),
**İster şablonu** (kaydedilmiş ister setini mevcut kamera kütüphanesine karşı
yeniden değerlendir).

Ollama yolu `use_ollama_var` işaretliyse Gemini anahtarı kontrol edilmeden
**önce** çalışır (yoksa anahtarsız kullanıcı yerel modele hiç ulaşamıyordu).
`_run_ollama_in_thread`: `analyze_with_ollama` senkron `urlopen(timeout=300)`
yapıyor, ilk model yüklemesinde arayüzü donduruyordu → daemon thread + ilerleme
diyaloğu + `after(150)` poll; yok edilmiş diyalog sessizce iptal eder.

`rule_based_compliance` artık iki tür ister çıkarır:
- Klasik anahtar-kelime (`extract_rule_requirements`, ~30 kalıp) — broşür alanına
  karşı `evaluate_rule_requirement`.
- **DORI/menzil** (`compliance_optics.extract_dori_requirements`: "30 m teşhis",
  "125 PPM @ 40 m", "25 m plaka") — her aday model için **optik motorla**
  (`calculate_for_camera` + `calculations.ground_distance_for_ppm`, hedefe nişan
  alan tilt) `evaluate_dori_requirement`. Matris satırında `evidence_kind`
  ("optik motor" / "broşür"), `standard_clause`, `spec_quote`, `confidence`.

`compliance_standards`: EN 62676-4 DORI tablosu (Detect 25 … Identify 250),
TR görev→PPM (`teşhis`→identify, `plaka`→143), kategori→madde eşlemesi.
`_TASK_TR` **özgül köklerle** eşleşir: "kontrol" ("erişim kontrolü") ve çıplak
"tanı" ("tanımlı") kaldırıldı — sahte DORI isteri üretiyorlardı; "kimlik" →
"kimlik tespit/belirle", "takip" → "hareket takib". `compliance_optics._DIST`
penceresi 40 → 90 karakter (uzun TR cümlesinde "plaka … 25 metre" kaçıyordu).
`find_ambiguities`: madde içinde herhangi bir rakam değil, **birim taşıyan**
sayı (`_SPEC_NUMBER`: MP/dB/lux/m/%…) varsa belirsiz sıfat atlanır — "1 adet
yüksek çözünürlüklü" artık işaretlenir. Sonuçta `ambiguities` /
`clarification_questions`.

`compliance_report.build_statement(result)` → EN 62676-4 uygunluk beyanı
(markdown; `write_statement_pdf` ince PDF). `requirement_library` şablonları
`%APPDATA%\<uygulama>\spec-templates\<slug>.json`.

`pypdf` opsiyonel (requirements.txt); yoksa PDF yalnız Gemini'ye gider.

**Yerel model.** `build_compliance_prompt` few-shot örnek + `dori`
kategori/`required_ppm`/`spec_quote` talimatı içerir; `compact_spec` 32k.
`analyze_with_ollama` `num_ctx=16384` (varsayılan 2k şartnameyi kesiyordu),
`num_predict=8192`, `keep_alive=10m`. `OLLAMA_MODELS` = qwen2.5:7b/14b/3b,
llama3.1:8b, gemma2:9b (Türkçe + JSON). `training_log`: her analiz +
her override JSONL'e yazılır (`%APPDATA%\<app>\training\compliance.jsonl`);
`build_instruction_dataset` bunları system/user/assistant çiftlerine çevirir,
**insan düzeltmelerini assistant hedefine katarak** — yani override UI bir
etiketleme hattı. Tezgâhta "🧠 Eğitim Verisi Dışa Aktar".

**Fine-tune + değerlendirme.** `scripts/finetune_compliance.py`: `training_log`'dan
dataset üret → `split_dataset` (spec hash'ine göre train/eval) → unsloth QLoRA
(r=32) → q4_k_m GGUF + Ollama `Modelfile`. `--prepare-only` GPU'suz veri hazırlar.
`scripts/eval_compliance.py`: gold JSONL'e karşı model (`rule` veya Ollama adı)
koşturur, metrik basar. Metrik çekirdeği `compliance_eval.py` (birim testli, GPU
yok): `dori_requirement_prf` (task/mesafe/ppm kümesi P/R/F1), `matrix_status_
accuracy` (`user_status` tercih edilir), `aggregate` (durum doğruluğu satır
sayısıyla ağırlıklı, DORI metrikleri yalnız parse edilenler üzerinden). Reçete:
`docs/yerel-model-egitimi.md`.

## Doğrulama alışkanlıkları

- Tk arayüzünü `xvfb-run` altında başsız çalıştır, ekran görüntüsü al ve **bak**.
  Kırpılmış etiket, sarmalanmış yazı, taşan kart kod incelemesinde görünmez, PNG'de
  bariz. Kalite etiketinin kırpıldığı ve üçüncü radyo butonunun taştığı böyle bulundu.
- `calculations.py` refaktöründe yeni çıktıyı eskisiyle birkaç yüz rastgele
  `CameraConfig` üzerinde diff'le. Amaçlanan ve işaretlenmiş düzeltmeler dışında
  bit-aynı olmalı.
- Arayüz değişikliğinde her iki sürümden tüm canvas item'larını (tip, koordinat,
  dolgu, yazı tipi) dök ve diff'le.
- Ağ koduna sahte sunucu enjekte et (`_fetch_tile`'ı monkeypatch et) ve mozaik/kırpma
  matematiğini bilinen bir fonksiyonla kodlanmış karolarla ölç.
- Platform farkları hata saklar. Bir hata yeniden üretilemiyorsa, raporu değil
  platformu şüphe altına al.

## Test paketi ve CI

`tests/` altında pytest. `py -3.13 -m pip install -r requirements-dev.txt`, sonra
`py -3.13 -m pytest` (veya `/check`).

- `test_optics_golden.py` — `tests/data/optics_golden.json` 150 tohumlu
  `CameraConfig` için `calculate_for_camera` + `ppm_at_distance` çıktısını
  donduruyor. Motoru bilerek değiştirdiysen `py -3.13 -m tests.gen_optics_golden`
  ile yeniden üret, JSON diff'ini **aynı commit'te** incele ve ekle. Bu, elle
  yapılan "rastgele config diff'i" alışkanlığının otomatik hâli.
- `test_mosaic_math.py` — `_fetch_tile` monkeypatch'li; Mercator/bbox/karo
  matematiği ve `_download_mosaic` kırpma + hata yolları.
- `test_terrain.py` — `is_measured` bayrağı, `download_terrain_dem` + yerel
  GeoTIFF (`_terrain_from_rasterio`, sahte rasterio) kuzey-güney `flipud`
  sözleşmesi, nodata doldurma, bilinear yükselti.
- `test_perimeter.py` — EN 62676-4 aralık formülü, BOM sayıları, boşluk tespiti
  (kapalı halka ≥%95, açık uçta kör-nokta boşluğu), köşe/uç kameraları.
- `test_cli_headless.py` — başsız CLI + `project_io` roundtrip.
- `test_viewshed.py` — LOS occlusion (duvar gölgesi), yatay + **dikey** FOV
  konisi (dik tilt yalnız yakını çerçeveler), DORI düşüşü, atmosferik menzil,
  `effective_px_ratio` optik limiti.
- `test_atmosphere.py` / `test_solar.py` — Koschmieder, bant, güneş konumu,
  parlama seviyeleri, yerel saat.
- `test_compliance.py` / `test_compliance_io.py` — DORI ister çıkarım, optik
  gecti/kaldi, belirsizlik, EN 62676-4 beyan, ister şablonu roundtrip, Ollama
  guard, "erişim kontrolü" sahte DORI üretmez, uzun cümle toleransı.
- `test_engineering_report.py` — viewshed+kapsama+perimetre → ASELSAN PDF/CSV;
  bölüm başlıkları, `%PDF-` başlığı, `is_measured` uyarısı / ÖLÇÜLMÜŞ DEM ayrımı.
- `test_multi_viewshed.py` — çoklu kamera birleşimi, tek kamera ile bit-aynı,
  örtüşme + best_cam takibi, en iyi-DORI birleştirme, `focal_mm_override`
  aradeğer + kelepçe, `with_profile=False` yalnız kesiti atlar,
  `placement_bounds_warnings`.
- `test_ptz_tour.py` — tur periyodu (dwell + slew + settle), zoom geçiş süresi,
  revizit = periyot − dwell (tek preset), zıt presetlerde revizit boşluğu,
  hiç-görülmeyende ∞.
- `test_perimeter_ptz.py` — PTZ yokken plan bit-aynı, her N. normal direğe
  takılma, sarım yönünden bağımsız dışa bakış, sınır modunda izlenen taraf,
  tele teşhis erişimi, `patrol_for`, planın odağıyla yerleşim, uçtan uca karma
  değerlendirme (tespit→teşhis = en hızlı PTZ).
- `test_surface_obstacles.py` — yükseklik etiketi ayrıştırma + kaynak sırası,
  raster kuzey-yukarı / `z_grid` yönü (aynalanmaz), avlu iç halkası, hücreden
  küçük bina, duvar gölgesi yalnız yüzeyle, direk bina üstünde de zeminde,
  ayna sunucuya düşme + önbellek, yüksek sesle hata, alan sınırı, kayıtlı
  bölge çıplak DEM + engel roundtrip, rapor yüzey satırı.
- `test_geotiff_dem.py` — TM ters dönüşüm, rasterio'suz UTM DEM ölçülmüş +
  güney-yukarı + konumlu, coğrafi DEM kare metreye, döşemeli/sıkıştırılmış
  COG (tifffile), georeferanssız TIFF ölçülmemiş, başsız CLI `--viewshed`.
- `test_geo_export.py` / `test_night.py` / `test_service_delivery.py` —
  pusula kaması, KML konumu `calculate_bbox` çerçevesinde, KMZ katmanı, DXF
  R12 yapısı + Türkçe kaçış, projeksiyonlu georef tam; ay evreleri yayımlanmış
  tarihlerle, lux düzeyleri, ışık kaynağı seçimi; gece kapsaması = motor +
  IR sınırı, yeni plan eski sonuçları düşürür, teklif paketi parçaları.
- `test_focal_optimizer.py` — odak adımları log, optimum iki uçtan kötü
  değil, alanlar `view_mask` ile birebir, açık arazide teşhis için tele kazanır,
  yön araması tepenin açık tarafını bulur, zarf her tekil bakışı kapsar (PTZ
  zarfı sabitinkini), dizilim başlangıçtan kötü olamaz ve kötü başlangıcı
  düzeltir, PTZ talep üzerine birleşime eklenir, çit bandı.
- `test_pole_optimizer.py` — `spacing_m` geçersizleştirmesi (None bit-aynı),
  ulaşılabilir hedefte planlayıcıdan az direk, planlayıcı üstü hedefte
  sıklaşma ve en seyrek geçen deneme, imkânsız hedefte en iyi çaba + not.
- `test_icons.py` — yazı tipi + lisans gömülü, her simge çizilebilir, emoji
  eşlemesi yalnız bilinen simgelere, `split_emoji`/`plain`, kaynakta emoji yok.
- `test_geotiff_surface.py` — TM meridyen yayı + UTM merkez meridyen, ED50 /
  bilinmeyen CRS reddi, nDSM bloğu WGS84 / UTM 36N / TUREF TM30'da doğru
  hücrede, DSM zemin çıkarma + dikey referans düzeltmesi, ölçülmüş yüzey
  yalnız veri olan yerde OSM'nin yerine geçer, georeferanssız TIFF hatası.
- `test_raster_smooth.py` — yumuşatma alanı ve rengi korur, merdiven çizgiye
  döner, iki sınıf arasında boşluk yok, rampa/boş durum, kırpılan içerik yerine
  oturur, FOV kaması.
- `test_ptz_animation.py` — dönüş/sektör pan modeli, koni dışında ışık yok,
  tepe arkası gölge, bir turda görülen = direğin görebildiği, iz söner,
  kare < 30 ms, kesit profili.
- `test_region_library.py` — kayıtlı bölge roundtrip (z_grid bit-aynı, Türkçe
  ad, PNG kayıpsız / Esri JPEG), ölçülmemiş bayrağı korunur, yarım/bozuk kayıt
  atlanır, yol geçişi koruması, aynı ad iki kimlik, `find_region` isteğe göre,
  proje + başsız CLI bölgeyi açar (yoksa hata).
- `test_database.py` — `is_ptz_camera` (yerleşik ASELSAN UMA T5/Fix, görünür
  bant "Genel … PTZ" modelleri, anahtar kelime, açık `is_ptz` alanı önceliği).
- `test_design_tokens.py` — iki tema aynı tokenlar, kontrast tabanları, tema/dil
  tercihi birbirini ezmez, `CCTV_THEME` önceliği, yazı tipi dosyaları + lisanslar.
- `test_status_summary.py` — gösterge kutuları motor sonucunu okur, ulaşılamayan
  tasarım seviyesi / kapatılmayan kör nokta (sırt sırta iki kamera) kritik, IR
  alarmı seviyeyi adlandırır, önem sırası + karşılaştırma modunda tekilleştirme,
  kontrol noktası ve hesap hatası alarmı.
- `test_i18n.py` — Türkçe kimlik, İngilizce çeviri + geri düşüş, `{}` biçim,
  tercih kalıcılığı, `CCTV_LANG` env önceliği.
- `test_cli_headless.py` — `--viewshed` / `--ptz` başsız akış + JSON + rapor.

`network` işaretli test yok (hepsi monkeypatch'li). Canlı sunucu denemesi
istersen elle: `py -3.13 -c "from cctv_simulator.online_map_loader import _fetch_tile; ..."`.

CI: `.github/workflows/ci.yml`, **windows-latest** (Tcl/Tk ve reportlab font
yolu Windows'a özgü). ruff (yalnız hata yakalayan alt küme, `pyproject.toml`) +
`compileall` + pytest + başsız CLI duman testi. mypy bloklamıyor.

## Başsız mod (CLI)

`py -3.13 -m cctv_simulator --project plan.json --export csv,xlsx,pdf --out ./rapor`
veya `--json` ile stdout'a sonuç. Tk yok, ekran yok.

- `project_io.py` disk şemasını (`ui/main_window.save_project` ile aynı) Tk'siz
  okur/yazar. GUI şemayı sahiplenir; `project_io` onu takip eder.
- `__main__.py` optik motoru koşturur, `analyze_dead_zone_coverage` çağırır,
  `exporters.py` yazıcılarını kullanır. Şema değişirse üç yeri de güncelle:
  `main_window.save_project`, `project_io`, gerekiyorsa `__main__._results_to_json`.

## Çoklu kamera + PTZ + başsız görüş alanı

`viewshed_3d.calculate_multi_camera_viewshed(terrain, [CameraPlacement…])` —
otoriter tekil motoru her yerleşim için koşturup grid'leri birleştirir:
`MultiViewshedResult` her hücrede en iyi DORI + `seen_count_grid` (yedeklilik) +
`best_cam_grid` + örtüşen/tek-kamera alan + `per_camera` listesi.
**Yedeklilik fiziksel kamera sayar, görünüm değil:** `CameraPlacement.camera_id`
aynı olan yerleşimler tek kameranın zaman paylaşımlı bakışlarıdır (PTZ
presetleri) — `seen_count_grid` / `overlap_area_m2` / `single_cover_area_m2`
onları bir kez sayar, `camera_count` fiziksel kamera adedidir (`per_camera`
görünüm listesi). `None` = kendi kamerası (sabit; eski davranış bit-aynı).
`evaluate_ptz_tour` tüm presetlere aynı `camera_id`'yi verir.

**Direk başına 360° görüş haritası (`mount_visibility.py`).** Arazi engellemesi
yalnız lens konumuna bağlı (pan/tilt/zoom'dan bağımsız), o yüzden
`mount_visibility(terrain, x, y, mast, max_range_m)` tam çember ışın yürütmesini
**bir kez** yapıp önbelleğe alır (LRU 16; anahtar arazi nesnesi + grid
geometrisi + direk), `view_mask(mv, camera, pan_deg=, tilt_deg=, focal_mm=)`
her bakışı birkaç dizi karşılaştırmasıyla çıkarır. Motorla **aynı yapı
taşlarını** kullanır — `viewshed_3d.march_rays` (engelleme), `view_limits`
(px/m tabanı, optik/atmosferik menzil), `zone_codes` (DORI) — bunlar
`calculate_3d_viewshed`'den çıkarıldı (motor bit-aynı). Fark yalnız ışın
örneklemesi; tek sabit bakış için otorite hâlâ `calculate_3d_viewshed`.
`ViewGrids.truncated`: istenen menzil haritanınkini aşıyor.
`view_layers` bakışın pan'dan bağımsız kısmıdır (dikey çerçeve, menzil, px/m,
görüş hattı); `view_mask` = katmanlar ∩ yatay FOV konisi.

**PTZ görsel tarama (`ptz_animation.py`).** Tarama/sektör modunda "▶ Görsel
Tarama (canlı)": `SweepPlayback` direk raster'ını + `view_layers`'ı **bir kez**
kurar (arka planda), sonra her kare (`frame(t)`, ~1–3 ms) o anki pan
konisinde görünen hücreleri DORI renginde, koni içinde arazi/bina gölgesinde
kalanları koyu, geçilen yeri sönen iz (`trail_s` ≈ 1,5 FOV / hız), bir kez
görüleni silik (`memory_alpha`) boyar. Hareket `pan_at` — `sweep_timing` ile
aynı sabit hız modeli (sektör: git, bekle, dön, bekle). Tuvalde yalnız
`ptz_anim` etiketli öğeler her kare silinip çizilir (tam harita render'ı
yok); raster görünür tuvale kırpılıp büyütülür, `map_raster`'ın hemen üstüne
konur; okuma kutusu camda. Kesit şeridi her 3 karede o anki pan'ı izler
(`sight_profile`: zemin + bina/ağaç + görülen/engel arkası). Kamera/arazi
ayarı değişince (`_anim_key`) oynatma durur. Ölçüldü: kare çizimi 6–12 ms.

**Varifokal / PTZ: tüm odak uzunlukları (`focal_optimizer.py`).** Viewshed bir
varifokali yalnız iki uçta (`min`/`max`) değerlendiriyordu; belirli bir DORI
seviyesindeki alan çoğu zaman aradaki bir odakta en büyüktür. Hepsi direk
raster'ı üstünde: `view_layers` (odak, eğim) başına bir kez, her yön yalnız koni
karşılaştırması (Kural 1 — motorla aynı yapı taşları).
- `sweep_focals`: log adımlı odaklar (`focal_steps`) × `auto_tilts` (hedef
  seviyenin bittiği mesafeye göre üç aday: kadraj üstü orada / merkez %55'te /
  üst ufkun hemen altı) × isteğe bağlı yön araması → odak başına DORI alan
  eğrisi + en iyi + iki uç. Amaç `"area"` (hedef seviyede alan) veya
  `"balanced"` (her seviye 1 puan). Alanlar `view_mask` ile birebir aynı (test).
- `envelope`: hücre başına tüm odak/eğim (PTZ'de tüm yönler) içinde en iyi
  DORI. Sabit varifokal için **eşzamanlı değil** — arayüz bunu yazar.
- `optimize_layout`: sabit direklerin (odak, eğim[, yön]) seçimi, ROI'deki
  (çit ±40 m bandı, `fence_roi`) **birleşik** kapsama üzerinde açgözlü
  koordinat taraması; seviye başına kapsama sayaçları (`cnt`) ile her aday
  yalnız kendi kutusunda puanlanır (adaylar kendi izine kırpılır — tüm
  pencereyi saklamak direk başına yüzlerce MB ediyordu). Her direğin mevcut
  ayarı aday 0'dır → sonuç başlangıç planından kötü olamaz. PTZ'ler talep
  üzerine erişim (zarf), sürekli kapsamaya sayılmaz.
- Arayüz: tekil sekmede "Varifokal" grubu (tarama + eğri grafiği + "Optimumu
  Uygula" → `_focal_override` → `calculate_3d_viewshed(focal_mm_override=)`,
  lens radyosu sıfırlar; "Zarfı Göster" → `envelope` katmanı). Çit sekmesinde
  "Odak optimizasyonu — sabit + PTZ": "Sonucu Plana Uygula" eski ve yeni planı
  **otoriter** `calculate_multi_camera_viewshed` ile aynı bantta puanlar.
  Dikkat: plan direk menzilini çit hedefinde (ör. 40 px/m) keser; bir DORI
  seviyesini puanlarken o seviyenin kendi menzili verilmeli (`full_reach`),
  yoksa motor ve optimizasyon farklı soruyu yanıtlar (8,6 vs 15,4 ha görüldü).
  Ölçüldü (124 direk, 2×2 km tepelik, 10 m): optimizasyon 2,7 sn, doğrulama
  1,3 sn; bantta Algılama 15,40 → 20,71 ha (motor) / 15,42 → 20,72 (tahmin).

**En az direk (`pole_optimizer.minimize_poles`).** Planlayıcı aralığı tek
direğin lens UCUNA göre, komşuları bilmeden ve güvenlik payıyla verir.
`generate_perimeter_plan(spacing_m=)` (None = bit-aynı) ile aralık aranır:
deneme 0 planlayıcının aralığı; hedefi tutuyorsa aralık ×1,45 büyür, ilk
başarısızlıktan sonra log uzayında ikiye bölünür; tutmuyorsa ×0,7 sıklaşır.
Her denemede `optimize_layout` direk başına odak/eğimi seçer; puan = çit
bandının (±40 m) hedef seviyedeki sürekli kapsama oranı. Hedefi tutan en
az direkli deneme kazanır; aynı direk sayısı önbellekten gelir (`trials`
farklı direk sayılarıdır, `steps` döngüyü ayrıca sınırlar). Hedef hiç
tutmazsa arayüz planı DEĞİŞTİRMEZ, yalnız raporlar. Kazanan plan arayüzde
`_apply_layout_result` ile otoriter motorla doğrulanır. Ölçüldü (2,4 km kare
çit, 2,8–12 mm 4 MP, 12 m direk, 124 direklik plan): %70 → 20 direk (motor
%77,8), %95 → 68 direk (motor %95,5); 6–7 sn. Planlayıcının kendi aralığında
bant kapsaması odak optimizasyonu sonrası bile ≤%97: kameralar çit boyunca
baktığından bandın dış kenarına yetişmez. `_plan_kwargs` / `_refresh_plan_stats`
"Otomatik Diz" ile aramanın aynı ayarla planlamasını sağlar.

**Sürekli PTZ taraması (`ptz_schedule.py`).** `SectorPatrol` (a→b→a, hız,
uçlarda bekleme) ve `Rotation360`; `evaluate_sweep(...)` → `SweepResult`.
Hücre başına revizit (bir periyotta en uzun izlenmeyen süre) ve tek bakış
süresi **kapalı formülle** (`sweep_timing`, açı açı maske yok): sektörde
boşluklar `2(S−hi)/ω + t_e` ve `2·lo/ω + t_e`, 360°'de `(360−HFOV)/ω`.
**`min_dwell_s`** (vars. 0,5 sn): bakışı bundan kısa süren hücre kapsama
değil `glimpse_mask` — 30 mm'de 90°/sn dönüş hiçbir hücreyi 0,12 sn'den uzun
tutmaz. Görünürlük `view_layers`'tan (sabit bakışla aynı kurallar). Sabit hız
varsayar (ivme yok, `ptz_tour` ile aynı). `evaluate_sweep` `view_layers`'a
sınırsız menzil ister: `truncated` = kameranın görüşü analiz menzilini aşıyor
(raster menzilini istek olarak verince hiç `True` olamıyordu). Testler formülü adım adım zaman
simülasyonuna karşı doğrular.

**Sabit + PTZ birleşimi ve tespit→teşhis (`ptz_fusion.py`).**
`fuse_coverage(static, [PTZCoverage…])`: DORI seviyesi başına her hücre
SÜREKLİ (bir sabit kamera ulaşıyor, revizit 0) / ARALIKLI (yalnız PTZ; revizit
= en küçük tekil PTZ boşluğu — birden çok PTZ'de muhafazakâr üst sınır) /
YOK. `PTZCoverage.from_sweep` / `from_tour`. `cue_response(...)`: PTZ ev
konumundan her hücreye nişan alıp hedef px/m'yi (vars. Teşhis 250) tutana
kadar geçen süre — odak `focal_for_ppm` (hücre merkezi eğik mesafesi),
hareket `ptz_tour.move_time_s` (tur geçişleriyle **aynı** model; tur
çıktısı 768 geçişte bit-aynı). Ulaşılamayan hücre +inf + neden kodu
(`CUE_NO_LOS` / `CUE_LENS` / `CUE_TILT` / `CUE_ATMOSPHERE` /
`CUE_OUT_OF_RANGE`, Türkçe `CUE_REASONS`). `detect_then_identify(static, cue)`:
yalnız sabit kameraların tespit ettiği hücrelerde.

**Karma çevre çiti: sabit + PTZ (`perimeter_ptz.py`).**
`generate_perimeter_plan(..., ptz_camera=, ptz_every_n=, ptz_patrol_span_deg=,
ptz_target_ppm=)` her N. normal direğe (1., N+1., …; köşe/uç guard'ları değil)
**aynı direğe** bir PTZ takar → `PerimeterPlanResult.ptz_cameras` (ayrı liste;
`placed_cameras` yalnız sabit kalır — çit boşluk analizi, kapsama ısı haritası,
parlama ve BOM satırları **sürekli** kapsamayı anlatır). PTZ satırında `pan_deg`
devriye merkezi (tesis: çitin dış normali — kapalı halkada sarım yönünden, açık
hatta merkezden uzağa; sınır/otoyol: direğin baktığı yön), `patrol_span_deg`
(360 = dönüş), `effective_range_m` = tele uçta `ptz_target_ppm`'e (vars. Teşhis
250) ulaşılan yer mesafesi; devriye tilt'i geniş ucun **motor** tespit erişimine
(`view_limits`: termalde Johnson 1,3 px/m) göre. `pole_count` değişmez, `camera_count` = sabit + PTZ,
bant genişliği modele göre (`_bitrate_mbps`). `evaluate_mixed_perimeter(terrain,
plan, fixed_cam, ptz_cam)` → `MixedPerimeterResult`: sabit direkler
`calculate_multi_camera_viewshed`, her PTZ `evaluate_sweep` (`patrol_for`:
Rotation360 / SectorPatrol) + `cue_response` (aynı direk raster'ı, tek önbellek
girişi; raster = max(tele teşhis erişimi, geniş uç tespit erişimi) — termal PTZ
75 m'de teşhis edip ~1,6 km'de tespit eder, yalnız birincisiyle boyutlanan
raster devriyeyi sessizce kırpıyordu), `fuse_coverage`, en hızlı PTZ'nin tepkisi (`cue_s`) ve
`detect_to_identify_s` + özet (tespit edilen alanın yüzde kaçı teşhis
edilebilir, medyan/%90/en kötü). `plan_placements(plan, camera)` sabit
direkleri **planın kendi odağıyla** (`focal_mm_override`) yerleşime çevirir —
arayüz eskiden her direğe `lens_mode="max"` veriyordu (geniş lensle dizilen plan
tam zoomda puanlanıyordu). Ölçüldü: 2×2 km tepelik arazi, 104 sabit + 17/34 PTZ
~0,44 sn.

`ptz_tour.evaluate_ptz_tour(terrain, PTZTour(presets=[PTZPreset…]))` — PTZ'yi
"aynı konumda farklı pan/tilt/zoom kameraları" olarak `calculate_multi_camera_
viewshed`'e verir (`PTZPreset.focal_mm` → `CameraPlacement.focal_mm_override`),
üstüne **revizit süresi** katmanı ekler: `_transition_s` her geçişte pan/tilt
slew + optik zoom süresi + `settle_s` oturma; `_revisit_for_mask` her benzersiz
preset-maskesi için döngüsel en uzun izlenmeyen boşluğu bulur. `revisit_grid`
saniye (∞ = hiç görülmez), `tour_period_s` (dwell + slew + zoom + settle
ayrışık), ortalama/en kötü revizit, sürekli/aralıklı/hiç-görülmeyen alan.

Başsız: `--viewshed` (proje `terrain.placements[]`) ve `--ptz` (`terrain.ptz`)
`__main__.py`'de; `--export pdf,csv` ile `<stem>-gorusalani.pdf/.csv` (ASELSAN
formatı), `--json` çıktısına özet. `project_io` şeması `terrain` bloğu:
`source/preset/file/width_m/viewshed_range_m/weather/placements/ptz` — eski
proje dosyaları değişmeden yüklenir (hepsi varsayılanlı).

## Görünüm: tasarım tokenları

Renk ve yazı tipinin tek kaynağı `design_tokens.py` ("CCTV Simülatör Tasarım
Sistemi" artifact'inin `tokens.json`'u ile aynı değerler). İki tema aynı token
adlarını taşır: `dark` ("Konsol", varsayılan) ve `light` ("Ofis"). Sıra:
`CCTV_THEME` env → `ui_prefs.json` `theme` → `dark`; değişiklik yeniden
başlatınca etkin (Çıktı sekmesi → Görünüm). `ui_prefs.json`'a **birleştirerek**
yazılır (`write_pref`) — eskiden dil kaydı dosyayı tümden eziyordu.

- **Yeni kodda hex yazma.** `tok("bg-1")`, tuvalde rol adı (`PALETTE["grid"]`,
  `_CANVAS_ROLES`: surface/grid/axis/label/ground/camera/ray…), PIL için
  `rgba(name, alpha)`. `theme.COLORS` eski anahtarlarıyla tokenlardan dolar.
- `theme._apply_design_theme`: iki ttkbootstrap teması (`cctv-konsol`,
  `cctv-ofis`) token değerleriyle kaydedilir; Treeview satır yüksekliği DPI
  ölçekli; eski tk widget'ları (Text/Listbox/Canvas) option veritabanından
  renk alır. Başarısız olursa stok tema kalır, pencere kaybolmaz.
- **Bir alanda tek birincil düğme** (`bootstyle="primary"` = yeşil vurgu);
  diğerleri `secondary`/`secondary-outline`. `warning` bootstyle'ı düğmede
  yok — sıcak renk yalnız durum/alarm içindir; tek istisna geri alınamaz
  silme düğmesinde `danger-outline`. ttkbootstrap `secondary` rengi `steel`
  (outline düğmenin yazısı + kenarı); `bg-2` olduğunda outline düğme yazısı
  zeminle aynı renkti, ekranda görünmüyordu. Dolu `secondary` düğme
  `_style_details`'te `bg-2` yüzeye geri çekilir — ttkbootstrap stilleri ilk
  kullanımda tembel kurduğu için orada önce bir kez kurdurulur, yoksa
  üstüne yazar. Eski `tk.Toplevel`/`tk.Frame` zemini option veritabanından.
- Yazı tipleri `cctv_simulator/fonts/` (Barlow, Barlow Semi Condensed, IBM Plex
  Mono, OFL lisansları yanında), `AddFontResourceExW(FR_PRIVATE)` ile yalnız bu
  süreç için yüklenir, kurulum yok; PyInstaller paketi `cctv_simulator/`'ı
  komple aldığı için `.exe`'ye girer. GDI aile adları: kalın başlık için
  `"Barlow Semi Condensed SemiBold"` (düz `"Barlow Semi Condensed"` Arial'a
  düşer). `theme.FONT_UI/FONT_DISPLAY/FONT_MONO`; yüklenemezse Segoe UI /
  Consolas. Barlow'da ■▲●◆ yok — Windows yedek fontundan çizilir.
- **Emoji yok, simge var.** Düğmeler `StyledButton(..., icon="save")` ile
  Lucide (ISC, `fonts/lucide/`) simgesi taşır; renk düğmenin yazı rengi
  (`_icon_colour`: dolu birincil → on-accent, çerçeveli → stil rengi). Diğer
  metinlerde emoji yok, durum işaretleri ● ▲ ■ ◆. `StyledButton` metnin
  başındaki emojiyi yine ayıklar (güvenlik ağı). `test_icons.py` kaynakta
  emoji kalmadığını denetler; yeni simge `icons.CODEPOINTS`'e eklenir
  (Lucide `font/info.json`, sürüm 0.460.0). Görünen etiket ≠ iç kod olan
  açılır kutular aynı değişkene bağlanmaz (3B hedef kutusu "human" yazıyordu).
- `test_design_tokens.py` iki temada her metin/zemin çiftinin kontrastını
  (4.5:1, kontrol kenarı 3:1) denetler; ilk koşuda açık temanın kenar rengini
  (2.97:1) yakaladı.
- Kullanıcı verisi olan DORI seviye renkleri (`models.DEFAULT_LEVELS`,
  projelere ve rapora yazılır) bilinçli olarak değiştirilmedi.

**Ana pencere yerleşimi** (artifact `ana-ekran.md`): üstte `brand` komuta
çubuğu (ürün adı, proje adı + `● Kaydedildi / ▲ Kaydedilmedi` — imza
`_project_signature`, kayıt/yüklemede `_mark_project_saved`; pencere
düğmeleri; alarm sayacı; Kaydet + tek birincil "PDF Rapor"), solda formlar,
ortada tablo/öneriler/tuval, sağda durum paneli, altta durum çubuğu (imleç,
optik özet, son mesaj, hesap süresi). Çubuk iki temada da lacivert, o yüzden
üstündeki çipler `tok(name, "dark")` okur. Durum paneli `status_summary`'nin
`build_kpis` / `build_alarms` çıktısını çizer — fizik türetmez (Kural 1);
alarm eşikleri `build_recommendations` ile aynı. Alarma tıklamak o kamerayı
seçer. Panel 320 px (<1600 px'te 280) × DPI; pencere 1150 px × DPI altına
inince bölmeden çıkar, komuta çubuğundaki alarm sayacı geri açar/kapatır.
Sol bölme 300–440 px **× DPI** (eskiden ham pikseldi: %150'de formlar 293 px'e
sıkışıp sağdan kırpılıyordu). Kısayollar: Ctrl+S/O/P, F5, Ctrl+1…4 pencereler.
Yan kesitte metre etiketleri zemin şeridinde ve ölçülen genişliğe göre
seyreltilir (şerit başlığıyla aynı satırdaydı, üst üste biniyordu).

## Arayüz dili (i18n)

`i18n.py` — **Türkçe kaynak metin anahtardır**: `t("Kaydet")` en'de "Save",
bulunamazsa Türkçe anahtarı döner (call site sarmak Türkçeyi hiç bozmaz).
`locale/en.json` düz `{türkçe: çeviri}` haritası. Dil sırası: `CCTV_LANG` env →
kayıtlı tercih (`user_data_dir()/ui_prefs.json`) → "tr". `cctv_dual_view_simulator.py`
başlangıçta `load_preferred_language()` çağırır; `main_window` "Çıktı" sekmesinde
dil seçici (yeniden başlatınca etkin — kurulu widget'lar yeniden çizilmez).
Çeviri kapsamı **artımlı**: şu an pencere başlıkları + yeni özelliklerin
metinleri sarılı; gövde metinlerinin çoğu hâlâ Türkçe-yalnız, `t()` sarıldıkça
`en.json`'a eklenir. PyInstaller `cctv_simulator/` dizinini komple veri olarak
aldığı için `locale/*.json` otomatik paketlenir.

## Görüş alanı / kapsama mühendislik raporu

`exporters.export_engineering_report_pdf` / `_csv` — `map_3d_window` "📄 Mühendislik
Raporu" düğmesi (`_export_engineering_report`) `ViewshedResult` + `CoverageGrid` +
`PerimeterPlanResult`'ı **ASELSAN kurumsal formatında** (aynı navi banner,
`_NumberedCanvas` üst/alt bilgi, gizlilik damgası) tek rapora aktarır. Bölümler:
1 yönetici özeti + **kaynak kutusu** (`is_measured` False → kırmızı "ölçüm değil,
bağlayıcı değil" uyarısı), 2 tekil viewshed (menzil sınırları, görünür/kör alan,
DORI zone alan dökümü), 3 çok kameralı kapsama (DORI seviye %), 4 çevre çiti BOM
+ direk tablosu + boşluklar, karma sabit+PTZ (`mixed=`, `_mixed_rows`: DORI
seviyesi başına sürekli/aralıklı alan, revizit, tespit→teşhis), son bölüm standart
referansları. `_viewshed_rows` / `_coverage_rows` / `_perimeter_rows` /
`_mixed_rows` hem PDF hem CSV'yi besler. ReportLab yoksa
`write_simple_pdf`'e düşer. Emoji glyph YOK (Segoe UI'de tofu) — "UYARI —" /
"DOĞRULANDI —" metin işaretçileri. Sadece GUI (proje şemasında arazi yok).

## DORI değerleri — tek kaynak

`models.DEFAULT_LEVELS` optik seviyeleri artık **EN 62676-4:2015 DORI** ile
bire bir: İzleme/Monitoring **12,5** · Algılama/Detection **25** ·
Gözlem/Observation **62,5** · Tanıma/Recognition **125** · Teşhis/Identification
**250** px/m. Eskiden Algılama 40 / Gözlem 80 idi (klasik arayüz ile
viewshed/compliance ayrışıyordu). `viewshed_3d.PPM_*`, `compliance_standards.DORI_PPM`
ve `modern_window` aynı beş değeri kullanır — birini değiştirirsen hepsini değiştir,
`optics_golden`'ı yeniden üret. Perimetre hedef PPM'i ayrı bir seçim
(`map_3d_window.PERIMETER_PPM_OPTIONS`): EN Detection 25 + "güçlendirilmiş çit" 40
+ üst seviyeler; etiket→ppm sözlükle çözülür (eski `"40" in metin` kırılgandı).

## Çevre çiti boşluk analizi

`generate_perimeter_plan` artık: (1) her kameraya menzilin ~%55'ine nişan alan
gerçekçi tilt, (2) açık perimetrede son noktaya "uç" direği, (3) keskin köşelere
(>20° dönüş) dışa bakan geniş açı "köşe" guard kamerası (küçük kör nokta),
(4) **gerçek boşluk tespiti** (`_analyse_fence_coverage`): çit çizgisi 2 m'de bir
örneklenir, her nokta yerleşik kameraların yatay konisi + kör nokta + hava-kapalı
menziline göre kapsanıyor mu; ardışık ≥4 m kapsanmayan koşu `FenceGap`.
`coverage_percentage` gerçek orandır (eskiden sabit 100/92.5, `gaps` hiç
dolmuyordu). `map_3d_window` BOM'da "Çit Kapsaması" satırı + haritada kırmızı
boşluk çizgileri + BOM CSV başlığında boşluk listesi. `_analyse_fence_coverage`
`terrain` verilince **DEM görüş hattı** de uygular (kamera gözünden çit noktasına
12 örnek, sırt 0.5 m aşarsa o kamera o noktayı görmüyor).

**Hat modu: tesis / sınır / otoyol.** `generate_perimeter_plan(..., line_mode=,
watch_side=)` üç farklı kamera-yönlendirme sözleşmesi sunar — çizginin *şekli*
değil, kameraların *neyi izlediği* farklı:
- `"facility"` (varsayılan, eski davranış): kapalı/açık halka, her direk hat
  boyunca bir sonraki direğe bakar (çiti tırmanma/kesme için izler), keskin
  köşelerde "köşe" guard kamerası devreye girer.
- `"border"`: her zaman açık çizgi mantığı; direkler hatta **dik**, `watch_side`
  ("left"/"right", çizim yönüne göre sol/sağ el) ile seçilen tek tarafa bakar —
  sınırın "içi" yok, ötesindeki bölge izlenir. Köşe guard'ı yok (bükülme, kapalı
  alanın kenarı değil).
- `"highway"`: direkler hattın **çizildiği yönün tersine** bakar (yaklaşan
  trafiğe karşı, ANPR mantığı) — ters yön isteniyorsa çizgiyi ters yönde çizin.
  Köşe guard'ı yok.

`_pole_heading()` bu üçünü tek yerde çözer; "uç" direği (açık perimetre) de
facility'de "son bacağa geri bak", diğer ikisinde normal direklerle aynı kuralı
kullanır. `_analyse_fence_coverage`/`compute_coverage_grid` değişmedi — ikisi de
zaten yerleşik `pan_deg`/`hfov_deg`/`vfov_deg` üzerinden çalışıyor, hangi
sözleşmeyle konulduklarını bilmelerine gerek yok.

`map_3d_window` "Çevre Çiti / Sınır / Otoyol" sekmesinde "🧭 Hat Modu" grubu:
mod seçilince (facility dışı) `fence_closed_var` otomatik kapatılır (kullanıcı
yine de kapalı halka seçebilir — örn. bir enklav sınırı). `export_terrain_state`/
`import_terrain_state` `line_mode`/`watch_side`'ı da taşır. BOM CSV'de "# Hat
Modu" satırı.

## Açık işler

- Klasik `main_window` DORI tablosu hâlâ berrak hava (bilinçli — DORI berrak-hava
  standardı; atmosfer 3B/harita/tezgâh pencerelerinde). `k` orada var.
- Occlusion: `march_rays` ≤0.5 hücre adım + bilinear yükseklik **ve** iki
  örnek arasında ışının kestiği düğüm çizgisi noktaları (`occluder_crossings`,
  vars. açık). Bilinear yüzey yalnız düğüm çizgilerinde kırılır; orada
  yükseklik iki düğüm arasında doğrusal = kesin. Eskiden tek hücrelik
  sırt/duvar eğik ışında %25'e kadar alçak okunuyordu (`test_thin_ridge`:
  duvar arkasında 219 sızan örnek → 0). Örneğin kendisiyle çakışan kesişim
  sayılmaz (yoksa 1e-15 yuvarlama düzlemi kendinden gizliyordu). Ön eleme:
  hücre köşe maksimumundan üst sınır açısı o ana kadarki ufku 5 cm
  (`_CROSSING_TOL_M`) aşmıyorsa kesişim hesaplanmaz. Maliyet: tekil viewshed
  ~+3 ms, direk raster'ı ~2× (sivri arazide). `compute_coverage_grid` (≤1
  hücre, `n_samp` tavanı 90) bu düzeltmeyi almadı; otorite tekil
  `calculate_3d_viewshed`.
- Yerel GeoTIFF arazisi yerel çerçevede 0,0'dan başlar; gerçek konum
  `TerrainData.georef`'te (KML / DXF onu kullanır). Izgara yakınsaması
  (UTM'de birkaç onda bir derece) yok sayılır.
- Gece modeli IR huzme açısını (geniş uçta FOV'dan dar olabilir) ve hedef
  yansıtıcılığını modellemez; IR menzili tüm DORI seviyelerini aynı yerde keser.
- Şartname görev sözlüğü (`_TASK_TR`) kısaltıldı; nadir eş anlamlılar
  ("gözetim", "seçme") kaçabilir — few-shot LLM yolu yakalar.

### Çoklu kamera viewshed + PTZ

**Ayrı sekme yok — kamera tipine göre otomatik.** `database.is_ptz_camera(model)`
kamera `camera_type`'ını (DB'deki "Kamera tipi" combobox: Sabit / PTZ / Speed
Dome …) + model adı + broşür özetini "ptz / speed dome / sürekli optik zoom"
için tarar; açık `is_ptz` alanı varsa o kazanır. `CameraConfig.camera_type`
alanı taşır. `map_3d_window`:
- **PTZ kamera seçilince** tekil sekmede **inline PTZ paneli**
  (`_build_ptz_panel` / `_update_ptz_panel_visibility`, `_on_camera_model_changed`
  toggle eder) belirir. "Mod" combobox'ı (`_PTZ_MODES`, `_on_ptz_mode_changed`
  ilgili çerçeveyi gösterir, eski modun sonucunu siler):
  - *Preset turu*: kaydırıcıları bakışa getir → "＋ Bu bakışı preset ekle" →
    "Turu Çöz" → `evaluate_ptz_tour` (`_ptz_layer = "tour"`).
  - *Sektör devriyesi* / *360° dönüş*: tilt/zoom yukarıdaki kaydırıcılardan,
    pan aralığı/hız/uç bekleme/en kısa bakış alanlardan → `_run_ptz_sweep` →
    `evaluate_sweep` (`_ptz_layer = "sweep"`; yalnız bakış atılan hücre gri).
  - *Tespit → Teşhis süresi*: hedef seviye, slew/zoom/oturma, "yalnız sabit
    kameraların tespit ettiği yerler" → `_run_cue` → `cue_response`
    (+ `detect_then_identify`); sonuç satırında medyan/%90/en kötü süre ve
    en sık ulaşılamama nedeni.
  Sweep ve cue `_run_in_background` ile worker thread'de (`after(50)` poll,
  hata `str(exc)` olarak kopyalanır, `_bg_busy` ikinci isteği yutar,
  yok edilmiş pencere sessiz çıkış). Sabit kamerada panel gizli.
- **Çevre çiti sekmesinde "🎯 Birleşik Viewshed"** paneli
  (`_build_combined_panel`): `_placements_from_perimeter` → `_run_multi_viewshed`
  → katman radyoları (`flow_layout`): DORI / örtüşme (`seen_count_grid`) /
  revizit (son çalışan tur **veya** tarama, `_ptz_layer`) / **tepki süresi**
  (cue, <3/3–6/6–10/≥10 sn bantları) / **Sabit+PTZ** (`_fused_coverage` →
  `fuse_coverage`, Algılama seviyesi sürekli yeşil / aralıklı amber; özet
  `mc_overlap_var`). >120 kamerada örnekleme sorar.
- Çevre çiti sekmesinde **"🔄 Karma Dizilim: Sabit + PTZ"** grubu
  (`_build_mixed_ptz_group`): PTZ modeli (kütüphanede `is_ptz_camera`), "her N
  direkte bir", devriye (360° / 180° / 90°) → `distribute_perimeter_cameras`
  planlayıcıya geçirir; "Karma Kapsamayı Hesapla" → `_run_mixed_perimeter`
  (arka plan, `_run_in_background(stat_var=)`) → `_mixed`, `_ptz_layer =
  "mixed"`: Sabit+PTZ / Revizit (PTZ'lerin en küçük boşluğu) / Tepki süresi
  (tespit→teşhis) katmanları. Haritada PTZ: direkte mor halka + teşhis
  erişiminde devriye yayı. BOM CSV ve mühendislik raporunda PTZ satırları
  ("12 PTZ"), `export/import_terrain_state` `mixed_ptz` bloğu.
- Harita DORI lejandı tek sütun, kutusu çizilen yazının `bbox`'ından (eski sabit
  110 px iki sütun >%100 DPI'da üst üste biniyordu); Gözlem satırı eklendi.

Overlay `_draw_multi_overlay_arr` → `_render_map_canvas` 2b bloğunda komposit.
Katman renkleri **tek tabloda**: `_LAYER_BANDS` (mod → başlık + `(lo, hi, RGBA,
lejant)` bantları, `_paint_bands`); haritanın sol üstündeki katman lejandı
(`_draw_layer_legend`, kutusu yazının bbox'ından) aynı tabloyu okur — lejant
haritanın kullanmadığı rengi anlatamaz. Refaktör beş katmanda bit-aynı.
Renkler tokenlardan (`rgba(...)`): kapsama kalitesi **soğuk DORI rampası**
(`dori-identify` … `dori-detect`), sıcak renk yalnız dikkat isteyen bantta
(yedeksiz 1 kamera, 30 sn+ revizit / 6 sn+ tepki, yalnız-PTZ aralıklı).
Tekil kamera DORI bindirmesi, çit ısı haritası ve tekil lejant da aynı
tabloyu/rampayı okur (eskiden üç ayrı sabit RGBA listesi + emojili lejant
vardı). `test_map_dori_layer_is_the_cold_ramp_and_warm_means_attention`.

**Cam bindirmeler** (`components.glass_under(cv, tag, backdrop, store)`):
katman lejandı, DORI lejandı, ölçek, çit bilgi kutusu, kamera etiketi ve 3B
HUD + hedef etiketi. Tk'de alfa yok; kutu, tuvalin altındaki görüntünün
(harita: `_render_map_canvas` sonunda tuval boyutlu `_backdrop`; 3B: render
edilen kare) PIL kırpıntısından bulanıklaştırılıp `glass` tokenıyla
boyanır, 4 px köşe + `glass-edge` kenar. PhotoImage'lar `_glass_photos`'ta,
her render'da sıfırlanır. 3B'de sürükleme karesi (`fast`) bulanıklığı atlar.
Ölçüldü: kare başına ~5 ms.

**Harita çizimi: pürüzsüz + hızlı.** Analiz hücre ızgarasıdır; en yakın
komşu büyütme her hücreyi kare blok yapıyordu (DORI yelpazesi merdiven).
`raster_smooth.smooth_overlay`: bindirmedeki her renk bir sınıf, sınıf
göstergeleri bilinear süper-örneklenir, 0,5 eşdeğer çizgisi sınır olur
(alan en yakın komşuyla aynı, basamak ≤2 px), arka plana geçişte kenar
yumuşar; >12 renk (sürekli rampa) → en yakın komşu, hiçbir şey uydurulmaz;
yalnız dolu hücrelerin kutusu işlenir. Tekil DORI ayrıca `clip_to_wedge` ile
optik koniye kırpılır. Canlı tarama kırpılMAZ (iz ve hafıza koninin
dışında). Bina katmanı kat başına sabit/10 m basamaklı alfa (az sınıf →
yumuşatılabilir). Hız: `_base_map_image` (kabartma + fotoğraf/rampa + bina)
arazi/mod/opaklık/fotoğraf anahtarıyla önbellekli; raster yalnız ekranda
görünen kutusu `resize(box=)` ile örneklenir (8× zumda ~7000 px kare
üretip Tk'ye kırptırıyordu). Ölçüldü: Kızılay fotoğraf+bina 130–180 → 40 ms,
tepelik arazi 8× zum 414 → 28 ms. Sayılar (alan, %) asla bu görüntüden
gelmez. İndirici "Analiz ızgarası" 200 / **400** (vars.) / 600 hücre: DEM
~30 m, ince ızgara rölyef eklemez; bina/ağaç ve gölge sınırlarını
keskinleştirir (400 = 200'ün ~4× işi; viewshed 4 → 11 ms). 3B HUD satırları `components.stack_lines` ile
ölçülen yüksekliğe göre dizilir (sabit ofsetler %150'de üst üste biniyordu);
DORI rozet rengi motorun hex'i değil soğuk rampa (`_dori_badge`: motor
Teşhis'i kırmızı veriyordu — en iyi seviye alarm gibi görünüyordu).
`_create_stat_row` değeri başlığın bıraktığı genişlikte sarar (karma özet iki
uçtan kırpılıyordu, Kural 8); çit bilgi kutusu da yazıdan boyutlanır. Termal
PTZ'de karma "Algılama" alanı Johnson 1,3 px/m tabanıdır
(`MixedPerimeterResult.ptz_detect_floor_ppm`) — arayüz özeti ve rapor bunu
yazar, 25 px/m diye etiketlemez.
`_export_engineering_report` `multi_viewshed` + `ptz` + `mixed` geçirir.

- **PTZ geçiş modeli** artık `_transition_s`: pan/tilt slew + optik zoom süresi
  (`zoom_full_sweep_s` × odak farkı / zoom aralığı) + `settle_s` oturma. Preset
  `focal_mm` ile ara zoom (`calculate_3d_viewshed(focal_mm_override=)`).
  İvme profili isteğe bağlı (`slew_accel_deg_s2`, yamuk/üçgen hız; `None` =
  eski sabit hız, bit-aynı). Hâlâ yok: alarm-tetikli slew, operatör müdahalesi
  (revizit sabit döngü varsayar).
- **Perf.** `calculate_multi_camera_viewshed` her kamera için `calculate_3d_
  viewshed(with_profile=False)` (kesit atlanır). Üç bit-aynı hızlandırma
  (`test_perf_paths_are_bit_identical`, `test_range_window_keeps_the_whole_cone`):
  (1) analitik koni yalnız kameranın **menzil penceresinde** (eskiden her
  kamera tüm araziyi tarıyordu: 308 kısa menzilli çit kamerası 0,95 → 0,32 sn);
  (2) `march_rays` ışın bloğunu en uzak ızgara çıkışında keser
  (`_steps_inside`; düşük tarafta bir hücre pay — `astype(intp)` sıfıra doğru
  keser); (3) bloklar ~32k örnek (`ray_chunk`, eskiden sabit 512 ışın ≈ 28 MB
  ara dizi, bellek bant genişliğine takılıyordu): 1,6 km direk haritası
  97 → 58 ms. 51 termal PTZ'li karma çit 6,8 → 2,5 sn. İş parçacığı denendi:
  bellek sınırlı, 4 iş parçacığında yalnız ~1,5×, eklenmedi.
- **İki çok-kamera motoru ayrı ama ölçülerek uyumlu.** `compute_coverage_grid`
  (çit bandı, kendi ince grid'i, 4 m) ve `calculate_multi_camera_viewshed`
  (arazi grid'i). Isı haritasını motora bağlamak **denendi ve reddedildi**:
  düz zeminde ikisi hücre küçüldükçe aynı alana yakınsıyor (0,5 m'de 328 m²),
  fark ayrıklaştırma — motor 10 m arazi hücresinde 20 m'lik konileri kaba
  sayıyor; bağlamak ısı haritasını 2,5× kabalaştırıp ~50× yavaşlatırdı. Gerçek
  ayrışma iki hatadan geliyordu, ikisi de düzeltildi: motorun basamak
  artefaktı (yukarıda) ve ısı haritası kutusunun koruma bandını kesmesi
  (25 m kenar < 40 m bant → paydanın beşte biri düşüyor, Algılama %52,8 vs
  %41,8). Artık kenar ≥ bant + hücre; `test_coverage_grid_matches_the_viewshed_
  engine_on_hills` iki motoru aynı bantta ±1,5 puanda tutar. Isı haritası
  arazi engelini artık her kamera sayısında uygular (eskiden >250 kamerada
  sessizce düşüyordu; 970 kamerada ~0,45 sn).
- **Placement doğrulama** var: `viewshed_3d.placement_bounds_warnings` arazi
  sınırı dışı yerleşimi uyarır (GUI dialog + CLI stderr). `terrain.grid` proje
  şemasında (prosedürel çözünürlük). Başsız `geotiff` yolu hâlâ test edilmedi.
- CLI görüş raporu başlığı: viewshed → ilk placement kamerası, PTZ → tur
  kamerası; placement'lar birden çok model kullanırsa stderr'e not.

### i18n (yeni — eksikler)

- **Kapsam çok dar.** ~15-20 metin sarılı (yalnız `main_window` + `map_3d_window`);
  8 UI modülünün 6'sında hiç `t()` yok. Gövde metinlerinin ~%98'i Türkçe-yalnız.
- Dil değişince canlı yeniden çizim yok (yeniden başlatma gerekir).
- ASELSAN PDF raporu, CLI mesajları, `errors.py`, `compliance*`, `exporters`
  hepsi sabit Türkçe.
- Sayı/tarih biçimi yerelleştirilmiyor (ondalık virgül/nokta).
- `en.json` çevirileri gözden geçirilmedi (makine kalitesi, alan uzmanı değil).

### Yerel model fine-tune (yeni — eksikler)

- `finetune_compliance._train` test edilmiyor (GPU + unsloth gerekir); yalnız
  `--prepare-only` yolu koşuyor.
- **Gold veri seti üretme aracı yok.** `eval_compliance.py` gold JSONL bekliyor
  ama EKAP şartnamesi + öğretmen model + insan düzeltme hattı için script yok
  (docs anlatıyor, kod yok).
- `compliance_eval` yalnız DORI ister + matris durumu ölçüyor;
  `ambiguities` / `clarification_questions` / `camera_scores` / `recommendation`
  puanlanmıyor. Matris doğruluğu tam `requirement_id` eşleşmesi istiyor — model
  isterleri yeniden numaralandırırsa 0 alır.
- RAG hâlâ yok (`docs/yerel-model-egitimi.md`: ⏳).

## cctv_iq — görüntü kalitesi ölçüm çekirdeği

`cctv_simulator/cctv_iq.py`. Başsız, numpy; dosya okumak için Pillow.

    py -3.13 -m cctv_simulator.cctv_iq edge.png --json
    py -3.13 -m cctv_simulator.cctv_iq edge.png --roi 120,80,220,300 --nominal-mp 8

Eğik kenar (ISO 12233) MTF: near-vertical/horizontal yüksek kontrastlı bir kenar
fotoğrafından ESF → LSF (Hamming) → FFT → MTF. `k = MTF50 frekansı / Nyquist
(0.5 cy/px)`, `(0, 1]` aralığına kırpılır. Etkin çizgi = nominal × k, etkin
MP = nominal × k².

`k` değerini `CameraConfig.effective_px_ratio` olarak geri besle:
`calculate_for_camera` `res_w`'yi `nominal × px_ratio` yapar. **FOV optik, değişmez;
yalnız PPM/menzil ölçekler.** `px_ratio = 1.0` (varsayılan) çıktıyı bit-aynı
bırakır — optics golden testi bunu doğrular. `OpticResult` artık
`nominal_res_width_px` ve `effective_px_ratio` taşır; `res_width_px` **etkin**
değerdir (motor yeniden ifadesi `ppm_at_distance` ve `modern_window` ile tutarlı
kalsın diye).

`synthetic_edge()` test/kalibrasyon için analitik erf kenarı üretir (scipy yok).

**GUI girişleri:** `modern_window` "Ölçülen Kalite" bölümü (`k` slider + "Eğik
kenar → k ölç" `cctv_iq.measure_file` + "etiket X MP → etkin Y MP"); `main_window`
kamera sekmesinde "Etkin piksel oranı k" alanı + aynı ölç düğmesi
(`_measure_edge_k`); kamera veritabanında `effective_px_ratio` alanı. Üçü de
`apply_camera_model` / `_apply_library_model` / `seed_from` ile kütüphaneden okur.
`main_window` `_read_float` min'i **0.05** (1.0 değil — o minimum olurdu).

## Atmosferik zayıflama (`atmosphere.py`)

Johnson/NATO ve DORI menzilleri berrak hava varsayar. `atmosphere` Koschmieder
(`σ_vis = 3.912 / V`) + banda göre faktör (`visible` 1.0, `nir` 0.85, `mwir`
0.55, `lwir` 0.40; damla rejiminde — kar/sis/kuvvetli yağmur — termal ×2.2).

- `transmittance(dist, vis_km, band, weather)` — yol iletimi (0,1].
- `usable_range_m(clear, ...)` — berrak menzili iletim `tau_min`'e düştüğü
  mesafeyle sınırlar.
- `band_for_camera(sensor, model, night_ir=)` — banda çözer.

`view_3d_window`'da "Hava" combobox'ı `WEATHER_PRESETS`'ten görüş mesafesi seçer
→ `render_camera_frame(visibility_km, weather)`. Termal palette hedef ΔT'yi
`grad + (hf-grad)·tau` ile söndürür; IR luma kazancı `×tau`; gündüz sahnesi
`vis < 20 km` altında pusa harmanlanır. HUD "iletim %N" gösterir, `tau < 0.30`'da
DORI rozeti kırmızı + "⚠️ ATMOSFER".

`calculate_3d_viewshed` ve `perimeter_planner.calculate_optimal_spacing` de
`visibility_km` / `weather` alır: `usable_range_m` optik menzili berrak havadaki
değerin altına indirir, `effective_max_range` bununla kapanır. İkisi de artık
`camera.effective_px_ratio`'yu `res_w`'ye uyguluyor. `map_3d_window` "Hava
(Görüş)" combobox'ı hem tekil viewshed'i hem perimetre dizilimini besler;
`modern_window` tezgâhında da "Hava" seçici var (verdict + ppm kartında
"atm. menzil" notu).

**Viewshed vektörel.** Eski `for ray: for step:` çift döngüsü (200×200 grid'de
~50–150 ms) tek (R,S) numpy bloğuna çevrildi (~4 ms). Ufuk açısı
`np.maximum.accumulate` ile kümülatif; ışın grid'den çıkınca
`np.logical_and.accumulate` durdurur; hücreye scatter ray-major sırada
(son yazan kazanır — eski döngüyle aynı). PPM/slant `math.hypot` → `np.hypot`
yüzünden ~1 ULP oynar.

**Işın yüksekliği bilinear.** `march_rays` örnek noktasının yüksekliğini
`z_grid`'den **bilinear** okur (düğüm kuralı, `get_elevation_at` ile aynı);
hücre ataması (`rr/cc`) değişmedi. En yakın hücre okuması her yamacı basamağa
çeviriyordu: kameradan uzaklaşan inişte her basamağın uzak ucu bir sonrakinin
başını örtüyor, kusursuz %8 düzlemsel yamacın %18–45'i "arazi engeli"
çıkıyordu (düzlem üstünden bakana kendini asla örtmez). Analitik referans:
`test_planar_down_slope_never_hides_itself` (2/5/10 m, %5–15). Bu düzeltme
tekil/birleşik viewshed, direk haritası, PTZ tarama ve tespit→teşhis
sonuçlarının hepsini yukarı çekti (2×2 km tepelik arazi, yalnız-PTZ
algılama: T5 268,7 → 299,7 ha). Maliyet: tekil viewshed ~6,7 → 8,5 ms, 1,5 km
direk haritası 55 → 110 ms (bilinear 4 toplama; float32 yalnız %13 kazandırdı,
hassasiyet için float64).

**Dikey FOV grid'de uygulanır.** `tilt_deg` eskiden yalnız kesit profilini
etkiliyordu; grid "araziyi görüyor mu + yatay FOV + piksel" hesaplıyordu, dik
açılı kamera bile uzağı kapsıyor gösteriyordu. Artık `elev_angle = arctan(dz/d)`
alt/üst ışın (`tilt ± vfov/2`) arasında değilse hücre **çerçeve dışı** (occlusion
DEĞİL — `ZONE_OUT_OF_FOV`). Terrain occlusion ufku fiziksel kalır (çerçeve dışı
sırt da engeller → `tan_for_max` `valid & ppm_ok` üzerinden). Analitik `in_fov_cone`
de dikey sınırlanır ki `coverage_pct` "çerçevelenebilir alanın yüzdesi" olsun.
`min_detect_ppm` tabanı 20 → **`PPM_DETECT` (25, EN 62676-4)**. `num_rays` uzak
kenarda ışın aralığı < 0.6 hücre olacak şekilde artırılır (yoksa `coverage_pct`
düşük okur), 2200 tavan.

## Çok kameralı birleşik kapsama (`perimeter_planner.compute_coverage_grid`)

Plan kutusu üzerinde grid; her hücre için **herhangi bir** yerleşik kameradan
ulaşılan en iyi px/m (yatay FOV konisi + **dikey FOV** `tilt ± vfov/2` + kör nokta
+ optik/atmosferik menzil). Dikey sınır önemli: sabit `tilt=-14°` yerine
`generate_perimeter_plan` artık optik ekseni ≈ menzilin %55'ine nişan alan
gerçekçi tilt hesaplar; tele lensle -1..-2°, geniş açıyla daha dik.
`terrain` verilirse **DEM üzerinden görüş hattı** (sırt engellemesi) de uygulanır:
kamera gözünden hücre zeminine düz çizgi, `n_samp` (5–12, kamera sayısına göre)
noktada `_sample_terrain` vektörel bilinear ile DEM örneklenir, herhangi biri
çizgiyi 0.5 m aşarsa hücre o kamera için kapalı. `occlusion_applied` bayrağı.
Payda = çitin ±40 m bandı (`_fence_band_mask`), böylece kötü hava korunan-bölge
kapsamasını **düşürür** (pastayı küçültmez). `map_3d_window` "Kapsama ısı
haritası" onay kutusu → DORI renkli yarı saydam PIL overlay (`_draw_coverage_overlay`,
grid satır 0 = güney → `np.flipud`) + seviye yüzdeleri + "arazi engeli dahil".

## Güneş / parlama (`solar.py`)

NOAA düşük-hassasiyet algoritması (`sun_position(lat, lon, utc) -> (az, el)`).
Naif datetime = UTC. `utc_offset_for_lon(lon)` = `round(lon/15)` (DST/sınır yok).
`worst_glare_over_day` yerel takvim gününü tarar, **yerel naif** datetime döner
(BOM sütunları `~HH:MM`, UTC değil). `assess_glare(view_az, hfov, sun_az, sun_el) -> (seviye, not)`:
güneş ufkun altında → "yok"; kadraj dışında → "düşük"; alçak güneş (≤12°) kadrajda
→ "yüksek" (arkadan ışık). `worst_glare_over_day` bütün UTC gününü tarar. **Ekinoksa yakın tarihler D/B bakan
kameralar için en kötü** (gündoğumu/batımı azimutu tam doğu/batı).

`map_3d` perimetre sekmesinde "☀️ Güneş / Parlama" bölümü: tarih girişi +
"Direk Bazında Parlama Analizi" → seçili günde direk başına en kötü seviye,
seviye başına direk sayısı. `_export_perimeter_bom` `lat_center/lon_center` varsa
"Parlama <seçili gün>" + "En kötü (yaz g.dönümü)" sütunları ekler (`~HH:MMZ`).

## 3B Kamera Bakış Açısı (`view_3d_window` + `scene_render`)

Sahne artık Tk primitifi değil, **PIL karesi**. `scene_render.render_camera_frame`
gökyüzü/zemin gradyanı + DORI bantları + silüet hedefi çizer, sonra:

- **Gerçek FOV** — 60 m'deki insan gerçekten ~15 px, uydurma detay kademesi yok.
- **MTF bulanıklığı** = `CameraConfig.effective_px_ratio` (`k`, cctv_iq).
- **Palet** — gündüz vinyet / gece IR (mesafeye göre kazanç düşüşü + gürültü +
  tarama çizgisi) / termal LUT (ironbow 256-girişli tablo).
- **Dijital zoom insetti** (sağ alt) — hedefe kırpıp büyütür; `_degrade` burada
  koşar (BOX küçültme = gerçek alçak geçiren, NEAREST büyütme + blok blur),
  menzilde sensör piksel bütçesi (`ppm`) görünür olur. Sahne-içi "hayalet
  figürler" denendi, zoom ile çakıştı, inset lehine bırakıldı.

Kare **düşük çözünürlükte** render edilir (720 px, `fast=True` sürüklerken 430)
ve ölçeklenir; sonra `engine.set_viewport_size(out_w, out_h)` ile geri alınır ki
`view_3d_window`'un tuval üstü çizdiği **net** ızgara/etiket/HUD hizalansın.
`schedule_render(fast=True)` sürüklerken ucuz kare + `_SETTLE_MS=180` sonra tam
kalite. Ölçüldü: hızlı ~25 ms, tam ~45 ms.

`ImageTk.PhotoImage(frame, master=self.canvas)` — `master=` zorunlu (Kural 5),
`self._frame_photo` referansı GC'yi önler.

## Çözülmüş (geçmiş açık işler)

- `clear_tile_cache()` artık `map_3d_window` "🗑️ Önbelleği Temizle" düğmesine bağlı;
  yanında `tile_cache_usage()` ile kare sayısı + MB gösteriliyor.
- Canlı karo sunucuları (esri / osm / opentopo / terrarium) gerçek ağda doğrulandı:
  tek karo + tam mozaik + Terrarium DEM (`is_measured=True`) çalışıyor.
- `is_measured` bayrağı perimetre BOM CSV'sine taşındı (başlık satırları + uyarı).
- `project-skills.zip` içindeki export skill'leri (`docx/pdf/xlsx/pptx`) kullanıcı
  seviyesine (`~/.claude/skills/`) açıldı.
