"""
3D Camera View (Camera Eye View) & Live DORI Simulation Window.
Provides an interactive 3D perspective viewport simulating what the camera sensor
and security operator see, with real-time PPM degradation/pixelation filters,
mannequin/vehicle targets, IR night vision, and live optics HUD overlay.
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
    ProjectedPoint,
    generate_ground_grid_lines,
    generate_dori_ground_polygons,
)


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

        from ..errors import guarded_build
        self.build_ok = guarded_build(self.window, self._build_ui,
                                      "3D Kamera Bakış Açısı")
        if not self.build_ok:
            return
        self._sync_slider_limits()
        self.render_3d_view()

    _RENDER_INTERVAL_MS = 33        # ~30 fps ceiling

    def schedule_render(self, delay_ms: int = None):
        """Collapse a burst of render requests into one frame."""
        if self._render_job is not None:
            return
        if delay_ms is None:
            delay_ms = self._RENDER_INTERVAL_MS
        self._render_job = self.window.after(delay_ms, self._run_scheduled_render)

    def _run_scheduled_render(self):
        self._render_job = None
        try:
            if not self.window.winfo_exists():
                return
        except tk.TclError:
            return
        self.render_3d_view()

    def close(self):
        if self._render_job is not None:
            try:
                self.window.after_cancel(self._render_job)
            except Exception:
                pass
            self._render_job = None
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
            command=lambda v: self.schedule_render(),   # PERF: per-pixel -> per-frame
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
        self.schedule_render()          # PERF: per-pixel -> per-frame

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
        self.schedule_render()          # PERF: canvas drag -> per-frame

    def render_3d_view(self):
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

        # Theme Styling based on active palette
        if p_mode == "thermal_white":
            sky_bg = "#0B0E11"
            ground_bg = "#1B2227"
            grid_color = "#37474F"
            text_color = "#FFFFFF"
            target_tint = "thermal_white"
        elif p_mode == "thermal_black":
            sky_bg = "#E0E0E0"
            ground_bg = "#ECEFF1"
            grid_color = "#90A4AE"
            text_color = "#000000"
            target_tint = "thermal_black"
        elif p_mode == "thermal_ironbow":
            sky_bg = "#0B0014"
            ground_bg = "#1A237E"
            grid_color = "#283593"
            text_color = "#FFD600"
            target_tint = "thermal_ironbow"
        elif is_night:
            sky_bg = "#0B0E11"
            ground_bg = "#161B1E"
            grid_color = "#2D373D"
            text_color = "#00E676"
            target_tint = "ir"
        else:  # Day
            sky_bg = "#90CAF9"
            ground_bg = "#CFD8DC"
            grid_color = "#90A4AE"
            text_color = "#FFFFFF"
            target_tint = "day"

        horizon_y = engine.get_horizon_y()

        # 3. Draw Sky and Ground Background
        if horizon_y <= 0:
            self.canvas.create_rectangle(0, 0, w, h, fill=ground_bg, outline="")
        elif horizon_y >= h:
            self.canvas.create_rectangle(0, 0, w, h, fill=sky_bg, outline="")
        else:
            self.canvas.create_rectangle(0, 0, w, horizon_y, fill=sky_bg, outline="")
            self.canvas.create_rectangle(0, horizon_y, w, h, fill=ground_bg, outline="")
            self.canvas.create_line(0, horizon_y, w, horizon_y, fill="#78909C", width=1, dash=(4, 4))
            self.canvas.create_text(w - 60, max(horizon_y - 12, 14), text="UFUK ÇİZGİSİ", fill="#546E7A", font=("Segoe UI", 7, "bold"))

        # 4. Draw DORI/DRI Color Zones on Ground (if enabled)
        if self.show_dori_zones_var.get():
            dori_polygons = generate_dori_ground_polygons(engine, self.app.ppm_levels)
            for dori in dori_polygons:
                pts = dori["points"]
                poly_coords = []
                all_visible = True
                for pt in pts:
                    if not pt.visible or pt.depth <= 0.05:
                        all_visible = False
                        break
                    poly_coords.extend([pt.u, pt.v])

                if all_visible and len(poly_coords) == 8:
                    color = dori["color"]
                    stipple_val = "gray12" if (is_night or is_thermal) else "gray25"
                    self.canvas.create_polygon(poly_coords, fill=color, outline=color, stipple=stipple_val, width=1)

        # 5. Draw 3D Ground Perspective Grid & Distance Markers
        if self.show_grid_var.get():
            grid_lines = generate_ground_grid_lines(engine, max_dist_m=max(target_dist * 1.4, 40.0))
            for line in grid_lines:
                p1 = line["p1"]
                p2 = line["p2"]
                if p1.visible and p2.visible and p1.depth > 0.05 and p2.depth > 0.05:
                    width = 2 if line.get("is_center") or line.get("is_major") else 1
                    color = "#37474F" if line.get("is_center") else grid_color
                    self.canvas.create_line(p1.u, p1.v, p2.u, p2.v, fill=color, width=width)

                    if line["type"] == "transverse" and line.get("p_mid"):
                        pm = line["p_mid"]
                        if 0 <= pm.u <= w and 0 <= pm.v <= h:
                            dist_txt = f"{line['distance']:.0f}m"
                            lbl_color = "#263238" if p_mode == "day" else ("#00E676" if is_night else "#ECEFF1")
                            self.canvas.create_text(
                                pm.u + 14,
                                pm.v,
                                text=dist_txt,
                                fill=lbl_color,
                                font=("Segoe UI", 7, "bold"),
                            )

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
        target_h = 1.8 if self.target_type_var.get() == "human" else (2.3 if self.target_type_var.get() == "vehicle" else 1.0)
        ppm = engine.calculate_ppm_at_distance(target_dist, target_h_m=target_h)
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

        # 8. Render Selected 3D Target (with Thermal or Optical Signatures)
        target_type = self.target_type_var.get()
        if target_type == "human":
            self._render_3d_human_mannequin(engine, lateral_offset, target_dist, ppm, target_tint)
        elif target_type == "vehicle":
            self._render_3d_vehicle(engine, lateral_offset, target_dist, ppm, target_tint)
        else:
            self._render_3d_test_chart(engine, lateral_offset, target_dist, ppm, target_tint)

        # 9. Optical Center Crosshair (Reticle)
        cx, cy = w / 2.0, h / 2.0
        cross_len = 12
        reticle_color = "#FF0039" if p_mode == "day" else ("#00E676" if is_night else "#FFD600")
        self.canvas.create_line(cx - cross_len, cy, cx + cross_len, cy, fill=reticle_color, width=1)
        self.canvas.create_line(cx, cy - cross_len, cx, cy + cross_len, fill=reticle_color, width=1)
        self.canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, outline=reticle_color, width=1)

        # 10. Top HUD On-Screen Overlay Card
        self._render_hud_overlay(w, h, camera, focal_mm, target_dist, ppm, target_px, pct_h, dori_name, badge_color, p_mode, engine.is_thermal)

    def _render_3d_human_mannequin(self, engine: Perspective3DEngine, x_m: float, y_m: float, ppm: float, target_tint: Optional[str]):
        """Renders an optically-accurate 1.8m human mannequin with live PPM degradation and thermal heat modeling."""
        p_feet = engine.project_point(Point3D(x_m, y_m, 0.0))
        p_knees = engine.project_point(Point3D(x_m, y_m, 0.5))
        p_waist = engine.project_point(Point3D(x_m, y_m, 0.9))
        p_chest = engine.project_point(Point3D(x_m, y_m, 1.4))
        p_neck = engine.project_point(Point3D(x_m, y_m, 1.5))
        p_head_top = engine.project_point(Point3D(x_m, y_m, 1.8))

        if not p_feet.visible or p_feet.depth <= 0.05:
            return

        mannequin_h = max(abs(p_feet.v - p_head_top.v), 3.0)
        mannequin_w = max(mannequin_h * 0.28, 1.5)
        cx = p_feet.u

        # Ground shadow / footprint
        shadow_rx = mannequin_w * 0.8
        shadow_ry = max(shadow_rx * 0.25, 2.0)
        shadow_col = "#000000" if target_tint in ["day", "ir", "thermal_white"] else "#CFD8DC"
        self.canvas.create_oval(
            cx - shadow_rx, p_feet.v - shadow_ry,
            cx + shadow_rx, p_feet.v + shadow_ry,
            fill=shadow_col, outline="", stipple="gray50"
        )

        # ── Thermal Signature Modes ──
        if target_tint == "thermal_white":
            body_col = "#FFFFFF"
            core_col = "#FFFDE7"
            head_col = "#FFFFFF"
            out_col = "#FFF9C4"
        elif target_tint == "thermal_black":
            body_col = "#000000"
            core_col = "#121212"
            head_col = "#000000"
            out_col = "#212121"
        elif target_tint == "thermal_ironbow":
            body_col = "#FF3D00"
            core_col = "#FFEB3B"
            head_col = "#FFC107"
            out_col = "#D50000"
        elif target_tint == "ir":
            body_col = "#78909C"
            core_col = "#90A4AE"
            head_col = "#B0BEC5"
            out_col = "#00E676"
        else:  # Day
            body_col = "#1565C0"
            core_col = "#ECEFF1"
            head_col = "#FFCC80"
            out_col = "#0D47A1"

        # ── Draw Body & Head with PPM/Resolution Level ──
        target_px = ppm * 1.8
        if ppm >= 250.0 or target_px >= 40:
            self.canvas.create_rectangle(cx - mannequin_w * 0.35, p_knees.v, cx - mannequin_w * 0.08, p_feet.v, fill=body_col, outline=out_col)
            self.canvas.create_rectangle(cx + mannequin_w * 0.08, p_knees.v, cx + mannequin_w * 0.35, p_feet.v, fill=body_col, outline=out_col)
            self.canvas.create_polygon(
                cx - mannequin_w * 0.5, p_chest.v,
                cx + mannequin_w * 0.5, p_chest.v,
                cx + mannequin_w * 0.38, p_waist.v,
                cx - mannequin_w * 0.38, p_waist.v,
                fill=body_col, outline=out_col, width=1.5
            )
            self.canvas.create_oval(cx - mannequin_w * 0.2, p_chest.v + 2, cx + mannequin_w * 0.2, p_waist.v - 2, fill=core_col, outline="")
            head_rx = mannequin_w * 0.26
            head_ry = (p_neck.v - p_head_top.v) * 0.55
            head_cy = (p_neck.v + p_head_top.v) * 0.5
            self.canvas.create_oval(cx - head_rx, head_cy - head_ry, cx + head_rx, head_cy + head_ry, fill=head_col, outline=out_col, width=1)
        elif ppm >= 80.0 or target_px >= 12:
            self.canvas.create_rectangle(cx - mannequin_w * 0.35, p_waist.v, cx + mannequin_w * 0.35, p_feet.v, fill=body_col, outline="")
            self.canvas.create_rectangle(cx - mannequin_w * 0.45, p_neck.v, cx + mannequin_w * 0.45, p_waist.v, fill=body_col, outline="")
            head_rx = mannequin_w * 0.25
            head_ry = (p_neck.v - p_head_top.v) * 0.55
            head_cy = (p_neck.v + p_head_top.v) * 0.5
            self.canvas.create_oval(cx - head_rx, head_cy - head_ry, cx + head_rx, head_cy + head_ry, fill=head_col, outline="")
        elif target_px >= 3.0:
            block_w = max(mannequin_w * 0.4, 2.5)
            self.canvas.create_rectangle(cx - block_w, p_neck.v, cx + block_w, p_feet.v, fill=body_col, outline="")
            head_r = max(mannequin_w * 0.25, 2.0)
            head_cy = (p_neck.v + p_head_top.v) * 0.5
            self.canvas.create_oval(cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r, fill=head_col, outline="")
        else:
            self.canvas.create_rectangle(cx - 1.5, p_head_top.v, cx + 1.5, p_feet.v, fill=body_col, outline="")

        # Floating Distance Tag
        tag_y = max(p_head_top.v - 18, 12)
        tag_txt = f"🧍 {y_m:.0f}m | {target_px:.1f} px"
        self.canvas.create_rectangle(cx - 45, tag_y - 8, cx + 45, tag_y + 8, fill="#212529", outline="#DEE2E6")
        self.canvas.create_text(cx, tag_y, text=tag_txt, fill="#FFFFFF", font=("Segoe UI", 8, "bold"))

    def _render_3d_vehicle(self, engine: Perspective3DEngine, x_m: float, y_m: float, ppm: float, target_tint: Optional[str]):
        """Renders an optically/thermally-modeled vehicle (2.3m wide, 1.6m tall) with engine & tire heat signatures."""
        p_fl = engine.project_point(Point3D(x_m - 1.15, y_m, 0.0))
        p_fr = engine.project_point(Point3D(x_m + 1.15, y_m, 0.0))
        p_hood_l = engine.project_point(Point3D(x_m - 1.15, y_m, 0.8))
        p_hood_r = engine.project_point(Point3D(x_m + 1.15, y_m, 0.8))
        p_roof_l = engine.project_point(Point3D(x_m - 0.95, y_m + 0.8, 1.55))
        p_roof_r = engine.project_point(Point3D(x_m + 0.95, y_m + 0.8, 1.55))
        p_plate = engine.project_point(Point3D(x_m, y_m, 0.4))

        if not p_fl.visible or p_fl.depth <= 0.05:
            return

        if target_tint == "thermal_white":
            body_col = "#78909C"
            engine_col = "#FFFFFF"
            tire_col = "#FFF59D"
            glass_col = "#37474F"
        elif target_tint == "thermal_black":
            body_col = "#424242"
            engine_col = "#000000"
            tire_col = "#000000"
            glass_col = "#ECEFF1"
        elif target_tint == "thermal_ironbow":
            body_col = "#283593"
            engine_col = "#FFEB3B"
            tire_col = "#FF5722"
            glass_col = "#1A237E"
        elif target_tint == "ir":
            body_col = "#37474F"
            engine_col = "#78909C"
            tire_col = "#546E7A"
            glass_col = "#263238"
        else:  # Day
            body_col = "#C62828"
            engine_col = "#B71C1C"
            tire_col = "#212121"
            glass_col = "#90CAF9"

        # Car Body & Windshield
        self.canvas.create_polygon(
            p_roof_l.u, p_roof_l.v,
            p_roof_r.u, p_roof_r.v,
            p_hood_r.u, p_hood_r.v,
            p_hood_l.u, p_hood_l.v,
            fill=glass_col, outline="#1E272C"
        )
        self.canvas.create_polygon(
            p_hood_l.u, p_hood_l.v,
            p_hood_r.u, p_hood_r.v,
            p_fr.u, p_fr.v,
            p_fl.u, p_fl.v,
            fill=body_col, outline="#212121", width=1.5
        )

        grid_w = abs(p_fr.u - p_fl.u) * 0.45
        if grid_w >= 4.0:
            cx = (p_fl.u + p_fr.u) / 2.0
            cy = (p_hood_l.v + p_fl.v) / 2.0
            self.canvas.create_rectangle(cx - grid_w / 2, cy - 4, cx + grid_w / 2, cy + 4, fill=engine_col, outline="")

        if target_tint == "day" and p_plate.visible and p_plate.depth > 0.05:
            plate_w = max(abs(p_fr.u - p_fl.u) * 0.32, 10.0)
            plate_h = max(plate_w * 0.24, 4.0)
            px, py = p_plate.u, p_plate.v
            self.canvas.create_rectangle(px - plate_w / 2, py - plate_h / 2, px + plate_w / 2, py + plate_h / 2, fill="#FFFFFF", outline="#212121", width=1)
            self.canvas.create_rectangle(px - plate_w / 2, py - plate_h / 2, px - plate_w / 2 + plate_w * 0.15, py + plate_h / 2, fill="#1565C0", outline="")
            if ppm >= 145.0 and plate_w >= 36:
                self.canvas.create_text(px + plate_w * 0.08, py, text="34 CCTV 2026", fill="#000000", font=("Segoe UI", int(max(plate_h * 0.65, 6)), "bold"))

        tag_y = max(p_roof_l.v - 16, 12)
        target_px = ppm * 2.3
        tag_txt = f"🚗 Araç | {y_m:.0f}m | {target_px:.1f} px"
        self.canvas.create_rectangle(p_plate.u - 65, tag_y - 8, p_plate.u + 65, tag_y + 8, fill="#212529", outline="#DEE2E6")
        self.canvas.create_text(p_plate.u, tag_y, text=tag_txt, fill="#FFFFFF", font=("Segoe UI", 8, "bold"))

    def _render_3d_test_chart(self, engine: Perspective3DEngine, x_m: float, y_m: float, ppm: float, target_tint: Optional[str]):
        """Renders an optical / thermal calibration chart."""
        p_c = engine.project_point(Point3D(x_m, y_m, 1.0))
        if not p_c.visible or p_c.depth <= 0.05:
            return

        size = max((engine.viewport_h / p_c.depth) * 0.8, 10.0)
        cx, cy = p_c.u, p_c.v

        chart_bg = "#FFFFFF" if target_tint in ["day", "thermal_white"] else "#212121"
        self.canvas.create_rectangle(cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2, fill=chart_bg, outline="#212121", width=2)
        for r_factor in [0.8, 0.6, 0.4, 0.2]:
            r = size * 0.5 * r_factor
            ring_col = "#D50000" if target_tint == "thermal_ironbow" else "#424242"
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=ring_col, width=1)

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
