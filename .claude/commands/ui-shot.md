---
description: Bir Tk penceresini gerçek ekranda aç, ekran görüntüsü al ve bak
argument-hint: "[pencere: view3d | map3d | workbench | main]"
---

CLAUDE.md doğrulama alışkanlığı: "Kırpılmış etiket, sarmalanmış yazı, taşan kart
kod incelemesinde görünmez, PNG'de bariz."

**Windows'ta `xvfb` yok.** Bunun yerine başsız bir harness yaz: sahte bir üst
uygulama (`FakeApp`) kur, ilgili pencereyi aç, durumu programatik sür, gerçek
pencereden `PIL.ImageGrab` ile PNG al, `scratch/` altına yaz, sonra **oku ve
incele**. Pencere kısa süre masaüstünde görünür — sorun değil.

Hedef pencere ($ARGUMENTS, yoksa `view3d`):

| arg | sınıf | notlar |
|---|---|---|
| `view3d` | `ui.view_3d_window.Camera3DViewWindow(FakeApp())` | `FakeApp` = `root` (`theme.create_app_window`), `_get_active_camera()`, `ppm_levels`, `view_3d_window=None` |
| `map3d` | `ui.map_3d_window.TerrainViewshedWindow(app=None)` | kendi `tk.Tk` kökünü kurar; `w.terrain = generate_procedural_terrain(...)` ata |
| `workbench` | `ui.modern_window.ModernOpticsWorkbench()` | bağımsız `ctk.CTk` |
| `main` | `ui.main_window` klasik uygulama | ağır; yalnız gerekiyorsa |

İskelet (`view3d`):

```python
import time
from cctv_simulator.theme import create_app_window
from cctv_simulator.models import CameraConfig, DEFAULT_LEVELS, PPMLevel
from cctv_simulator.ui.view_3d_window import Camera3DViewWindow
from PIL import ImageGrab

class FakeApp:
    def __init__(self):
        self.root = create_app_window("t"); self.root.withdraw()
        self._cam = CameraConfig(name="K1", focal_min_mm=4, focal_max_mm=25,
                                 pole_height_m=4, tilt_deg=8, ir_range_m=40)
        self.ppm_levels = [PPMLevel(**{k: getattr(x, k)
                           for k in ("key","name","ppm","color","level_type")})
                           for x in DEFAULT_LEVELS]
        self.view_3d_window = None
    def _get_active_camera(self): return self._cam

app = FakeApp(); w = Camera3DViewWindow(app); app.view_3d_window = w
w.window.geometry("1200x760"); w.window.update(); time.sleep(0.3)
w.target_dist_var.set(40); w.render_3d_view(); w.window.update()
c = w.canvas
ImageGrab.grab(bbox=(c.winfo_rootx(), c.winfo_rooty(),
                     c.winfo_rootx()+c.winfo_width(),
                     c.winfo_rooty()+c.winfo_height())).save("scratch/shot.png")
w.close()
```

Çalıştırma: `PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.13 <harness.py>` (harness'i
scratchpad'e yaz). Sonra `scratch/shot.png`'yi Read ile aç, kırpma/taşma/1x1
tuval/yanlış kontrast için bak. Bittiğinde `scratch/` PNG'lerini sil.

`ImageTk.PhotoImage(img, master=widget)` — `master=` zorunlu (CLAUDE.md Kural 5).
