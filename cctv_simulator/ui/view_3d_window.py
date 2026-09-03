"""
3D Camera View (Camera Eye View) & Live DORI Simulation Window.

The scene is a PIL frame from ``scene_render.render_camera_frame`` — true field
of view, a silhouette target, MTF blur from the measured ``effective_px_ratio``,
day / IR-night / thermal palettes, and a digital-zoom inset that reveals the
sensor's pixel budget at range. The grid, distance labels, reticle and HUD are
drawn crisp on top as Tk canvas items.
"""
import math
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Dict, List, Tuple, Optional, Any

from ..config import SENSOR_DIMS_MM, RESOLUTIONS
from ..models import CameraConfig, PPMLevel
from ..theme import is_themed, COLORS, StyledButton, fit_and_center_window, set_window_icon
from ..perspective_3d import (
    Perspective3DEngine,
    Point3D,
    generate_ground_grid_lines,
    generate_dori_ground_polygons,
)

try:
    from PIL import Image, ImageTk
    from ..scene_render import render_camera_frame
    _PIL_OK = True
except Exception:  # pragma: no cover - PIL is a hard dep, this is belt-and-braces
    _PIL_OK = False


class Camera3DViewWindow:
    """Standalone / dockable 3D Camera Eye View and live DORI preview window."""

    def __init__(self, parent_app: Any):
        self.app = parent_app
        self.window = tk.Toplevel(self.app.root)
        self.window.title("👁️ 3D Kamera Bakış Açısı ve Canlı DORI Simülatörü (Camera Eye View)")

        fit_and_center_window(self.window, default_w=1280, default_h=780, min_w=900, min_h=580, maximize=False)
        set_window_icon(self.window)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        # State
        self.target_dist_var = tk.DoubleVar(value=15.0)
        self.target_type_var = tk.StringVar(value="human")  # 'human', 'vehicle', 'chart'
        self.lens_mode_var = tk.StringVar(value="min")  # 'min' or 'max'
        self.palette_mode_var = tk.StringVar(value="auto")  # 'auto', 'day', 'ir', 'thermal_white', 'thermal_black', 'thermal_ironbow'
        self.show_dori_zones_var = tk.BooleanVar(value=True)
        self.show_grid_var = tk.BooleanVar(value=True)
        self.target_lateral_offset_var = tk.DoubleVar(value=0.0)  # Lateral offset left/right (m)

        # HUD Info Variables
        self.hud_ppm_var = tk.StringVar(value="-- PPM")
        self.hud_dori_var = tk.StringVar(value="--")
        self.hud_status_color = "#2780E3"
        self.hud_optics_var = tk.StringVar(value="")
        self.hud_algo_var = tk.StringVar(value="")

        # PERF: ttk.Scale fires its command on every pixel of slider travel and
        # <Configure> arrives in bursts; each call rebuilds a Perspective3DEngine,
        # does canvas.delete("all") and issues ~47 create_* calls. Coalesce them.
        self._render_job = None
        self._settle_job = None      # full-quality redraw after a fast drag settles
        self._frame_photo = None

        from ..errors import guarded_build
        self.build_ok = guarded_build(self.window, self._build_ui,
                                      "3D Kamera Bakış Açısı")
        if not self.build_ok:
            return
        self._sync_slider_limits()
        self.render_3d_view()

    _RENDER_INTERVAL_MS = 33        # ~30 fps ceiling
    _SETTLE_MS = 180               # full-quality redraw this long after the last drag event

    def schedule_render(self, delay_ms: int = None, fast: bool = False):
        """Collapse a burst of render requests into one frame.

        ``fast=True`` (slider / canvas drag) renders a cheap low-res frame now
        and queues one full-quality redraw once the drag stops.
        """
        if self._settle_job is not None:
            try:
                self.window.after_cancel(self._settle_job)
            except Exception:
                pass
            self._settle_job = None
        if fast:
            self._settle_job = self.window.after(self._SETTLE_MS, lambda: self._safe_render(fast=False))
        if self._render_job is not None:
            return
        if delay_ms is None:
            delay_ms = self._RENDER_INTERVAL_MS
        self._pending_fast = fast
        self._render_job = self.window.after(delay_ms, self._run_scheduled_render)

    def _run_scheduled_render(self):
        self._render_job = None
        self._safe_render(fast=getattr(self, "_pending_fast", False))

    def _safe_render(self, fast: bool = False):
        try:
            if not self.window.winfo_exists():
                return
        except tk.TclError:
            return
        self.render_3d_view(fast=fast)

    def close(self):
        for attr in ("_render_job", "_settle_job"):
            job = getattr(self, attr, None)
            if job is not None:
                try:
                    self.window.after_cancel(job)
                except Exception:
                    pass
                setattr(self, attr, None)
        if self.app.view_3d_window == self:
            self.app.view_3d_window = None
        self.window.destroy()

    def lift(self):
        self.window.lift()
        self.window.focus_force()

    def update_from_parent(self):
        """Called whenever camera parameters change in the main app.

        PERF: the main window now only calls this on a full (non-light) pass,
        i.e. on mouse release rather than on every drag pixel.
        """
        if not self.window.winfo_exists():
            return
        self._sync_slider_limits()
        self.schedule_render()

    def _sync_slider_limits(self):
        """Scales target distance slider dynamically up to 8000m for thermal cameras / zoom lenses."""
        camera = self.app._get_active_camera()
        sens = camera.sensor_name.upper()
        focal_max = camera.focal_max_mm

        if "LWIR" in sens or "MWIR" in sens or "TERMAL" in camera.model_name.upper():
            max_d = 8000.0 if focal_max >= 200.0 else (3000.0 if focal_max >= 50.0 else 1000.0)
            if self.palette_mode_var.get() == "auto":
                self.palette_mode_var.set("thermal_white")
        elif focal_max >= 50.0:
            max_d = 500.0
        else:
            max_d = 100.0

        if hasattr(self, "scale_dist"):
            self.scale_dist.configure(to=max_d)

    def _build_ui(self):
        # ── Main layout: Top control bar, Center 3D Canvas, Bottom Info Bar ──
        control_bar = ttk.Frame(self.window, padding=(10, 8, 10, 6))
        control_bar.pack(side=tk.TOP, fill=tk.X)

        # Target Distance Control
        ttk.Label(control_bar, text="Hedef Mesafe:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 4))
        self.lbl_dist_val = ttk.Label(control_bar, text=f"{self.target_dist_var.get():.1f} m", font=("Segoe UI", 9, "bold"), foreground=COLORS["accent"], width=8)
        self.lbl_dist_val.pack(side=tk.LEFT, padx=(0, 4))

        self.scale_dist = ttk.Scale(
            control_bar,
            from_=1.0,
            to=100.0,
            variable=self.target_dist_var,
            orient=tk.HORIZONTAL,
            length=160,
            command=self._on_dist_slider_changed,
        )
        self.scale_dist.pack(side=tk.LEFT, padx=(0, 12))

        # Lateral Offset (Left / Right)
        ttk.Label(control_bar, text="Yanal Konum:").pack(side=tk.LEFT, padx=(0, 4))
        scale_lateral = ttk.Scale(
            control_bar,
            from_=-25.0,
            to=25.0,
            variable=self.target_lateral_offset_var,
            orient=tk.HORIZONTAL,
            length=90,
            command=lambda v: self.schedule_render(fast=True),   # PERF: per-pixel -> per-frame
        )
        scale_lateral.pack(side=tk.LEFT, padx=(0, 12))

        # Target Type Combobox
        ttk.Label(control_bar, text="Hedef:").pack(side=tk.LEFT, padx=(0, 4))
        combo_target = ttk.Combobox(
            control_bar,
            textvariable=self.target_type_var,
            values=["🧍 İnsan Mankeni (1.8m)", "🚗 Araç & TR Plaka (1.5m)", "🎯 EN 62676-4 Test Panosu"],
            state="readonly",
            width=22,
        )
        combo_target.current(0)
        combo_target.pack(side=tk.LEFT, padx=(0, 10))
        combo_target.bind("<<ComboboxSelected>>", self._on_target_type_selected)

        # Lens Mode (Wide / Tele)
        ttk.Label(control_bar, text="Lens:").pack(side=tk.LEFT, padx=(0, 4))
        combo_lens = ttk.Combobox(
            control_bar,
            textvariable=self.lens_mode_var,
            values=["min", "max"],
            state="readonly",
            width=5,
        )
        combo_lens.current(0)
        combo_lens.pack(side=tk.LEFT, padx=(0, 10))
        combo_lens.bind("<<ComboboxSelected>>", lambda e: self.render_3d_view())

        # Palette / Sensor Mode Combobox (Optical, IR, Thermal Palettes)
        ttk.Label(control_bar, text="Palet:").pack(side=tk.LEFT, padx=(0, 4))
        self.combo_palette = ttk.Combobox(
            control_bar,
            textvariable=self.palette_mode_var,
            values=[
                "Optik (Gündüz)",
                "🌙 Gece (IR Aydınlatma)",
                "🔥 Termal: White Hot",
                "⚫ Termal: Black Hot",
                "🌈 Termal: Ironbow (Isı)",
            ],
            state="readonly",
            width=18,
        )
        self.combo_palette.current(0)
        self.combo_palette.pack(side=tk.LEFT, padx=(0, 10))
        self.combo_palette.bind("<<ComboboxSelected>>", self._on_palette_selected)

        # Options Checkbuttons
        ttk.Checkbutton(control_bar, text="DORI/DRI", variable=self.show_dori_zones_var, command=self.render_3d_view).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Checkbutton(control_bar, text="Izgara", variable=self.show_grid_var, command=self.render_3d_view).pack(side=tk.LEFT, padx=(0, 10))

        # Action Buttons
        StyledButton(control_bar, text="📸 Snapshot (PNG)", command=self.save_snapshot, bootstyle="info-outline").pack(side=tk.RIGHT)

        # ── Center 3D Canvas ──
        canvas_container = ttk.Frame(self.window)
        canvas_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))

        self.canvas = tk.Canvas(canvas_container, bg="#1E272C", highlightthickness=0, borderwidth=1, relief="sunken")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        # PERF: a resize emits a burst of <Configure>; debounce it.
        self.canvas.bind("<Configure>", lambda e: self.schedule_render(80))
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)

        # ── Bottom Status & HUD Summary Bar ──
        bottom_bar = ttk.Frame(self.window, padding=(12, 6, 12, 8))
        bottom_bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.lbl_hud_dori = tk.Label(
            bottom_bar,
            textvariable=self.hud_dori_var,
            font=("Segoe UI", 10, "bold"),
            fg="#FFFFFF",
            bg="#2780E3",
            padx=10,
            pady=3,
            relief="flat",
        )
        self.lbl_hud_dori.pack(side=tk.LEFT, padx=(0, 12))

        self.lbl_hud_ppm = ttk.Label(
            bottom_bar,
            textvariable=self.hud_ppm_var,
            font=("Segoe UI", 11, "bold"),
            foreground=COLORS["accent"],
        )
        self.lbl_hud_ppm.pack(side=tk.LEFT, padx=(0, 20))

        self.lbl_hud_optics = ttk.Label(
            bottom_bar,
            textvariable=self.hud_optics_var,
            font=("Segoe UI", 9),
            foreground=COLORS["text_fg"],
        )
        self.lbl_hud_optics.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _on_dist_slider_changed(self, val):
        dist = float(val)
        self.lbl_dist_val.configure(text=f"{dist:.1f} m")
        self.schedule_render(fast=True)

    def _on_target_type_selected(self, event=None):
        val = self.target_type_var.get()
        if "İnsan" in val:
            self.target_type_var.set("human")
        elif "Araç" in val:
            self.target_type_var.set("vehicle")
        elif "Test" in val:
            self.target_type_var.set("chart")
        self.render_3d_view()

    def _on_palette_selected(self, event=None):
        val = self.combo_palette.get()
        if "White Hot" in val:
            self.palette_mode_var.set("thermal_white")
        elif "Black Hot" in val:
            self.palette_mode_var.set("thermal_black")
        elif "Ironbow" in val:
            self.palette_mode_var.set("thermal_ironbow")
        elif "Gece" in val:
            self.palette_mode_var.set("ir")
        else:
            self.palette_mode_var.set("day")
        self.render_3d_view()

    def _on_canvas_drag(self, event):
        """Allows clicking and dragging left/right or up/down on canvas to adjust target distance and lateral offset."""
        w = max(self.canvas.winfo_width(), 100)
        h = max(self.canvas.winfo_height(), 100)

        max_d = getattr(self.scale_dist, "cget", lambda k: 100.0)("to") if hasattr(self, "scale_dist") else 100.0
        try:
            max_d = float(max_d)
        except Exception:
            max_d = 100.0

        # Dragging X changes lateral offset
        norm_x = (event.x / w - 0.5) * 2.0
        max_lat = 25.0 if max_d < 200 else (max_d * 0.15)
        self.target_lateral_offset_var.set(round(norm_x * max_lat, 1))

        # Dragging Y changes distance
        norm_y = max(0.05, min(0.95, event.y / h))
        dist = 1.0 + (1.0 - norm_y) * (max_d - 1.0)
        self.target_dist_var.set(round(dist, 1))
        self.lbl_dist_val.configure(text=f"{dist:.1f} m")
        self.schedule_render(fast=True)

    def render_3d_view(self, fast: bool = False):
        """Main rendering pipeline for 3D Camera Eye View."""
        if not self.window.winfo_exists():
            return

        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 100 or h < 100:
            return

        self.canvas.delete("all")

        # 1. Fetch current active camera from main app
        camera = self.app._get_active_camera()
        focal_mm = camera.focal_min_mm if self.lens_mode_var.get() == "min" else camera.focal_max_mm
        target_dist = self.target_dist_var.get()
        lateral_offset = self.target_lateral_offset_var.get()

        engine = Perspective3DEngine(camera, focal_mm=focal_mm, viewport_size=(w, h))

        # 2. Determine active palette mode
        p_mode = self.palette_mode_var.get()
        if p_mode == "auto":
            p_mode = "thermal_white" if engine.is_thermal else "day"

        is_thermal = p_mode in ["thermal_white", "thermal_black", "thermal_ironbow"]
        is_night = p_mode == "ir"

        # Grid overlay tint that reads over the rendered frame.
        if p_mode == "thermal_black":
            grid_color, ground_bg = "#90A4AE", "#ECEFF1"
        elif is_thermal:
            grid_color, ground_bg = "#5A6B78", "#1B2227"
        elif is_night:
            grid_color, ground_bg = "#2E4A3A", "#161B1E"
        else:
            grid_color, ground_bg = "#B0BEC5", "#CFD8DC"

        horizon_y = engine.get_horizon_y()
        target_h = 1.8 if self.target_type_var.get() == "human" else (2.3 if self.target_type_var.get() == "vehicle" else 1.0)
        ppm = engine.calculate_ppm_at_distance(target_dist, target_h_m=target_h)

        # 3+4. Render the degraded camera frame (sky/ground, DORI bands, targets,
        # sensor+MTF resolution loss, palette) as one PIL image and blit it.
        dori_polygons = generate_dori_ground_polygons(engine, self.app.ppm_levels)
        if _PIL_OK:
            frame, _inset_zoom = render_camera_frame(
                engine, w=w, h=h,
                target_type=self.target_type_var.get(),
                target_dist=target_dist, lateral_offset=lateral_offset,
                ppm=ppm, palette=p_mode, k=max(getattr(camera, "effective_px_ratio", 1.0), 0.05),
                show_dori=self.show_dori_zones_var.get(),
                dori_polys=dori_polygons,
                ir_range_m=camera.ir_range_m if is_night else 0.0,
                fast=fast,
            )
            self._frame_photo = ImageTk.PhotoImage(frame, master=self.canvas)
            self.canvas.create_image(0, 0, anchor=tk.NW, image=self._frame_photo)
        else:
            self.canvas.create_rectangle(0, 0, w, h, fill=ground_bg, outline="")

        if 0 < horizon_y < h:
            self.canvas.create_line(0, horizon_y, w, horizon_y, fill="#B0BEC5", width=1, dash=(4, 4))
            self.canvas.create_text(w - 54, max(horizon_y - 12, 14), text="UFUK", fill="#CFD8DC", font=("Segoe UI", 7, "bold"))

        # 5. Draw 3D Ground Perspective Grid & Distance Markers
        if self.show_grid_var.get():
            grid_lines = generate_ground_grid_lines(engine, max_dist_m=max(target_dist * 1.4, 40.0))
            center_col = "#455A64" if p_mode == "day" else "#5A6B78"
            lbl_color = "#1A2327" if p_mode == "day" else ("#00E676" if is_night else "#ECEFF1")
            last_label_v = -999.0
            for line in grid_lines:
                p1 = line["p1"]
                p2 = line["p2"]
                if p1.visible and p2.visible and p1.depth > 0.05 and p2.depth > 0.05:
                    width = 2 if line.get("is_center") or line.get("is_major") else 1
                    color = center_col if line.get("is_center") else grid_color
                    self.canvas.create_line(p1.u, p1.v, p2.u, p2.v, fill=color, width=width)

                    if (line["type"] == "transverse" and line.get("p_mid") and line.get("is_major")):
                        pm = line["p_mid"]
                        if 0 <= pm.u <= w and 0 <= pm.v <= h and abs(pm.v - last_label_v) > 13:
                            last_label_v = pm.v
                            self.canvas.create_text(pm.u + 14, pm.v, text=f"{line['distance']:.0f} m",
                                                    fill=lbl_color, font=("Segoe UI", 7, "bold"))

        # 6. Night Vision IR Illuminator Spotlight Cone
        if is_night and camera.ir_range_m > 0:
            ir_r = camera.ir_range_m
            p_ir = engine.project_point(Point3D(0.0, ir_r, 0.0))
            if p_ir.visible and p_ir.depth > 0.05:
                cone_w = min(w * 0.85, max(120, (1.0 - p_ir.depth / 80.0) * w))
                cone_h = min(h * 0.7, max(80, (1.0 - p_ir.depth / 80.0) * h))
                self.canvas.create_oval(
                    w / 2 - cone_w / 2,
                    p_ir.v - cone_h / 2,
                    w / 2 + cone_w / 2,
                    p_ir.v + cone_h / 2,
                    outline="#00E676",
                    width=1,
                    dash=(6, 6),
                )
                self.canvas.create_text(
                    w / 2,
                    p_ir.v + cone_h / 2 + 10,
                    text=f"IR Aydınlatma Sınırı ({ir_r:.0f}m)",
                    fill="#00E676",
                    font=("Segoe UI", 8, "bold"),
                )

        # 7. Calculate PPM, DRI and Target Telemetry
        dori_name, badge_color, dori_desc = engine.get_dori_status(ppm, target_h_m=target_h)

        target_px = ppm * target_h
        pct_h = (target_px / max(engine.res_h, 1)) * 100.0

        # Update HUD Bottom Bar
        self.hud_dori_var.set(f"🎯 {dori_name.split(' (')[0]}")
        self.lbl_hud_dori.configure(bg=badge_color)

        if engine.is_thermal:
            self.hud_ppm_var.set(f"{target_px:.1f} px  (%{pct_h:.2f})  |  {ppm:.2f} PPM")
            algo_status = "✅ Algoritma %1.5 Eşiği OK" if pct_h >= 1.5 else "⚠️ Algoritma %1.5 Eşiği Altında"
            self.hud_optics_var.set(
                f"{algo_status}  •  {camera.name} | Lens: {focal_mm:.1f}mm | "
                f"HFOV: {engine.hfov_deg:.1f}° | Hedef: {target_dist:.1f}m ({lateral_offset:+.1f}m) | {dori_desc}"
            )
        else:
            self.hud_ppm_var.set(f"{ppm:.1f} PPM")
            res_str = camera.resolution_name.split(" (")[0]
            self.hud_optics_var.set(
                f"Kamera: {camera.name} | Lens: {focal_mm:.1f}mm ({'Geniş' if self.lens_mode_var.get() == 'min' else 'Dar'}) | "
                f"HFOV: {engine.hfov_deg:.1f}° | Direk: {camera.pole_height_m:.1f}m | Hedef: {target_dist:.1f}m | {res_str}"
            )

        # 8. Crisp floating tag over the (degraded) target
        p_head = engine.project_point(Point3D(lateral_offset, target_dist, target_h))
        if p_head.visible and p_head.depth > 0.05 and 0 <= p_head.u <= w:
            tag_y = max(p_head.v - 16, 12)
            icon = "🚗" if self.target_type_var.get() == "vehicle" else ("🎯" if self.target_type_var.get() == "chart" else "🧍")
            tag_txt = f"{icon} {target_dist:.0f} m · {target_px:.0f} px"
            self.canvas.create_rectangle(p_head.u - 52, tag_y - 9, p_head.u + 52, tag_y + 9,
                                         fill="#12151A", outline=badge_color)
            self.canvas.create_text(p_head.u, tag_y, text=tag_txt, fill="#FFFFFF", font=("Segoe UI", 8, "bold"))

        # 9. Optical Center Crosshair (Reticle)
        cx, cy = w / 2.0, h / 2.0
        cross_len = 12
        reticle_color = "#FF0039" if p_mode == "day" else ("#00E676" if is_night else "#FFD600")
        self.canvas.create_line(cx - cross_len, cy, cx + cross_len, cy, fill=reticle_color, width=1)
        self.canvas.create_line(cx, cy - cross_len, cx, cy + cross_len, fill=reticle_color, width=1)
        self.canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, outline=reticle_color, width=1)

        # 10. Top HUD On-Screen Overlay Card
        self._render_hud_overlay(w, h, camera, focal_mm, target_dist, ppm, target_px, pct_h, dori_name, badge_color, p_mode, engine.is_thermal)

    def _render_hud_overlay(self, w: int, h: int, camera: CameraConfig, focal_mm: float, dist_m: float, ppm: float, target_px: float, pct_h: float, dori_name: str, badge_color: str, p_mode: str, is_thermal: bool):
        """Draws a semi-transparent HUD telemetry overlay in the top-left corner."""
        box_w = 290
        box_h = 115 if is_thermal else 95
        margin = 12

        self.canvas.create_rectangle(
            margin, margin,
            margin + box_w, margin + box_h,
            fill="#1E272C",
            outline=badge_color,
            width=1.5,
            stipple="gray75" if p_mode == "day" else "gray50",
        )

        if is_thermal:
            mode_str = f"🔥 TERMAL ({p_mode.replace('thermal_', '').upper()})"
            header_col = "#FFD600"
        elif p_mode == "ir":
            mode_str = "🌙 GECE (IR)"
            header_col = "#00E676"
        else:
            mode_str = "☀️ GÜNDÜZ OPTİK"
            header_col = "#90CAF9"

        self.canvas.create_text(margin + 10, margin + 14, text=f"{camera.name} | {mode_str}", anchor=tk.W, fill=header_col, font=("Segoe UI", 8, "bold"))

        if is_thermal:
            self.canvas.create_text(margin + 10, margin + 36, text=f"{target_px:.1f} px  (%{pct_h:.2f} dikey)", anchor=tk.W, fill=badge_color, font=("Segoe UI", 15, "bold"))
            algo_txt = "✅ Algoritma %1.5 Eşiği: UYUMLU" if pct_h >= 1.5 else "⚠️ Algoritma %1.5 Eşiği: YETERSİZ"
            algo_col = "#00E676" if pct_h >= 1.5 else "#FF5252"
            self.canvas.create_text(margin + 10, margin + 60, text=algo_txt, anchor=tk.W, fill=algo_col, font=("Segoe UI", 8, "bold"))
            self.canvas.create_text(margin + 10, margin + 78, text=f"NATO DRI: {dori_name}", anchor=tk.W, fill="#FFFFFF", font=("Segoe UI", 8, "bold"))
            self.canvas.create_text(margin + 10, margin + 96, text=f"Mesafe: {dist_m:.0f}m  |  Lens: {focal_mm:.0f}mm  |  PPM: {ppm:.2f}", anchor=tk.W, fill="#B0BEC5", font=("Segoe UI", 8))
        else:
            self.canvas.create_text(margin + 10, margin + 38, text=f"{ppm:.1f} PPM", anchor=tk.W, fill=badge_color, font=("Segoe UI", 16, "bold"))
            self.canvas.create_text(margin + 10, margin + 62, text=f"EN 62676-4 DORI: {dori_name}", anchor=tk.W, fill="#FFFFFF", font=("Segoe UI", 8, "bold"))
            self.canvas.create_text(margin + 10, margin + 80, text=f"Mesafe: {dist_m:.1f}m  |  Lens: {focal_mm:.1f}mm", anchor=tk.W, fill="#B0BEC5", font=("Segoe UI", 8))

    def save_snapshot(self):
        """Exports the current 3D canvas viewport as a clean PNG image."""
        try:
            file_path = filedialog.asksaveasfilename(
                title="3D Kamera Görüntüsünü Kaydet",
                defaultextension=".png",
                filetypes=[("PNG Görseli", "*.png"), ("Tüm Dosyalar", "*.*")],
                initialfile=f"3D_Camera_View_{self.app._get_active_camera().name.replace(' ', '_')}.png",
                parent=self.window,
            )
            if not file_path:
                return

            try:
                from PIL import ImageGrab
                x = self.canvas.winfo_rootx()
                y = self.canvas.winfo_rooty()
                w = self.canvas.winfo_width()
                h = self.canvas.winfo_height()
                img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
                img.save(file_path)
                messagebox.showinfo("Başarılı", f"3D Görsel başarıyla kaydedildi:\n{file_path}", parent=self.window)
            except Exception:
                ps_path = file_path.replace(".png", ".ps")
                self.canvas.postscript(file=ps_path, colormode="color")
                messagebox.showinfo("Bilgi", f"3D Çizim Postscript (.ps) formatında kaydedildi:\n{ps_path}", parent=self.window)

        except Exception as exc:
            messagebox.showerror("Hata", f"Görsel kaydedilirken hata oluştu:\n{exc}", parent=self.window)
