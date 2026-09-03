---
description: Arayüzü başsız başlat, ekran görüntüsü al ve bak
argument-hint: "[pencere: main | workbench | map3d | view3d]"
---

CLAUDE.md doğrulama alışkanlığı: "Tk arayüzünü `xvfb-run` altında başsız çalıştır,
ekran görüntüsü al ve **bak**. Kırpılmış etiket, sarmalanmış yazı, taşan kart kod
incelemesinde görünmez, PNG'de bariz."

Hedef pencere ($ARGUMENTS, yoksa `main`):
- `main`     → `cctv_dual_view_simulator.py`
- `workbench`→ `cctv_optics_workbench.py`
- `map3d` / `view3d` → ana uygulamadan ilgili pencereyi aç

Adımlar:

1. `xvfb-run -a --server-args="-screen 0 1600x1000x24" py -3.13 <giriş>` ile başlat.
   Pencere `after(...)` ile geç kuruluyorsa (view_3d `after(60, ...)`, map_3d tuval
   kuruluşta <100px) ekran görüntüsünden önce birkaç saniye bekle.
2. `scrot` / `import` / uygulama içi export ile PNG al, `scratch/` altına yaz.
3. Görüntüyü **oku ve incele**: kırpılan etiket, saran yazı, taşan kart/radyo butonu,
   1x1 kalan tuval, yanlış tema kontrastı.
4. Bulguları madde madde yaz. Sorun yoksa bunu açıkça söyle.

`ImageTk.PhotoImage(img, master=widget)` — `master=` zorunlu (CLAUDE.md Kural 5).
