"""
3D Topographic Map, Viewshed Analysis, Satellite Orthophoto Layer & Multi-Camera Perimeter Auto-Planner Window.

Provides:
1. True geographic orientation (0 vertical or horizontal mirroring) for Satellite imagery & DEM.
2. HD & Ultra-HD Zoom levels (up to Zoom 18, ~0.5m/pixel) for sharp building & fence recognition.
3. Exact 1:1 isometric metric coordinate transformations (0 pixel drift between Camera and DORI FOV).
4. Online high-resolution satellite downloader (Esri, OSM, OpenTopoMap) with Turkey & custom coordinates.
5. Satellite Orthophoto Layer (.jpg, .png, .tif) with Hybrid Hillshade blending.
6. Layer mode switcher: Satellite (Uydu) / Elevation (Yükselti) / Hybrid (Hibrit).
7. Interactive Zoom In/Out (Mouse Wheel, +/- Buttons) & Pan navigation (Right/Middle Drag).
8. Accurate optical DORI range bounding & dynamic CAD scale bar.
9. Single-camera 3D Viewshed & line-of-sight raymarching.
10. Multi-camera perimeter / fence line auto-planner (50 to 100+ cameras).
11. Bill of Materials (BOM), storage (TB), and bandwidth (Mbps) calculation.
12. Excel / CSV / PNG export of perimeter camera layout.
"""
from __future__ import annotations

import csv
import math
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageTk

from ..config import SENSOR_DIMS_MM, RESOLUTIONS
from ..database import load_camera_library
from ..models import CameraConfig
from ..online_map_loader import (
    PRESET_LOCATIONS,
    TILE_SERVERS,
    clear_tile_cache,
    describe_quality,
    download_satellite_mosaic,
    fetch_online_elevation_grid,
    tile_cache_usage,
)
from ..perimeter_planner import (
    FenceGap,
    PerimeterPlanResult,
    PlacedCamera,
    calculate_optimal_spacing,
    generate_perimeter_plan,
    point_along_polyline,
)
from ..terrain_loader import TerrainData, generate_procedural_terrain, load_geotiff_or_dem
from ..theme import COLORS, StyledButton, fit_and_center_window
from ..viewshed_3d import (
    PPM_DETECT,
    PPM_IDENT,
    PPM_OBSERVE,
    PPM_RECOG,
    ZONE_DETECT,
    ZONE_IDENT,
    ZONE_OBSERVE,
    ZONE_OCCLUDED,
    ZONE_OUT_OF_FOV,
    ZONE_RECOG,
    ViewshedResult,
    calculate_3d_viewshed,
)


# Color Tokens for CAD Topography
BG_DARK = "#14171C"
PANEL_DARK = "#1E2229"
PANEL_BORDER = "#323842"
TEXT_WHITE = "#ECEFF4"
TEXT_MUTED = "#8B949E"
ACCENT_CYAN = "#00E5FF"
ACCENT_AMBER = "#FFB300"
ACCENT_GREEN = "#00E676"
ACCENT_RED = "#FF4D6D"
ACCENT_PURPLE = "#CE93D8"



class _DownloadCancelled(Exception):
    """Raised inside the download worker when its dialog has gone away.

    Tk is not thread-safe and `after()` on a destroyed widget raises TclError.
    Pressing "Vazgeç" mid-download used to kill the worker with an unhandled
    TclError, whose own error handler then raised a second one. This unwinds
    the worker quietly instead.
    """


class TerrainViewshedWindow:
    """Integrated 3D Topography, Satellite Imagery, Viewshed & Multi-Camera Perimeter Auto-Planner."""

    def __init__(self, app=None):
        self.app = app
        self.root = app.root if app is not None else None

        if self.root is not None:
            self.window = tk.Toplevel(self.root)
        else:
            self.window = tk.Tk()

        self.window.title("🗺️ 3D Arazi, HD Uydu Haritası, Görüş Hattı & Çevre Çiti Planlayıcı")
        fit_and_center_window(self.window, default_w=1520, default_h=920, min_w=1080, min_h=700, maximize=False)

        # State & Data (Default: 2000m x 2000m terrain grid)
        self.terrain: TerrainData = generate_procedural_terrain("Hâkim Tepe & Vadi", grid_size=200, cell_size_m=10.0)
        self.camera_library = load_camera_library()
        self.current_camera = self.app._get_active_camera() if self.app is not None else CameraConfig()

        # Operational Mode: "single" vs "perimeter"
        self.planner_mode_var = tk.StringVar(value="single")

        # Map Layer & Satellite Variables
        self.map_layer_mode_var = tk.StringVar(value="hybrid")  # "hybrid", "satellite", "elevation"
        self.satellite_opacity_var = tk.DoubleVar(value=80.0)    # 0 to 100 %
        self.user_satellite_img: Optional[Image.Image] = None
        self.satellite_path_var = tk.StringVar(value="Varsayılan Arazi Uydu Görünümü")

        # Pan & Zoom State (Isometric 1:1 Scale)
        self.zoom_level: float = 1.0       # 0.25x to 10.0x
        self.view_center_x: float = self.terrain.width_m * 0.5
        self.view_center_y: float = self.terrain.height_m * 0.5
        self.mouse_nav_tool_var = tk.StringVar(value="place")  # "place" or "pan"
        self._last_drag_x: Optional[float] = None
        self._last_drag_y: Optional[float] = None

        # Terrain Calibration Var
        self.terrain_width_input_var = tk.StringVar(value=f"{self.terrain.width_m:.0f}")

        # Single Camera Variables
        self.cam_x_var = tk.DoubleVar(value=self.terrain.width_m * 0.5)
        self.cam_y_var = tk.DoubleVar(value=self.terrain.height_m * 0.5)
        self.mast_height_var = tk.DoubleVar(value=12.0)
        self.pan_deg_var = tk.DoubleVar(value=45.0)
        self.tilt_deg_var = tk.DoubleVar(value=-4.0)
        self.lens_mode_var = tk.StringVar(value="min")
        self.max_range_var = tk.DoubleVar(value=500.0)
        self.terrain_preset_var = tk.StringVar(value="Hâkim Tepe & Vadi")
        self.show_contours_var = tk.BooleanVar(value=True)
        self.show_dori_var = tk.BooleanVar(value=True)
        self.earth_curv_var = tk.BooleanVar(value=True)

        # Perimeter Planner Variables
        self.fence_points: List[Tuple[float, float]] = []
        self.is_drawing_fence = False
        self.target_ppm_var = tk.StringVar(value="Algılama / İnsan Tespiti (40 PPM - Standart Çit)")
        self.overlap_pct_var = tk.DoubleVar(value=15.0)
        self.fence_closed_var = tk.BooleanVar(value=True)
        self.selected_pole_id: Optional[int] = None
        self.perimeter_plan: Optional[PerimeterPlanResult] = None

        # Telemetry string vars
        self.telemetry_var = tk.StringVar(value="Harita üzerinde bir noktaya tıklayarak kamerayı konumlandırın.")
        self.stat_cam_elev_var = tk.StringVar(value="-")
        self.stat_vis_area_var = tk.StringVar(value="-")
        self.stat_occ_area_var = tk.StringVar(value="-")
        self.stat_coverage_var = tk.StringVar(value="-")
        self.stat_max_reach_var = tk.StringVar(value="-")
        self.lbl_zoom_readout_var = tk.StringVar(value="🔍 %100")

        # Perimeter BOM Vars
        self.bom_cam_count_var = tk.StringVar(value="-")
        self.bom_fence_len_var = tk.StringVar(value="-")
        self.bom_spacing_var = tk.StringVar(value="-")
        self.bom_bandwidth_var = tk.StringVar(value="-")
        self.bom_storage_var = tk.StringVar(value="-")

        self.result: Optional[ViewshedResult] = None
        self._map_photo = None
        self._render_job = None

        # Cross-section profile <-> map hover linkage.
        # _profile_plot is stashed by _render_profile_canvas so the hover handler
        # can map a mouse-x back to a distance without recomputing the layout.
        self._profile_plot: Optional[dict] = None
        self._profile_hover: Optional[dict] = None

        self._build_ui()
        self._sync_active_camera()
        self._init_default_fence_sample()
        self.window.after(80, self.recalculate_viewshed)

        self.window.protocol("WM_DELETE_WINDOW", self.close)
        try:
            self.window.lift()
            self.window.focus_force()
        except Exception:
            pass

    def close(self):
        if self._render_job is not None:
            try:
                self.window.after_cancel(self._render_job)
            except Exception:
                pass
            self._render_job = None
        if self.app is not None and getattr(self.app, "viewshed_window", None) == self:
            self.app.viewshed_window = None
        self.window.destroy()

    def _init_default_fence_sample(self):
        w, h = self.terrain.width_m, self.terrain.height_m
        cx, cy = w * 0.5, h * 0.5
        rx, ry = w * 0.32, h * 0.32
        self.fence_points = [
            (round(cx - rx, 1), round(cy - ry * 0.5, 1)),
            (round(cx - rx * 0.5, 1), round(cy + ry, 1)),
            (round(cx + rx * 0.5, 1), round(cy + ry, 1)),
            (round(cx + rx, 1), round(cy - ry * 0.2, 1)),
            (round(cx + rx * 0.4, 1), round(cy - ry, 1)),
            (round(cx - rx * 0.4, 1), round(cy - ry, 1)),
        ]

    def _sync_active_camera(self):
        if self.app is not None:
            self.current_camera = self.app._get_active_camera()

        focal_min = self.current_camera.focal_min_mm
        focal_max = self.current_camera.focal_max_mm
        focal_cur = focal_min if self.lens_mode_var.get() == "min" else focal_max
        sens = self.current_camera.sensor_name.upper()

        sw, sh = SENSOR_DIMS_MM.get(self.current_camera.sensor_name, (5.6, 4.2))
        res_w, res_h = RESOLUTIONS.get(self.current_camera.resolution_name, (1920, 1080))

        if "LWIR" in sens or "MWIR" in sens or "TERMAL" in self.current_camera.model_name.upper():
            opt_reach = (focal_cur * res_w) / (sw * 1.3)
            max_limit = min(20000.0, max(2000.0, opt_reach * 1.2))
            self.max_range_var.set(min(opt_reach, self.terrain.width_m))
        else:
            opt_reach = (focal_cur * res_w) / (sw * 25.0)
            max_limit = min(5000.0, max(300.0, opt_reach * 1.5))
            self.max_range_var.set(round(opt_reach, 1))

        if hasattr(self, "slider_range"):
            self.slider_range.configure(to=max_limit)

    def _build_ui(self):
        main_paned = ttk.PanedWindow(self.window, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True)

        # ── LEFT CONTROL RAIL (375px) ──
        left_frame = ttk.Frame(main_paned, padding=(10, 8, 8, 8), width=375)
        main_paned.add(left_frame, weight=0)

        # Mode Selector Notebook
        self.mode_notebook = ttk.Notebook(left_frame)
        self.mode_notebook.pack(fill=tk.BOTH, expand=True)

        self.tab_single = ttk.Frame(self.mode_notebook, padding=6)
        self.tab_perimeter = ttk.Frame(self.mode_notebook, padding=6)

        self.mode_notebook.add(self.tab_single, text="⛰️ Tekil Kamera & Viewshed")
        self.mode_notebook.add(self.tab_perimeter, text="🛡️ Çevre Çiti (50-100 Kamera)")
        self.mode_notebook.bind("<<NotebookTabChanged>>", self._on_mode_tab_changed)

        # ── TAB 1: SINGLE CAMERA VIEWSHED ──
        self._build_single_camera_tab(self.tab_single)

        # ── TAB 2: MULTI-CAMERA PERIMETER AUTO-PLANNER ──
        self._build_perimeter_planner_tab(self.tab_perimeter)

        # ── RIGHT AREA: MAP CANVAS (TOP) + ELEVATION PROFILE / BOM (BOTTOM) ──
        right_frame = ttk.Frame(main_paned)
        main_paned.add(right_frame, weight=1)

        right_paned = ttk.PanedWindow(right_frame, orient=tk.VERTICAL)
        right_paned.pack(fill=tk.BOTH, expand=True)

        # Top Map Canvas Frame
        map_container = ttk.Frame(right_paned)
        right_paned.add(map_container, weight=3)

        # Map Toolbar & Header
        map_header = tk.Frame(map_container, bg=PANEL_DARK, height=36)
        map_header.pack(fill=tk.X)

        # Title
        tk.Label(map_header, text="🗺️ TOPOĞRAFYA & HD UYDU HARİTASI", font=("Segoe UI", 9, "bold"), fg=TEXT_WHITE, bg=PANEL_DARK).pack(side=tk.LEFT, padx=10, pady=4)

        # Navigation & Zoom Buttons Toolbar
        btn_zoom_in = tk.Button(map_header, text="➕ Yakınlaş", font=("Segoe UI", 8, "bold"), fg=ACCENT_CYAN, bg="#262D37", relief="flat", padx=6, pady=2, command=self._zoom_in)
        btn_zoom_in.pack(side=tk.LEFT, padx=2)

        btn_zoom_out = tk.Button(map_header, text="➖ Uzaklaş", font=("Segoe UI", 8, "bold"), fg=ACCENT_CYAN, bg="#262D37", relief="flat", padx=6, pady=2, command=self._zoom_out)
        btn_zoom_out.pack(side=tk.LEFT, padx=2)

        btn_zoom_reset = tk.Button(map_header, text="🔍 Sıfırla (%100)", font=("Segoe UI", 8), fg=TEXT_WHITE, bg="#262D37", relief="flat", padx=6, pady=2, command=self._reset_zoom)
        btn_zoom_reset.pack(side=tk.LEFT, padx=2)

        tk.Label(map_header, textvariable=self.lbl_zoom_readout_var, font=("Segoe UI", 8, "bold"), fg=ACCENT_AMBER, bg=PANEL_DARK).pack(side=tk.LEFT, padx=6)

        # Tool Radio (Place vs Pan)
        tk.Radiobutton(map_header, text="📍 Yerleştir", variable=self.mouse_nav_tool_var, value="place", bg=PANEL_DARK, fg=TEXT_WHITE, selectcolor="#1E2229", activebackground=PANEL_DARK).pack(side=tk.LEFT, padx=(8, 2))
        tk.Radiobutton(map_header, text="✋ Kaydır (Pan)", variable=self.mouse_nav_tool_var, value="pan", bg=PANEL_DARK, fg=TEXT_WHITE, selectcolor="#1E2229", activebackground=PANEL_DARK).pack(side=tk.LEFT, padx=2)

        # Telemetry
        tk.Label(map_header, textvariable=self.telemetry_var, font=("Segoe UI", 8), fg=ACCENT_CYAN, bg=PANEL_DARK).pack(side=tk.RIGHT, padx=10)

        # Interactive Map Canvas
        self.map_canvas = tk.Canvas(map_container, bg=BG_DARK, highlightthickness=0)
        self.map_canvas.pack(fill=tk.BOTH, expand=True)
        self.map_canvas.bind("<Configure>", lambda e: self.schedule_recalculate(80))

        # Mouse Navigation Bindings (Left Click, Middle Drag, Right Drag, Mouse Wheel)
        self.map_canvas.bind("<Button-1>", self._on_canvas_b1_press)
        self.map_canvas.bind("<B1-Motion>", self._on_canvas_b1_motion)
        self.map_canvas.bind("<ButtonRelease-1>", self._on_canvas_b1_release)

        # Middle click / Right click Pan
        self.map_canvas.bind("<Button-2>", self._on_pan_start)
        self.map_canvas.bind("<B2-Motion>", self._on_pan_motion)
        self.map_canvas.bind("<Button-3>", self._on_pan_start)
        self.map_canvas.bind("<B3-Motion>", self._on_pan_motion)

        # Mouse Wheel Zoom
        self.map_canvas.bind("<MouseWheel>", self._on_canvas_mousewheel)
        self.map_canvas.bind("<Button-4>", lambda e: self._zoom_at_point(e.x, e.y, 1.25))
        self.map_canvas.bind("<Button-5>", lambda e: self._zoom_at_point(e.x, e.y, 1.0 / 1.25))

        self.map_canvas.bind("<Motion>", self._on_map_mouse_move)

        # Bottom Profile Canvas Frame
        profile_container = ttk.Frame(right_paned)
        right_paned.add(profile_container, weight=1)

        prof_header = tk.Frame(profile_container, bg=PANEL_DARK, height=24)
        prof_header.pack(fill=tk.X)
        self.lbl_bottom_title = tk.Label(prof_header, text="📐 OPTİK EKSEN ZEMİN & IŞIN KESİT PROFİLİ (ELEVATION PROFILE)", font=("Segoe UI", 8, "bold"), fg=TEXT_MUTED, bg=PANEL_DARK)
        self.lbl_bottom_title.pack(side=tk.LEFT, padx=10, pady=2)

        self.profile_canvas = tk.Canvas(profile_container, bg="#121519", highlightthickness=0, height=140)
        self.profile_canvas.pack(fill=tk.BOTH, expand=True)
        self.profile_canvas.bind("<Motion>", self._on_profile_hover)
        self.profile_canvas.bind("<Leave>", self._on_profile_leave)

    def _build_single_camera_tab(self, parent):
        # Satellite & Layer Settings
        grp_layer = ttk.LabelFrame(parent, text="🛰️ Harita Katmanı & Uydu Görüntüsü", padding=6)
        grp_layer.pack(fill=tk.X, pady=(0, 6))

        frame_mode = ttk.Frame(grp_layer)
        frame_mode.pack(fill=tk.X, pady=2)
        ttk.Label(frame_mode, text="Harita Tipi:").pack(side=tk.LEFT)
        ttk.Radiobutton(frame_mode, text="🌓 Hibrit", variable=self.map_layer_mode_var, value="hybrid", command=self.schedule_recalculate).pack(side=tk.LEFT, padx=4)
        ttk.Radiobutton(frame_mode, text="🛰️ Uydu", variable=self.map_layer_mode_var, value="satellite", command=self.schedule_recalculate).pack(side=tk.LEFT, padx=4)
        ttk.Radiobutton(frame_mode, text="⛰️ Yükselti", variable=self.map_layer_mode_var, value="elevation", command=self.schedule_recalculate).pack(side=tk.LEFT)

        StyledButton(grp_layer, text="🌐 Çevrimiçi Uydu İndir (Koordinat/Bölge)", command=self._open_online_map_downloader_dialog, bootstyle="success").pack(fill=tk.X, pady=(4, 2))
        StyledButton(grp_layer, text="📁 Yerel Uydu / Ortofoto Yükle (.png/.jpg/.tif)", command=self._load_local_satellite, bootstyle="info-outline").pack(fill=tk.X, pady=(0, 2))

        frame_cache = ttk.Frame(grp_layer)
        frame_cache.pack(fill=tk.X, pady=(2, 0))
        self.lbl_tile_cache = ttk.Label(frame_cache, text="", font=("Segoe UI", 8), foreground=ACCENT_CYAN)
        self.lbl_tile_cache.pack(side=tk.LEFT)
        StyledButton(frame_cache, text="🗑️ Önbelleği Temizle", command=self._clear_tile_cache, bootstyle="secondary-outline").pack(side=tk.RIGHT)
        self._refresh_tile_cache_label()

        # Satellite Opacity Slider
        frame_op = ttk.Frame(grp_layer)
        frame_op.pack(fill=tk.X, pady=2)
        ttk.Label(frame_op, text="Uydu Opaklığı:").pack(side=tk.LEFT)
        self.lbl_op_val = ttk.Label(frame_op, text=f"%{self.satellite_opacity_var.get():.0f}", font=("Segoe UI", 8, "bold"), foreground=ACCENT_CYAN)
        self.lbl_op_val.pack(side=tk.RIGHT)
        scale_op = ttk.Scale(grp_layer, from_=0.0, to=100.0, variable=self.satellite_opacity_var, orient=tk.HORIZONTAL, command=self._on_opacity_slider_changed)
        scale_op.pack(fill=tk.X, pady=(0, 4))

        # Terrain Preset & Metraj Calibration
        grp_terrain = ttk.LabelFrame(parent, text="Topoğrafya & Harita Ölçeği (Metraj)", padding=6)
        grp_terrain.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(grp_terrain, text="Arazi Şablonu:").pack(anchor="w")
        combo_terrain = ttk.Combobox(
            grp_terrain,
            textvariable=self.terrain_preset_var,
            values=["Hâkim Tepe & Vadi", "Sınır Karakolu & Dağlık Arazi", "Kanyon & Geçit", "Düzlük & Tümsekler"],
            state="readonly",
        )
        combo_terrain.pack(fill=tk.X, pady=(2, 4))
        combo_terrain.bind("<<ComboboxSelected>>", self._on_terrain_preset_changed)

        StyledButton(grp_terrain, text="📁 Yerel GeoTIFF / DEM Yükle", command=self._load_local_dem, bootstyle="secondary-outline").pack(fill=tk.X, pady=(0, 4))

        frame_scale = ttk.Frame(grp_terrain)
        frame_scale.pack(fill=tk.X, pady=2)
        ttk.Label(frame_scale, text="Harita Genişliği (Metre):").pack(side=tk.LEFT)
        entry_tw = ttk.Entry(frame_scale, textvariable=self.terrain_width_input_var, width=8)
        entry_tw.pack(side=tk.LEFT, padx=4)
        StyledButton(frame_scale, text="Ölçekle", command=self._apply_custom_terrain_width, bootstyle="info-outline").pack(side=tk.LEFT)

        # Camera & Mast Controls
        grp_cam = ttk.LabelFrame(parent, text="Kamera Kulesi & Yönlendirme", padding=6)
        grp_cam.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(grp_cam, text="Kamera Modeli:").pack(anchor="w")
        self.cam_model_var = tk.StringVar(value=self.current_camera.name)
        combo_models = ttk.Combobox(grp_cam, textvariable=self.cam_model_var, values=list(self.camera_library.keys()), state="readonly")
        combo_models.pack(fill=tk.X, pady=(2, 4))
        combo_models.bind("<<ComboboxSelected>>", self._on_camera_model_changed)

        frame_mast = ttk.Frame(grp_cam)
        frame_mast.pack(fill=tk.X, pady=2)
        ttk.Label(frame_mast, text="Kule / Direk Boyu:").pack(side=tk.LEFT)
        self.lbl_mast_val = ttk.Label(frame_mast, text=f"{self.mast_height_var.get():.1f} m", font=("Segoe UI", 9, "bold"), foreground=ACCENT_CYAN)
        self.lbl_mast_val.pack(side=tk.RIGHT)
        scale_mast = ttk.Scale(grp_cam, from_=1.0, to=50.0, variable=self.mast_height_var, orient=tk.HORIZONTAL, command=self._on_slider_changed)
        scale_mast.pack(fill=tk.X, pady=(0, 4))

        frame_pan = ttk.Frame(grp_cam)
        frame_pan.pack(fill=tk.X, pady=2)
        ttk.Label(frame_pan, text="Yön (Pan Açısı):").pack(side=tk.LEFT)
        self.lbl_pan_val = ttk.Label(frame_pan, text=f"{self.pan_deg_var.get():.0f}°", font=("Segoe UI", 9, "bold"))
        self.lbl_pan_val.pack(side=tk.RIGHT)
        scale_pan = ttk.Scale(grp_cam, from_=0.0, to=360.0, variable=self.pan_deg_var, orient=tk.HORIZONTAL, command=self._on_slider_changed)
        scale_pan.pack(fill=tk.X, pady=(0, 4))

        frame_tilt = ttk.Frame(grp_cam)
        frame_tilt.pack(fill=tk.X, pady=2)
        ttk.Label(frame_tilt, text="Eğim (Tilt Açısı):").pack(side=tk.LEFT)
        self.lbl_tilt_val = ttk.Label(frame_tilt, text=f"{self.tilt_deg_var.get():.1f}°", font=("Segoe UI", 9, "bold"))
        self.lbl_tilt_val.pack(side=tk.RIGHT)
        scale_tilt = ttk.Scale(grp_cam, from_=-45.0, to=15.0, variable=self.tilt_deg_var, orient=tk.HORIZONTAL, command=self._on_slider_changed)
        scale_tilt.pack(fill=tk.X, pady=(0, 4))

        frame_range = ttk.Frame(grp_cam)
        frame_range.pack(fill=tk.X, pady=2)
        ttk.Label(frame_range, text="Analiz Menzili:").pack(side=tk.LEFT)
        self.lbl_range_val = ttk.Label(frame_range, text=f"{self.max_range_var.get():.0f} m", font=("Segoe UI", 9, "bold"), foreground=ACCENT_AMBER)
        self.lbl_range_val.pack(side=tk.RIGHT)
        self.slider_range = ttk.Scale(grp_cam, from_=50.0, to=1500.0, variable=self.max_range_var, orient=tk.HORIZONTAL, command=self._on_slider_changed)
        self.slider_range.pack(fill=tk.X, pady=(0, 4))

        frame_lens = ttk.Frame(grp_cam)
        frame_lens.pack(fill=tk.X, pady=2)
        ttk.Label(frame_lens, text="Lens:").pack(side=tk.LEFT)
        ttk.Radiobutton(frame_lens, text="Geniş (Min)", variable=self.lens_mode_var, value="min", command=self._on_lens_mode_toggled).pack(side=tk.LEFT, padx=6)
        ttk.Radiobutton(frame_lens, text="Dar (Tele)", variable=self.lens_mode_var, value="max", command=self._on_lens_mode_toggled).pack(side=tk.LEFT)

        # Stats
        grp_stats = ttk.LabelFrame(parent, text="Görüş & Kapsama İstatistikleri", padding=6)
        grp_stats.pack(fill=tk.X, pady=(0, 4))
        self._create_stat_row(grp_stats, "Hâkim İrtifa:", self.stat_cam_elev_var, ACCENT_CYAN)
        self._create_stat_row(grp_stats, "Görünür Kapsama:", self.stat_vis_area_var, ACCENT_GREEN)
        self._create_stat_row(grp_stats, "Kör Nokta (Tepe Arkası):", self.stat_occ_area_var, ACCENT_RED)
        self._create_stat_row(grp_stats, "Net Görüş Oranı:", self.stat_coverage_var, ACCENT_AMBER)
        self._create_stat_row(grp_stats, "Max Görüş Menzili:", self.stat_max_reach_var, TEXT_WHITE)

    def _build_perimeter_planner_tab(self, parent):
        grp_tools = ttk.LabelFrame(parent, text="Çit & Sınır Çizim Araçları", padding=6)
        grp_tools.pack(fill=tk.X, pady=(0, 6))

        btn_draw = StyledButton(grp_tools, text="✏️ Çit Noktaları Ekle / Çiz (Haritaya Tıkla)", command=self._toggle_fence_drawing, bootstyle="info")
        btn_draw.pack(fill=tk.X, pady=(0, 4))
        self.btn_draw_fence = btn_draw

        row_actions = ttk.Frame(grp_tools)
        row_actions.pack(fill=tk.X, pady=2)
        StyledButton(row_actions, text="⚡ Otomatik Diz", command=self.distribute_perimeter_cameras, bootstyle="success").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
        StyledButton(row_actions, text="🗑️ Temizle", command=self._clear_fence, bootstyle="danger-outline").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

        grp_params = ttk.LabelFrame(parent, text="Dizilim & Güvenlik Kriterleri", padding=6)
        grp_params.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(grp_params, text="Kamera Modeli:").pack(anchor="w")
        self.combo_perim_model = ttk.Combobox(grp_params, textvariable=self.cam_model_var, values=list(self.camera_library.keys()), state="readonly")
        self.combo_perim_model.pack(fill=tk.X, pady=(2, 4))
        self.combo_perim_model.bind("<<ComboboxSelected>>", self._on_camera_model_changed)

        ttk.Label(grp_params, text="Hedef Güvenlik / PPM Standardı:").pack(anchor="w")
        combo_ppm = ttk.Combobox(
            grp_params,
            textvariable=self.target_ppm_var,
            values=[
                "Algılama / İnsan Tespiti (40 PPM - Standart Çit)",
                "Gözlem / Hareket Takibi (80 PPM)",
                "Tanıma / Teşhis Öncesi (125 PPM)",
                "Teşhis / Kimlik Tespiti (250 PPM - Kritik Bölge)",
            ],
            state="readonly",
        )
        combo_ppm.pack(fill=tk.X, pady=(2, 4))
        combo_ppm.bind("<<ComboboxSelected>>", lambda e: self.distribute_perimeter_cameras())

        frame_ov = ttk.Frame(grp_params)
        frame_ov.pack(fill=tk.X, pady=2)
        ttk.Label(frame_ov, text="Kör Nokta Bindirme (Overlap):").pack(side=tk.LEFT)
        self.lbl_overlap_val = ttk.Label(frame_ov, text=f"%{self.overlap_pct_var.get():.0f}", font=("Segoe UI", 9, "bold"), foreground=ACCENT_GREEN)
        self.lbl_overlap_val.pack(side=tk.RIGHT)
        scale_ov = ttk.Scale(grp_params, from_=5.0, to=30.0, variable=self.overlap_pct_var, orient=tk.HORIZONTAL, command=self._on_overlap_slider_changed)
        scale_ov.pack(fill=tk.X, pady=(0, 4))

        frame_ph = ttk.Frame(grp_params)
        frame_ph.pack(fill=tk.X, pady=2)
        ttk.Label(frame_ph, text="Çit Direk Yüksekliği:").pack(side=tk.LEFT)
        self.lbl_perim_mast = ttk.Label(frame_ph, text=f"{self.mast_height_var.get():.1f} m", font=("Segoe UI", 9, "bold"), foreground=ACCENT_CYAN)
        self.lbl_perim_mast.pack(side=tk.RIGHT)
        scale_ph = ttk.Scale(grp_params, from_=3.0, to=12.0, variable=self.mast_height_var, orient=tk.HORIZONTAL, command=self._on_slider_changed)
        scale_ph.pack(fill=tk.X, pady=(0, 4))

        grp_bom = ttk.LabelFrame(parent, text="📊 Keşif & Malzeme Listesi (BOM)", padding=6)
        grp_bom.pack(fill=tk.X, pady=(0, 6))

        self._create_stat_row(grp_bom, "Toplam Kamera / Direk:", self.bom_cam_count_var, ACCENT_CYAN)
        self._create_stat_row(grp_bom, "Toplam Çevre Çiti:", self.bom_fence_len_var, TEXT_WHITE)
        self._create_stat_row(grp_bom, "Ortalama Direk Aralığı:", self.bom_spacing_var, ACCENT_GREEN)
        self._create_stat_row(grp_bom, "Tahmini Ağ Trafiği:", self.bom_bandwidth_var, ACCENT_AMBER)
        self._create_stat_row(grp_bom, "30 Günlük RAID Depolama:", self.bom_storage_var, ACCENT_PURPLE)

        StyledButton(parent, text="💾 Kamera Listesi & BOM İndir (.xlsx / .csv)", command=self._export_perimeter_bom, bootstyle="primary-outline").pack(fill=tk.X, pady=(4, 0))

    # ── TILE CACHE ──
    def _refresh_tile_cache_label(self):
        if not hasattr(self, "lbl_tile_cache"):
            return
        try:
            files, total = tile_cache_usage()
        except Exception:
            files, total = 0, 0
        if files == 0:
            self.lbl_tile_cache.configure(text="Karo önbelleği boş")
        else:
            self.lbl_tile_cache.configure(text=f"Karo önbelleği: {files} kare · {total / 1_048_576:.1f} MB")

    def _clear_tile_cache(self):
        try:
            removed = clear_tile_cache()
        except Exception as exc:
            messagebox.showerror("Önbellek", f"Önbellek temizlenemedi:\n{exc}", parent=self.window)
            return
        self._refresh_tile_cache_label()
        if removed == 0:
            messagebox.showinfo("Önbellek", "Silinecek karo bulunamadı.", parent=self.window)
        else:
            messagebox.showinfo("Önbellek", f"{removed} önbelleklenmiş karo silindi.", parent=self.window)

    # ── SATELLITE & OPACITY HANDLERS ──
    def _load_local_satellite(self):
        file_path = filedialog.askopenfilename(
            title="Uydu Haritası / Ortofoto / Plan Resmi Seç",
            filetypes=[("Resim Dosyaları", "*.jpg *.jpeg *.png *.tif *.tiff *.webp *.bmp"), ("Tüm Dosyalar", "*.*")],
        )
        if not file_path:
            return
        try:
            img = Image.open(file_path)
            self.user_satellite_img = img.convert("RGBA")
            self.satellite_path_var.set(Path(file_path).name)
            self.map_layer_mode_var.set("hybrid")
            self.schedule_recalculate()
            msg = f"'{Path(file_path).name}' uydu haritası araziye başarıyla giydirildi.\nBoyut: {img.width}x{img.height} piksel"
            messagebox.showinfo("Uydu Haritası Yüklendi", msg)
        except Exception as exc:
            messagebox.showerror("Hata", f"Uydu resmi yüklenemedi:\n{exc}")

    def _on_opacity_slider_changed(self, _v=None):
        self.lbl_op_val.configure(text=f"%{self.satellite_opacity_var.get():.0f}")
        self.schedule_recalculate(30)

    # ── ONLINE SATELLITE & DEM DOWNLOADER DIALOG ──
    def _open_online_map_downloader_dialog(self):
        dlg = tk.Toplevel(self.window)
        dlg.title("🌐 Çevrimiçi HD Uydu Haritası & Yükselti İndirici")
        fit_and_center_window(dlg, default_w=620, default_h=710, min_w=540, min_h=620)
        dlg.transient(self.window)
        dlg.grab_set()

        # 1. FIXED BOTTOM ACTION BAR (Always visible at bottom)
        bottom_bar = tk.Frame(dlg, bg=PANEL_DARK, padx=14, pady=12)
        bottom_bar.pack(side=tk.BOTTOM, fill=tk.X)

        status_lbl = ttk.Label(bottom_bar, text="İndirmeye hazır.", font=("Segoe UI", 9, "bold"), foreground=ACCENT_CYAN)
        status_lbl.pack(anchor="w", pady=(0, 6))

        progress_var = tk.DoubleVar(value=0.0)
        pbar = ttk.Progressbar(bottom_bar, variable=progress_var, maximum=100.0)
        pbar.pack(fill=tk.X, pady=(0, 8))

        btn_row = ttk.Frame(bottom_bar)
        btn_row.pack(fill=tk.X)

        # Shared between the Tk thread and the download worker.
        dl_state = {"cancelled": False, "running": False}

        def _close_dialog():
            dl_state["cancelled"] = True
            try:
                dlg.grab_release()
            except tk.TclError:
                pass
            try:
                dlg.destroy()
            except tk.TclError:
                pass

        btn_cancel = StyledButton(btn_row, text="✖️ Vazgeç", command=_close_dialog, bootstyle="secondary-outline")
        btn_cancel.pack(side=tk.LEFT, padx=(0, 8))
        dlg.protocol("WM_DELETE_WINDOW", _close_dialog)

        btn_download = StyledButton(btn_row, text="⬇️ HD UYDU VE YÜKSELTİ VERİSİNİ İNDİR & UYGULA", bootstyle="success")
        btn_download.pack(side=tk.RIGHT, fill=tk.X, expand=True)

        # 2. CONTENT BODY
        frm = ttk.Frame(dlg, padding=(16, 12, 16, 6))
        frm.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="🌐 Çevrimiçi HD Uydu & Sayısal Yükselti İndirici", font=("Segoe UI", 12, "bold"), foreground=TEXT_WHITE).pack(anchor="w", pady=(0, 4))
        ttk.Label(frm, text="Esri World Imagery ve OpenStreetMap üzerinden yüksek çözünürlüklü gerçek uydu ortofotosunu ve 3D arazi modelini doğrudan indirin.", wraplength=570).pack(anchor="w", pady=(0, 10))

        # Location & Coordinates Box
        grp_loc = ttk.LabelFrame(frm, text="1. Konum ve Koordinatlar", padding=10)
        grp_loc.pack(fill=tk.X, pady=(0, 10))

        row_pre = ttk.Frame(grp_loc)
        row_pre.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(row_pre, text="Hazır Bölge:").pack(side=tk.LEFT)
        preset_var = tk.StringVar(value="Hakkari - Çukurca Sınır Hattı")
        combo_pre = ttk.Combobox(row_pre, textvariable=preset_var, values=list(PRESET_LOCATIONS.keys()) + ["-- Özel Koordinat --"], state="readonly", width=38)
        combo_pre.pack(side=tk.LEFT, padx=6)

        row_coords = ttk.Frame(grp_loc)
        row_coords.pack(fill=tk.X, pady=2)
        ttk.Label(row_coords, text="Enlem (Lat):").pack(side=tk.LEFT)
        lat_var = tk.StringVar(value="37.2483")
        entry_lat = ttk.Entry(row_coords, textvariable=lat_var, width=14)
        entry_lat.pack(side=tk.LEFT, padx=6)

        ttk.Label(row_coords, text="Boylam (Lon):").pack(side=tk.LEFT, padx=(12, 0))
        lon_var = tk.StringVar(value="43.6150")
        entry_lon = ttk.Entry(row_coords, textvariable=lon_var, width=14)
        entry_lon.pack(side=tk.LEFT, padx=6)

        def _on_loc_preset_changed(_e=None):
            p = preset_var.get()
            if p in PRESET_LOCATIONS:
                la, lo, _ = PRESET_LOCATIONS[p]
                lat_var.set(f"{la:.4f}")
                lon_var.set(f"{lo:.4f}")

        combo_pre.bind("<<ComboboxSelected>>", _on_loc_preset_changed)

        # Dimension, Source & Quality Box
        grp_opt = ttk.LabelFrame(frm, text="2. Harita Boyutu ve Çözünürlük Kalitesi", padding=10)
        grp_opt.pack(fill=tk.X, pady=(0, 6))

        row_dim = ttk.Frame(grp_opt)
        row_dim.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(row_dim, text="Harita Boyutu:").pack(side=tk.LEFT)
        dim_var = tk.StringVar(value="2000")
        for val, lbl in [("1000", "1 km"), ("2000", "2 km"), ("3000", "3 km"), ("5000", "5 km"), ("10000", "10 km")]:
            ttk.Radiobutton(row_dim, text=lbl, variable=dim_var, value=val).pack(side=tk.LEFT, padx=4)

        # Stacked, not three-across: at 620 px the third option was clipped.
        row_qual = ttk.Frame(grp_opt)
        row_qual.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(row_qual, text="Detay Kalitesi (hedef):").pack(anchor="w", pady=(0, 2))
        qual_var = tk.StringVar(value="hd")
        ttk.Radiobutton(row_qual, text="💎 Yüksek (HD — hedef ~1 m/px)", variable=qual_var, value="hd").pack(anchor="w", pady=1)
        ttk.Radiobutton(row_qual, text="🔍 Ultra HD (hedef ~0.5 m/px — en net)", variable=qual_var, value="ultra_hd").pack(anchor="w", pady=1)
        ttk.Radiobutton(row_qual, text="⚡ Hızlı (Standart — hedef ~2 m/px)", variable=qual_var, value="standard").pack(anchor="w", pady=1)

        row_src = ttk.Frame(grp_opt)
        row_src.pack(fill=tk.X, pady=2)
        ttk.Label(row_src, text="Harita / Uydu Kaynağı:").pack(anchor="w", pady=(0, 2))
        src_var = tk.StringVar(value="esri")
        ttk.Radiobutton(row_src, text="🛰️ Esri World Imagery (Yüksek Çözünürlüklü Gerçek Uydu Fotoğrafı)", variable=src_var, value="esri").pack(anchor="w", pady=2)
        ttk.Radiobutton(row_src, text="🗺️ OpenStreetMap (Cadde, Tesis, Bina ve Yol Planı)", variable=src_var, value="osm").pack(anchor="w", pady=2)
        ttk.Radiobutton(row_src, text="⛰️ OpenTopoMap (Eşyükselti Eğrileri ve Topoğrafik Kabartma)", variable=src_var, value="opentopo").pack(anchor="w", pady=2)

        # The quality labels above are a request, not a promise: at 10 km the
        # server simply does not serve 0.5 m/px. Show what will actually arrive.
        quality_info_lbl = ttk.Label(grp_opt, text="", foreground=ACCENT_CYAN,
                                     font=("Segoe UI", 9, "bold"), wraplength=540,
                                     justify="left")
        quality_info_lbl.pack(anchor="w", pady=(8, 0))
        attrib_lbl = ttk.Label(grp_opt, text="", foreground=TEXT_MUTED,
                               font=("Segoe UI", 8), wraplength=540)
        attrib_lbl.pack(anchor="w", pady=(2, 0))

        def _refresh_quality_info(*_args):
            try:
                lat_now = float(lat_var.get().replace(",", "."))
                w_now = float(dim_var.get())
            except (ValueError, tk.TclError):
                quality_info_lbl.configure(text="")
                return
            src_now = src_var.get()
            quality_info_lbl.configure(
                text="Gerçekleşecek çözünürlük: "
                     + describe_quality(w_now, qual_var.get(), lat_now, src_now))
            attrib_lbl.configure(text=TILE_SERVERS.get(src_now, {}).get("attribution", ""))

        for _var in (dim_var, qual_var, src_var, lat_var):
            _var.trace_add("write", _refresh_quality_info)
        _refresh_quality_info()

        def _post(fn):
            """Hand a callback to the Tk main loop, aborting if the dialog is gone."""
            if dl_state["cancelled"]:
                raise _DownloadCancelled()
            try:
                if not dlg.winfo_exists():
                    raise _DownloadCancelled()
                dlg.after(0, fn)
            except (tk.TclError, RuntimeError):
                # TclError: the widget is gone. RuntimeError: Tk refused a call
                # from this thread ("main thread is not in main loop").
                raise _DownloadCancelled()

        def _do_download():
            if dl_state["running"]:
                return
            try:
                clat = float(lat_var.get().replace(",", "."))
                clon = float(lon_var.get().replace(",", "."))
                w_m = float(dim_var.get())
                source = src_var.get()
                quality = qual_var.get()
            except ValueError:
                messagebox.showerror("Hata", "Lütfen geçerli sayısal koordinatlar girin.", parent=dlg)
                return

            dl_state["running"] = True
            btn_download.configure(state="disabled")
            status_lbl.configure(text="🛰️ Uydu kareleri indiriliyor, lütfen bekleyin...")

            def _thread_worker():
                def _on_progress(done, total):
                    pct = (done / max(total, 1)) * 100.0
                    _post(lambda: progress_var.set(pct))
                    _post(lambda d=done, t=total, p=pct: status_lbl.configure(
                        text=f"🛰️ Uydu kareleri: {d}/{t} (%{p:.0f})"))

                def _on_dem_progress(done, total):
                    pct = (done / max(total, 1)) * 100.0
                    _post(lambda: progress_var.set(pct))
                    _post(lambda d=done, t=total: status_lbl.configure(
                        text=f"⛰️ Yükselti (DEM) kareleri: {d}/{t}"))

                try:
                    sat_img = download_satellite_mosaic(
                        center_lat=clat,
                        center_lon=clon,
                        width_m=w_m,
                        height_m=w_m,
                        source=source,
                        quality=quality,
                        progress_callback=_on_progress,
                    )
                    meta = dict(sat_img.info)
                    _post(lambda: status_lbl.configure(
                        text="⛰️ Gerçek yükselti modeli (DEM) indiriliyor..."))
                    terr = fetch_online_elevation_grid(
                        center_lat=clat,
                        center_lon=clon,
                        width_m=w_m,
                        height_m=w_m,
                        progress_callback=_on_dem_progress,
                    )
                except _DownloadCancelled:
                    return                       # dialog closed; nobody to report to
                except Exception as exc:
                    # `exc` is deleted when the except block ends, so a lambda
                    # closing over it raises NameError when after() finally runs
                    # it - which is why download failures used to show nothing.
                    err = str(exc) or type(exc).__name__
                    dl_state["running"] = False
                    try:
                        _post(lambda: messagebox.showerror(
                            "İndirme Hatası", f"Harita indirilemedi:\n{err}", parent=self.window))
                        _post(lambda: btn_download.configure(state="normal"))
                        _post(lambda: status_lbl.configure(text="İndirme başarısız oldu."))
                    except _DownloadCancelled:
                        pass
                    return

                def _on_success():
                    dl_state["running"] = False
                    self.user_satellite_img = sat_img.convert("RGBA")
                    self.terrain = terr
                    self.terrain_width_input_var.set(f"{terr.width_m:.0f}")
                    self.view_center_x = terr.width_m * 0.5
                    self.view_center_y = terr.height_m * 0.5
                    self.cam_x_var.set(terr.width_m * 0.5)
                    self.cam_y_var.set(terr.height_m * 0.5)
                    self.map_layer_mode_var.set("hybrid")
                    self.satellite_path_var.set(
                        f"{preset_var.get()} ({w_m / 1000:.1f} km · "
                        f"{sat_img.width}x{sat_img.height}px · "
                        f"~{meta.get('m_per_px', 0.0):.2f} m/px)"
                    )
                    self._sync_active_camera()
                    self._init_default_fence_sample()
                    self._refresh_tile_cache_label()
                    if self.planner_mode_var.get() == "perimeter":
                        self.distribute_perimeter_cameras()
                    else:
                        self.recalculate_viewshed()
                    _close_dialog()

                    detail = (
                        f"Bölge     : {preset_var.get()}\n"
                        f"Kapsam    : {w_m / 1000:.1f} km × {w_m / 1000:.1f} km\n"
                        f"Ortofoto  : {sat_img.width}×{sat_img.height} px "
                        f"(zoom {meta.get('zoom', '?')}, ~{meta.get('m_per_px', 0.0):.2f} m/px, "
                        f"{meta.get('tiles_ok', '?')}/{meta.get('tiles_total', '?')} kare)\n"
                        f"Yükselti  : {terr.name}\n"
                        f"Rakım     : {terr.min_elev_m:.0f} m – {terr.max_elev_m:.0f} m\n\n"
                        f"{meta.get('attribution', '')}\n{terr.source_note}"
                    )
                    if terr.is_measured:
                        messagebox.showinfo("Harita ve Yükselti İndirildi", detail,
                                            parent=self.window)
                    else:
                        # Never let synthetic relief pass as a measurement: the
                        # viewshed is nothing but terrain occlusion.
                        messagebox.showwarning(
                            "Uydu indirildi — yükselti TEMSİLİ",
                            "Ortofoto gerçek, ancak gerçek yükselti (DEM) verisi alınamadı.\n"
                            "Arazi temsilîdir; görüş alanı (viewshed) sonuçları BAĞLAYICI DEĞİLDİR.\n\n"
                            + detail,
                            parent=self.window,
                        )

                try:
                    _post(_on_success)
                except _DownloadCancelled:
                    pass

            threading.Thread(target=_thread_worker, daemon=True).start()

        btn_download.configure(command=_do_download)

    # ── ISOMETRIC 1:1 METRIC COORDINATE TRANSFORMS ──
    def _get_px_per_meter(self) -> float:
        w = max(self.map_canvas.winfo_width(), 100)
        h = max(self.map_canvas.winfo_height(), 100)
        margin = 30
        base_scale = min((w - 2 * margin) / max(self.terrain.width_m, 1.0),
                         (h - 2 * margin) / max(self.terrain.height_m, 1.0))
        return base_scale * self.zoom_level

    def world_to_screen_px(self, xm: float, ym: float) -> Tuple[float, float]:
        cx = self.map_canvas.winfo_width() * 0.5
        cy = self.map_canvas.winfo_height() * 0.5
        scale = self._get_px_per_meter()
        px = cx + (xm - self.view_center_x) * scale
        py = cy - (ym - self.view_center_y) * scale
        return px, py

    def screen_px_to_world(self, px: float, py: float) -> Tuple[float, float]:
        cx = self.map_canvas.winfo_width() * 0.5
        cy = self.map_canvas.winfo_height() * 0.5
        scale = max(self._get_px_per_meter(), 1e-6)
        xm = self.view_center_x + (px - cx) / scale
        ym = self.view_center_y - (py - cy) / scale
        return xm, ym

    # ── PAN & ZOOM NAVIGATION HANDLERS ──
    def _zoom_in(self):
        self._zoom_at_center(1.3)

    def _zoom_out(self):
        self._zoom_at_center(1.0 / 1.3)

    def _reset_zoom(self):
        self.zoom_level = 1.0
        self.view_center_x = self.terrain.width_m * 0.5
        self.view_center_y = self.terrain.height_m * 0.5
        self.lbl_zoom_readout_var.set("🔍 %100")
        self.schedule_recalculate()

    def _zoom_at_center(self, factor: float):
        self.zoom_level = max(0.25, min(10.0, self.zoom_level * factor))
        self.lbl_zoom_readout_var.set(f"🔍 %{self.zoom_level * 100:.0f}")
        self.schedule_recalculate()

    def _zoom_at_point(self, mouse_screen_x: float, mouse_screen_y: float, factor: float):
        world_x, world_y = self.screen_px_to_world(mouse_screen_x, mouse_screen_y)
        old_zoom = self.zoom_level
        new_zoom = max(0.25, min(10.0, self.zoom_level * factor))

        if abs(new_zoom - old_zoom) > 0.001:
            self.zoom_level = new_zoom
            self.view_center_x = world_x + (self.view_center_x - world_x) * (old_zoom / new_zoom)
            self.view_center_y = world_y + (self.view_center_y - world_y) * (old_zoom / new_zoom)
            self.lbl_zoom_readout_var.set(f"🔍 %{self.zoom_level * 100:.0f}")
            self.schedule_recalculate()

    def _on_canvas_mousewheel(self, event):
        factor = 1.25 if event.delta > 0 else (1.0 / 1.25)
        self._zoom_at_point(event.x, event.y, factor)

    def _on_pan_start(self, event):
        self._last_drag_x = event.x
        self._last_drag_y = event.y

    def _on_pan_motion(self, event):
        if self._last_drag_x is None or self._last_drag_y is None:
            self._last_drag_x = event.x
            self._last_drag_y = event.y
            return

        dx_px = event.x - self._last_drag_x
        dy_px = event.y - self._last_drag_y
        self._last_drag_x = event.x
        self._last_drag_y = event.y

        scale = max(self._get_px_per_meter(), 1e-6)
        self.view_center_x -= dx_px / scale
        self.view_center_y += dy_px / scale

        self.schedule_recalculate(20)

    def _on_canvas_b1_press(self, event):
        if self.mouse_nav_tool_var.get() == "pan":
            self._on_pan_start(event)
        else:
            self._on_map_clicked(event)

    def _on_canvas_b1_motion(self, event):
        if self.mouse_nav_tool_var.get() == "pan":
            self._on_pan_motion(event)
        else:
            self._on_map_dragged(event)

    def _on_canvas_b1_release(self, event):
        self._last_drag_x = None
        self._last_drag_y = None

    def _apply_custom_terrain_width(self):
        try:
            val = float(self.terrain_width_input_var.get().replace(",", "."))
            if val < 50.0 or val > 500000.0:
                messagebox.showwarning("Geçersiz Metraj", "Harita genişliği 50 metre ile 500.000 metre arasında olmalıdır.")
                return
            self.terrain.set_custom_width_m(val)
            self._sync_active_camera()
            self._init_default_fence_sample()
            self.recalculate_viewshed()
            msg = f"Harita genişliği {val:,.0f} metre ({val/1000.0:.2f} km) olarak kalibre edildi.\nHücre çözünürlüğü: {self.terrain.cell_size_m:.2f} m/piksel"
            messagebox.showinfo("Ölçek Güncellendi", msg)
        except ValueError:
            messagebox.showerror("Hata", "Lütfen geçerli bir sayısal metraj girin.")

    def _on_lens_mode_toggled(self):
        self._sync_active_camera()
        self.schedule_recalculate()

    def _on_mode_tab_changed(self, _e=None):
        selected_idx = self.mode_notebook.index(self.mode_notebook.select())
        self.planner_mode_var.set("single" if selected_idx == 0 else "perimeter")
        if self.planner_mode_var.get() == "perimeter":
            self.lbl_bottom_title.configure(text="📊 50-100 KAMERALIK ÇEVRE ÇİTİ DİZİLİMİ & KULE MATRİSİ (PERIMETER SCHEDULE)")
            if self.perimeter_plan is None:
                self.distribute_perimeter_cameras()
        else:
            self.lbl_bottom_title.configure(text="📐 OPTİK EKSEN ZEMİN & IŞIN KESİT PROFİLİ (ELEVATION PROFILE)")
        self.recalculate_viewshed()

    def _toggle_fence_drawing(self):
        self.is_drawing_fence = not self.is_drawing_fence
        if self.is_drawing_fence:
            self.btn_draw_fence.configure(text="🛑 Çizimi Bitir / Kilitle", bootstyle="warning")
            self.telemetry_var.set("Harita üzerinde sırayla tıklayarak çevre çiti köşe noktalarını belirleyin.")
        else:
            self.btn_draw_fence.configure(text="✏️ Çit Noktaları Ekle / Çiz (Haritaya Tıkla)", bootstyle="info")
            self.distribute_perimeter_cameras()

    def _clear_fence(self):
        self.fence_points.clear()
        self.perimeter_plan = None
        self.recalculate_viewshed()

    def _on_overlap_slider_changed(self, _v=None):
        self.lbl_overlap_val.configure(text=f"%{self.overlap_pct_var.get():.0f}")
        self.distribute_perimeter_cameras()

    def distribute_perimeter_cameras(self):
        if len(self.fence_points) < 2:
            return

        ppm_text = self.target_ppm_var.get()
        if "40" in ppm_text:
            target_ppm = 40.0
        elif "80" in ppm_text:
            target_ppm = 80.0
        elif "125" in ppm_text:
            target_ppm = 125.0
        elif "250" in ppm_text:
            target_ppm = 250.0
        else:
            target_ppm = 40.0

        overlap_frac = self.overlap_pct_var.get() / 100.0
        mast_h = self.mast_height_var.get()

        self.perimeter_plan = generate_perimeter_plan(
            terrain=self.terrain,
            fence_points=self.fence_points,
            camera=self.current_camera,
            target_ppm=target_ppm,
            overlap_pct=overlap_frac,
            mast_height_m=mast_h,
            lens_mode=self.lens_mode_var.get(),
            is_closed_loop=self.fence_closed_var.get(),
        )

        p = self.perimeter_plan
        self.bom_cam_count_var.set(f"{p.camera_count} Adet Kamera / Direk")
        if p.total_fence_length_m >= 1000:
            self.bom_fence_len_var.set(f"{p.total_fence_length_m:,.0f} m ({p.total_fence_length_m / 1000.0:.2f} km)")
        else:
            self.bom_fence_len_var.set(f"{p.total_fence_length_m:,.0f} m")

        self.bom_spacing_var.set(f"{p.avg_spacing_m:.1f} metre")
        self.bom_bandwidth_var.set(f"{p.estimated_bandwidth_mbps:.1f} Mbps")
        self.bom_storage_var.set(f"{p.estimated_storage_30days_tb:.1f} TB")

        self.recalculate_viewshed()

    def _export_perimeter_bom(self):
        if not self.perimeter_plan or not self.perimeter_plan.placed_cameras:
            messagebox.showwarning("Kayıt", "Dışa aktarılacak çevre çiti planı bulunamadı. Lütfen önce 'Otomatik Diz' butonuna basın.")
            return

        file_path = filedialog.asksaveasfilename(
            title="Perimetre Kamera Listesi & BOM Kaydet",
            defaultextension=".csv",
            filetypes=[("CSV Dosyası", "*.csv"), ("Tüm Dosyalar", "*.*")],
        )
        if not file_path:
            return

        measured = bool(getattr(self.terrain, "is_measured", False))
        try:
            with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["# Arazi kaynağı", self.terrain.name])
                writer.writerow(["# Yükselti verisi", "ÖLÇÜLMÜŞ DEM" if measured else "TEMSİLİ — ölçüm değil, sonuçlar bağlayıcı değildir"])
                writer.writerow(["# Not", getattr(self.terrain, "source_note", "").replace("\n", " ")])
                writer.writerow([])
                writer.writerow(["Direk No", "Kamera Modeli", "Sensör", "Çözünürlük", "Konum X (m)", "Konum Y (m)", "Zemin Rakımı (m)", "Direk Boyu (m)", "Toplam İrtifa (m)", "Pan Açısı (°)", "Tilt Açısı (°)", "Odak (mm)", "HFOV (°)", "Etkin Menzil (m)", "Kör Nokta (m)"])
                for cam in self.perimeter_plan.placed_cameras:
                    writer.writerow([
                        cam.pole_id,
                        cam.camera_model,
                        cam.sensor_name,
                        cam.resolution_name,
                        cam.x_m,
                        cam.y_m,
                        cam.ground_z_m,
                        cam.mast_height_m,
                        cam.total_z_m,
                        cam.pan_deg,
                        cam.tilt_deg,
                        cam.focal_mm,
                        cam.hfov_deg,
                        cam.effective_range_m,
                        cam.dead_zone_m,
                    ])
            note = "" if measured else (
                "\n\n⚠ Arazi yükseltisi TEMSİLİ (ölçülmüş DEM değil). "
                "Zemin rakımı, toplam irtifa ve kör nokta değerleri bağlayıcı değildir."
            )
            messagebox.showinfo("BOM Aktarıldı", f"Toplam {len(self.perimeter_plan.placed_cameras)} kameralık perimetre listesi başarıyla kaydedildi:\n{file_path}{note}")
        except Exception as exc:
            messagebox.showerror("Kayıt Hatası", f"Dosya kaydedilemedi:\n{exc}")

    def _create_stat_row(self, parent, label: str, var: tk.StringVar, color: str):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=1)
        ttk.Label(row, text=label, font=("Segoe UI", 8)).pack(side=tk.LEFT)
        lbl_val = tk.Label(row, textvariable=var, font=("Segoe UI", 8, "bold"), fg=color, bg=PANEL_DARK)
        lbl_val.pack(side=tk.RIGHT)

    def _on_slider_changed(self, _val=None):
        self.lbl_mast_val.configure(text=f"{self.mast_height_var.get():.1f} m")
        self.lbl_pan_val.configure(text=f"{self.pan_deg_var.get():.0f}°")
        self.lbl_tilt_val.configure(text=f"{self.tilt_deg_var.get():.1f}°")
        self.lbl_range_val.configure(text=f"{self.max_range_var.get():.0f} m")
        if hasattr(self, "lbl_perim_mast"):
            self.lbl_perim_mast.configure(text=f"{self.mast_height_var.get():.1f} m")
        if self.planner_mode_var.get() == "perimeter":
            self.distribute_perimeter_cameras()
        else:
            self.schedule_recalculate()

    def _on_terrain_preset_changed(self, _e=None):
        preset = self.terrain_preset_var.get()
        self.terrain = generate_procedural_terrain(preset, grid_size=200, cell_size_m=10.0)
        self.terrain_width_input_var.set(f"{self.terrain.width_m:.0f}")
        self.view_center_x = self.terrain.width_m * 0.5
        self.view_center_y = self.terrain.height_m * 0.5
        self.cam_x_var.set(self.terrain.width_m * 0.5)
        self.cam_y_var.set(self.terrain.height_m * 0.5)
        self.user_satellite_img = None
        self._init_default_fence_sample()
        if self.planner_mode_var.get() == "perimeter":
            self.distribute_perimeter_cameras()
        else:
            self.recalculate_viewshed()

    def _load_local_dem(self):
        file_path = filedialog.askopenfilename(
            title="Sayısal Yükseklik Modeli (DEM / GeoTIFF / PNG) Seç",
            filetypes=[("GeoTIFF / DEM / Images", "*.tif *.tiff *.dem *.asc *.png"), ("Tüm Dosyalar", "*.*")],
        )
        if not file_path:
            return
        try:
            self.terrain = load_geotiff_or_dem(file_path)
            self.terrain_width_input_var.set(f"{self.terrain.width_m:.0f}")
            self.view_center_x = self.terrain.width_m * 0.5
            self.view_center_y = self.terrain.height_m * 0.5
            self.cam_x_var.set(self.terrain.width_m * 0.5)
            self.cam_y_var.set(self.terrain.height_m * 0.5)
            self.terrain_preset_var.set(self.terrain.name)
            self._sync_active_camera()
            self._init_default_fence_sample()
            self.recalculate_viewshed()
            msg = f"'{self.terrain.name}' arazisi başarıyla yüklendi.\nBoyut: {self.terrain.cols}x{self.terrain.rows} hücre ({self.terrain.width_m:,.0f}m x {self.terrain.height_m:,.0f}m)"
            messagebox.showinfo("DEM Yüklendi", msg)
        except Exception as exc:
            messagebox.showerror("Yükleme Hatası", f"DEM dosyası yüklenemedi:\n{exc}")

    def _on_camera_model_changed(self, _e=None):
        model_name = self.cam_model_var.get()
        if model_name in self.camera_library:
            data = self.camera_library[model_name]
            self.current_camera.name = model_name
            self.current_camera.model_name = model_name
            if data.get("sensor_name") in SENSOR_DIMS_MM:
                self.current_camera.sensor_name = data["sensor_name"]
            if data.get("resolution_name") in RESOLUTIONS:
                self.current_camera.resolution_name = data["resolution_name"]
            self.current_camera.focal_min_mm = float(data.get("focal_min_mm", 4.0))
            self.current_camera.focal_max_mm = float(data.get("focal_max_mm", 12.0))
            self._sync_active_camera()
            if self.planner_mode_var.get() == "perimeter":
                self.distribute_perimeter_cameras()
            else:
                self.recalculate_viewshed()

    def _on_map_clicked(self, event):
        world_x, world_y = self.screen_px_to_world(event.x, event.y)

        if 0.0 <= world_x <= self.terrain.width_m and 0.0 <= world_y <= self.terrain.height_m:
            if self.is_drawing_fence:
                self.fence_points.append((round(world_x, 1), round(world_y, 1)))
                self.distribute_perimeter_cameras()
            elif self.planner_mode_var.get() == "perimeter" and self.perimeter_plan:
                for cam in self.perimeter_plan.placed_cameras:
                    if math.hypot(cam.x_m - world_x, cam.y_m - world_y) < (self.terrain.width_m * 0.04 / self.zoom_level):
                        self.selected_pole_id = cam.pole_id
                        self.telemetry_var.set(f"📍 Seçilen Direk #{cam.pole_id}: ({cam.x_m:.0f}m, {cam.y_m:.0f}m) · Z: {cam.total_z_m:.1f}m · Yön: {cam.pan_deg:.0f}° · Menzil: {cam.effective_range_m:.0f}m")
                        self.recalculate_viewshed()
                        return
            else:
                self.cam_x_var.set(round(world_x, 1))
                self.cam_y_var.set(round(world_y, 1))
                self.schedule_recalculate(40)

    def _on_map_dragged(self, event):
        if not self.is_drawing_fence and self.planner_mode_var.get() == "single" and self.mouse_nav_tool_var.get() == "place":
            self._on_map_clicked(event)

    def _on_map_mouse_move(self, event):
        world_x, world_y = self.screen_px_to_world(event.x, event.y)

        if 0.0 <= world_x <= self.terrain.width_m and 0.0 <= world_y <= self.terrain.height_m:
            elev = self.terrain.get_elevation_at(world_x, world_y)

            if self.planner_mode_var.get() == "perimeter" and self.perimeter_plan:
                self.telemetry_var.set(f"Konum: ({world_x:.0f}m, {world_y:.0f}m) · Rakım: {elev:.1f} m · Toplam Çit: {self.perimeter_plan.total_fence_length_m:,.0f} m · {self.perimeter_plan.camera_count} Kamera")
            else:
                dx = world_x - self.cam_x_var.get()
                dy = world_y - self.cam_y_var.get()
                dist = math.hypot(dx, dy)

                status_text = ""
                if self.result is not None:
                    c = int((world_x - self.terrain.origin_x) / self.terrain.cell_size_m)
                    r = int((world_y - self.terrain.origin_y) / self.terrain.cell_size_m)
                    if 0 <= r < self.terrain.rows and 0 <= c < self.terrain.cols:
                        code = self.result.dori_grid[r, c]
                        ppm = self.result.ppm_grid[r, c]
                        if code == ZONE_OCCLUDED:
                            status_text = " · ⚠️ KÖR NOKTA (Tepe Arkası)"
                        elif code == ZONE_IDENT:
                            status_text = f" · 🟢 Teşhis ({ppm:.0f} px/m)"
                        elif code == ZONE_RECOG:
                            status_text = f" · 🟡 Tanıma ({ppm:.0f} px/m)"
                        elif code == ZONE_OBSERVE:
                            status_text = f" · 🟠 Gözlem ({ppm:.0f} px/m)"
                        elif code == ZONE_DETECT:
                            status_text = f" · 🔴 Algılama ({ppm:.0f} px/m)"

                self.telemetry_var.set(
                    f"Konum: ({world_x:.0f}m, {world_y:.0f}m) · Rakım: {elev:.1f} m · Mesafe: {dist:.1f} m{status_text}"
                )

    def schedule_recalculate(self, delay_ms: int = 50):
        if self._render_job is not None:
            return
        self._render_job = self.window.after(delay_ms, self._run_scheduled_recalc)

    def _run_scheduled_recalc(self):
        self._render_job = None
        self.recalculate_viewshed()

    def recalculate_viewshed(self):
        if not self.window.winfo_exists():
            return

        cam_x = self.cam_x_var.get()
        cam_y = self.cam_y_var.get()
        mast_h = self.mast_height_var.get()
        pan = self.pan_deg_var.get()
        tilt = self.tilt_deg_var.get()
        lens = self.lens_mode_var.get()
        max_r = self.max_range_var.get()
        curv = self.earth_curv_var.get()

        self.result = calculate_3d_viewshed(
            terrain=self.terrain,
            cam_x_m=cam_x,
            cam_y_m=cam_y,
            mast_height_m=mast_h,
            camera=self.current_camera,
            lens_mode=lens,
            pan_deg=pan,
            tilt_deg=tilt,
            max_range_m=max_r,
            earth_curvature=curv,
            ray_step_m=3.5,
        )

        r = self.result
        self.stat_cam_elev_var.set(f"{r.cam_ground_z_m:.1f} m + {r.mast_height_m:.1f} m = {r.cam_total_z_m:.1f} m")
        if r.visible_area_m2 >= 1e6:
            self.stat_vis_area_var.set(f"{r.visible_area_m2 / 1e6:.2f} km²")
        else:
            self.stat_vis_area_var.set(f"{r.visible_area_m2:,.0f} m²")

        self.stat_occ_area_var.set(f"{r.occluded_area_m2:,.0f} m²")
        self.stat_coverage_var.set(f"% {r.coverage_pct:.1f}")
        self.stat_max_reach_var.set(f"{r.max_los_reach_m:.1f} m (Optik Sınır: {r.optical_limit_m:.0f}m)")

        self._render_map_canvas()
        self._render_profile_canvas()

    # ── MAP CANVAS RENDERING WITH TRUE ORIENTATION & HD RESOLUTION ──
    def _render_map_canvas(self):
        cv = self.map_canvas
        cv.delete("all")
        w = cv.winfo_width()
        h = cv.winfo_height()
        if w < 50 or h < 50:
            return

        rows, cols = self.terrain.rows, self.terrain.cols
        z_grid = self.terrain.z_grid
        z_min, z_max = self.terrain.min_elev_m, self.terrain.max_elev_m
        z_range = max(z_max - z_min, 1.0)
        norm_z = (z_grid - z_min) / z_range

        # 1. Base Hillshade Matrix
        dz_dx, dz_dy = np.gradient(z_grid)
        slope = np.hypot(dz_dx, dz_dy)
        aspect = np.arctan2(-dz_dy, -dz_dx)
        sun_azimuth = np.radians(315.0)
        sun_altitude = np.radians(45.0)
        shaded = np.sin(sun_altitude) + np.cos(sun_altitude) * slope * 0.08 * np.cos(sun_azimuth - aspect)
        shaded = np.clip(shaded, 0.45, 1.35)

        layer_mode = self.map_layer_mode_var.get()
        sat_opacity = self.satellite_opacity_var.get() / 100.0

        # Build / Fetch Satellite Texture at Full High-Resolution
        if self.user_satellite_img is not None:
            base_w = self.user_satellite_img.width
            base_h = self.user_satellite_img.height

            # Generate North-Up Elevation Hillshade Texture
            r_elev = np.clip((norm_z * 240 + 20) * shaded, 0, 255).astype(np.uint8)
            g_elev = np.clip(((1.0 - np.abs(norm_z - 0.4) * 1.5) * 180 + 35) * shaded, 0, 255).astype(np.uint8)
            b_elev = np.clip((norm_z * 160 + 25) * shaded, 0, 255).astype(np.uint8)
            elev_arr = np.dstack((r_elev, g_elev, b_elev))
            elev_img = Image.fromarray(np.flipud(elev_arr), mode="RGB").resize((base_w, base_h), Image.Resampling.BILINEAR).convert("RGBA")

            if layer_mode == "satellite":
                full_map_img = self.user_satellite_img.copy()
            elif layer_mode == "elevation":
                full_map_img = elev_img
            else:
                # Hybrid: Blend high-res satellite with North-Up hillshade without flipping satellite!
                sat_rgba = self.user_satellite_img.convert("RGBA")
                full_map_img = Image.blend(sat_rgba, elev_img, 1.0 - sat_opacity)

        else:
            # Procedural Photorealistic Satellite Orthophoto Generator (Render at sharp 800x800)
            base_w, base_h = max(cols * 4, 800), max(rows * 4, 800)
            r_sat = np.clip((norm_z * 180 + 70) * shaded, 0, 255).astype(np.uint8)
            g_sat = np.clip(((1.0 - norm_z) * 130 + 65) * shaded, 0, 255).astype(np.uint8)
            b_sat = np.clip((norm_z * 100 + 45) * shaded, 0, 255).astype(np.uint8)
            sat_arr = np.dstack((r_sat, g_sat, b_sat))

            r_elev = np.clip((norm_z * 240 + 20) * shaded, 0, 255).astype(np.uint8)
            g_elev = np.clip(((1.0 - np.abs(norm_z - 0.4) * 1.5) * 180 + 35) * shaded, 0, 255).astype(np.uint8)
            b_elev = np.clip((norm_z * 160 + 25) * shaded, 0, 255).astype(np.uint8)
            elev_arr = np.dstack((r_elev, g_elev, b_elev))

            if layer_mode == "satellite":
                comp = sat_arr
            elif layer_mode == "elevation":
                comp = elev_arr
            else:
                comp = np.clip(sat_arr * sat_opacity + elev_arr * (1.0 - sat_opacity), 0, 255).astype(np.uint8)

            full_map_img = Image.fromarray(np.flipud(comp), mode="RGB").resize((base_w, base_h), Image.Resampling.BILINEAR).convert("RGBA")

        # 2. Composite DORI Overlay on full terrain grid (North-Up)
        if self.planner_mode_var.get() == "single" and self.show_dori_var.get() and self.result is not None:
            if self.result.dori_grid.shape == (rows, cols):
                dori = np.flipud(self.result.dori_grid)
                dori_overlay = np.zeros((rows, cols, 4), dtype=np.uint8)
                dori_overlay[dori == ZONE_IDENT] = [0, 230, 118, 140]
                dori_overlay[dori == ZONE_RECOG] = [255, 179, 0, 125]
                dori_overlay[dori == ZONE_OBSERVE] = [255, 112, 67, 110]
                dori_overlay[dori == ZONE_DETECT] = [255, 77, 109, 95]
                dori_overlay[dori == ZONE_OCCLUDED] = [12, 14, 18, 205]

                dori_img = Image.fromarray(dori_overlay, mode="RGBA").resize((full_map_img.width, full_map_img.height), Image.Resampling.NEAREST)
                full_map_img = Image.alpha_composite(full_map_img, dori_img)

        # 3. Exact Metric Screen Placement for Terrain Raster
        img_x0, img_y0 = self.world_to_screen_px(0.0, self.terrain.height_m)
        img_x1, img_y1 = self.world_to_screen_px(self.terrain.width_m, 0.0)
        img_w = int(round(img_x1 - img_x0))
        img_h = int(round(img_y1 - img_y0))

        if img_w > 10 and img_h > 10:
            resized_terrain = full_map_img.resize((img_w, img_h), Image.Resampling.BILINEAR)
            self._map_photo = ImageTk.PhotoImage(resized_terrain)
            cv.create_image(img_x0, img_y0, image=self._map_photo, anchor="nw")
            cv.create_rectangle(img_x0, img_y0, img_x1, img_y1, outline=PANEL_BORDER, width=2)

        # 4. DYNAMIC CAD SCALE BAR
        scale_m_per_px = 1.0 / max(self._get_px_per_meter(), 1e-6)
        screen_view_w_m = w * scale_m_per_px
        if screen_view_w_m <= 150.0:
            target_bar_m = 20.0
        elif screen_view_w_m <= 350.0:
            target_bar_m = 50.0
        elif screen_view_w_m <= 800.0:
            target_bar_m = 100.0
        elif screen_view_w_m <= 2500.0:
            target_bar_m = 250.0
        elif screen_view_w_m <= 6000.0:
            target_bar_m = 500.0
        elif screen_view_w_m <= 15000.0:
            target_bar_m = 1000.0
        else:
            target_bar_m = 5000.0

        bar_px_len = target_bar_m * self._get_px_per_meter()
        scale_x2 = w - 30
        scale_x1 = scale_x2 - bar_px_len
        scale_y = h - 25

        cv.create_rectangle(scale_x1 - 10, scale_y - 20, scale_x2 + 10, scale_y + 10, fill="#101317", outline=PANEL_BORDER)
        cv.create_line(scale_x1, scale_y, scale_x2, scale_y, fill=TEXT_WHITE, width=3)
        cv.create_line(scale_x1, scale_y - 5, scale_x1, scale_y + 5, fill=TEXT_WHITE, width=2)
        cv.create_line(scale_x2, scale_y - 5, scale_x2, scale_y + 5, fill=TEXT_WHITE, width=2)

        scale_text = f"{target_bar_m:,.0f} m" if target_bar_m < 1000 else f"{target_bar_m/1000.0:.1f} km"
        cv.create_text((scale_x1 + scale_x2) / 2, scale_y - 10, text=f"Ölçek: {scale_text}", fill=ACCENT_CYAN, font=("Segoe UI", 8, "bold"))

        # 5. PERIMETER MODE VECTOR OVERLAYS
        if self.planner_mode_var.get() == "perimeter":
            if len(self.fence_points) >= 2:
                pts_px = []
                for pt in self.fence_points:
                    pts_px.append(self.world_to_screen_px(pt[0], pt[1]))

                if self.fence_closed_var.get() and len(pts_px) > 2:
                    pts_px.append(pts_px[0])

                for i in range(len(pts_px) - 1):
                    p1, p2 = pts_px[i], pts_px[i+1]
                    cv.create_line(p1[0], p1[1], p2[0], p2[1], fill="#FFD600", width=3, dash=(8, 4))

                for i, (px, py) in enumerate(pts_px[:-1] if self.fence_closed_var.get() else pts_px):
                    cv.create_oval(px - 5, py - 5, px + 5, py + 5, fill="#FF6D00", outline="#FFFFFF", width=2)

            if self.perimeter_plan and self.perimeter_plan.placed_cameras:
                _pcams = self.perimeter_plan.placed_cameras
                _plabel_step = 1 if len(_pcams) <= 25 else max(2, len(_pcams) // 18)
                for _pi, cam in enumerate(_pcams):
                    c_px, c_py = self.world_to_screen_px(cam.x_m, cam.y_m)
                    pan_r = math.radians(cam.pan_deg)
                    half_h_r = math.radians(cam.hfov_deg / 2.0)
                    reach = cam.effective_range_m

                    lx, ly = self.world_to_screen_px(cam.x_m + reach * math.sin(pan_r - half_h_r),
                                                      cam.y_m + reach * math.cos(pan_r - half_h_r))
                    rx, ry = self.world_to_screen_px(cam.x_m + reach * math.sin(pan_r + half_h_r),
                                                      cam.y_m + reach * math.cos(pan_r + half_h_r))

                    hover_id = self._profile_hover.get("pole_id") if self._profile_hover else None
                    is_sel = (cam.pole_id == self.selected_pole_id)
                    is_hover = (cam.pole_id == hover_id)
                    cone_color = ACCENT_CYAN if is_sel else ("#FFD600" if is_hover else "#00E676")
                    cv.create_line(c_px, c_py, lx, ly, fill=cone_color, width=1, dash=(3, 3))
                    cv.create_line(c_px, c_py, rx, ry, fill=cone_color, width=1, dash=(3, 3))
                    cv.create_line(lx, ly, rx, ry, fill=cone_color, width=1)

                    pole_bg = ACCENT_CYAN if is_sel else ("#FFD600" if is_hover else "#2979FF")
                    rad = 6 if (is_sel or is_hover) else 4
                    cv.create_oval(c_px - rad, c_py - rad, c_px + rad, c_py + rad, fill=pole_bg, outline="#FFFFFF", width=1)
                    # Pole number, thinned out so it stays readable on a long fence.
                    if is_sel or is_hover or (_pi % _plabel_step == 0) or _pi == len(_pcams) - 1:
                        big = is_sel or is_hover
                        cv.create_text(c_px, c_py - rad - 5, text=str(cam.pole_id), anchor="s",
                                       fill="#FFFFFF" if big else "#9FB3C8",
                                       font=("Segoe UI", 8 if big else 7, "bold" if big else "normal"))

                leg_x, leg_y = 30, h - 85
                cv.create_rectangle(leg_x - 6, leg_y - 6, leg_x + 250, leg_y + 68, fill="#16191E", outline=PANEL_BORDER)
                cv.create_text(leg_x, leg_y + 2, text=f"ÇEVRE ÇİTİ · {self.perimeter_plan.camera_count} KAMERA AKTİF", anchor="w", font=("Segoe UI", 8, "bold"), fill=ACCENT_CYAN)
                cv.create_text(leg_x, leg_y + 22, text=f"• Toplam Çit: {self.perimeter_plan.total_fence_length_m:,.0f}m", anchor="w", font=("Segoe UI", 8), fill=TEXT_WHITE)
                cv.create_text(leg_x, leg_y + 38, text=f"• Ortalama Direk Aralığı: {self.perimeter_plan.avg_spacing_m:.1f}m", anchor="w", font=("Segoe UI", 8), fill=ACCENT_GREEN)
                cv.create_text(leg_x, leg_y + 54, text="• %100 Kesintisiz Kör Noktasız Örtüşme", anchor="w", font=("Segoe UI", 8), fill="#FFD600")

        else:
            # 6. SINGLE CAMERA VECTOR OVERLAYS
            cx, cy = self.cam_x_var.get(), self.cam_y_var.get()
            c_px, c_py = self.world_to_screen_px(cx, cy)
            pan_rad = math.radians(self.pan_deg_var.get())
            half_hfov_rad = math.radians(self.result.hfov_deg / 2.0) if self.result else math.radians(20.0)
            reach_m = self.result.max_range_m if self.result else self.max_range_var.get()

            ray_l_x, ray_l_y = self.world_to_screen_px(cx + reach_m * math.sin(pan_rad - half_hfov_rad),
                                                       cy + reach_m * math.cos(pan_rad - half_hfov_rad))
            ray_r_x, ray_r_y = self.world_to_screen_px(cx + reach_m * math.sin(pan_rad + half_hfov_rad),
                                                       cy + reach_m * math.cos(pan_rad + half_hfov_rad))

            cv.create_line(c_px, c_py, ray_l_x, ray_l_y, fill=ACCENT_CYAN, width=2, dash=(6, 3))
            cv.create_line(c_px, c_py, ray_r_x, ray_r_y, fill=ACCENT_CYAN, width=2, dash=(6, 3))

            ray_c_x, ray_c_y = self.world_to_screen_px(cx + reach_m * math.sin(pan_rad),
                                                       cy + reach_m * math.cos(pan_rad))
            cv.create_line(c_px, c_py, ray_c_x, ray_c_y, fill=ACCENT_AMBER, width=2)

            cv.create_oval(c_px - 8, c_py - 8, c_px + 8, c_py + 8, fill=ACCENT_CYAN, outline="#FFFFFF", width=2)
            cv.create_text(c_px, c_py - 16, text=f"📹 Kamera ({reach_m:.0f}m Optik Menzil)", fill=ACCENT_CYAN, font=("Segoe UI", 9, "bold"))

            leg_x, leg_y = 30, h - 85
            cv.create_rectangle(leg_x - 6, leg_y - 6, leg_x + 220, leg_y + 68, fill="#16191E", outline=PANEL_BORDER)
            cv.create_text(leg_x, leg_y + 2, text="DORI & GÖRÜŞ LEJANDI", anchor="w", font=("Segoe UI", 8, "bold"), fill=TEXT_MUTED)

            badges = [
                ("🟢 Teşhis (>=250 px/m)", ACCENT_GREEN),
                ("🟡 Tanıma (>=125 px/m)", ACCENT_AMBER),
                ("🔴 Algılama (>=25 px/m)", ACCENT_RED),
                ("⬛ Kör Nokta (Tepe Arkası)", "#8B949E"),
            ]
            for i, (text, col) in enumerate(badges):
                bx = leg_x if i % 2 == 0 else (leg_x + 110)
                by = leg_y + 22 + (i // 2) * 20
                cv.create_text(bx, by, text=text, anchor="w", font=("Segoe UI", 8), fill=col)

    def _render_profile_canvas(self):
        cv = self.profile_canvas
        cv.delete("all")
        self._profile_plot = None   # invalidated until a branch below rebuilds it
        w = cv.winfo_width()
        h = cv.winfo_height()
        if w < 50 or h < 30:
            return

        pad_l, pad_r, pad_t, pad_b = 60, 20, 20, 25
        plot_w = w - pad_l - pad_r
        plot_h = h - pad_t - pad_b

        if self.planner_mode_var.get() == "perimeter" and self.perimeter_plan:
            cams = self.perimeter_plan.placed_cameras
            if not cams:
                return

            total_len = self.perimeter_plan.total_fence_length_m
            elevs = [c.ground_z_m for c in cams]
            min_z = min(elevs) - 5.0
            max_z = max([c.total_z_m for c in cams]) + 8.0
            z_span = max(max_z - min_z, 5.0)

            to_x = lambda dist: pad_l + (dist / max(total_len, 1.0)) * plot_w
            to_y = lambda z: pad_t + (1.0 - ((z - min_z) / z_span)) * plot_h

            cv.create_line(pad_l, pad_t + plot_h, pad_l + plot_w, pad_t + plot_h, fill=PANEL_BORDER)

            pts = [(pad_l, to_y(min_z))]
            cum_d = 0.0
            for i, cam in enumerate(cams):
                if i > 0:
                    prev = cams[i-1]
                    cum_d += math.hypot(cam.x_m - prev.x_m, cam.y_m - prev.y_m)
                pts.append((to_x(cum_d), to_y(cam.ground_z_m)))
            pts.append((to_x(total_len), to_y(min_z)))

            cv.create_polygon(pts, fill="#2A313C", outline="#4C566A", width=2)

            # Label every pole when there is room; thin out when the fence is
            # crowded, but always keep the selected/hovered ones legible.
            hover_pole_id = self._profile_hover.get("pole_id") if self._profile_hover else None
            label_step = 1 if len(cams) <= 20 else max(2, len(cams) // 15)

            cum_d = 0.0
            for i, cam in enumerate(cams):
                if i > 0:
                    prev = cams[i-1]
                    cum_d += math.hypot(cam.x_m - prev.x_m, cam.y_m - prev.y_m)
                px = to_x(cum_d)
                by = to_y(cam.ground_z_m)
                ty = to_y(cam.total_z_m)
                is_sel = (cam.pole_id == self.selected_pole_id)
                is_hover = (cam.pole_id == hover_pole_id)
                col = ACCENT_CYAN if is_sel else ("#FFD600" if is_hover else "#2979FF")
                cv.create_line(px, by, px, ty, fill=col, width=3 if (is_sel or is_hover) else 2)
                cv.create_oval(px - 3, ty - 3, px + 3, ty + 3, fill=col, outline="#FFFFFF")
                if is_sel or is_hover or (i % label_step == 0) or i == len(cams) - 1:
                    cv.create_text(
                        px, ty - 9, text=f"#{cam.pole_id}", anchor="s",
                        fill=col if (is_sel or is_hover) else TEXT_MUTED,
                        font=("Segoe UI", 7, "bold" if (is_sel or is_hover) else "normal"),
                    )

            cv.create_text(pad_l + 6, pad_t - 10, text=f"ÇEVRE ÇİTİ · {len(cams)} DİREK · #=direk no · fareyle gez → haritada göster", anchor="w", fill=ACCENT_CYAN, font=("Segoe UI", 8, "bold"))

            self._profile_plot = {
                "mode": "perimeter", "pad_l": pad_l, "pad_t": pad_t,
                "plot_w": plot_w, "plot_h": plot_h, "total_len": max(total_len, 1.0),
            }
            self._draw_profile_hover_overlay()

        else:
            if self.result is None:
                return
            dists = self.result.profile_dists_m
            elevs = self.result.profile_terrain_elev_m
            rays = self.result.profile_ray_elev_m
            vis = self.result.profile_is_visible

            max_d = max(np.max(dists), 10.0)
            min_z = min(np.min(elevs), self.result.cam_ground_z_m) - 5.0
            max_z = max(np.max(elevs), self.result.cam_total_z_m, np.max(rays)) + 10.0
            z_span = max(max_z - min_z, 5.0)

            to_x = lambda d: pad_l + (d / max_d) * plot_w
            to_y = lambda z: pad_t + (1.0 - ((z - min_z) / z_span)) * plot_h

            cv.create_line(pad_l, pad_t, pad_l, pad_t + plot_h, fill=PANEL_BORDER)
            cv.create_line(pad_l, pad_t + plot_h, pad_l + plot_w, pad_t + plot_h, fill=PANEL_BORDER)

            for d_tick in np.linspace(0, max_d, 6):
                tx = to_x(d_tick)
                cv.create_line(tx, pad_t + plot_h, tx, pad_t + plot_h + 4, fill=PANEL_BORDER)
                cv.create_text(tx, pad_t + plot_h + 12, text=f"{d_tick:.0f}m", fill=TEXT_MUTED, font=("Segoe UI", 7))

            for z_tick in np.linspace(min_z, max_z, 4):
                ty = to_y(z_tick)
                cv.create_line(pad_l - 4, ty, pad_l, ty, fill=PANEL_BORDER)
                cv.create_text(pad_l - 8, ty, text=f"{z_tick:.0f}m", fill=TEXT_MUTED, font=("Segoe UI", 7), anchor="e")

            pts_terrain = [(pad_l, to_y(min_z))]
            for d, el in zip(dists, elevs):
                pts_terrain.append((to_x(d), to_y(el)))
            pts_terrain.append((to_x(max_d), to_y(min_z)))
            cv.create_polygon(pts_terrain, fill="#2A313C", outline="#4C566A", width=2)

            cam_base_y = to_y(self.result.cam_ground_z_m)
            cam_top_y = to_y(self.result.cam_total_z_m)
            cv.create_line(pad_l, cam_base_y, pad_l, cam_top_y, fill=ACCENT_CYAN, width=4)
            cv.create_oval(pad_l - 4, cam_top_y - 4, pad_l + 4, cam_top_y + 4, fill=ACCENT_CYAN, outline="#FFFFFF")

            pts_ray = [(to_x(d), to_y(rz)) for d, rz in zip(dists, rays)]
            for i in range(len(pts_ray) - 1):
                color = ACCENT_GREEN if vis[i] else ACCENT_RED
                dash = () if vis[i] else (4, 3)
                cv.create_line(pts_ray[i][0], pts_ray[i][1], pts_ray[i + 1][0], pts_ray[i + 1][1], fill=color, width=2, dash=dash)

            self._profile_plot = {
                "mode": "single", "pad_l": pad_l, "pad_t": pad_t,
                "plot_w": plot_w, "plot_h": plot_h, "max_d": float(max_d),
                "cam_xy": (self.cam_x_var.get(), self.cam_y_var.get()),
                "pan_deg": self.pan_deg_var.get(),
            }
            self._draw_profile_hover_overlay()

    # ── CROSS-SECTION PROFILE <-> MAP HOVER LINKAGE ──
    def _on_profile_hover(self, event):
        """Mouse over the bottom profile -> pin the matching spot on the map."""
        pp = self._profile_plot
        if not pp:
            return
        frac = (event.x - pp["pad_l"]) / max(pp["plot_w"], 1.0)
        frac = min(max(frac, 0.0), 1.0)
        try:
            if pp["mode"] == "perimeter" and self.perimeter_plan:
                dist = frac * pp["total_len"]
                wx, wy, _seg = point_along_polyline(self.perimeter_plan.fence_points, dist)
                cams = self.perimeter_plan.placed_cameras
                pole = min(cams, key=lambda c: math.hypot(c.x_m - wx, c.y_m - wy)) if cams else None
                elev = self.terrain.get_elevation_at(wx, wy)
                label = f"Çit {dist:.0f} m · {elev:.0f} m rakım"
                if pole is not None:
                    label += f"  →  en yakın direk #{pole.pole_id}"
                self._profile_hover = {
                    "wx": wx, "wy": wy, "sx": event.x, "elev": elev,
                    "pole_id": pole.pole_id if pole else None, "label": label,
                }
            elif pp["mode"] == "single" and self.result is not None:
                dist = frac * pp["max_d"]
                pan = math.radians(pp["pan_deg"])
                cx, cy = pp["cam_xy"]
                wx = cx + dist * math.sin(pan)
                wy = cy + dist * math.cos(pan)
                elev = self.terrain.get_elevation_at(wx, wy)
                self._profile_hover = {
                    "wx": wx, "wy": wy, "sx": event.x, "elev": elev,
                    "pole_id": None, "label": f"Optik eksen {dist:.0f} m · {elev:.0f} m rakım",
                }
            else:
                return
        except Exception:
            self._profile_hover = None
            return
        self._draw_profile_hover_overlay()

    def _on_profile_leave(self, _event=None):
        self._profile_hover = None
        for cv in (self.profile_canvas, self.map_canvas):
            try:
                cv.delete("phover")
            except Exception:
                pass

    def _draw_profile_hover_overlay(self):
        hv = self._profile_hover
        pp = self._profile_plot
        if not hv or not pp:
            return

        def _boxed_text(canvas, x, y, text, anchor):
            item = canvas.create_text(x, y, text=text, anchor=anchor, fill="#FFD600",
                                      font=("Segoe UI", 8, "bold"), tags="phover")
            bx = canvas.bbox(item)
            if bx:
                canvas.create_rectangle(bx[0] - 3, bx[1] - 2, bx[2] + 3, bx[3] + 2,
                                        fill="#12151A", outline="#FFD600", width=1, tags="phover")
                canvas.tag_raise(item)

        cv = self.profile_canvas
        try:
            cv.delete("phover")
            top = pp["pad_t"]
            bot = pp["pad_t"] + pp["plot_h"]
            sx = max(pp["pad_l"], min(hv["sx"], pp["pad_l"] + pp["plot_w"]))
            cv.create_line(sx, top, sx, bot, fill="#FFD600", width=1, dash=(3, 2), tags="phover")
            near_right = sx > pp["pad_l"] + pp["plot_w"] * 0.55
            _boxed_text(cv, sx + (-6 if near_right else 6), bot - 6, hv["label"],
                        "se" if near_right else "sw")
        except Exception:
            pass

        mc = self.map_canvas
        try:
            mc.delete("phover")
            mx, my = self.world_to_screen_px(hv["wx"], hv["wy"])
            mc.create_line(mx - 10, my, mx + 10, my, fill="#FFD600", width=2, tags="phover")
            mc.create_line(mx, my - 10, mx, my + 10, fill="#FFD600", width=2, tags="phover")
            mc.create_oval(mx - 6, my - 6, mx + 6, my + 6, outline="#FFD600", width=2, tags="phover")
            lx, ly, anc = mx + 13, my + 16, "w"
            if mx > mc.winfo_width() * 0.6:
                lx, anc = mx - 13, "e"
            if my < 30:
                ly = my + 20
            _boxed_text(mc, lx, ly, hv["label"], anc)
        except Exception:
            pass
