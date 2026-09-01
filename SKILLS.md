# Proje skill'leri

Bu klasör, hesabındaki skill tanımlarının bu projeye kopyalanmış hâli.

**Nasıl çalışır.** Bir klasörü Claude Code ile açtığında `.claude/skills/<ad>/SKILL.md`
dosyaları proje kapsamlı skill olarak okunur. Yani bu dosyalar projeyle birlikte
taşınır; başka bir makinede aynı klasörü açan da aynı skill'leri görür.

**Ne yapmaz.** Bu kopyalar hesabına skill *eklemez*. Cowork tarafında skill'ler
hesap seviyesinde durur ve zaten etkinler; buradaki dosyalar onların bir
kopyası. Bir skill'i hesabına kaydetmek istersen `.skill` dosyası olarak
paketlenip gönderilmesi gerekir.

`_manifest.json` senkronizasyon kaydı, skill değil.
`combined-skills` dahil edilmedi: diğerlerinin hepsinin tek dosyada
birleştirilmiş 2.8 MB'lık kopyasıydı.

---

## Bu projede işe yarayanlar

| Skill | Ne için |
|---|---|
| **cctv-simulator** | Bu oturumda çıkarılan proje kuralları: mimari haritası, optik motor sözleşmesi, sürükleme performansı kuralları, DORI renk sistemi, PyInstaller/Tcl tuzakları. Aşağıda ayrıca anlatılıyor. |
| **codebase-memory** | Yapısal kod sorguları: kim kimi çağırıyor, ölü kod, yüksek fan-out, refactor adayları. `calculate` fan-in 24 bulgusu bununla çıktı. |
| **industrial-brutalist-ui** | Optik tezgâhının görsel dili: katı ızgara, uç tipografi kontrastı, sıfır köşe yarıçapı, hairline bölmeler, analog bozunma dokusu. |
| **docx / pdf / xlsx / pptx** | `exporters.py` bu dört formatı üretiyor. Export tarafına dokunurken ilgili skill'i oku. |
| **caveman / caveman-commit / caveman-compress** | Kısa iletişim biçimi, Conventional Commits mesajları, bellek dosyası sıkıştırma. |
| **skill-creator** | Yeni proje skill'i yazmak veya `cctv-simulator` skill'ini geliştirmek için. |

## Kısmen

| Skill | Not |
|---|---|
| **design-taste-frontend** | Kendi Bölüm 13'ü "dashboard / yoğun ürün arayüzü"nü **kapsam dışı** ilan ediyor, ki bu projenin arayüzü tam olarak o. İşe yarayan kısmı: AI-tell listesi, kopya denetimi, kontrast ve buton kuralları. Landing page kısımları geçerli değil. |

## Bu projeyle ilgisi yok

`ab-testing` (büyüme deneyleri), `morning` (günlük brief), `import-memory`
(başka bir asistandan bellek aktarımı), `caveman-optimize` (Caveman CLI
bağlantısı gerektiriyor).

---

## `cctv-simulator` skill'i neden var

Diğerleri genel amaçlı. Bu oturumda çıkan ve hiçbir genel skill'de yazmayan
şeyler var; onları kaybetmemek için ayrı bir skill'e yazdım:

- Optik motorun tek doğruluk kaynağı olması ve arayüzün fizik türetmemesi.
- Sürükleme olaylarının neden doğrudan `calculate()` çağırmaması gerektiği
  (ölçülen 18.93 ms → 0.69 ms) ve debounce deseninin nasıl kurulduğu.
- `ppm_levels` değiştiğinde hangi cache'lerin geçersizleşmesi gerektiği.
- EN 62676-4 eşikleri, renk sistemi ve DORI bantlarının Pillow ile neden
  kompozit edildiği.
- Optik tezgâh penceresinin neden `CTkToplevel` değil `tk.Toplevel` olduğu.
- Üç ayrı derleme hatası (`tkinter` yok / `init.tcl` yok / `msgcat` yok),
  sebepleri ve kalıcı çözümleri.
- İşe yarayan doğrulama alışkanlıkları: Xvfb + ekran görüntüsü, rastgele
  girdiyle çıktı diff'i, canvas item dökümü karşılaştırması.

Kod değiştikçe bu dosyayı da güncel tut; yanlış bir skill hiç olmamasından
kötüdür.
