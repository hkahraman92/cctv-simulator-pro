# Açık İşler — Kod İncelemesi (2026-09-17)

Aşağıdaki liste tüm `cctv_simulator/` kod tabanının (15.617 satır, 30+ modül) beş
paralel derin incelemeyle taranması ve en kritik bulguların bizzat kodda
doğrulanmasıyla çıkarıldı. "**Doğrulandı**" işaretli bulgular satır satır okunup
tetikleyici senaryo ile teyit edildi. "Şüpheli" işaretliler incelemeyi yapan
ajanın güçlü emare bulduğu ama ek doğrulama gerektiren noktalar — ele alırken
önce tekrar üretmeyi deneyin.

## 1. Kritik buglar (doğrulandı) — ✅ 5/5 DÜZELTİLDİ (2026-09-17)

Beşi de düzeltildi ve doğrulandı: golden optik test (`tests/data/optics_golden.json`)
yeniden üretildi (yalnızca dik-aşağı-bakan 2/150 senaryo değişti — 1.2'nin tam
izole bir düzeltme olduğunun kanıtı), tam `pytest` paketi + `ruff` yeşil, 1.1
için ayrıca uçtan uca bir entegrasyon testi (GUI'de çit + PTZ turu kur → kaydet
→ `project_io` round-trip → başsız CLI'nin `--viewshed`/`--ptz` yollarının
gerçekten çalıştığını doğrula → yeni pencerede geri yükle) elle koşturuldu.

### 1.1 GUI'de kurulan arazi/çit/PTZ planı proje dosyasına hiç yazılmıyor — ✅ düzeltildi
**`ui/main_window.py:1599-1612` (`save_project`) vs `project_io.py:87-111`**

`project_io.save_project` (şema sürüm 2.0) `terrain` bloğunu (`source/preset/
placements/ptz/...`) JSON'a yazıyor ve başsız CLI'nin `--viewshed`/`--ptz`
yolları tamamen bu bloğa bağımlı (`__main__.py`: blok yoksa `ValueError`).
Ama klasik arayüzün "Projeyi Kaydet" butonunun çağırdığı
`main_window.save_project` **kendi elle kurduğu `dict`'i** yazıyor ve içinde
`"terrain"` anahtarı hiç yok — `project_io.save_project`'i de çağırmıyor.
`ui/map_3d_window.py` içinde de placements/PTZ'yi diske yazan **hiçbir kod
bulunmuyor** (grep: sıfır eşleşme).

**Tetikleyici senaryo:** Kullanıcı "Arazi ve Harita" penceresinde çevre çiti +
kamera dizilimi + PTZ turu kurar, ana pencerede "Projeyi Kaydet"e basar →
arazi verisi **sessizce kaybolur**. Aynı dosyayı sonra
`py -3.13 -m cctv_simulator --project plan.json --viewshed` ile açmaya
çalışırsa her zaman "terrain.placements listesi gerekli" hatası alır.
CLAUDE.md'nin kendi koyduğu "şema değişirse üç yeri de güncelle" kuralının
tam da bahsettiği senaryo ama kurallardan biri hiç uygulanmamış.

**Öneri:** `main_window.save_project`/`load_project`'i `project_io.save_project`/
`load_project`'i sarmalayacak şekilde yeniden yaz (ya da en azından
`map_3d_window`'daki terrain/placements/ptz state'ini oradan okuyup dict'e
ekle). Bir de proje yükleme/kaydetme roundtrip testine terrain alanı ekleyin
(`test_cli_headless.py` şu an GUI kaydını hiç kullanmıyor, bu yüzden kaçmış).

**Yapılan düzeltme:** `map_3d_window.TerrainViewshedWindow`'a
`export_terrain_state()`/`import_terrain_state()` eklendi (tekil viewshed
sliderları, çevre çiti `fence_points`/perimeter plan, ≥2 presetli PTZ turu
→ `project_io` terrain şemasına eş `placements`/`ptz` sözlükleri). Kamera
map penceresinin kendi kütüphanesinden seçildiği ve ana pencerenin kamera
listesinde olmayabileceği için, `save_project` referans verilen kamerayı
gerekirse kaydedilen listeye ekliyor. `main_window.save_project` artık açık
`viewshed_window` varsa bu bloğu `data["terrain"]`'e yazıyor;
`load_project` bloğu `self._pending_terrain_state`'e alıyor,
`open_terrain_viewshed` bir sonraki açılışta `TerrainViewshedWindow`'a
`terrain_state=` olarak geçiriyor (pencere kendi sekmesini/planını/PTZ
presetlerini otomatik geri kuruyor). Hem `project_io` round-trip'i hem
CLI'nin `_run_viewshed`/`_run_ptz` yollarının gerçek çıktı ürettiği hem de
geri-yükleme sonrası pencerenin doğru sekmeye geçip planı yeniden
oluşturduğu bir Tk entegrasyon scriptiyle doğrulandı.

### 1.2 Kör nokta sentineli ters: dik-aşağı bakan kamera "999 m kör nokta" raporluyor — ✅ düzeltildi
**`calculations.py:128-129`**

```python
if bottom_ray_deg >= 89.9:
    dead_zone_m = 999.0
```

`bottom_ray_deg` 90°'ye yaklaştıkça (kamera neredeyse dik aşağı bakıyor)
`vertical_drop / tan(bottom_ray_deg)` doğal olarak **sıfıra** gider (kamera
tabanını neredeyse hiç kör bırakmaz). Kod bunun yerine dev bir sentinel (999 m)
atıyor — mantık tam ters.

**Tetikleyici senaryo:** Kısa direk + yüksek tilt (ör. giriş kapısı üstü
kamera, `tilt_deg=75`, dar VFOV → `bottom_ray_deg≥89.9`). `dead_zone_area_m2`
ve ölü bölge paneli devasa, hiç var olmayan bir kör alan gösterir;
`analyze_dead_zone_coverage` bu alanı "kapatılması gereken" dev bir bölge
sanıp yanlış öneriler üretir.

**Öneri:** Sentinel'i `0.0` yap (`tan(89.9°)≈573`, sayısal olarak zaten
güvenli — branch'in amacı muhtemelen `tan(90°)` tekilliğini önlemekti, ama
sonucu ters atamışlar).

**Yapılan düzeltme:** `calculations.py:128-131` sentinel `0.0` yapıldı.
`tests/data/optics_golden.json` yeniden üretildi; 150 senaryodan yalnız
dik-tilt'li C051/C060 değişti (`dead_zone_m`/`dead_zone_area_m2` → 0,
önceden "Kör noktada" işaretlenen satırlar artık doğru mesafeleriyle "Aktif").

### 1.3 3B Kamera Bakış Açısı penceresi ölçülen `effective_px_ratio`'yu (k) hiç uygulamıyor — ✅ düzeltildi
**`perspective_3d.py:40-42`**

```python
res_info = RESOLUTIONS.get(camera.resolution_name, (2688, 1520))
self.res_w, self.res_h = res_info
```

`effective_px_ratio` (`cctv_iq` ile eğik-kenar ölçümünden gelen k) yalnız
`calculations.py`, `viewshed_3d.py`, `perimeter_planner.py`,
`compliance_optics.py`, `modern_window.py` içinde uygulanıyor.
`perspective_3d.py` içinde `effective_px_ratio` hiç geçmiyor (grep ile
doğrulandı), `ui/view_3d_window.py` da `Perspective3DEngine`'i doğrudan
nominal çözünürlükle kuruyor.

**Tetikleyici senaryo:** Kullanıcı "Eğik kenar → k ölç" ile k=0.6 ölçtü (lens/
sensör gerçekte etikette yazandan bulanık) → optik tablo, kuşbakışı plan ve
viewshed doğru şekilde düşük PPM/DORI gösterirken, **3B Kamera Bakış Açısı
penceresi hâlâ etiket çözünürlüğüyle** hesaplayıp daha iyimser PPM/DORI rozeti
gösteriyor. Bu, CLAUDE.md Kural 1'in ("optik motor tek doğruluk kaynağıdır")
doğrudan ihlali — iki görünüm ayrışıyor.

**Öneri:** `Perspective3DEngine.__init__`'te `res_w = res_info[0] *
max(getattr(camera, "effective_px_ratio", 1.0), 0.05)` satırını ekle (diğer
modüllerdeki desenle aynı).

**Yapılan düzeltme:** Tam olarak önerilen satır eklendi. Doğrulama:
k=0.5 ölçülmüş bir kamerada `Perspective3DEngine.res_w` nominal değerin
tam yarısı çıkıyor.

### 1.4 Şartname sayı ayrıştırma: Türkçe binlik nokta 1000× küçük okunuyor — ✅ düzeltildi
**`compliance_optics.py:28,32-33`**

```python
_DIST = r"(\d+(?:[.,]\d+)?)\s*(?:metre\w{0,5}|m)(?![a-zğüşıöçA-ZĞÜŞİÖÇ])"
def _num(s: str) -> float:
    return float(s.replace(",", "."))
```

`_num` yalnız ondalık virgülü noktaya çeviriyor; binlik ayırıcı noktayı
temizlemiyor.

**Tetikleyici senaryo:** Şartnamede "1.500 metre menzil" (=1500 m, Türkçe
binlik gösterim) geçerse, regex "1.500" yakalar, `_num("1.500")` →
`float("1.500") = 1.5` metreye dönüşür. DORI/menzil isteri **1000× küçük**
çıkar, optik motor karşısında her kamera kolayca "geçti" der — uzun menzilli
(perimetre/sınır) şartnamelerde sessiz ve tehlikeli bir yanlış-geçirme.

**Öneri:** Sayıyı ayrıştırırken TR biçimini ayırt et: nokta grupları 3
haneliyse binlik (kaldır), virgülden sonrası ondalık. Ya da daha basiti: metre
cümlelerinde binlik nokta nadir kullanıldığından, `\d{1,3}(\.\d{3})+` deseni
ayrı yakalanıp noktalar silinsin, sonra virgül ondalık noktaya çevrilsin.

**Yapılan düzeltme:** Tam bu yaklaşım uygulandı — `_DIST`/`_PPM` regex'leri
artık `\d{1,3}(?:\.\d{3})+(?:,\d+)?` (TR binlik) ile `\d+(?:[.,]\d+)?` (düz)
arasında seçim yapıyor, `_num` binlik-nokta-yalnız deseni ayrı ele alıyor.
Doğrulama: `"1.500 metre" → 1500.0` (önceden 1.5).

### 1.5 Çevre çiti planında kör nokta/BOM hesabı ile gerçek kamera tilt'i tutarsız — ✅ düzeltildi
**`perimeter_planner.py:134` (`calculate_optimal_spacing`) vs `:205-206`
(`generate_perimeter_plan`)**

`calculate_optimal_spacing` içindeki kör nokta/direk aralığı hesabı **sabit
`tilt_deg = 15.0`** varsayıyor. Aynı fonksiyonu çağıran `generate_perimeter_plan`
ise gerçek kamerayı `default_tilt = -max(1.5, min(25.0,
atan(mast_height_m/aim_dist)))` ile, menzilin ~%55'ine nişan alacak şekilde
konuşlandırıyor (tele lensle ~1-2°, geniş açıyla daha dik).

**Tetikleyici senaryo:** Tele lensli kamera seçilince gerçek tilt ~2°'ye iner
ama BOM'daki kör nokta/direk aralığı hâlâ 15° varsayımıyla hesaplanmış olur —
raporlanan kör nokta mesafesi ile sahada gerçekleşen kör nokta birbirini
tutmaz (tele lenste gerçek kör nokta çok daha büyük olabilir, BOM'da olduğundan
küçük görünür).

**Öneri:** `calculate_optimal_spacing`'e gerçek tilt'i parametre olarak geçir
(ya da `generate_perimeter_plan`, aralığı hesapladıktan sonra dead_zone'u
`default_tilt` ile yeniden hesaplasın).

**Yapılan düzeltme:** `calculate_optimal_spacing` içindeki sabit `tilt_deg =
15.0` kaldırıldı; artık `generate_perimeter_plan`'ın kullandığı aynı
"menzilin ~%55'ine nişan al" formülüyle (`aim_dist`/`atan(mast/aim_dist)`,
1.5°-25° kelepçeli) tilt hesaplanıyor — iki fonksiyon artık aynı kamera
tilt'i üzerinden anlaşıyor. Fonksiyonun 3-elemanlı dönüş imzası (dolayısıyla
`tests/test_perimeter.py`'deki mevcut unpacking) değişmedi; ilişkisel
testler (`test_spacing_matches_en62676_slant_formula` vb.) yeşil kaldı.

## 2. Ek bulgular — ✅ 10/11 düzeltildi, 1/11 yanlış alarm (2026-09-17)

Hepsi tek tek kodda doğrulanıp (gerekirse küçük bir repro/monkeypatch ile)
ele alındı. Tam `pytest` + `ruff` yeşil.

1. **`viewshed_3d.py:215` — ✅ düzeltildi.** Ufuk birikimi `tan_for_max =
   np.where(valid & ppm_ok, tan_angle, -1e18)` yalnız çözünürlük eşiğini
   geçen adımları katıyordu; yorum "her geçerli adım" diyordu ama kod
   uymuyordu. `ppm_ok` şartı kaldırıldı (`np.where(valid, ...)`) — yakın-dik
   bir sırt artık düşük çözünürlüklü olsa bile ufuk hesabına giriyor.
2. **`online_map_loader.py:324-336` — ✅ düzeltildi.** `progress_callback`
   iptal için exception fırlattığında artık tüm `futures` üzerinde `.cancel()`
   çağrılıyor (henüz başlamamış indirmeler iptal), sonra yeniden raise
   ediliyor — `with` bloğunun `shutdown(wait=True)`'ı artık yalnız o an
   çalışan (`max_workers` kadar) indirmeyi bekliyor, kuyruktaki yüzlercesini
   değil.
3. **`online_map_loader.py:359-368` — ✅ düzeltildi.** `_download_mosaic`
   başına açık bir doğrulama eklendi: bbox 180° meridyenini kesiyor veya
   sınırların dışındaysa artık sessizce yanlış kırpmak yerine Türkçe
   `RuntimeError` ile yüksek sesle başarısız oluyor (`calculate_bbox`
   `center_lon ± delta_lon`'u hiç kelepçelemiyordu).
4. **`calculations.py` `ground_distance_for_ppm` — ✅ düzeltildi.** Artık
   hesapladığı mesafe `result.dead_zone_m` içine düşüyorsa `0.0` döndürüyor
   — ana tablonun `effective_dist <= dead_zone_m → "Kör noktada"` kuralıyla
   artık aynı fikirde.
5. **`ptz_tour.py:174` (`never_seen_area_m2`) — ❌ yanlış alarm, kod
   değişmedi.** `combined.dori_grid` (`calculate_multi_camera_viewshed`)
   `ZONE_OCCLUDED`'ı tam olarak "en az bir preset'in konisinde ama hiçbirinde
   görünür değil" hücrelerine atıyor — bu da `~covered` (mask==0) ile örtüşen
   ayrı bir küme, aynı küme değil. Duvarlı bir arazide elle doğrulandı:
   `never_seen_area_m2 = 44416.0` (sıfır değil) — metrik canlı ve doğru.
6. **`perimeter_planner.py` `_analyse_fence_coverage._covered` vs
   `compute_coverage_grid` — ✅ düzeltildi.** Boşluk analizine
   `compute_coverage_grid` ile aynı dikey FOV kontrolü eklendi
   (`tilt ± vfov/2` içindeki yükseliş açısı); artık iki fonksiyon aynı
   yerleşik kameralar için aynı "görülüyor mu" tanımını kullanıyor.
7. **`ui/view_3d_window.py:82-101` (`schedule_render`) — ✅ düzeltildi.**
   `_render_job` zaten kuyruktaysa artık erken dönmeden önce
   `_pending_fast = _pending_fast and fast` ile kuyruktaki işi güncelliyor —
   `fast=True` sonrası gelen bir `fast=False` isteği artık kuyruktaki işi tam
   kaliteye yükseltiyor, iptal edilen `_settle_job`'ın yerini sessizce boş
   bırakmıyor.
8. **`ui/main_window.py` `export_pdf` — ✅ düzeltildi.** Diğer `export_*`
   fonksiyonlarındaki `if not path: return` eklendi.
9. **`ui/map_3d_window.py` `_placements_from_perimeter` — ✅ düzeltildi.**
   `max_range_m` artık her direğin kendi `effective_range_m`'i (ilk direğin
   menziline taban olarak dayatılmıyor) — köşe/uç muhafız kameraları
   birleşik viewshed'de artık olduğundan uzun menzilli görünmüyor.
10. **`ui/camera_db_window.py` `save_model` — ✅ düzeltildi.** Yerleşik
    (`DEFAULT_CAMERA_LIBRARY`) bir kamera yeniden adlandırıldığında artık
    kullanıcı JSON'una `eski_isim: null` "mezar taşı" kaydı yazılıyor;
    `database.load_camera_library` bunu görünce eski varsayılan adı
    kütüphaneden düşürüyor (kopya kayıt kalmıyor). Monkeypatch'li testle
    doğrulandı.
11. **`ptz_tour.py` preset maskesi — ✅ düzeltildi.** `np.int64` yerine
    `object` dtype (sınırsız Python int) kullanılıyor; 70 preset'lik bir
    turla elle test edildi, çökme yok, artık pratik bir preset sayısı sınırı
    kalmadı.

## 3. Yenilikçi özellik fikirleri

### Optik / atmosfer / görüntü kalitesi
- **Parlamaya duyarlı tilt/heading önerisi** — `optimize_tilt_calc` şu an
  yalnız PPM/kör nokta/geometri cezalandırıyor. `solar.worst_glare_over_day`
  sonucunu aynı ceza fonksiyonuna katıp "bu yıl en kötü gün arkadan ışık
  riskini en aza indiren" heading/tilt kombinasyonunu otomatik önersin.
- **Kalibrasyon sürüklenmesi izleme** — `cctv_iq` ölçümlerini zaman damgalı
  JSONL'e kaydedip aynı kameranın ardışık ölçümlerinde `k` düşüşünü (lens
  kirlenmesi/odak kayması) tespit edip arayüzde "temizlik/servis zamanı"
  uyarısı versin.
- **İklime dayalı olasılıksal menzil raporu** — `atmosphere.WEATHER_PRESETS`
  dağılımına göre "yılın %X gününde Teşhis menzili ≥ Y m" şeklinde tek-anlık
  yerine istatistiksel kapsama raporu; ASELSAN mühendislik raporuna yeni bölüm
  olarak eklenebilir.

### Arazi / viewshed / harita
- **Zaman-içi viewshed karşılaştırması** — aynı arazi için farklı mevsim/hava
  kombinasyonlarını toplu koşturup "kış sisi vs yaz berrak" kapsama farkını
  tek PDF'te gösteren bir "mevsimsel dayanıklılık" raporu.
- **Otomatik kamera konumu önerisi (boşluk kapatıcı)** — mevcut
  `calculate_multi_camera_viewshed` altyapısını tersine çevirip, arazi + hedef
  poligon verildiğinde boşluk hücrelerini minimize eden aday mast
  konumlarını öneren bir asistan.
- **DEM güven skoru görselleştirmesi** — `is_measured=False` ikili bayrağın
  ötesinde, Terrarium/Open-Elevation kaynaklarının bilinen çözünürlük hatasını
  `ppm_grid` üzerine "belirsizlik payı" (± m) katmanı olarak göstermek.

### Çevre güvenliği / PTZ / şartname
- **PTZ preset sırası optimizasyonu** — tur şu an kullanıcı sırasıyla dönüyor;
  pan açılarına göre TSP-benzeri bir "en kısa slew rotası" çözücü, aynı dwell
  bütçesiyle ortalama revizit süresini düşürsün.
- **Alarm-tetikli preset kesintisi simülasyonu** — "VCA tetiklenirse X
  preset'e Y saniye öncelik ver, sonra tura geri dön" senaryosunu modelleyip
  olay-tetiklemeli operasyonda worst-case revizit'in nasıl değiştiğini
  göstersin (CLAUDE.md bunu "yok" diye not etmişti — somut uygulama önerisi).
- **Sabit kamera + PTZ boşluk-doldurma turu** — `compute_coverage_grid`'in hiç
  görülmeyen hücrelerini otomatik PTZ preset'lerine çeviren bir üretici;
  sabit kameraların kapsamadığı alanları PTZ tur listesine enjekte etsin.

### Arayüz / 3B görünüm
- **Sürüklerken "hayalet" önceki kare + fade** — `schedule_render(fast=True)`
  sırasında eski tam-kalite kareyi düşük opaklıkla altta tutup üstüne yeni
  fast kareyi bindirmek, düşük çözünürlüklü ara kareler arası titremeyi
  azaltır.
- **Yan yana A/B lens karşılaştırma modu** — mevcut lens ile önerilen
  alternatif modelin kamera-gözü karesini split-screen göstermek;
  `update_alternative_models` zaten adayları hesaplıyor, sadece görselleştirme
  eksik.
- **Kuşbakışı plan ↔ 3B pencere çift yönlü hedef sürükleme** — plan üzerinde
  hedefi sürükleyince 3B penceredeki mesafe slider'ı da canlı güncellensin.

### Proje yönetimi / altyapı
- **Proje şeması bütünlük denetleyicisi** — `project_io.load_project`'e (veya
  `--doctor` CLI bayrağına) cameras var ama terrain/placements yok gibi
  tutarsızlıkları erken yakalayan bir tarama eklensin (bkz. madde 1.1).
- **"Araziyi de kaydet" hatırlatıcısı** — `map_3d_window` kapatılırken açık
  bir arazi/PTZ oturumu varsa, kalıcı şema birleştirmesi yapılana kadar en
  azından kullanıcıyı "bu veri kaydedilmeyecek" diye uyarsın.
- **Kamera veritabanında "türetilmiş" (override) kayıt izleme** — varsayılan
  bir modeli düzenlemek üstüne yazmak yerine "X modelinden türetildi" bağlantılı
  yeni bir kayıt oluştursun; kopya-isim sorununu kökten çözer.
