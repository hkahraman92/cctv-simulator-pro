## -9. Kullanıcı bildirimi: VFOV her yerde yanlış — sensör tablosu sabit 4:3 varsayıyordu — ✅ düzeltildi (2026-09-18)

Kullanıcı, PDF'teki kamera matrisinde "HFOV / VFOV" sütununu inceleyip
şüphelendi: *"oranı 16:9 olmuyor sanki bir sorun olabilir mi hesaplamada"*
(1/2.8″ sensör, 3.2mm odak, 2MP/1080p kamera için HFOV=82.4° / VFOV=66.5°).

**Kök neden:** `config.SENSOR_DIMS_MM` her `"1/x\""` sensör format etiketini
**sabit 4:3 fiziksel oranla** kodluyor (örn. `1/2.8"` → 5.60×4.20mm,
h/w=0,75 — eski analog yayın tüpü format geleneği). Ama bu isimle satılan
gerçek CCTV/IP sensörlerin neredeyse tamamı **16:9** (1920×1080, 2688×1520,
3840×2160 gibi çözünürlüklerle satılıyorlar). VFOV bu sabit 4:3 yükseklikten
hesaplanıyordu, seçilen çözünürlüğün gerçek piksel en-boy oranı hiç dikkate
alınmıyordu — ve `CameraConfig`'in varsayılan çözünürlüğü bile
("4 MP 2K 2688×1520", oran 1,77) 4:3 değil, yani neredeyse HİÇBİR gerçekçi
kurulum doğru VFOV almıyordu.

Bu yalnız PDF'teki bir görüntü sorunu değildi — VFOV, kör nokta
(`dead_zone_m`), geometrik menzil (`max_geom_dist_m`), çevre çiti
kamera tilt'i/aralığı, 3B kamera görünümü ve arazi görüş alanının dikey
konisi dahil **her yerde** bu hatalı sabit VFOV'u kullanıyordu.

**Düzeltme:** `config.sensor_height_mm(sensor_w_mm, res_w_px, res_h_px,
fallback_h_mm)` eklendi — sensör yüksekliğini artık sabit tablodan değil,
sensör genişliği × seçilen çözünürlüğün piksel en-boy oranından türetiyor.
Beş VFOV hesaplama noktası (`calculations.py`, `perimeter_planner.py` ×2,
`perspective_3d.py`, `viewshed_3d.py`) bu ortak fonksiyona geçirildi; her
birinde `effective_px_ratio` (yatay MTF düzeltmesi) uygulanmadan ÖNCEKİ
ham çözünürlük oranı kullanıldığı doğrulandı (px_ratio yatay bir düzeltme,
fiziksel sensör oranını değiştirmemeli).

`optics_golden.json` yeniden üretildi: 150 senaryodan **96'sı** değişti
(hepsi 16:9/non-4:3 çözünürlük kullananlar — HFOV hep aynı kaldı, yalnız
VFOV ve ondan türeyen alanlar değişti), **54'ü** bit-aynı kaldı (gerçek 4:3
çözünürlük seçenekleri: 5MP, 12MP). Diff elle örneklendi, beklenen desene
tam uydu. `test_perimeter.py`'deki köşe-guard kapsama eşiği (≥95,0 → ≥94,0)
küçük ve beklenen bir düşüşü (94,7 ölçüldü) yansıtacak şekilde güncellendi —
daha dar/doğru VFOV, köşelerde biraz daha az dikey kapsama demek, bu gerçek
fizik, test hatası değil.

Kullanıcının örneğiyle elle doğrulandı: 1/2.8″, 3,2mm, 1080p → HFOV 82,4°
(değişmedi), VFOV 66,5° → **52,4°** (doğru). Tam `pytest` (159) + `ruff
check` yeşil.

## -8. PDF kurumsal marka metni + rapor imzası güncellendi (2026-09-18)

Kullanıcı isteği: banner/başlık/altbilgideki uzun ASELSAN ünvan metinleri
kısaltılsın (*"ASELSAN - UGES" olarak geçsin*) ve rapora hazırlayan bilgisi
eklensin (*"Harun KAHRAMAN, Ürün Teknik Yöneticisi"*).

`exporters.py`'deki 4 yer güncellendi — hem `export_pdf` (klasik "PDF
Mühendislik Raporu") hem `export_engineering_report_pdf` (görüş alanı/kapsama
raporu) aynı `_NumberedCanvas` sınıfını paylaştığı için üst bilgi (sayfa > 1
başlık şeridi) + alt bilgi (her sayfa footer'ı) tek bir yerden, ayrıca her
iki fonksiyonun kendi kapak banner'ı ayrı ayrı: "ASELSAN A.Ş. • SAVUNMA VE
GÜVENLİK TEKNOLOJİLERİ SEKTÖR BAŞKANLIĞI" / "ASELSAN ELEKTRO-OPTİK & GÜVENLİK
SİSTEMLERİ" → **"ASELSAN - UGES"**. Her iki raporun sonuna (son bölümden
sonra, `doc.build`'den hemen önce) ince bir ayraç + sağa hizalı **"Raporu
Hazırlayan: Harun KAHRAMAN — Ürün Teknik Yöneticisi"** imza satırı eklendi.

`pytest` (159) + `ruff check` yeşil; hem `root_win=None` hem gerçek pencere
yoluyla PDF üretimi elle doğrulandı.

## -7. Yeni özellik: PDF raporuna yatay profil + kuş bakışı görüntüsü — ✅ eklendi (2026-09-18)

Kullanıcı isteği: *"PDF raporuna yatay profil ve kuş bakışı resimleri de
eklense güzel olur"*. `exporters.export_pdf` (klasik arayüzün "PDF Mühendislik
Raporu" düğmesi) o ana kadar yalnızca tablolar üretiyordu, canlı tuval
görünümünden hiç görsel almıyordu.

`export_png`'nin zaten kullandığı `ImageGrab.grab(bbox=...)` ekran-yakalama
tekniği (`main_window.canvas` tek bir tuval üzerinde profili üstte, kuş
bakışını altta çizer — bkz. Kural, `split_y = height * 0.46`) paylaşılan bir
`_capture_canvas_snapshot(root_win, canvas_widget)` yardımcısına çıkarıldı;
hata durumunda (Pillow yok, pencere henüz ekranda değil) `None` döner ve rapor
görsel bölümü atlanarak yine de üretilir — PDF export'un tamamını
kırmaz. `export_pdf`'e opsiyonel `root_win=`/`canvas_widget=` parametreleri
eklendi (`main_window.export_pdf` artık `self.root`/`self.canvas` geçiyor);
verilirse yakalanan görüntü ReportLab `Image` flowable'ı olarak "2. YATAY
PROFİL VE KUŞ BAKIŞI GÖRÜNÜMÜ" başlığı altına, sayfa genişliğine (523 pt) ve
380 pt yükseklik tavanına en-boy oranı korunarak sığdırılıyor. Sonraki bölüm
numaraları (Kamera Matrisi, DORI Analizi, Uygunluk Matrisi, Tavsiyeler) buna
göre 3-6'ya kaydırıldı.

Hem `root_win=None` (tablo-yalnız, eski davranış) hem gerçek bir Tk penceresi
+ tuvalle (ekran görüntüsü gömülü) elle doğrulandı: ikinci PDF'te
`/Subtype /Image` nesnesi var, birincisinde yok. Tam `pytest` (159 test) +
`ruff check` yeşil.

## -6. Tarama: bağımsız optik tezgâh başlatıcısı hata raporlamayı kurmuyordu — ✅ düzeltildi (2026-09-18)

Kullanıcı sorusu üzerine ("açık işler neler kaldı, bug hala var mı") daha önce
incelenmemiş üç küçük dosya tarandı: `cctv_optics_workbench.py`,
`cctv_simulator/__init__.py`, `cctv_simulator/ui/__init__.py`.

**Bulgu:** `cctv_optics_workbench.py` (bağımsız `python
cctv_optics_workbench.py` başlatıcısı) `install_error_reporting()`'i
argümansız çağırıyordu. `errors.py`'deki bu fonksiyon, yalnızca bir Tk `root`
verildiğinde `tk.Tk.report_callback_exception` / `tk.Toplevel.report_
callback_exception`'ı **sınıf seviyesinde** yamalıyor — bu yama olmadan Tk
widget callback'leri içinde atılan istisnalar hiçbir yere gitmiyor (donmuş/
pencereli derlemede stderr yok, sessizce kayboluyor). Ana giriş noktası
(`cctv_dual_view_simulator.py`) root'unu önce oluşturup doğru şekilde
geçiyordu; bağımsız tezgâh başlatıcısı bunu atlamıştı çünkü kendi root'unu
`launch()` içinde (`ui/modern_window.py`) gizlice oluşturuyor.
`customtkinter.CTk`'nin `tkinter.Tk`'nin alt sınıfı olduğu doğrulandı, yani
sınıf yaması doğru root ile çalışır.

**Düzeltme:** `main()` gerçek pencereyi oluşturmadan önce atılabilir bir
`tk.Tk()` kökü açıp `install_error_reporting(_bootstrap_root)` çağırıyor
(sınıf yamasını tetiklemek için), sonra onu yok edip normal akışa devam
ediyor. Elle doğrulandı: yama, geçici root yok edildikten sonra oluşturulan
gerçek `ctk.CTk()` penceresine de uygulanıyor. `__init__.py`'lerin ikisi de
temiz — kural 7'ye (tembel import) uygun, ek sorun yok. Tam `pytest` (159
test) + `ruff check` yeşil.

## -4. Kullanıcı bildirimi: bir önceki 150m düzeltmesi kısa menzilli kameralarda yanlış ölçek yaratıyordu — ✅ düzeltildi (2026-09-18)

Bir önceki -3 maddesindeki düzeltme (sabit 150 m tavanının kaldırılması)
gerçek ama gizli ikinci bir bugı açığa çıkardı. Kullanıcı: *"25ppm'i 50 metre
olan bir kamera için 3000m'de yeşil boyuyor"*.

**Kök neden:** `models.DEFAULT_LEVELS` termal Johnson/Algoritma "tespit"
kriterlerini de içeriyor — bazıları **1.3 px/m** gibi son derece düşük PPM
değerlerinde (uzun menzilli termal hedef tespiti için, çıplak "bir şey var"
seviyesinde). PPM ∝ 1/mesafe olduğu için, HERHANGİ bir kamera bu gevşek
kriteri binlerce metrede "karşılıyor". 150 m tavanı kalkınca,
`_get_max_draw_distance` artık bu aşırı gevşek yardımcı seviyeyi de "gerçek
menzil" sayıp tüm görünümü ona göre binlerce metreye geriyordu — ve çizim
döngüsü o mesafeye kadar o seviyenin rengini boyuyordu. Kullanıcının
belirttiği "kameraların focal length'ine oranlı olmalı" tam da bunun
karşılığı: yalnızca standart, anlamlı DORI kriterleri ölçeği belirlemeli.

**Düzeltme:** `ui/canvas_drawer.py`'de `_get_max_draw_distance`,
`_draw_side_lane` ve `_draw_topdown_result` artık yalnızca **EN 62676-4'ün
en gevşek tanımlı DORI kademesi olan İzleme/Monitoring (12,5 px/m) ve üzeri**
PPM seviyelerini ölçek/boyama için dikkate alıyor (`_MIN_SCALE_PPM = 12.5`,
sabit; standarttan geliyor, keyfi değil). Daha gevşek yardımcı kriterler
(Johnson/Algoritma) hâlâ sonuç tablosunda görünüyor, sadece görünümü
germiyor/boyamıyor. Hem kısa menzilli (artık ~150 m'de makul kalıyor) hem
gerçek uzun menzilli PTZ (hâlâ ~11.500 m'ye doğru genişleyebiliyor, -3
maddesindeki düzeltme korunuyor) senaryolarıyla gerçek uygulama üzerinden
elle doğrulandı. Tam `pytest` yeşil.

## -5. Yeni özellik: "En İyi Kurulum" (odak + tilt birlikte) — ✅ eklendi (2026-09-18)

Kullanıcı isteği: *"istediğim algoritmayı ve isteri belirtsem, bana en iyi
tilt açısını ve focal length'i veren bir yapı"*. Gerekli parçaların çoğu
zaten vardı, ayrı ayrı: `update_lens_suggestion` gereken odağı kapalı-form
formülle hesaplıyordu, `optimize_tilt_calc` sabit lens ile tilt tarıyordu —
ikisi birleştirilmemişti.

`calculations.optimize_camera_setup(camera, distance, level, ppm_levels)`
eklendi: önce gereken odağı analitik hesaplıyor (hedefin eğik mesafesi
tilt'ten bağımsız olduğu için sweep gerekmiyor), kameranın kendi
`focal_min_mm`/`focal_max_mm` aralığına kelepçeliyor, sonra o **tam odakla**
`optimize_tilt_calc`'ı çağırıp en iyi tilt'i buluyor. Kapsam bilinçli olarak
**mevcut kamera modelini** optimize ediyor (kütüphanede farklı model aramıyor
— o `update_alternative_models`'ın işi, kullanıcı onu tercih etti).

`ui/main_window.py`: "Asistan" sekmesinde "🎯 En İyi Kurulum (Odak + Tilt)"
paneli — "Lens Önerisi" bölümündeki hedef mesafe + seçili PPM seviyesini
kullanıyor, iki buton ("Hesapla" / "İkisini de Uygula"). Aralık dışına
çıkarsa (gereken odak kameranın max'ından büyükse) açıkça "⚠ Lens yetersiz"
uyarısı + en yakın ayarla ulaşılan gerçek PPM gösteriliyor. Gerçek uygulama
üzerinden hem aralık-içi (15 m/Identification → 7.9 mm/23.0°, tam 250 px/m)
hem aralık-dışı (80 m/Identification → gereken 41.7 mm, kelepçeli 12 mm/11.5°,
yalnızca 72 px/m) senaryolarıyla elle doğrulandı. Tam `pytest` yeşil.

# Açık İşler — Kod İncelemesi (2026-09-17)

Aşağıdaki liste tüm `cctv_simulator/` kod tabanının (15.617 satır, 30+ modül) beş
paralel derin incelemeyle taranması ve en kritik bulguların bizzat kodda
doğrulanmasıyla çıkarıldı. "**Doğrulandı**" işaretli bulgular satır satır okunup
tetikleyici senaryo ile teyit edildi. "Şüpheli" işaretliler incelemeyi yapan
ajanın güçlü emare bulduğu ama ek doğrulama gerektiren noktalar — ele alırken
önce tekrar üretmeyi deneyin.

## -3. Kullanıcı bildirimi: PTZ/uzun menzilli kamerada profil ve kuşbakışı 150m'de kesiliyor — ✅ düzeltildi (2026-09-18)

**`ui/canvas_drawer.py:133-150` (`_get_max_draw_distance`)**

Klasik pencerenin "YATAY PROFİL" (kesit) ve "KUŞBAKIŞI / PLAN ÜSTÜ" çizimlerinde
gösterim mesafesi kameranın gerçek menzili ne olursa olsun **sabit 150 m'de**
kesiliyordu:

```python
if largest >= 150.0:
    return 150.0        # already clamped, stop scanning
return max(15.0, min(largest, 150.0))
```

**Tetikleyici senaryo:** PTZ/tele/termal gibi 150 m'nin çok ötesine görüş
sağlayan bir kamerada, 150 m'den sonraki tüm PPM/DORI bantları (Tanıma,
Gözlem, Algılama vb.) ve geometrik limit sessizce çizilmiyordu — kullanıcı
"150m'den sonrası yok" olarak fark etti. `grid_step()` (aynı dosya) zaten
5000 m+ mesafeler için ızgara adımı tanımlıyordu, yani bu bir render
kısıtı değil, unutulmuş/yanlış bir tavandı.

**Yapılan düzeltme:** Sabit 150 m tavanı kaldırıldı; `_get_max_draw_distance`
artık kameranın gerçek `OpticResult` verisinden (kör nokta, PPM mesafeleri,
geometrik limit) hesaplanan gerçek `largest` değerini olduğu gibi döndürüyor.
Gerçek render motoru (tuval öğeleri doğrudan sorgulanarak, ekran görüntüsü
almadan) test edildi: 110.769 m'lik gerçek bir PTZ menzilinde 18 DORI/kör-nokta
poligonu tuvalin tamamına doğru yayılıyor, mesafe etiketleri gerçek uzak
değerlere kadar çiziliyor. Tam `pytest` yeşil.

## -2. Üçüncü tur derin inceleme — ✅ 9 bug bulundu ve düzeltildi (2026-09-17)

Daha önce hiç ya da yalnız birkaç fonksiyonuyla incelenmiş büyük dosyalara
(canvas_drawer.py, camera_db_window.py — tam, view_3d_window.py — tam,
main_window.py — tam, compliance_standards.py — tam, perspective_3d.py'nin
geri kalanı, config.py veri bütünlüğü) beş yeni paralel inceleme ajanı
çalıştırıldı. 9 gerçek bug bulundu, hepsi düzeltildi. Tam `pytest` + `ruff`
yeşil; çoğu için elle Tk entegrasyon testi yazıldı.

1. **`camera_db_window.py` sayısal alanlarda Türkçe binlik nokta 1000× küçük
   okunuyordu** — `compliance_optics.py`'de zaten düzeltilmiş **aynı bug
   deseni**: `white_light_range_m`/`dori_identify_m` gibi alanlara "1.500"
   (=1500 m) girilirse `float("1.500")=1.5` olarak kaydediliyordu, hiçbir
   uyarı olmadan. Aynı `_parse_tr_number` yaklaşımı burada da uygulandı.
2. **`canvas_drawer.py` — kamera listesi boşken manuel ölçek modunda
   çöküyordu** — `auto_view_scale=False` dalında `min()/max()` boş
   jeneratörle çağrılabiliyordu (`auto_view_scale=True` dalı zaten
   korunuyordu, asimetrikti). `main_window.delete_camera` şu an son kamerayı
   silmeye izin vermediği için bugün ulaşılamıyor ama savunma eklendi.
3. **`canvas_drawer.py` ışın çizim eşiği (0.2°) motorun eşiğiyle (0.5°)
   uyuşmuyordu** — `calculations.py` `top_ray_deg ≤ 0.5°`'yi "geometrik
   limit yok" sayarken, kesit çizimi 0.2°'ye kadar sonlu bir "zemine değme"
   noktası hesaplıyordu (Kural 1 ihlali). 0.5 ile hizalandı.
4. **`view_3d_window.py` — hedef tipi/palet ilk açılışta yanlış
   davranıyordu** — `.current(0)` bağlı `Combobox`'ta görünen metni
   (`"🧍 İnsan Mankeni (1.8m)"`) doğrudan `target_type_var`'a yazıyordu,
   `__init__`'in kurduğu iç kodu (`"human"`) eziyordu; normalizasyon yalnız
   kullanıcı Combobox'a dokununca çalışıyordu. Sonuç: **ilk render'da**
   `target_h` 1.8m yerine 1.0m'ye düşüyordu, termal kamera paleti
   "auto"dan hiç `thermal_white`'a geçmiyordu. Aynı bug `palette_mode_var`
   için de vardı. `.current(0)`'dan sonra iç kod açıkça geri yazılıyor artık.
5. **`view_3d_window.py` — kamera değişince hedef mesafe slider'ı yeni
   sınıra kelepçelenmiyordu** — tele/termal kamerada `target_dist_var=5000`
   iken kısa menzilli bir kameraya geçilirse slider görsel olarak sona
   yaslanıyordu ama değişken 5000'de kalıyor, render/HUD bu bayat değeri
   kullanmaya devam ediyordu.
6. **`view_3d_window.py` — yanal ofset slider'ı sabit ±25 aralığındaydı,
   sürükleme mantığı çok daha geniş bir aralık (±1200'e kadar) yazabiliyordu**
   — slider görsel olarak ±25'te tıkanmışken gerçek `target_lateral_offset_var`
   çok daha büyük olabiliyordu, render bunu kullanıyordu. Artık
   `_sync_slider_limits()` her iki slider'ı da (`_max_lateral_for` ortak
   fonksiyonuyla) aynı formülle senkronluyor.
7. **`map_3d_window.py` `TerrainViewshedWindow` `guarded_build` kullanmıyordu**
   — diğer 4 alt pencerenin (camera_db_window, view_3d_window,
   spec_assistant, modern_window) hepsi kullanıyor. En büyük pencerenin
   (2000+ satır UI kurulumu) inşası sırasında bir istisna fırlarsa, yarım
   kurulmuş, boş, kapatma protokolü bağlanmamış bir Toplevel ekranda asılı
   kalıyordu. `guarded_build` ile sarmalandı, `main_window.open_terrain_viewshed`
   diğer `open_*` fonksiyonlarıyla aynı `build_ok` desenine uyduruldu.
8. **`compliance_standards.py` `_TASK_TR` sözlük sırası "kimlik tespit"i
   "tespit" gölgeliyordu** — "tespit" (→Detection, 25 px/m) sözlükte "kimlik
   tespit"ten (→Identification, 250 px/m) önce geliyordu; "kimlik tespit"
   alt dizesi "tespit"i içerdiği için ilk-eşleşen-kazanır mantığı hep
   "tespit"te duruyordu. Bir şartnamenin "kimlik tespit" görevi **10× daha
   zayıf** bir eşikle doğrulanıyordu, sessizce. Artık en-uzun-ifade-önce
   eşleşiyor (gelecekteki eklemelere karşı da korur).
9. **`perspective_3d.py` `generate_dori_ground_polygons` zemin bantlarını
   sabit 100 m'de kırpıyordu** — aynı dosyadaki `generate_ground_grid_lines`
   termal/tele kameralar için 8000 m'ye kadar çiziyordu ama DORI renkli
   zemin bantları hep 100 m'de kesiliyordu; uzun menzilli bir kamerada
   (örn. termal 4km) Teşhis dışındaki tüm bantlar sıfır-yükseklikte
   (görünmez) çıkıyordu. Artık `max_dist_m` (kameranın aktif menzil
   slider'ından) parametre olarak geçiyor, termal/uzun-menzilli kameralarda
   kırpma sınırı buna göre ölçekleniyor. Elle doğrulandı: termal kamerada
   eskiden 3 farklı (çoğu sıfır-yükseklik) bant, düzeltmeden sonra 0.5m'den
   4000m'ye kadar 8 ayrı, anlamlı genişlikte bant.

Kod değiştirilmedi (yanlış alarm):
- `cctv_dual_view_simulator.py`'de `DualViewCCTVDesignApp(root)` kurucusunun
  `guarded_build`/try-except ile sarılmaması şüphesi araştırıldı —
  `install_error_reporting(root)` zaten `sys.excepthook`'u koşulsuz kuruyor,
  bu da Tk callback'leriyle sınırlı değil, ana script gövdesindeki HERHANGİ
  bir yakalanmamış istisnayı da kapsıyor. Elle test edildi: kurucu içinde
  fırlatılan bir istisna gerçekten hata diyaloğu + log dosyası üretiyor,
  "sessizce hiç açılmama" senaryosu doğrulanamadı.

## -1. İkinci tur derin inceleme — ✅ 13 bug bulundu ve düzeltildi (2026-09-17)

İlk turda yüzeysel geçilen büyük dosyalar (compliance.py, exporters.py,
spec_assistant.py, modern_window.py) ve viewshed_3d.py/terrain_loader.py'nin
geri kalanı için beş yeni paralel inceleme ajanı çalıştırıldı. 13 gerçek bug
bulundu, hepsi düzeltildi ve doğrulandı (tam `pytest` + `ruff` yeşil, çoğu
için elle repro/adversarial test yazıldı). "Fizik/altyapı" turu
(cctv_iq/solar/atmosphere/scene_render/i18n/errors/theme/models/config) ve
compliance_eval.py/spec_pdf.py/project_io.py/**main**.py/database.py'de
gerçek bug bulunamadı (temiz).

1. **`compliance.py` IR regex kelime sınırı yok** — `(?:ir|ayd[ıi]nlatma)`
   Türkçe "-ebilir/-ir" fiil ekiyle her yerde eşleşiyordu ("izlenebilir" gibi
   çok yaygın bir ekten sahte IR isteri üretiyordu). `\bir\b` yapıldı.
2. **`compliance.py` WDR regex'i bağlamsız "NN dB" yakalıyordu** — S/N oranı
   gibi ilgisiz bir dB değeri sahte WDR isteri üretiyordu. Artık "wdr"/"hdr"
   kelimesinin gerçekten yakınında (±20 karakter) bir dB arıyor.
3. **`compliance.py` sıcaklık çıkarımı tüm belgeden derece-numaralı sayı
   topluyordu** — FOV/tilt açıları gibi ilgisiz "derece" değerleri, belgede
   herhangi bir yerde "sıcak" geçiyorsa sahte bir çalışma sıcaklığı aralığı
   üretiyordu. Artık yalnız "sıcak..." geçen CÜMLE içindeki sayıları alıyor.
4. **`exporters.py` — ReportLab XML/markup enjeksiyon çökmesi** — kamera adı,
   PPM seviye adı, hedef adı, LLM'den gelen şartname uygunluk metni
   (`requirement`/`evidence`) gibi serbest metinler kaçışsız `Paragraph()`'a
   veriliyordu; içinde `<b>`/`<br>`/`<font>` gibi kapatılmamış bir "tag"
   geçerse (örn. LLM'in ürettiği "< 30m" gibi bir ifade) **tüm PDF üretimi
   çöküyordu**. `xml.sax.saxutils.escape` ile merkezi bir `_esc()` yardımcı
   fonksiyonu eklendi, ~15 çağrı noktasında (her iki rapor motorunda) ve
   `_kv()` yardımcısında (mühendislik raporunun tüm anahtar/değer
   tablolarını tek yerden kapsıyor) uygulandı. Adversarial testle (kapanmamış
   `<b>`/`<br>` içeren kamera/hedef/uygunluk verisiyle) doğrulandı — artık
   çökmüyor.
5. **`spec_assistant.py` blocker tespiti 10+ DORI isterinde bozuluyordu** —
   `requirement_id[-2:-1] == "D"` yalnız tek haneli DORI rid'lerinde ("P1-D1")
   doğru çalışıyordu; "P1-D10"dan itibaren yanlış karaktere bakıp
   "Uyumsuz" bir DORI isterini blocker olarak işaretlemeyi bırakıyordu (skor
   yanlışlıkla iyimser çıkabiliyordu). Sondaki rakamlar kırpılıp "D" ile
   bitip bitmediğine bakılıyor artık.
6. **`spec_assistant.py` async analiz (Gemini/Ollama) yanlış metni
   loglayabiliyordu** — `apply_compliance_result`/`analyze_spec_rule_based`
   şartname kutusunu **sonuç geldiğinde** (dakikalar sonra) yeniden
   okuyordu; kullanıcı bu sırada kutuyu düzenlerse/temizlerse eğitim
   logu (`training_log`) yanlış/boş metni doğru sonuçla eşleştiriyordu.
   Artık analiz başlarken yakalanan metin callback'lere açıkça taşınıyor.
7. **`modern_window.py` DORI grafiğinde "Gözlem" (Observation) bandı hiç
   çizilmiyordu** — yalnız Teşhis/Tanıma/Algılama bantları vardı, 62,5 px/m
   (Gözlem) sağlanan bir mesafe sessizce "Algılama" (25 px/m) bandına
   yutuluyordu — kullanıcı zayıf bir sonucu güçlüymüş gibi görebiliyordu.
   `PPM_OBSERVE`/`C_OBSERVE` ile 4. bant eklendi.
8. **`modern_window.py` hedef işaretçisi ile "EN 62676-4 uygunluk" kartı
   çelişebiliyordu** — işaretçinin `ok` hesabı yalnız PPM+kör nokta+geometrik
   limit kontrol ediyordu, karttaki nihai karar ayrıca atmosfer/IR/lüks de
   katıyordu; sisli havada veya yetersiz IR menzilinde işaretçi yeşil
   gösterirken kart "UYGUN DEĞİL" yazabiliyordu. Tek bir `_evaluate_requirement()`
   artık her ikisini de besliyor.
9. **`terrain_loader.py` yerel GeoTIFF'te `lat_center`/`lon_center` hiç
   yazılmıyordu** — çevrimiçi indirilen DEM'ler bu alanları dolduruyordu ama
   kullanıcı kendi (muhtemelen daha güvenilir) dosyasını yüklerse
   "☀️ Güneş / Parlama" analizi hiçbir uyarı vermeden sessizce kayboluyordu.
   Coğrafi (derece) CRS'te doğrudan, projeksiyonlu (UTM vb.) CRS'te
   `rasterio.warp.transform` ile WGS84'e çevrilip dolduruluyor artık; CRS
   yoksa/dönüşüm başarısızsa eskisi gibi sessizce `None` kalıyor (çökme yok).
   3 yeni test (coğrafi, projeksiyonlu, CRS'siz) eklendi.
10. **`requirement_library.list_templates()` slug gösteriyordu, gerçek adı
    değil** — "Şirket A" olarak kaydedilen bir şablon arayüzde "sirket-a"
    diye listeleniyordu. Artık her JSON'un içindeki gerçek `name` alanı
    okunup gösteriliyor (dosya adı hâlâ slug, sadece görünüm düzeldi).
11. **`scripts/eval_compliance.py` boş `{}` kamera kütüphanesiyle
    çalıştırılıyordu** — hem "rule" hem Ollama runner'ı `rule_based_compliance`/
    `analyze_with_ollama`'ya boş kütüphane veriyordu; "rule" temelinde matris
    hep `[]` kalıp "Matris durum doğruluğu" metriği sistematik olarak
    yanıltıcı çıkıyordu. `database.load_camera_library()` ile gerçek
    kütüphane veriliyor artık.

Kod değiştirilmedi (yanlış alarm/zaten doğru bulundu):
- `spec_assistant.py:530` override loglama — aynı desen ama düşük riskli,
  fork'un notu doğrultusunda incelendi, ayrı bir düzeltme gerektirmedi.
- `training_log.build_instruction_dataset`'in aynı `spec_sha` için yalnız ilk
  analizi temel alması — belgelenmiş bir tasarım kısıtı, bug değil.
- `_terrain_from_rasterio`'daki `px_w < 0.05` coğrafi/projeksiyon sezgisi —
  teorik olarak çok yüksek çözünürlüklü (&lt;5 cm/piksel) projeksiyonlu yerel
  bir DEM'i yanlış sınıflandırabilir, ama nadiren tetiklenir; not düşüldü.

## 0. Yeni özellik: Hat Modu — Tesis / Sınır / Otoyol — ✅ EKLENDİ (2026-09-17)

Kullanıcı gözlemi doğru çıktı: `generate_perimeter_plan` (ve GUI'si) yalnız
**tesis çevre çiti** senaryosunu varsayıyordu — her direk hat boyunca bir
sonraki direğe bakıyordu (çiti tırmanma/kesme için izleme mantığı), kapalı
halka UI'da varsayılan ve açık uç seçeneği için hiç kontrol yoktu. Sınır
güvenliği (hatta dik, tek tarafı izleme) ve otoyol/güzergah gözetimi
(trafiğe karşı ANPR mantığı) için ayrı bir kamera yönlendirme sözleşmesi
yoktu.

**Eklenen:** `perimeter_planner.generate_perimeter_plan(..., line_mode=
"facility"|"border"|"highway", watch_side="left"|"right")`:
- `facility` (varsayılan, eski davranış bit-aynı): hat boyunca bakış, köşe
  guard kameraları.
- `border`: direkler hatta **dik**, `watch_side` ile seçilen tek tarafa
  bakar (çizim yönüne göre sol/sağ el kuralı), köşe guard'ı yok.
- `highway`: direkler hattın **çizildiği yönün tersine** bakar (yaklaşan
  trafiğe karşı), köşe guard'ı yok.

`map_3d_window`'da "🧭 Hat Modu" grubu (kapalı halka onay kutusu — önceden
hiç UI kontrolü yoktu — + hat modu/taraf seçici), `export_terrain_state`/
`import_terrain_state` round-trip'i, BOM CSV'de "# Hat Modu" satırı, sekme
başlığı "Çevre Çiti / Sınır / Otoyol" oldu. `tests/test_perimeter.py`'e 4
yeni test (yön doğruluğu + köşe guard'ının border/highway'de devre dışı
kalması). Tam `pytest` + `ruff` yeşil; Tk entegrasyon scriptiyle GUI
tarafı da (mod değişimi → doğru pan_deg → export/import round-trip) elle
doğrulandı. `CLAUDE.md`'ye kısa bir tasarım notu eklendi.

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
