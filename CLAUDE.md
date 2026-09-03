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
  errors.py                          global hata yakalama, log, guarded_build
  calculations.py                    OPTİK MOTOR - tek doğruluk kaynağı
  perspective_3d.py                  3B izdüşüm
  terrain_loader.py                  TerrainData, prosedürel arazi, GeoTIFF/DEM
  viewshed_3d.py                     ışın yürütmeli görüş alanı, dünya eğriliği
  perimeter_planner.py               çevre çiti kamera dizilimi, BOM
  online_map_loader.py               karo indirme, ortofoto mozaik, GERÇEK DEM
  compliance.py                      Gemini + kural tabanlı şartname analizi
  exporters.py                       CSV / XLSX / PDF / PNG
  database.py / config.py / models.py
  ui/main_window.py                  DualViewCCTVDesignApp - klasik arayüz
    ui/canvas_drawer.py              yan kesit + kuş bakışı plan
    ui/view_3d_window.py             3B kamera görüşü
    ui/modern_window.py              EN 62676-4 optik tezgâhı (customtkinter)
    ui/map_3d_window.py              3B arazi, uydu, viewshed, çevre planlayıcı
    ui/spec_assistant.py             uygunluk matrisi
    ui/camera_db_window.py           kamera veritabanı (yönetici parolası)
cctv_optics_workbench.py             optik tezgâhı için bağımsız başlatıcı
```

## Kurallar

**1. Optik motor tek doğruluk kaynağıdır.**
`calculations.calculate_for_camera()` ve `ppm_at_distance()` fiziği sahiplenir.
Arayüz çizer, fizik türetmez. Bir görünüm bir PPM eşiği için menzil istiyorsa,
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
ve 3B pencere güncellemesi. **`analyze_dead_zone_coverage`, ölü bölge paneli, hedef
analizi ve `_draw_canvas` her light geçişte yine koşar** — ölü bölge analizi 2.66x
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

## Arazi ve çevrimiçi harita

**Uydurma rölyefi ölçüm gibi sunma.** Viewshed, arazi engellemesinden ibarettir;
uydurma sırt, güven veren yanlış bir kapsama raporu üretir. `TerrainData.is_measured`
yalnız gerçek DEM'de `True` (GeoTIFF, Terrarium karoları, yükselti API'si).
Prosedürel önayarlar, gri tonlamalı heightmap ve son çare arazi `False` ve arayüz
`showinfo` yerine `showwarning` açar. Bu bayrağı dışa aktarımlara da taşı.

**Yön.** `TerrainData.z_grid` satır 0 = **güney** (artan +Y kuzey). Mozaik satır 0 =
kuzey. Render `np.flipud` yapar. DEM'i `z_grid`'e yazarken `flipud` şart, yoksa arazi
kuzey-güney aynalanır.

**Başarısız indirme yüksek sesle başarısız olur.** Sessizce düşen karolar düz gri bir
mozaik bırakıyor, arayüz de bunu "başarıyla indirildi" diye raporluyordu.
`_download_mosaic` %70 altına düşerse `RuntimeError` atar.

**Karo sunucusu nezaketi.** Gerçek User-Agent (tarayıcı taklidi OSM politikasına
aykırı ve IP bloklatır), OSM/OpenTopo için `max_workers=2`, disk önbelleği
(`%APPDATA%\<uygulama>\tile-cache`), `MAX_TILES=400` bütçesi, kaynağa göre atıf.
Esri uç noktası `{z}/{y}/{x}`; OSM ve OpenTopoMap `{z}/{x}/{y}` — karıştırma.

**Kırpma global Mercator piksel uzayında.** Enlemde doğrusal interpolasyon yanlış;
Mercator Y = `asinh(tan(lat))`.

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
dizinde. Sorun çıkarsa önce `cctv_simulator_minimal.spec` (sıfır Tcl/Tk müdahalesi,
`console=True`) dene — PyInstaller sağlıklı bir yorumlayıcıyla tkinter'ı kendi
başına halleder. Üç arıza da gerçekten yaşandı:

1. **`No module named 'tkinter'`** — derleyen yorumlayıcıda/venv'de tkinter yok.
2. **`Can't find a usable init.tcl`** — `TCL_LIBRARY` kurulmamış. PyInstaller'ın kendi
   tkinter runtime hook'u yalnız `tkinter` modül grafiğindeyse devreye girer.
3. **`invalid command name "::msgcat::mcmset"`** — `msgcat` bir Tcl Modülü:
   `tcl8/<sürüm>/msgcat-*.tm`. **Linux'ta `tcl8/` dizini `tcl8.6/` içinde, Windows'ta
   kardeşi.** Dört tur boyunca Linux'ta yeniden üretilememesinin sebebi buydu.
   Kaynaktan çalışırken çözümü: `configure_tk_paths()` `theme.py`'nin **en üstünde**,
   `ttkbootstrap` import edilmeden önce.

Her derleme `build/tkinter-diagnostic.txt` yazar. Tahmin etmeden önce onu oku.

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

## Açık işler

- `clear_tile_cache()` var ama hiçbir butona bağlı değil; önbellek sınırsız büyüyor.
- Canlı karo sunucusu istekleri hiç denenmedi (geliştirme konteynerinde dış ağ
  kapalıydı). URL şablonları ve eşzamanlılık kâğıt üstünde doğru.
- Yol haritası kalanı: `cctv_iq` ölçüm çekirdeği (numpy, başsız, eğik kenar MTF,
  JSON çıktı), `CameraConfig.effective_px_ratio`'nun `calculate_for_camera` içinden
  akması, tezgâhta ölçülen ve etiket değerinin yan yana gösterilmesi.
  MTF50/Nyquist oranı `k`, etkin MP = nominal × k².
- `project-skills.zip` açılmadı. İçindekiler `.claude/skills/` altına çıkarılabilir.
