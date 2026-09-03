---
description: Lint + byte-compile + test paketini çalıştır
---

Sırayla çalıştır, hepsini raporla:

1. `py -3.13 -m ruff check .` — hata yakalayan alt küme (pyproject.toml'de tanımlı)
2. `py -3.13 -m compileall -q cctv_simulator` — tüm paket derleniyor mu
3. `py -3.13 -m pytest -m "not network"` — ağsız birim testleri
4. (isteğe bağlı) `py -3.13 -m pytest -m network` — canlı karo/DEM sunucu testleri

Başarısız adımda çıktıyı olduğu gibi göster, geçerse tek satır "OK" de.

Not: `pytest`/`ruff` yoksa `py -3.13 -m pip install -r requirements-dev.txt`.
Optik motoru değiştirdiysen `/optics-diff` de çalıştır.
