# CCTV Simülatör Pro — sistem denetimi

Masaüstü kabuğu, `feat/desktop-shell` @ `9b4172f`. Bu denetimde depoda hiçbir dosya değişmedi.

## 0. Nasıl denetlendi, ne denetlenemedi

| Yöntem | Kapsam |
|---|---|
| Tuş tuş operatör sürüşü (her adımda panel metni + ekran görüntüsü) | **Arazi & Görüş Alanı**: tek kamera sekmesi + çevre çiti sekmesi, iki tur (43 + 25 adım) |
| Ekran taraması, 1440×900 / 1280×720 / 1024×640 | **7 modülün hepsi**: taşma, ekran dışı kontrol, konsol hatası, küçük yazı, adsız düğme |
| Kaynak okuma | Bütün sayfalar, panel bileşenleri, PTZ motoru ve servis yöntemleri |
| Başsız ölçüm (motor doğrudan) | PTZ sorusu: sabit / devriye / erişim zarfı / tepki süresi |

İşaretler: **[Ç]** çalışan uygulamada görüldü · **[K]** koddan doğrulandı, uygulamada denenmedi · **[E]** ekran taramasında görüldü.

**Yapılamayan:** alan planı, şehir, CAD, Optik, 3B, Kütüphane, Şartname ve Raporlar'ın tuş tuş sürüşü. Geliştirme uygulaması bellek azlığı nedeniyle sistem tarafından kapatıldı; sormadan yeniden başlatmadım. Bu modüllerdeki bulgular ekran taraması ve kod okumasına dayanıyor.

> **Sonradan doğrulandı (13.11.2026): ikisi de doğru fizik.** Sis 0,8 km'de kontrast 471 m'ye kadar yetiyor, 12 mm lensin optik sınırı 230 m — hava sınır değil (Yoğun sis 0,3 km'de menzil 177 m'ye düşüyor). Kadrajda arazi gölgesi olmadığı için direği yükseltmek alanı artırmıyor (1,16 → 1,11 ha; eğim −15°'de 0,03 → 0,21 ha). Tekil sonuç artık nedenini yazıyor.

**Doğrulayamadığım iki gözlem** (hata olabilir, doğru fizik de olabilir): tek kamerada "Sis · 0,8 km" seçmek sonucu değiştirmedi (1,15 ha / 226 m); direk yüksekliğini 6 → 12 m yapmak görünür alanı değiştirmedi.

---

## 1. PTZ: "tüm taranan alana göre" hesaplamak

### Bugün motor ne yapıyor
PTZ için üç ayrı kavram var, uygulama bunları farklı yerlerde ve tutarsız kullanıyor:

| Kavram | Anlamı | Bugün nerede |
|---|---|---|
| Anlık bakış | Kameranın şu an baktığı kadraj | Tek kamera sekmesi — PTZ **tele uçta donmuş** tek kadraj olarak açılıyor |
| Devriye | Bir tur içinde üstünden geçilen alan (aralıklı, revizit süreli) | Plan / birleşik kapsama — **yalnız geniş uç, tek eğim, sabit 20°/sn** |
| Erişim zarfı | Pan × eğim × zoom ile istenince ulaşılabilen her yer (tepki süreli) | Yalnız "karma dizilim" panelinde, tepki süresi olarak |

Planlayıcılar (direk dizimi, en az direk, optimizasyon, alan planı) PTZ kapsamasını **hiç saymıyor**; PTZ plana sonradan takılan bir eklenti.

### Ölçüm
Düzen: 2×2 km tepelik arazi, 10 m hücre, 3 km kapalı çit, sabit kamera "Genel 4MP varifokal 2,8–12" (6 m direk, 40 px/m, geniş uç) → 130 direk / 134 kamera; her 4 direkte bir PTZ (33 adet); hedef boyu 1,8 m.

**±40 m çit bandının payı (%), 4 MP 42× PTZ (6–252 mm):**

| | Algılama | Gözlem | Tanıma | Teşhis |
|---|---|---|---|---|
| Sabit kameralar (sürekli) | 69,7 | 11,5 | 0,9 | 0,0 |
| PTZ devriyesi, bugünkü hesap (6 mm, 20°/sn, revizit ≤ 15 sn) | 97,7 | 45,2 | 5,2 | **0,0** |
| PTZ'nin ulaşabildiği (pan × eğim × zoom) | 100 | 100 | 100 | **99,9** |

Bandın %100'üne 250 px/m ile ≤ 5 sn'de nişan alınabiliyor. 2 MP 32× PTZ'de tablo aynı desende (devriye 96,1 / 21,8 / 2,4 / 0,0; zarf 100 / 100 / 100 / 99,6). 150 m dış şeritte sabit Algılama %20,0, devriye %50,1, zarf %100 (Teşhis %95,3).

Yani uygulama bugün 42× bir PTZ'ye bantta **%0 Teşhis, %5 Tanıma** yazıyor; aynı kamera bandın tamamını birkaç saniyede teşhis edebiliyor. Fikrin doğru: bugünkü model PTZ'yi ciddi biçimde eksik değerlendiriyor.

**Tek PTZ, 360° dönüş, 150 m dış şeritte görülen alan (ha) — odak × hız:**

| Odak | HFOV | 20°/sn (tur 18 sn) Alg / Göz / Tan | 6°/sn (tur 60 sn) Alg / Göz / Tan |
|---|---|---|---|
| 6,0 mm (bugünkü seçim) | 61,9° | 1,91 / 0,34 / 0,07 | aynı |
| 12,7 mm | 31,7° | 6,91 / 1,42 / 0,29 | aynı |
| 26,8 mm | 15,3° | **8,07 / 5,07 / 0,90** | aynı |
| 56,5 mm | 7,3° | 0 — 4,07 ha yalnız "kısa bakış" | 4,07 / 3,89 / 1,90 |
| 119 mm | 3,5° | 0 — 1,87 ha kısa bakış | 1,87 / 1,87 / 1,74 (Teşhis 0,85) |
| 252 mm | 1,6° | 0 | 0 — 0,63 ha kısa bakış |

Tek direk, tek arazi ölçümü; her satırda motorun üç eğim adayından en iyisi kullanıldı.

- **Geniş uç devriye için en kötü odak.** Aynı 18 sn'lik turda 26,8 mm, Algılama alanını 4,2×, Gözlem alanını 15× büyütüyor (6 mm'de 25 px/m ancak ~90 m'ye kadar tutuyor).
- **Hız ile odak bağlı:** bir hücrenin sayılması için bakış ≥ 0,5 sn sürmeli → hız ≤ HFOV / 0,5. Sabit 20°/sn, HFOV'u 10°'den dar her devriyeyi sessizce sıfırlıyor.
- Hız görülen alanı değiştirmiyor, yalnız reviziti; asıl ödünleşim **seviye × revizit**.

### Öneri
"Tüm taranan alan"ı sürekli kapsamayla **aynı yüzdeye katmak yanlış olur**: zarf "%100 Teşhis" der ama tele uçtaki PTZ aynı anda 2°'lik bir dilimi görür. Doğru model üç ayrı sütun:

1. **Sürekli** (sabit kameralar) — birincil ölçüt olarak kalsın.
2. **Devriye** — "revizit ≤ X sn ve bakış ≥ Y sn ile görülen alan". Odak, eğim ve hız **aranmalı** (geniş uç sabit değil); hız ve uç bekleme kullanıcıya açılmalı.
3. **Talep üzerine erişim** — "T sn içinde hedef px/m'ye ulaşılan alan" (zarf + tepki süresi).

Ek olarak:
- Tek kamera sekmesinde PTZ varsayılanı zarf / devriye görünümü olsun; anlık kadraj onun üstünde bindirme.
- Planlayıcıya "PTZ destekli hedef" seçeneği: sabit kameralar Algılama'yı sürekli tutar, Tanıma / Teşhis'i PTZ T sn içinde sağlar. Gerekçe ölçümde: bantta sabit kamerayla Gözlem %80, **532 direkle bile tutmadı** (en iyi %62,4); 33 PTZ aynı bandı ≤ 5 sn'de Teşhis'e çıkarıyor.
- Termal PTZ'de tepki hedefi Johnson olmalı (aşağıda A4).
- Maliyet uygun: zarf 33 PTZ için ~7 sn, 65 PTZ için ~13 sn (devriye 0,4–1,4 sn); arka plan işi olarak koşar.

---

## 2. Yanlış ya da yanıltıcı sonuç üreten bulgular

**A1. "Optimumu Uygula" kamerayı kör bırakabiliyor [Ç].** Özel 4 MP 2,8–12 mm, 12 m direk: tarama "en iyi 7,4 mm · eğim −37,1° · Tanıma 0,01 ha (şimdi 0,00)" dedi; uygulayınca görünür alan 1,15 ha → 0,00 ha, en uzak görüş 226 m → 0 m. Kazanç birkaç hücreyse uyarmalı; motorun daha kötü puanladığı ayarı hiç uygulamamalı.

**A2. Dizilim optimizasyonu bandı büyütürken çiti açıyor [Ç].** "Bant: 13,97 → 15,46 ha (%100)" derken ön tahmine göre kapsanmayan çit 3 m → 168 m (6 boşluk), çit kapsaması %100 → %94,4. Amaç fonksiyonunda çit çizgisi kısıtı yok.

**A3. Sınır modunda başlık yanlış ölçüt [Ç].** Kameralar tasarım gereği hatta dik bakıyor; başlık "Çit çizgisi · motor · 40 px/m %0,0" ve kırmızı "Kapsanmayan çit ≈ 1.217 m · 1 boşluk". Asıl hedef (izlenen şerit, %29,5 tutuyor) altta kalıyor.

**A4. Termal PTZ'nin tepki hedefi EN 250 px/m [Ç + K].** Arayüz ve servis varsayılanı 250; Johnson anahtarı yok. UMA T10: ulaşılan 4,21 ha, "max odak yetersiz 123,64 ha". Ölçüm (UMA T5, 150 m şerit): 250 px/m → %43,5; Johnson teşhis 13,33 px/m → %100. Uygulamanın geri kalanı termali Johnson'la sınıflıyor.

**A5. "Medyan" kutusu ortalamayı gösteriyor [K].** `PtzPanels.tsx:129` — etiket "Medyan", değer `mean_response_s`; serviste tepki için medyan yok.

**A6. Tek kamerada PTZ tele uçta açılıyor [Ç].** UMA T10 → 350 mm (FOV 2,5°); 360° tarama orada değerlendirilince "izlenen alan 0,05 ha". Plan akışı aynı kamerayı 35 mm'de (FOV 24,8°) tarıyor — iki sekme aynı kameraya iki ayrı sonuç veriyor. Model değişince eski kaydırıcı durumu (eğim −37,1°) taşınıyor.

**A7. Haritadaki FOV kaması eğimi yok sayıyor [Ç].** −37° eğimde kama optik menzile kadar çiziliyor; kamera oraya bakmıyor.

**A8. "Kapsanmayan çit" satırı seçili hedefi izlemiyor [Ç].** Hedef Gözlem seçilince başlık %65,7'ye düşüyor, alt satır hâlâ "≈ 15 m · hepsi 4 m'den kısa parçalar".

**A9. Aynı plan için iki sayı [Ç].** "Çit · ön tahmin %100,0" → motor %96,0. Ayrıca "%100,0" ile "kapsanmayan ≈ 3 m" yan yana.

**A10. Yanıltıcı uyarı [Ç].** −37° eğimde "Kameranın görüşü analiz menzilini aşıyor; menzili büyütün".

**A11. En az direk araması tutmayan hedefi 2 dakika kovalıyor [Ç].** Gözlem %80: 120 sn sürdü, 532 direğe (5,6 m aralık) kadar sıklaştırdı, sonra "hedef tutmadı". Pay artmayı bırakınca ya da aralık anlamsızlaşınca erken çıkmalı.

**A12. Termal kamerada EN etiketleri [Ç + K].** En az direk / optimizasyon / karşılaştırma / kapsama hedefi seçicileri sabit "Gözlem ≥62,5 · Tanıma ≥125 · Teşhis ≥250" yazıyor; termal bu eşiklerle sınıflanmıyor.

**A13. Hedef kaydırıcısı EN seviyesine oturmuyor [K].** 25–250, adım 5: 62,5 seçilemiyor; varsayılan 40 px/m bir EN seviyesi değil.

**A14. Varsayımlar iki kez yazılıyor [K].** Ağ panelinde `n.assumptions` hem liste hem "Varsayımlar" açılır bölümü olarak (`PlanningPanels.tsx:94` ve `:108`).

---

## 3. Eksikler (iş akışını kesen)

**Proje / kabuk**
- "Yeni proje" yok; geri al / yinele yok [K].
- Ayarlar, tema, dil, yardım, hakkında yok [K + E]. Arayüz tercihleri hiçbir yere kaydedilmiyor.
- Kısayol yalnız Ctrl+S / Ctrl+O; modül geçişi kısayolu yok [K].
- Onay ve giriş için yerel `window.confirm` / `window.prompt` (8 yer); yönetici şifresi değişimi düz metin kutusunda [K].
- Alt durum çubuğundaki "Hesap … ms" yalnız CAD sayfasından besleniyor, diğer modüllerde bayat değer duruyor [E].
- Proje adı bağlantı gibi boyanmış ama tıklanmıyor [E].

**Modüller arası**
- CAD'deki proje kameraları ile Arazi sayfasının kamerası birbirinden bağımsız [K]. Projeye yalnız tek arazi yerleşimi yazılıyor.
- Üç ayrı hava durumu durumu; eğim işareti farklı (Optik / 3B aşağı pozitif, Arazi −45…10); birim "PPM" ve "px/m" karışık [K + E].
- Hedef **yedi ayrı yerde** seçiliyor (plan px/m, kapsama hedefi, optimizasyon seviyesi, en az direk seviyesi, karşılaştırma seviyesi, alan seviyesi, PTZ tepki hedefi) ve birbirinden habersiz.

**CAD** — "Seçiliyi Sil" onaysız, seçim 1. kameraya atlıyor [K]. "Kamera ekle" 16×16 px, araç düğmeleri 23×23 px ve yalnız `title` [E]. 1024 px'te inceleme paneli tamamen ekran altında [E].

**Optik** — görevler 4 EN seviyesine sabit; projenin 17 özel seviyesi yok sayılıyor [K]. Varsayılan açılış "UYGUN DEĞİL" (Teşhis, 20 m, 2,8 mm) ve "0,0 lux" [E].

**3B** — hedef mesafesi 300 m ile sınırlı (42× PTZ 376 m'de teşhis ediyor) [K]; anlık görüntü / dışa aktarım yok.

**Arazi**
- Çit köşesi sürükleme / düzenleme yok; kaydırıcılarda sayısal giriş yok [K].
- PTZ hızı ve uç bekleme sabit (20°/sn, 2 sn) [K].
- Aynı anda tek hesap; sürerken bütün düğmeler kilitli (İptal var) [K].
- Senaryo sınırı 12; 13.'de en eskisi sessizce atılıyor, silme onaysız [K].
- Gece senaryosu bilgisayarın saatini kullanıyor; prosedürel arazide konum elle giriliyor [Ç + K].
- Tek bildirim şeridi: PTZ uyarısı "arazi ölçüm değil" uyarısının üstüne yazıyor [Ç].
- PTZ seçicisinde broşür başlığından gelen adlar ("AB-0000-3951 - FULL HD ÇÖZÜNÜRLÜK / 25 FPS …") ayırt edilemiyor [Ç].

**Kütüphane** — sıralama yok, satırlar klavyeyle seçilemiyor, "projede kullan" yok, içe / dışa aktarım yok [K]. ODAK sütunu boş yer varken kesiliyor; tema dışı beyaz kaydırma köşesi [E].

**Şartname** — şablon çalıştırma (▶) düğmesinin adı da `title`'ı da yok [E]. Matris dışa aktarımı yok; belirsizlik listesi gösterilmiyor [K].

**Raporlar** — "Optik Raporu Yaz" klasör seçilene kadar açıklamasız kapalı; hedef klasör hatırlanmıyor; sayfa büyük ölçüde boş [E + K].

---

## 4. Görsel ve yerleşim

- **Arazi sağ sütunu tek uzun kaydırma** [Ç]: çit sekmesinde plan, kapsama, optimizasyon, ağ, gece, yaklaşma, senaryo, en az direk, karşılaştırma, karma PTZ alt alta; sonuçlar ekranın altında kalıyor, sol sütun yarı boş.
- DORI lejandında dört yakın mavi ayırt edilmiyor [Ç].
- "Görüş hattı kesiti": lejant yok, y ekseni birimi yok, kamera / direk işareti yok, görüş hattı çizilmiyor [Ç].
- CAD: "tekerlek: yakınlaştır" ipucu "40 m" eksen etiketinin üstüne biniyor [E].
- 3B: "50 m" ızgara etiketi hedef etiketinin arkasında; 1440'ta görünümün altı boş [E].
- Optik: "hedef 20,0 m" etiketi işaretçiye değiyor [E].
- 1024 px'te Arazi'nin sağ paneli sol sütunun altına düşüyor, harita ~360 px kalıyor [E].
- 11 px altı yazı: Kütüphane 48, Optik 26, CAD 24 öğe [E]. Üst çubuk simge düğmeleri 22×22 px.
- Uzun etiket sarıp kesiliyor: "Sırt sırta: direk başına 2 kamera (yedekli, kör bölgesiz)"; kamera seçicisinde ad kırpılıyor [Ç].

Taramada temiz çıkanlar: üç pencere boyutunda konsol hatası yok, yatay sayfa kaydırması yok, ekran dışına düşen kontrol yok.

---

## 5. Kendi işime eleştiri

Yukarıdakilerin bir kısmı doğrudan benim son turlarda yaptıklarım:

- **Son commit (`9b4172f`)**: tekerlek ipucunu eksen etiketinin üstüne koydum (bölüm 4).
- **Varsayımların çift yazılması (A14)**: ağ panelini düzeltirken eski bloğu kaldırmadım.
- **Sabit EN etiketleri (A12)**: karşılaştırma seviye seçicisini eklerken termali düşünmedim; oysa "termal Johnson'la sınıflanır" kuralını aynı turda yazmıştım.
- **"Sırt sırta" etiketi**: çok uzun, dar panelde kesiliyor.
- **Optimizasyon (A1, A2)**: tek bir alanı büyütmeye odaklı kurdum; "başlangıçtan kötü olamaz" güvencesi yalnız o tek ölçüt için geçerli, çit çizgisi ya da kameranın hiç görmemesi korunmuyor.
- **En az direk (A11)**: durma koşulu deneme sayısı; ulaşılamayan hedefi erken tanımıyor.
- **PTZ**: devriyeyi geniş uca sabitledim ve hızı koda gömdüm; ölçüm bunun PTZ'yi en zayıf haliyle değerlendirdiğini gösteriyor.

Daha geniş bakınca: uygulama **özellik biriktirerek** büyüdü (ANPR, şehir, gizli yaklaşma, gece, ağ, senaryo, alan planı…), temel kullanım altyapısı geride kaldı (yeni proje, geri al, sayısal giriş, köşe düzenleme, ayarlar, CAD ↔ Arazi bağı). Her özellik kendi hedef seçicisini ve kendi varsayılanını getirdi; bölüm 2'deki tutarsızlıkların yarısı (A8, A9, A12, A13) aynı kökten: **projede tek bir "ister" nesnesi yok**. "Neresi, hangi seviye, sürekli mi aralıklı mı, gece / hava koşulu" bir kez tanımlanıp bütün paneller ondan okusa bu sınıf hata kendiliğinden kapanır ve plan için uygunluk beyanı (açık iş) da aynı nesneden çıkar.

İkinci eksik doğrulama tarafında: motor kendi içinde tutarlı (golden testler, iki motorun karşılaştırması) ama **gerçek kamera görüntüsüne karşı** hiç doğrulanmadı; ve ilk açılışta gelen arazi prosedürel, yani yeni kullanıcının gördüğü ilk sayılar (uyarıya rağmen) kurgusal.

---

## 6. Önerilen sıra

1. **Küçük, kesin düzeltmeler**: A5 (medyan), A14 (çift varsayım), A12 (termal etiketleri), A4 (termal tepki hedefi), A3 (sınır başlığı), A8, A10, tekerlek ipucu, uzun etiketler.
2. **Optimizasyon güvenceleri**: A1 (kör bırakan ayarı uygulama), A2 (çit çizgisi kısıtı), A11 (erken çıkış).
3. **PTZ modeli**: devriye odak / eğim / hız araması, üç sütunlu rapor, hız ayarı, tek kamera varsayılanı, "PTZ destekli hedef".
4. **Tek ister nesnesi** + Arazi sağ sütununu adımlara bölmek.
5. **Temel kullanım**: yeni proje, geri al, sayısal giriş, çit köşesi düzenleme, CAD ↔ Arazi kamera bağı, ayarlar.
