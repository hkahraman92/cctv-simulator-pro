---
description: Optik motor regresyon diff'i — rastgele CameraConfig'lerle eski/yeni çıktıyı karşılaştır
argument-hint: "[örnek sayısı, varsayılan 300]"
---

CLAUDE.md doğrulama alışkanlığı: "`calculations.py` refaktöründe yeni çıktıyı eskisiyle
birkaç yüz rastgele `CameraConfig` üzerinde diff'le. Amaçlanan ve işaretlenmiş
düzeltmeler dışında bit-aynı olmalı."

Adımlar:

1. `git stash` yoksa: mevcut çalışma ağacındaki `calculations.py` = "yeni".
   Karşılaştırma tabanı = `git show HEAD:calculations.py` veya kullanıcının belirttiği ref.
2. Bir kerelik script yaz (`scratch/optics_diff.py`):
   - Sabit seed'li RNG ile $ARGUMENTS (yoksa 300) adet `CameraConfig` üret — sensör,
     lens, çözünürlük, mesafe, yükseklik, açı alanlarını gerçekçi aralıklarda gez.
   - Her config için `calculate_for_camera()` ve `ppm_at_distance()` çağır.
   - Sonuçları iki sürüm için ayrı ayrı JSON'a dök, alan alan diff'le.
3. Farkları raporla: alan adı, config, eski değer, yeni değer, |Δ|.
   Kayan nokta için tolerans 1e-9; üstünü gerçek fark say.
4. Beklenen: yalnızca kullanıcının bu turda amaçladığı düzeltmelerin dokunduğu
   alanlar değişmeli. Açıklanamayan her fark bir regresyon adayıdır — durdur, göster.

Optik motor tek doğruluk kaynağı; arayüz fizik türetmez (CLAUDE.md Kural 1).
