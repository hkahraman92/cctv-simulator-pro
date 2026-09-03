import json
import math
from dataclasses import asdict
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser
from typing import Dict, List, Any, Optional, Tuple

from ..config import SENSOR_DIMS_MM, RESOLUTIONS, configure_tk_paths, get_admin_password
from ..models import CameraConfig, TargetPoint, PPMLevel, OpticResult, DEFAULT_LEVELS
from ..database import load_camera_library
from ..calculations import (
    calculate_for_camera,
    analyze_dead_zone_coverage,
    target_analysis_for_result,
    ppm_at_distance,
    optimize_tilt_calc,
    angle_diff,
    mode_label,
)
from ..exporters import export_csv, export_png, export_pdf, export_excel
from ..theme import is_themed, COLORS, StyledButton, fit_and_center_window
from .canvas_drawer import CanvasDrawer, canvas_to_world, top_plot_from_rect
from .spec_assistant import SpecAssistantWindow
from .camera_db_window import CameraDatabaseWindow
from .view_3d_window import Camera3DViewWindow
from .map_3d_window import TerrainViewshedWindow

configure_tk_paths()




class DualViewCCTVDesignApp:
    SENSOR_DIMS_MM = SENSOR_DIMS_MM
    RESOLUTIONS = RESOLUTIONS
    DEFAULT_LEVELS = DEFAULT_LEVELS

    def __init__(self, root: tk.Tk):
        self.root = root
        if not is_themed():
            self.root.title("Gelişmiş CCTV Görüş Alanı ve Proje Simülatörü")

        self.camera_library = load_camera_library()
        self.ppm_levels = [PPMLevel(**asdict(level)) for level in self.DEFAULT_LEVELS]
        self.cameras = [CameraConfig()]
        self.selected_camera_index = 0
        self.plan_path = ""
        self.plan_width_m = 45.0
        self.plan_photo_cache: Dict[str, Any] = {"photo": None, "source": None}
        self.auto_view_scale = tk.BooleanVar(value=True)
        self.plan_tool = tk.StringVar(value="inspect")
        self.drag_action = None
        self.target_point = TargetPoint()
        self.last_selected_results: List[OpticResult] = []
        self.last_all_results: Dict[str, List[OpticResult]] = {}
        self.last_canvas_context: Dict[str, Any] = {}
        self.selected_level_key = None
        self.suspend_calculate = False

        # PERF: drag/resize event coalescing. <B1-Motion> fires 60-120x/sec and
        # each event used to run the whole calculate() pipeline, but Tk paints
        # only once per frame, so almost all of that work was discarded unseen.
        self._calc_job = None            # pending after() id, None = idle
        self._calc_pending_full = False  # does the queued pass need the panels?
        self._level_by_name = None       # _selected_design_level() lookup cache
        self._level_by_key = None
        self._levels_desc_cache = None   # PPM levels sorted high -> low
        self.workbench_window = None     # modern optics workbench, if opened

        self.info_var = tk.StringVar(value="")
        self.mouse_var = tk.StringVar(value="Hazır")
        self.status_var = tk.StringVar(value="")
        self.lens_mode = tk.StringVar(value="min")
        self.level_color_var = tk.StringVar(value="#9CCC65")
        self.target_info_var = tk.StringVar(value="Kontrol noktası seçilmedi.")
        self.lens_suggestion_var = tk.StringVar(value="")
        self.alternative_models_var = tk.StringVar(value="")
        self.optimization_var = tk.StringVar(value="")
        self.dead_zone_var = tk.StringVar(value="Hesaplama bekleniyor...")
        self.design_distance_var = tk.StringVar(value="20.0")
        self.design_level_var = tk.StringVar(value="Ozel: TR Plaka")
        self.last_alternative_model_names: List[str] = []
        self.spec_window: Optional[SpecAssistantWindow] = None
        self.camera_db_window: Optional[CameraDatabaseWindow] = None
        self.view_3d_window: Optional[Camera3DViewWindow] = None

        self._build_ui()
        self.drawer = CanvasDrawer(self.canvas)
        self._load_camera_to_form()
        self._refresh_camera_combo()
        self._refresh_level_tree()
        self.calculate()
        # Show fully rendered window smoothly without flicker
        try:
            self.root.deiconify()
        except Exception:
            pass

    def _build_ui(self):
        # ── Main horizontal PanedWindow: left controls | right canvas ──
        self.main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        self.main_paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # ── Left panel ──
        left_frame = ttk.Frame(self.main_paned, padding=(10, 10, 6, 10))
        self.main_paned.add(left_frame, weight=0)

        camera_header = ttk.LabelFrame(left_frame, text=" Kamera Yönetimi ", padding=8)
        camera_header.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(camera_header, text="Aktif kamera:").grid(row=0, column=0, sticky=tk.W)
        self.combo_camera = ttk.Combobox(camera_header, state="readonly", width=28)
        self.combo_camera.grid(row=1, column=0, columnspan=3, sticky=tk.EW, pady=(2, 6))
        self.combo_camera.bind("<<ComboboxSelected>>", self._on_camera_selected)

        StyledButton(camera_header, text="Ekle", command=self.add_camera, bootstyle="success-outline").grid(row=2, column=0, sticky=tk.EW, padx=(0, 3))
        StyledButton(camera_header, text="Kopyala", command=self.duplicate_camera, bootstyle="info-outline").grid(row=2, column=1, sticky=tk.EW, padx=3)
        StyledButton(camera_header, text="Sil", command=self.delete_camera, bootstyle="danger-outline").grid(row=2, column=2, sticky=tk.EW, padx=(3, 0))
        camera_header.columnconfigure((0, 1, 2), weight=1)

        self.left_notebook = ttk.Notebook(left_frame)
        self.left_notebook.pack(fill=tk.BOTH, expand=True)

        # The camera tab grew past a min-height window; wrap it in a scroller.
        _cam_outer = ttk.Frame(self.left_notebook)
        _cam_canvas = tk.Canvas(_cam_outer, highlightthickness=0, borderwidth=0)
        _cam_sb = ttk.Scrollbar(_cam_outer, orient="vertical", command=_cam_canvas.yview)
        _cam_canvas.configure(yscrollcommand=_cam_sb.set)
        _cam_canvas.pack(side="left", fill="both", expand=True)
        _cam_sb.pack(side="right", fill="y")
        self.tab_camera = ttk.Frame(_cam_canvas, padding=8)
        _cam_win = _cam_canvas.create_window((0, 0), window=self.tab_camera, anchor="nw")
        self.tab_camera.bind("<Configure>",
                             lambda e: _cam_canvas.configure(scrollregion=_cam_canvas.bbox("all")))
        _cam_canvas.bind("<Configure>", lambda e: _cam_canvas.itemconfigure(_cam_win, width=e.width))
        _cam_canvas.bind("<Enter>", lambda e: _cam_canvas.bind_all(
            "<MouseWheel>", lambda ev: _cam_canvas.yview_scroll(int(-ev.delta / 120), "units")))
        _cam_canvas.bind("<Leave>", lambda e: _cam_canvas.unbind_all("<MouseWheel>"))

        self.tab_project = ttk.Frame(self.left_notebook, padding=8)
        self.tab_assistant = ttk.Frame(self.left_notebook, padding=8)
        self.tab_levels = ttk.Frame(self.left_notebook, padding=8)
        self.tab_export = ttk.Frame(self.left_notebook, padding=8)

        self.left_notebook.add(_cam_outer, text="Kamera")
        self.left_notebook.add(self.tab_project, text="Proje")
        self.left_notebook.add(self.tab_assistant, text="Asistan")
        self.left_notebook.add(self.tab_levels, text="PPM")
        self.left_notebook.add(self.tab_export, text="Çıktı")

        self._build_camera_tab()
        self._build_project_tab()
        self._build_assistant_tab()
        self._build_levels_tab()
        self._build_export_tab()

        # ── Right panel ──
        right_frame = ttk.Frame(self.main_paned, padding=(6, 6, 6, 6))
        self.main_paned.add(right_frame, weight=1)

        top_info = ttk.Frame(right_frame)
        top_info.pack(fill=tk.X, pady=(0, 6))

        try:
            from ..config import resource_path
            logo_path = resource_path("assets/cctv_logo_36.png")
            if logo_path.exists():
                self.logo_img = tk.PhotoImage(file=str(logo_path), master=self.root)
                ttk.Label(top_info, image=self.logo_img).pack(side=tk.LEFT, padx=(0, 8))
        except Exception:
            pass

        # Buttons are packed first so pack() reserves their full width; the
        # info label then takes whatever is left instead of squeezing them.
        StyledButton(top_info, text="🗺️ 3D Arazi & Viewshed Analizi", command=self.open_terrain_viewshed, bootstyle="success").pack(
            side=tk.RIGHT, padx=(8, 0)
        )
        StyledButton(top_info, text="👁️ 3D Kamera Bakış Açısı", command=self.open_3d_view, bootstyle="primary").pack(
            side=tk.RIGHT, padx=(8, 0)
        )
        StyledButton(top_info, text="◈ Optik Tezgâhı", command=self.open_optics_workbench, bootstyle="info").pack(
            side=tk.RIGHT, padx=(8, 0)
        )
        ttk.Label(top_info, textvariable=self.status_var, foreground=COLORS["danger"]).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Label(top_info, textvariable=self.info_var, font=("Segoe UI", 10, "bold"), foreground=COLORS["accent"]).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )

        table_frame = ttk.LabelFrame(right_frame, text=" Görev Kapsama Sınırları ve Analiz Tablosu ", padding=6)
        table_frame.pack(fill=tk.X, pady=(0, 8))

        columns = ("camera", "mode", "level", "type", "ppm", "opt_dist", "ground_dist", "status")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=8)
        headings = {
            "camera": "Kamera",
            "mode": "Lens",
            "level": "Görev / Algoritma",
            "type": "Sınıf",
            "ppm": "PPM",
            "opt_dist": "Optik Menzil",
            "ground_dist": "Etkin Menzil",
            "status": "Durum",
        }
        widths = {
            "camera": 110,
            "mode": 70,
            "level": 165,
            "type": 80,
            "ppm": 70,
            "opt_dist": 100,
            "ground_dist": 100,
            "status": 160,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor=tk.CENTER if column != "level" else tk.W)
        self.tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        table_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        table_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=table_scroll.set)

        recommendation_frame = ttk.LabelFrame(right_frame, text=" Otomatik Kurulum Önerileri ", padding=6)
        recommendation_frame.pack(fill=tk.X, pady=(0, 8))
        self.recommendation_text = tk.Text(
            recommendation_frame,
            height=4,
            wrap=tk.WORD,
            font=("Segoe UI", 9),
            bg=COLORS["text_bg"],
            fg=COLORS["text_fg"],
            relief="flat",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#DEE2E6",
        )
        self.recommendation_text.pack(fill=tk.X)
        self.recommendation_text.configure(state=tk.DISABLED)

        self.canvas_frame = ttk.LabelFrame(
            right_frame,
            text=" Üst: Yatay Profil | Alt: Kuşbakışı / Plan Üstü Çoklu Kamera ",
            padding=5,
        )
        self.canvas_frame.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(self.canvas_frame, bg=COLORS["canvas_bg"], borderwidth=1, relief="groove")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        # PERF: a window resize emits a burst of <Configure>; debounce it.
        self.canvas.bind("<Configure>", lambda event: self.schedule_calculate(full=True, delay_ms=80))
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<Leave>", lambda event: self.mouse_var.set("Hazır"))
        self.canvas.bind("<Button-1>", self._on_canvas_button_1)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag_1)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.bind("<Button-3>", self._on_canvas_button_3)
        self.canvas.bind("<B3-Motion>", self._on_canvas_drag_3)
        # PERF: <B3-Motion> rotates the camera but had no release handler, so a
        # right-drag never triggered the closing full pass. Bind it.
        self.canvas.bind("<ButtonRelease-3>", self._on_canvas_release)

        ttk.Label(right_frame, textvariable=self.mouse_var, anchor=tk.W).pack(fill=tk.X, pady=(4, 0))

        # ── Set initial sash position after geometry is resolved ──
        def _set_initial_sash():
            try:
                win_w = self.root.winfo_width()
                if win_w < 100:
                    win_w = self.root.winfo_screenwidth()
                # Left panel gets ~25 % of window width (min 340, max 420)
                left_w = max(340, min(420, int(win_w * 0.25)))
                self.main_paned.sashpos(0, left_w)
            except Exception:
                pass

        self.root.after(200, _set_initial_sash)

    def _build_camera_tab(self):
        row = 0
        ttk.Label(self.tab_camera, text="Kamera adı:").grid(row=row, column=0, sticky=tk.W, pady=2)
        self.entry_cam_name = ttk.Entry(self.tab_camera, width=28)
        self.entry_cam_name.grid(row=row, column=1, sticky=tk.EW, pady=2)
        row += 1

        ttk.Label(self.tab_camera, text="Hazır model:").grid(row=row, column=0, sticky=tk.W, pady=2)
        self.combo_camera_model = ttk.Combobox(
            self.tab_camera, values=list(self.camera_library.keys()), state="readonly"
        )
        self.combo_camera_model.grid(row=row, column=1, sticky=tk.EW, pady=2)
        row += 1
        StyledButton(self.tab_camera, text="Seçili Modeli Uygula", command=self.apply_camera_model, bootstyle="primary-outline").grid(
            row=row, column=0, columnspan=2, sticky=tk.EW, pady=(2, 4)
        )
        row += 1

        ttk.Label(self.tab_camera, text="Sensör boyutu:").grid(row=row, column=0, sticky=tk.W, pady=2)
        self.combo_sensor = ttk.Combobox(
            self.tab_camera, values=list(self.SENSOR_DIMS_MM.keys()), state="readonly", width=25
        )
        self.combo_sensor.grid(row=row, column=1, sticky=tk.EW, pady=2)
        row += 1

        ttk.Label(self.tab_camera, text="Çözünürlük:").grid(row=row, column=0, sticky=tk.W, pady=2)
        self.combo_res = ttk.Combobox(
            self.tab_camera, values=list(self.RESOLUTIONS.keys()), state="readonly", width=25
        )
        self.combo_res.grid(row=row, column=1, sticky=tk.EW, pady=2)
        row += 1

        ttk.Separator(self.tab_camera).grid(row=row, column=0, columnspan=2, sticky=tk.EW, pady=8)
        row += 1

        self.entry_lens_min = self._add_entry(self.tab_camera, row, "Min odak (geniş):", "2.8")
        row += 1
        self.entry_lens_max = self._add_entry(self.tab_camera, row, "Max odak (dar):", "12.0")
        row += 1
        self.entry_pole_h = self._add_entry(self.tab_camera, row, "Direk yüksekliği:", "4.0")
        row += 1
        self.entry_tilt = self._add_entry(self.tab_camera, row, "Aşağı eğim / tilt:", "15.0")
        row += 1
        self.entry_target_h = self._add_entry(self.tab_camera, row, "Hedef yüksekliği:", "1.8")
        row += 1

        ttk.Separator(self.tab_camera).grid(row=row, column=0, columnspan=2, sticky=tk.EW, pady=8)
        row += 1

        self.entry_pos_x = self._add_entry(self.tab_camera, row, "Plan X konumu (m):", "0.0")
        row += 1
        self.entry_pos_y = self._add_entry(self.tab_camera, row, "Plan Y konumu (m):", "0.0")
        row += 1
        self.entry_heading = self._add_entry(self.tab_camera, row, "Yön açısı (°):", "0.0")
        row += 1
        self.entry_ir_range = self._add_entry(self.tab_camera, row, "IR mesafesi (m):", "30.0")
        row += 1
        self.entry_min_lux = self._add_entry(self.tab_camera, row, "Minimum lux:", "0.01")
        row += 1
        self.entry_eff_px = self._add_entry(self.tab_camera, row, "Etkin piksel oranı k (cctv_iq):", "1.0")
        row += 1
        StyledButton(self.tab_camera, text="📷 Eğik kenar → k ölç",
                     command=self._measure_edge_k, bootstyle="secondary-outline").grid(
            row=row, column=0, columnspan=2, sticky=tk.EW, pady=(0, 4))
        row += 1

        mode_frame = ttk.LabelFrame(self.tab_camera, text=" Analiz Modu ", padding=6)
        mode_frame.grid(row=row, column=0, columnspan=2, sticky=tk.EW, pady=(8, 6))
        ttk.Radiobutton(mode_frame, text="Geniş açı", variable=self.lens_mode, value="min", command=self.calculate).pack(
            anchor=tk.W
        )
        ttk.Radiobutton(mode_frame, text="Dar açı", variable=self.lens_mode, value="max", command=self.calculate).pack(
            anchor=tk.W
        )
        ttk.Radiobutton(
            mode_frame,
            text="Geniş + dar karşılaştır",
            variable=self.lens_mode,
            value="compare",
            command=self.calculate,
        ).pack(anchor=tk.W)
        row += 1

        StyledButton(self.tab_camera, text="Kamerayı Güncelle", command=lambda: self.calculate(show_errors=True), bootstyle="success").grid(
            row=row, column=0, columnspan=2, sticky=tk.EW, pady=(4, 2)
        )
        row += 1
        StyledButton(self.tab_camera, text="👁️ 3D Kamera Bakış Açısı", command=self.open_3d_view, bootstyle="primary-outline").grid(
            row=row, column=0, columnspan=2, sticky=tk.EW, pady=(2, 0)
        )
        row += 1
        StyledButton(self.tab_camera, text="◈ Optik Tezgâhı (EN 62676-4)", command=self.open_optics_workbench, bootstyle="info-outline").grid(
            row=row, column=0, columnspan=2, sticky=tk.EW, pady=(2, 0)
        )

        self.tab_camera.columnconfigure(1, weight=1)
        for widget in (
            self.entry_cam_name,
            self.combo_sensor,
            self.combo_res,
            self.entry_lens_min,
            self.entry_lens_max,
            self.entry_pole_h,
            self.entry_tilt,
            self.entry_target_h,
            self.entry_pos_x,
            self.entry_pos_y,
            self.entry_heading,
            self.entry_ir_range,
            self.entry_min_lux,
        ):
            widget.bind("<Return>", lambda event: self.calculate(show_errors=True))
            widget.bind("<FocusOut>", lambda event: self.calculate())

    def _build_project_tab(self):
        StyledButton(self.tab_project, text="👁️ 3D Kamera Bakış Açısı (Camera Eye View)", command=self.open_3d_view, bootstyle="primary").pack(
            fill=tk.X, pady=(0, 8)
        )
        StyledButton(self.tab_project, text="Projeyi Kaydet", command=self.save_project, bootstyle="success").pack(fill=tk.X, pady=(0, 6))
        StyledButton(self.tab_project, text="Proje Yükle", command=self.load_project, bootstyle="info").pack(fill=tk.X, pady=(0, 10))
        StyledButton(self.tab_project, text="Şartname / Compliance Matrix", command=self.open_spec_assistant, bootstyle="warning").pack(
            fill=tk.X, pady=(0, 6)
        )
        StyledButton(self.tab_project, text="Kamera Veritabanı", command=self.open_camera_database, bootstyle="secondary").pack(
            fill=tk.X, pady=(0, 10)
        )

        plan_frame = ttk.LabelFrame(self.tab_project, text=" Plan / Kroki ", padding=8)
        plan_frame.pack(fill=tk.X)
        ttk.Button(plan_frame, text="Plan Görseli Yükle", command=self.load_plan_image).grid(
            row=0, column=0, columnspan=2, sticky=tk.EW, pady=(0, 6)
        )
        ttk.Button(plan_frame, text="Planı Temizle", command=self.clear_plan_image).grid(
            row=1, column=0, columnspan=2, sticky=tk.EW, pady=(0, 6)
        )
        self.entry_plan_width = self._add_entry(plan_frame, 2, "Plan genişliği (m):", "45.0")
        self.entry_plan_width.bind("<Return>", lambda event: self.calculate(show_errors=True))
        self.entry_plan_width.bind("<FocusOut>", lambda event: self.calculate())
        ttk.Checkbutton(
            plan_frame,
            text="Bakış açısına göre otomatik ölçekle",
            variable=self.auto_view_scale,
            command=self.calculate,
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))
        self.lbl_plan_path = ttk.Label(plan_frame, text="Plan yok", wraplength=245, foreground="#546E7A")
        self.lbl_plan_path.grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))
        plan_frame.columnconfigure(1, weight=1)

        note = (
            "Plan görüntüsü PNG/GIF ise doğrudan açılır. JPG yeniden boyutlandırma ve PNG çıktı için "
            "Pillow kuruluysa daha iyi sonuç verir."
        )
        ttk.Label(self.tab_project, text=note, wraplength=260, foreground="#455A64").pack(fill=tk.X, pady=(10, 0))

    def _build_assistant_tab(self):
        tool_frame = ttk.LabelFrame(self.tab_assistant, text=" Plan Etkileşimi ", padding=8)
        tool_frame.pack(fill=tk.X, pady=(0, 8))
        for text, value in (
            ("İncele", "inspect"),
            ("Kamera taşı", "move"),
            ("Yön çevir", "rotate"),
            ("Hedef seç", "target"),
        ):
            ttk.Radiobutton(tool_frame, text=text, variable=self.plan_tool, value=value).pack(anchor=tk.W)

        target_frame = ttk.LabelFrame(self.tab_assistant, text=" Hedef Kontrolü ", padding=8)
        target_frame.pack(fill=tk.X, pady=(0, 8))
        self.entry_target_name = self._add_entry(target_frame, 0, "Hedef adı:", self.target_point.name)
        ttk.Label(target_frame, text="Gereken seviye:").grid(row=1, column=0, sticky=tk.W, pady=2, padx=(0, 6))
        self.combo_target_level = ttk.Combobox(target_frame, textvariable=self.design_level_var, state="readonly")
        self.combo_target_level.grid(row=1, column=1, sticky=tk.EW, pady=2)
        self.combo_target_level.bind("<<ComboboxSelected>>", lambda event: self.calculate())
        ttk.Button(target_frame, text="Hedefi Temizle", command=self.clear_target_point).grid(
            row=2, column=0, columnspan=2, sticky=tk.EW, pady=(6, 4)
        )
        ttk.Label(target_frame, textvariable=self.target_info_var, wraplength=255, foreground="#37474F").grid(
            row=3, column=0, columnspan=2, sticky=tk.W
        )
        target_frame.columnconfigure(1, weight=1)

        lens_frame = ttk.LabelFrame(self.tab_assistant, text=" Lens Önerisi ", padding=8)
        lens_frame.pack(fill=tk.X, pady=(0, 8))
        self.entry_design_distance = self._add_entry(lens_frame, 0, "Hedef mesafe (m):", self.design_distance_var.get())
        self.entry_design_distance.bind("<Return>", lambda event: self.calculate(show_errors=True))
        self.entry_design_distance.bind("<FocusOut>", lambda event: self.calculate())
        ttk.Button(lens_frame, text="Lens Öner", command=self.update_lens_suggestion).grid(
            row=1, column=0, columnspan=2, sticky=tk.EW, pady=(6, 4)
        )
        ttk.Label(lens_frame, textvariable=self.lens_suggestion_var, wraplength=255, foreground="#01579B").grid(
            row=2, column=0, columnspan=2, sticky=tk.W
        )
        lens_frame.columnconfigure(1, weight=1)

        alternative_frame = ttk.LabelFrame(self.tab_assistant, text=" Alternatif Model Önerisi ", padding=8)
        alternative_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(alternative_frame, text="Alternatifleri Bul", command=self.update_alternative_models).pack(
            fill=tk.X, pady=(0, 4)
        )
        ttk.Button(alternative_frame, text="İlk Öneriyi Uygula", command=self.apply_first_alternative_model).pack(
            fill=tk.X, pady=(0, 4)
        )
        ttk.Label(
            alternative_frame,
            textvariable=self.alternative_models_var,
            wraplength=255,
            foreground="#263238",
        ).pack(fill=tk.X)

        optimize_frame = ttk.LabelFrame(self.tab_assistant, text=" Kör Nokta Optimizasyonu ", padding=8)
        optimize_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(optimize_frame, text="En İyi Tilt Hesapla", command=lambda: self.optimize_tilt(False)).pack(
            fill=tk.X, pady=(0, 4)
        )
        ttk.Button(optimize_frame, text="Tilt Değerini Uygula", command=lambda: self.optimize_tilt(True)).pack(
            fill=tk.X, pady=(0, 4)
        )
        ttk.Label(optimize_frame, textvariable=self.optimization_var, wraplength=255, foreground="#4E342E").pack(
            fill=tk.X
        )

        dead_zone_frame = ttk.LabelFrame(self.tab_assistant, text=" Ölü Bölge Analizi ", padding=8)
        dead_zone_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(
            dead_zone_frame,
            textvariable=self.dead_zone_var,
            wraplength=255,
            foreground="#B71C1C",
            justify=tk.LEFT,
        ).pack(fill=tk.X)

    def _build_levels_tab(self):
        columns = ("name", "type", "ppm", "color")
        self.level_tree = ttk.Treeview(self.tab_levels, columns=columns, show="headings", height=8)
        for column, title, width in (
            ("name", "Seviye", 120),
            ("type", "Sınıf", 75),
            ("ppm", "PPM", 60),
            ("color", "Renk", 70),
        ):
            self.level_tree.heading(column, text=title)
            self.level_tree.column(column, width=width, anchor=tk.CENTER if column != "name" else tk.W)
        self.level_tree.pack(fill=tk.X, pady=(0, 8))
        self.level_tree.bind("<<TreeviewSelect>>", self._on_level_selected)

        form = ttk.LabelFrame(self.tab_levels, text=" Katman Düzenle ", padding=8)
        form.pack(fill=tk.X)
        self.entry_level_name = self._add_entry(form, 0, "Ad:", "")
        self.entry_level_ppm = self._add_entry(form, 1, "PPM:", "")
        ttk.Label(form, text="Sınıf:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.combo_level_type = ttk.Combobox(form, values=["Standart", "Algoritma", "Özel"], state="readonly")
        self.combo_level_type.grid(row=2, column=1, sticky=tk.EW, pady=2)
        self.combo_level_type.set("Özel")
        ttk.Label(form, text="Renk:").grid(row=3, column=0, sticky=tk.W, pady=2)
        color_row = ttk.Frame(form)
        color_row.grid(row=3, column=1, sticky=tk.EW, pady=2)
        self.level_color_swatch = tk.Label(color_row, bg=self.level_color_var.get(), width=4, relief="solid", bd=1)
        self.level_color_swatch.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(color_row, text="Seç", command=self.choose_level_color).pack(side=tk.LEFT)
        form.columnconfigure(1, weight=1)

        button_row = ttk.Frame(self.tab_levels)
        button_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(button_row, text="Ekle / Güncelle", command=self.add_or_update_level).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4)
        )
        ttk.Button(button_row, text="Sil", command=self.delete_level).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _build_export_tab(self):
        StyledButton(self.tab_export, text="Excel (.xlsx) Rapor Dışa Aktar", command=self.export_excel, bootstyle="success").pack(fill=tk.X, pady=(0, 6))
        StyledButton(self.tab_export, text="PDF Mühendislik Raporu (ASELSAN Formatı)", command=self.export_pdf, bootstyle="danger").pack(fill=tk.X, pady=(0, 6))
        StyledButton(self.tab_export, text="CSV Tablo Dışa Aktar", command=self.export_csv, bootstyle="info").pack(fill=tk.X, pady=(0, 6))
        StyledButton(self.tab_export, text="PNG Görsel Dışa Aktar", command=self.export_png, bootstyle="info").pack(fill=tk.X, pady=(0, 10))
        export_note = (
            "PDF Raporu ASELSAN Kurumsal Kimliği ve Savunma Sanayii standartlarında, "
            "DORI menzilleri, kamera matrisi, kör nokta ve şartname uyumluluk tablolarını içeren resmi mühendislik raporu olarak üretilir."
        )
        ttk.Label(self.tab_export, text=export_note, wraplength=260, foreground="#002D62").pack(fill=tk.X)

    def _add_entry(self, parent: ttk.Frame, row: int, label: str, default: str) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=2, padx=(0, 6))
        entry = ttk.Entry(parent)
        entry.grid(row=row, column=1, sticky=tk.EW, pady=2)
        entry.insert(0, default)
        return entry

    def _set_entry(self, entry: ttk.Entry, value: Any):
        entry.delete(0, tk.END)
        entry.insert(0, f"{value:g}" if isinstance(value, float) else str(value))

    def _read_float(self, entry: ttk.Entry, name: str, min_val: Optional[float] = None) -> float:
        raw = entry.get().strip().replace(",", ".")
        try:
            val = float(raw)
        except ValueError as exc:
            raise ValueError(f"{name} geçerli bir sayı olmalı.") from exc
        if min_val is not None and val < min_val:
            raise ValueError(f"{name} en az {min_val:g} olmalı.")
        return val

    def _get_active_camera(self) -> CameraConfig:
        if 0 <= self.selected_camera_index < len(self.cameras):
            return self.cameras[self.selected_camera_index]
        return self.cameras[0] if self.cameras else CameraConfig()

    def open_terrain_viewshed(self):
        """3D Topographical Map, Terrain Elevation & Viewshed (Line-of-Sight) Analysis."""
        if getattr(self, "viewshed_window", None) is not None and self.viewshed_window.window.winfo_exists():
            try:
                self.viewshed_window.window.lift()
                self.viewshed_window.window.focus_force()
                return
            except tk.TclError:
                pass
            self.viewshed_window = None

        try:
            self.viewshed_window = TerrainViewshedWindow(self)
        except Exception as exc:
            from ..errors import report
            report(exc, "3D Arazi Analizi / Başlatma")

    def open_3d_view(self):
        if getattr(self, "view_3d_window", None) is not None and self.view_3d_window.window.winfo_exists():
            self.view_3d_window.lift()
            return
        window = Camera3DViewWindow(self)
        # A failed build destroys its own window; do not keep a dead handle,
        # or the next click finds a stale object and silently does nothing.
        self.view_3d_window = window if getattr(window, "build_ok", True) else None

    def open_optics_workbench(self):
        """Modern EN 62676-4 optics workbench, seeded from the selected camera."""
        existing = getattr(self, "workbench_window", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except tk.TclError:
                pass
            self.workbench_window = None

        try:
            from .modern_window import OpticsWorkbenchWindow
        except ImportError:
            messagebox.showinfo(
                "Optik tezgâhı",
                "Bu ekran customtkinter ve pillow gerektiriyor.\n\n"
                "Kurulum:\n    pip install customtkinter pillow")
            return

        def _closed():
            self.workbench_window = None

        window = OpticsWorkbenchWindow(self.root, on_close=_closed)
        if not getattr(window, "build_ok", True):
            self.workbench_window = None
            return
        self.workbench_window = window
        try:
            window.seed_from(self.cameras[self.selected_camera_index])
        except Exception as exc:
            from ..errors import report
            report(exc, "Optik Tezgâhı / kamera aktarımı", show=False)

    def open_spec_assistant(self):
        if getattr(self, "spec_window", None) is not None and self.spec_window.window.winfo_exists():
            self.spec_window.lift()
            return
        window = SpecAssistantWindow(self)
        self.spec_window = window if getattr(window, "build_ok", True) else None

    def open_camera_database(self):
        if getattr(self, "camera_db_window", None) is not None and self.camera_db_window.window.winfo_exists():
            self.camera_db_window.lift()
            return
        if not self._prompt_admin_password():
            return
        window = CameraDatabaseWindow(self)
        self.camera_db_window = window if getattr(window, "build_ok", True) else None

    def _prompt_admin_password(self) -> bool:
        dialog = tk.Toplevel(self.root)
        dialog.title("Yönetici Girişi (Admin Access)")
        dialog.transient(self.root)
        dialog.grab_set()

        fit_and_center_window(dialog, default_w=420, default_h=280, min_w=380, min_h=260, maximize=False)
        dialog.resizable(False, False)

        result = {"authorized": False}

        ttk.Label(
            dialog,
            text="🔒 Kamera Veritabanı Yönetici Erişimi",
            font=("Segoe UI", 10, "bold"),
            foreground=COLORS["accent"],
        ).pack(anchor=tk.W, padx=16, pady=(16, 4))

        ttk.Label(
            dialog,
            text="Veritabanını düzenlemek için lütfen admin şifresini girin:\n(Varsayılan şifre: admin)",
            wraplength=360,
            foreground=COLORS["muted_fg"],
        ).pack(anchor=tk.W, padx=16, pady=(0, 10))

        entry_frame = ttk.Frame(dialog)
        entry_frame.pack(fill=tk.X, padx=16, pady=4)
        ttk.Label(entry_frame, text="Şifre:").pack(side=tk.LEFT, padx=(0, 8))
        pass_entry = ttk.Entry(entry_frame, show="*")
        pass_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        pass_entry.focus_set()

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=16, pady=16)

        def check_pass(event=None):
            entered = pass_entry.get().strip()
            actual = get_admin_password()
            if entered == actual:
                result["authorized"] = True
                dialog.destroy()
            else:
                messagebox.showerror(
                    "Giriş Başarısız",
                    "Hatalı yönetici şifresi! Kamera veritabanına erişim engellendi.",
                    parent=dialog,
                )
                pass_entry.delete(0, tk.END)

        pass_entry.bind("<Return>", check_pass)

        StyledButton(btn_frame, text="Giriş Yap", command=check_pass, bootstyle="success").pack(side=tk.RIGHT, padx=(4, 0))
        StyledButton(btn_frame, text="İptal", command=dialog.destroy, bootstyle="secondary-outline").pack(side=tk.RIGHT)

        self.root.wait_window(dialog)
        return result["authorized"]

    def add_camera(self):
        if not self._commit_form_to_selected(show_errors=True):
            return
        index = len(self.cameras) + 1
        new_cam = CameraConfig(name=f"Kamera {index}")
        self.cameras.append(new_cam)
        self.selected_camera_index = len(self.cameras) - 1
        self._refresh_camera_combo()
        self._load_camera_to_form()
        self.calculate()

    def duplicate_camera(self):
        if not self._commit_form_to_selected(show_errors=True):
            return
        current = self.cameras[self.selected_camera_index]
        dup = CameraConfig(**asdict(current))
        dup.name = f"{current.name} (Kopya)"
        dup.pos_x_m += 2.0
        self.cameras.append(dup)
        self.selected_camera_index = len(self.cameras) - 1
        self._refresh_camera_combo()
        self._load_camera_to_form()
        self.calculate()

    def delete_camera(self):
        if len(self.cameras) <= 1:
            messagebox.showwarning("Kamera yönetimi", "En az bir kamera bulunmalıdır.")
            return
        del self.cameras[self.selected_camera_index]
        self.selected_camera_index = max(0, self.selected_camera_index - 1)
        self._refresh_camera_combo()
        self._load_camera_to_form()
        self.calculate()

    def _on_camera_selected(self, event=None):
        if not self._commit_form_to_selected():
            self.combo_camera.current(self.selected_camera_index)
            return
        self.selected_camera_index = self.combo_camera.current()
        self._load_camera_to_form()
        self.calculate()

    def _refresh_camera_combo(self):
        names = [f"{i + 1}. {cam.name}" for i, cam in enumerate(self.cameras)]
        self.combo_camera.configure(values=names)
        if 0 <= self.selected_camera_index < len(names):
            self.combo_camera.current(self.selected_camera_index)

    def _refresh_camera_model_values(self, selected: str = ""):
        current = selected or self.combo_camera_model.get()
        values = list(self.camera_library.keys())
        self.combo_camera_model.configure(values=values)
        if current in values:
            self.combo_camera_model.set(current)

    def apply_camera_model(self):
        model_name = self.combo_camera_model.get()
        model = self.camera_library.get(model_name, {})
        if not model:
            return
        camera = self.cameras[self.selected_camera_index]
        camera.model_name = model_name
        if "sensor_name" in model:
            camera.sensor_name = model["sensor_name"]
        if "resolution_name" in model:
            camera.resolution_name = model["resolution_name"]
        if "focal_min_mm" in model:
            camera.focal_min_mm = float(model["focal_min_mm"])
        if "focal_max_mm" in model:
            camera.focal_max_mm = float(model["focal_max_mm"])
        if "ir_range_m" in model:
            camera.ir_range_m = float(model["ir_range_m"])
        if "min_lux" in model:
            camera.min_lux = float(model["min_lux"])
        camera.effective_px_ratio = float(model.get("effective_px_ratio", 1.0) or 1.0)
        self._load_camera_to_form()
        self.calculate()

    def _measure_edge_k(self):
        path = filedialog.askopenfilename(
            title="Eğik kenar fotoğrafı (yüksek kontrast, ~5° eğik kenar)",
            filetypes=[("Görsel", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp"), ("Tümü", "*.*")],
        )
        if not path:
            return
        try:
            from ..cctv_iq import measure_file
            res = measure_file(path)
        except Exception as exc:
            messagebox.showerror("Ölçüm başarısız",
                                 f"Eğik kenar MTF ölçülemedi:\n{exc}\n\n"
                                 "Kenar dikey/yatay ve ~2–15° eğik olmalı.")
            return
        self._set_entry(self.entry_eff_px, round(res["k"], 3))
        self.calculate(show_errors=True)
        messagebox.showinfo(
            "cctv_iq ölçümü",
            f"Kenar açısı : {res['edge_angle_deg']:.1f}°\n"
            f"MTF50       : {res['mtf50_cy_px']:.3f} çevrim/piksel\n"
            f"k           : {res['k']:.3f}\n\n"
            "Etkin piksel oranı optik motora uygulandı.")

    def _load_camera_to_form(self):
        self.suspend_calculate = True
        camera = self.cameras[self.selected_camera_index]
        self.entry_cam_name.delete(0, tk.END)
        self.entry_cam_name.insert(0, camera.name)
        if camera.model_name in self.camera_library:
            self.combo_camera_model.set(camera.model_name)
        else:
            self.combo_camera_model.set("Özel kamera")
        self.combo_sensor.set(camera.sensor_name)
        self.combo_res.set(camera.resolution_name)
        self._set_entry(self.entry_lens_min, camera.focal_min_mm)
        self._set_entry(self.entry_lens_max, camera.focal_max_mm)
        self._set_entry(self.entry_pole_h, camera.pole_height_m)
        self._set_entry(self.entry_tilt, camera.tilt_deg)
        self._set_entry(self.entry_target_h, camera.target_height_m)
        self._set_entry(self.entry_pos_x, camera.pos_x_m)
        self._set_entry(self.entry_pos_y, camera.pos_y_m)
        self._set_entry(self.entry_heading, camera.heading_deg)
        self._set_entry(self.entry_ir_range, camera.ir_range_m)
        self._set_entry(self.entry_min_lux, camera.min_lux)
        self._set_entry(self.entry_eff_px, getattr(camera, "effective_px_ratio", 1.0))
        self.suspend_calculate = False

    def _commit_form_to_selected(self, show_errors=False) -> bool:
        if self.suspend_calculate:
            return True
        camera = self.cameras[self.selected_camera_index]
        try:
            name = self.entry_cam_name.get().strip()
            if not name:
                raise ValueError("Kamera adı boş olamaz.")
            sensor_name = self.combo_sensor.get()
            resolution_name = self.combo_res.get()
            focal_min_mm = self._read_float(self.entry_lens_min, "Min odak", 0.1)
            focal_max_mm = self._read_float(self.entry_lens_max, "Max odak", focal_min_mm)
            pole_height_m = self._read_float(self.entry_pole_h, "Direk yüksekliği", 0.1)
            tilt_deg = self._read_float(self.entry_tilt, "Tilt açısı", 0.0)
            target_height_m = self._read_float(self.entry_target_h, "Hedef yüksekliği", 0.0)
            pos_x_m = self._read_float(self.entry_pos_x, "Plan X konumu")
            pos_y_m = self._read_float(self.entry_pos_y, "Plan Y konumu")
            heading_deg = self._read_float(self.entry_heading, "Yön açısı") % 360
            ir_range_m = self._read_float(self.entry_ir_range, "IR mesafesi", 0.0)
            min_lux = self._read_float(self.entry_min_lux, "Minimum lux", 0.0)
            effective_px_ratio = min(max(self._read_float(self.entry_eff_px, "Etkin piksel oranı", 0.05), 0.05), 1.0)

            if target_height_m >= pole_height_m:
                raise ValueError("Hedef yüksekliği direk yüksekliğinden küçük olmalıdır.")

            camera.name = name
            camera.model_name = self.combo_camera_model.get() or "Özel kamera"
            camera.sensor_name = sensor_name
            camera.resolution_name = resolution_name
            camera.focal_min_mm = focal_min_mm
            camera.focal_max_mm = focal_max_mm
            camera.pole_height_m = pole_height_m
            camera.tilt_deg = tilt_deg
            camera.target_height_m = target_height_m
            camera.pos_x_m = pos_x_m
            camera.pos_y_m = pos_y_m
            camera.heading_deg = heading_deg
            camera.ir_range_m = ir_range_m
            camera.min_lux = min_lux
            camera.effective_px_ratio = effective_px_ratio

            self.combo_camera.configure(values=[f"{i + 1}. {c.name}" for i, c in enumerate(self.cameras)])
            self.combo_camera.current(self.selected_camera_index)
            return True
        except ValueError as exc:
            self.status_var.set(str(exc))
            if show_errors:
                messagebox.showerror("Girdi hatası", str(exc))
            return False

    # ── PERF: event coalescing ──────────────────────────────────────────────
    _DRAG_INTERVAL_MS = 33          # ~30 fps ceiling; use 16 for 60 fps

    def schedule_calculate(self, full: bool = False, delay_ms: int = None):
        """Collapse a burst of calculate() requests into a single run.

        Continuous input (dragging, resizing) should call this instead of
        calculate(). If a run is already queued the request is merged into it.
        """
        if full:
            self._calc_pending_full = True
        if self._calc_job is not None:
            return
        if delay_ms is None:
            delay_ms = self._DRAG_INTERVAL_MS
        self._calc_job = self.root.after(delay_ms, self._run_scheduled_calculate)

    def _run_scheduled_calculate(self):
        self._calc_job = None
        full = self._calc_pending_full
        self._calc_pending_full = False
        try:
            if not self.root.winfo_exists():
                return
        except tk.TclError:
            return
        self.calculate(light=not full)

    def flush_calculate(self):
        """Drop any queued light pass and run one full pass now.

        Called on mouse release, so the panels and the 3D view catch up exactly
        once at the end of a drag instead of on every pixel of it.
        """
        if self._calc_job is not None:
            try:
                self.root.after_cancel(self._calc_job)
            except Exception:
                pass
            self._calc_job = None
        self._calc_pending_full = False
        self.calculate()

    def calculate(self, show_errors=False, light=False):
        """light=True -> geometry + canvas only (used while dragging).
        light=False -> full pass, including the tables and the 3D window.
        """
        if self.suspend_calculate:
            return
        if self.canvas.winfo_width() < 120 or self.canvas.winfo_height() < 120:
            return
        if not self._commit_form_to_selected(show_errors=show_errors):
            return
        if not self._update_plan_width_from_entry(show_errors=show_errors):
            return

        try:
            mode = self.lens_mode.get()
            modes = ["min", "max"] if mode == "compare" else [mode]
            # PERF: build into a local dict and rebind once, so an exception
            # mid-loop cannot leave a half-built self.last_all_results visible.
            results: Dict[str, List[OpticResult]] = {}
            for camera in self.cameras:
                results[camera.name] = [calculate_for_camera(camera, item, self.ppm_levels) for item in modes]
            self.last_all_results = results
            selected_camera = self.cameras[self.selected_camera_index]
            self.last_selected_results = results[selected_camera.name]

            # PERF: these four rebuild widgets that are not being looked at
            # mid-drag. Skipping them cuts a drag frame from ~8-25 ms to ~1-3 ms.
            if not light:
                self._populate_table()
                self._populate_recommendations()
                self.update_lens_suggestion()
                self.update_alternative_models()

            self._update_target_analysis()
            analyze_dead_zone_coverage(self.last_all_results, self.cameras)
            self._update_dead_zone_panel()
            self._draw_canvas()
            self._update_info_label()

            if not light:
                view_3d = getattr(self, "view_3d_window", None)
                if view_3d is not None and view_3d.window.winfo_exists():
                    view_3d.update_from_parent()
            self.status_var.set("")
        except Exception as exc:
            self.status_var.set(f"Hata: {exc}")
            if show_errors:
                messagebox.showerror("Hesaplama hatası", str(exc))

    def _update_plan_width_from_entry(self, show_errors=False) -> bool:
        try:
            self.plan_width_m = self._read_float(self.entry_plan_width, "Plan genişliği", 1.0)
            return True
        except ValueError as exc:
            self.status_var.set(str(exc))
            if show_errors:
                messagebox.showerror("Plan ölçeği", str(exc))
            return False

    def _populate_table(self):
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)   # PERF: 1 Tcl round trip, not len(rows)
        insert = self.tree.insert
        for result in self.last_selected_results:
            for row in sorted(result.rows, key=lambda item: item.ppm, reverse=True):
                dist_text = "-" if row.ground_distance_m <= 0 else f"{row.ground_distance_m:.2f} m"
                insert(
                    "",
                    tk.END,
                    values=(
                        row.camera,
                        row.mode,
                        row.level,
                        row.level_type,
                        f"{row.ppm:g}",
                        f"{row.optical_distance_m:.2f} m",
                        dist_text,
                        row.status,
                    ),
                )

    def _populate_recommendations(self):
        self.recommendation_text.configure(state=tk.NORMAL)
        self.recommendation_text.delete("1.0", tk.END)
        lines = []
        for result in self.last_selected_results:
            prefix = mode_label(result.mode)
            for recommendation in result.recommendations:
                lines.append(f"{prefix}: {recommendation}")
        self.recommendation_text.insert("1.0", "\n".join(lines))
        self.recommendation_text.configure(state=tk.DISABLED)

    def _update_info_label(self):
        if not self.last_selected_results:
            self.info_var.set("")
            return
        parts = []
        for result in self.last_selected_results:
            geom_text = "açık" if not math.isfinite(result.max_geom_dist_m) else f"{result.max_geom_dist_m:.1f} m"
            dz_text = f"{result.dead_zone_m:.1f} m ({result.dead_zone_area_m2:.0f} m²)"
            parts.append(
                f"{mode_label(result.mode)} HFOV {result.hfov_deg:.1f}° / VFOV {result.vfov_deg:.1f}° / "
                f"Kör nokta {dz_text} / Geom. limit {geom_text}"
            )
        self.info_var.set(" | ".join(parts))

    def _update_dead_zone_panel(self):
        if not self.last_selected_results:
            self.dead_zone_var.set("Hesaplama bekleniyor...")
            return
        lines = []
        for result in self.last_selected_results:
            mode = mode_label(result.mode)
            cam = result.camera
            if result.dead_zone_m < 0.01:
                lines.append(f"{mode}: Ölü bölge yok ✓")
                continue
            lines.append(f"── {mode} ({result.focal_mm:g} mm) ──")
            lines.append(f"Ölü bölge mesafesi: {result.dead_zone_m:.1f} m")
            lines.append(f"Ölü bölge alanı: {result.dead_zone_area_m2:.1f} m²")
            lines.append(f"En geniş yatay kapalı: ±{result.dead_zone_left_m:.1f} m")
            lines.append(f"Dikey yükseklik farkı: {result.vertical_drop_m:.1f} m")
            lines.append(f"Alt ışın açısı: {result.bottom_ray_deg:.1f}°")
            if result.dead_zone_covered_by:
                cams = ", ".join(result.dead_zone_covered_by)
                lines.append(f"✓ Kaplayan kamera(lar): {cams}")
            elif len(self.cameras) > 1:
                lines.append("✗ Bu ölü bölgeyi kapatan kamera yok!")
            if result.dead_zone_m > 3:
                ideal_tilt = math.degrees(math.atan(result.vertical_drop_m / 3.0))
                vfov_half = result.vfov_deg / 2
                suggested_tilt = max(ideal_tilt - vfov_half, 5.0)
                lines.append(
                    f"💡 ≤3 m ölü bölge için tilt ≈ {suggested_tilt:.0f}° "
                    f"veya yükseklik ≤ {cam.target_height_m + 3.0 * math.tan(math.radians(result.bottom_ray_deg)):.1f} m"
                )
        self.dead_zone_var.set("\n".join(lines))

    def _draw_canvas(self):
        self.last_canvas_context = self.drawer.draw_all(
            cameras=self.cameras,
            selected_camera_index=self.selected_camera_index,
            last_all_results=self.last_all_results,
            last_selected_results=self.last_selected_results,
            ppm_levels=self.ppm_levels,
            target_point=self.target_point,
            selected_design_level=self._selected_design_level(),
            plan_path=self.plan_path,
            plan_width_m=self.plan_width_m,
            auto_view_scale=self.auto_view_scale.get(),
            plan_photo_cache=self.plan_photo_cache,
        )

    def _selected_design_level(self) -> PPMLevel:
        # PERF: O(1) dict lookup. Call self._invalidate_level_cache() anywhere
        # self.ppm_levels is replaced or mutated.
        if self._level_by_name is None:
            self._level_by_name = {level.name: level for level in self.ppm_levels}
            self._level_by_key = {level.key: level for level in self.ppm_levels}
        level = self._level_by_name.get(self.design_level_var.get())
        if level is not None:
            return level
        level = self._level_by_key.get(self.target_point.required_level_key)
        if level is not None:
            return level
        return max(self.ppm_levels, key=lambda item: item.ppm)

    def _invalidate_level_cache(self):
        self._level_by_name = None
        self._level_by_key = None
        self._levels_desc_cache = None

    def _update_target_analysis(self):
        if hasattr(self, "entry_target_name"):
            self.target_point.name = self.entry_target_name.get().strip() or "Kontrol Noktası"
        level = self._selected_design_level()
        self.target_point.required_level_key = level.key
        if not self.target_point.active:
            self.target_info_var.set("Kontrol noktası seçilmedi.")
            return
        lines = []
        for result in self.last_selected_results:
            data = target_analysis_for_result(self.target_point, result, level)
            lines.append(
                f"{data['mode']}: {data['distance']:.1f} m, {data['ppm']:.0f} px/m, "
                f"{data['angle_diff']:+.1f}°, {data['status']}"
            )
        self.target_info_var.set(f"{self.target_point.name} ({level.name})\n" + "\n".join(lines))

    def update_lens_suggestion(self):
        if not self.last_selected_results:
            return
        try:
            distance = self._current_design_distance()
        except Exception:
            self.lens_suggestion_var.set("Hedef mesafe sayısal olmalı.")
            return
        level = self._selected_design_level()
        camera = self.cameras[self.selected_camera_index]
        sensor_w, _ = SENSOR_DIMS_MM[camera.sensor_name]
        res_w, _ = RESOLUTIONS[camera.resolution_name]
        vertical_drop = max(camera.pole_height_m - camera.target_height_m, 0.05)
        optical_distance = math.sqrt(distance**2 + vertical_drop**2)
        required_focal = (level.ppm * sensor_w * optical_distance) / res_w
        if required_focal < camera.focal_min_mm:
            verdict = "mevcut lens yeterli, daha geniş açı da mümkün"
        elif required_focal <= camera.focal_max_mm:
            verdict = "mevcut lens aralığında"
        else:
            verdict = "mevcut max odak yetersiz"
        self.lens_suggestion_var.set(
            f"{level.name} için {distance:.1f} m hedefte önerilen odak: {required_focal:.1f} mm ({verdict})."
        )

    def update_alternative_models(self):
        if not self.last_selected_results:
            return
        try:
            distance = self._current_design_distance()
        except Exception:
            self.alternative_models_var.set("Alternatif için hedef mesafe sayısal olmalı.")
            self.last_alternative_model_names = []
            return

        level = self._selected_design_level()
        current_camera = self.cameras[self.selected_camera_index]
        vertical_drop = max(current_camera.pole_height_m - current_camera.target_height_m, 0.05)
        optical_distance = math.sqrt(distance**2 + vertical_drop**2)
        angle_diff_val = None
        if self.target_point.active:
            dx = self.target_point.x_m - current_camera.pos_x_m
            dy = self.target_point.y_m - current_camera.pos_y_m
            angle_diff_val = angle_diff(math.degrees(math.atan2(dy, dx)), current_camera.heading_deg)

        candidates = []
        rejected_by_focal = 0
        for model_name, model in self.camera_library.items():
            if not model or model_name == current_camera.model_name:
                continue
            sensor_name = model.get("sensor_name")
            resolution_name = model.get("resolution_name")
            if sensor_name not in SENSOR_DIMS_MM or resolution_name not in RESOLUTIONS:
                continue
            sensor_w, _ = SENSOR_DIMS_MM[sensor_name]
            res_w, _ = RESOLUTIONS[resolution_name]
            focal_min = self._model_float(model, "focal_min_mm", 2.8)
            focal_max = self._model_float(model, "focal_max_mm", focal_min)
            if focal_max < focal_min:
                focal_min, focal_max = focal_max, focal_min
            ir_range = self._model_float(model, "ir_range_m", 0.0)
            min_lux = self._model_float(model, "min_lux", 0.01)
            required_focal = (level.ppm * sensor_w * optical_distance) / res_w
            if required_focal > focal_max:
                rejected_by_focal += 1
                continue

            chosen_focal = min(max(required_focal, focal_min), focal_max)
            ppm_at_target = (res_w * chosen_focal) / max(optical_distance * sensor_w, 0.01)
            hfov = math.degrees(2 * math.atan(sensor_w / (2 * chosen_focal)))
            angle_ok = True if angle_diff_val is None else abs(angle_diff_val) <= hfov / 2
            ir_ok = ir_range <= 0 or distance <= ir_range

            focal_margin = max(0.0, focal_max - required_focal)
            ir_margin = 999.0 if ir_range <= 0 else ir_range - distance
            lux_bonus = min(30.0, max(0.0, 0.05 - min_lux) * 400)
            score = 0.0
            score += 300 if not ir_ok else 0
            score += 180 if not angle_ok else 0
            score += abs(chosen_focal - required_focal) * 3
            score += max(0.0, 8.0 - focal_margin) * 2
            score -= min(80.0, max(0.0, ir_margin))
            score -= lux_bonus

            candidates.append(
                {
                    "name": model_name,
                    "stock": model.get("stock_code", ""),
                    "required_focal": required_focal,
                    "chosen_focal": chosen_focal,
                    "focal_min": focal_min,
                    "focal_max": focal_max,
                    "ppm": ppm_at_target,
                    "ir_range": ir_range,
                    "ir_ok": ir_ok,
                    "angle_ok": angle_ok,
                    "hfov": hfov,
                    "min_lux": min_lux,
                    "score": score,
                }
            )

        candidates.sort(key=lambda item: item["score"])
        best = candidates[:5]
        self.last_alternative_model_names = [item["name"] for item in best]
        if not best:
            self.alternative_models_var.set(
                f"{level.name} için {distance:.1f} m hedefte uygun alternatif bulunamadı. "
                f"{rejected_by_focal} model lens aralığı nedeniyle elendi."
            )
            return

        current_status = self._current_model_status_text(current_camera, level, distance, optical_distance)
        lines = [current_status, f"En iyi alternatifler ({level.name}, {distance:.1f} m):"]
        for index, item in enumerate(best, start=1):
            flags = []
            if not item["ir_ok"]:
                flags.append("IR kısa")
            if not item["angle_ok"]:
                flags.append("açı dışı")
            flag_text = " | " + ", ".join(flags) if flags else ""
            stock = f"{item['stock']} " if item["stock"] else ""
            lines.append(
                f"{index}. {stock}{item['focal_min']:.1f}-{item['focal_max']:.1f} mm, "
                f"önerilen {item['required_focal']:.1f} mm, IR {item['ir_range']:.0f} m, "
                f"{item['ppm']:.0f} px/m{flag_text}"
            )
        self.alternative_models_var.set("\n".join(lines))

    def apply_first_alternative_model(self):
        if not self.last_alternative_model_names:
            self.update_alternative_models()
        if not self.last_alternative_model_names:
            messagebox.showinfo("Alternatif model", "Uygulanacak alternatif model bulunamadı.")
            return
        model_name = self.last_alternative_model_names[0]
        self.combo_camera_model.set(model_name)
        self.apply_camera_model()

    def _current_design_distance(self) -> float:
        if self.target_point.active:
            camera = self.cameras[self.selected_camera_index]
            return max(0.1, math.hypot(self.target_point.x_m - camera.pos_x_m, self.target_point.y_m - camera.pos_y_m))
        return self._read_float(self.entry_design_distance, "Hedef mesafe", 0.1)

    def _model_float(self, model: dict, key: str, default: float) -> float:
        try:
            return float(model.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    def _current_model_status_text(self, camera: CameraConfig, level: PPMLevel, distance: float, optical_distance: float) -> str:
        sensor_w, _ = SENSOR_DIMS_MM[camera.sensor_name]
        res_w, _ = RESOLUTIONS[camera.resolution_name]
        required_focal = (level.ppm * sensor_w * optical_distance) / res_w
        focal_ok = required_focal <= camera.focal_max_mm
        ir_ok = camera.ir_range_m <= 0 or distance <= camera.ir_range_m
        if focal_ok and ir_ok:
            verdict = "Mevcut model hedefi karşılıyor."
        elif not focal_ok and not ir_ok:
            verdict = "Mevcut model lens ve IR açısından yetersiz."
        elif not focal_ok:
            verdict = "Mevcut modelin max odak değeri yetersiz."
        else:
            verdict = "Mevcut modelin IR mesafesi yetersiz."
        return f"{verdict} Gereken odak: {required_focal:.1f} mm."

    def optimize_tilt(self, apply_value=False):
        if not self._commit_form_to_selected(show_errors=True):
            return
        try:
            distance = self._read_float(self.entry_design_distance, "Hedef mesafe", 0.1)
        except ValueError as exc:
            messagebox.showerror("Optimizasyon", str(exc))
            return
        level = self._selected_design_level()
        camera = self.cameras[self.selected_camera_index]
        res = optimize_tilt_calc(camera, self.lens_mode.get(), distance, level, self.ppm_levels)
        if not res:
            return
        tilt, result, ppm = res
        text = (
            f"Önerilen tilt: {tilt:.1f}° | Kör nokta: {result.dead_zone_m:.1f} m | "
            f"{distance:.1f} m hedefte {ppm:.0f} px/m"
        )
        self.optimization_var.set(text)
        if apply_value:
            self._set_entry(self.entry_tilt, f"{tilt:.1f}")
            self.calculate(show_errors=True)

    def _on_canvas_motion(self, event):
        if not self.last_selected_results or not self.last_canvas_context:
            return
        top_rect = self.last_canvas_context.get("top_rect")
        world = self.last_canvas_context.get("world")
        if top_rect and event.y >= top_rect[1] and world:
            plot = top_plot_from_rect(top_rect)
            if plot[0] <= event.x <= plot[2] and plot[1] <= event.y <= plot[3]:
                x, y = canvas_to_world(event.x, event.y, plot, world)
                self.mouse_var.set(f"Plan koordinatı: X {x:.1f} m, Y {y:.1f} m")
                return

        result = self.last_selected_results[0]
        width = self.canvas.winfo_width()
        split_y = int(self.canvas.winfo_height() * 0.46)
        lane_rect = (0, 26, width, split_y - 4)
        origin_x = lane_rect[0] + 64
        right_x = lane_rect[2] - 28
        px_per_m = (right_x - origin_x) / max(self.last_canvas_context.get("max_draw_dist", 1), 1)
        distance = max(0.0, (event.x - origin_x) / max(px_per_m, 0.01))
        ppm = ppm_at_distance(result, distance)
        level_name = self._level_name_for_ppm(ppm)
        self.mouse_var.set(f"Mesafe {distance:.1f} m | Yaklaşık {ppm:.0f} px/m | Seviye: {level_name}")

    def _on_canvas_button_1(self, event):
        location = self._event_to_top_world(event)
        if not location:
            self.drag_action = None
            return
        x, y, plot, world = location
        nearest = self._nearest_camera_index(event.x, event.y, plot, world)
        if nearest is not None:
            self.selected_camera_index = nearest
            self._refresh_camera_combo()
            self._load_camera_to_form()
        tool = self.plan_tool.get()
        if tool == "target":
            self._set_target_point(x, y)
            self.drag_action = "target"
        elif tool == "rotate":
            self._set_selected_camera_heading(x, y)
            self.drag_action = "rotate"
        elif tool == "move":
            self._set_selected_camera_position(x, y)
            self.drag_action = "move"
        elif nearest is not None:
            self.calculate()
            self.drag_action = None

    def _on_canvas_drag_1(self, event):
        location = self._event_to_top_world(event)
        if not location or not self.drag_action:
            return
        x, y, _, _ = location
        if self.drag_action == "move":
            self._set_selected_camera_position(x, y)
        elif self.drag_action == "rotate":
            self._set_selected_camera_heading(x, y)
        elif self.drag_action == "target":
            self._set_target_point(x, y)

    def _on_canvas_button_3(self, event):
        location = self._event_to_top_world(event)
        if not location:
            return
        x, y, plot, world = location
        nearest = self._nearest_camera_index(event.x, event.y, plot, world)
        if nearest is not None:
            self.selected_camera_index = nearest
            self._refresh_camera_combo()
            self._load_camera_to_form()
        self._set_selected_camera_heading(x, y)
        self.drag_action = "rotate"

    def _on_canvas_drag_3(self, event):
        location = self._event_to_top_world(event)
        if not location:
            return
        x, y, _, _ = location
        self._set_selected_camera_heading(x, y)

    def _on_canvas_release(self, event):
        self.drag_action = None
        self.flush_calculate()   # PERF: the single full pass for the whole drag

    def _event_to_top_world(self, event) -> Optional[Tuple[float, float, Tuple[float, float, float, float], Tuple[float, float, float, float]]]:
        top_rect = self.last_canvas_context.get("top_rect")
        world = self.last_canvas_context.get("world")
        if not top_rect or not world:
            return None
        plot = top_plot_from_rect(top_rect)
        if not (plot[0] <= event.x <= plot[2] and plot[1] <= event.y <= plot[3]):
            return None
        x, y = canvas_to_world(event.x, event.y, plot, world)
        return x, y, plot, world

    def _nearest_camera_index(self, px: float, py: float, plot: Tuple[float, float, float, float], world: Tuple[float, float, float, float]) -> Optional[int]:
        best_index = None
        best_distance = 18.0
        for index, camera in enumerate(self.cameras):
            cx, cy = self.drawer.canvas_to_plot_point(camera.pos_x_m, camera.pos_y_m, plot, world) if hasattr(self.drawer, 'canvas_to_plot_point') else (0, 0) # Fallback if needed
            # Use module level world_to_canvas:
            from .canvas_drawer import world_to_canvas as w2c
            cx, cy = w2c(camera.pos_x_m, camera.pos_y_m, plot, world)
            distance = math.hypot(px - cx, py - cy)
            if distance < best_distance:
                best_distance = distance
                best_index = index
        return best_index

    def _set_selected_camera_position(self, x: float, y: float):
        camera = self.cameras[self.selected_camera_index]
        camera.pos_x_m = round(x, 2)
        camera.pos_y_m = round(y, 2)
        self._set_entry(self.entry_pos_x, camera.pos_x_m)
        self._set_entry(self.entry_pos_y, camera.pos_y_m)
        self.schedule_calculate()   # PERF: coalesced; flushed on button release

    def _set_selected_camera_heading(self, x: float, y: float):
        camera = self.cameras[self.selected_camera_index]
        dx = x - camera.pos_x_m
        dy = y - camera.pos_y_m
        if math.hypot(dx, dy) < 0.05:
            return
        camera.heading_deg = round(math.degrees(math.atan2(dy, dx)) % 360, 1)
        self._set_entry(self.entry_heading, camera.heading_deg)
        self.schedule_calculate()   # PERF: coalesced; flushed on button release

    def _set_target_point(self, x: float, y: float):
        self.target_point.active = True
        self.target_point.name = self.entry_target_name.get().strip() or "Kontrol Noktası"
        self.target_point.x_m = round(x, 2)
        self.target_point.y_m = round(y, 2)
        self.schedule_calculate()   # PERF: coalesced; flushed on button release

    def _levels_desc(self) -> List[PPMLevel]:
        """PPM levels, highest first. PERF: cached - this used to be re-sorted
        on every <Motion> event and on every table repopulation."""
        if self._levels_desc_cache is None:
            self._levels_desc_cache = sorted(self.ppm_levels, key=lambda item: item.ppm, reverse=True)
        return self._levels_desc_cache

    def _level_name_for_ppm(self, ppm: float) -> str:
        for level in self._levels_desc():
            if ppm >= level.ppm:
                return level.name
        return "PPM altı"

    def load_plan_image(self):
        path = filedialog.askopenfilename(
            title="Plan görseli aç",
            filetypes=[
                ("Görseller", "*.png *.gif *.jpg *.jpeg *.bmp"),
                ("PNG", "*.png"),
                ("Tüm dosyalar", "*.*"),
            ],
        )
        if not path:
            return
        self.plan_path = path
        self.plan_photo_cache = {"photo": None, "source": None}
        self.lbl_plan_path.configure(text=Path(path).name)
        self.calculate()

    def clear_plan_image(self):
        self.plan_path = ""
        self.plan_photo_cache = {"photo": None, "source": None}
        self.lbl_plan_path.configure(text="Plan yok")
        self.calculate()

    def clear_target_point(self):
        self.target_point.active = False
        self.target_info_var.set("Kontrol noktası seçilmedi.")
        self.calculate()

    def _refresh_level_tree(self):
        # PERF/correctness: this runs after every ppm_levels mutation (add,
        # delete, project load), so it is the right place to drop the caches.
        self._invalidate_level_cache()
        children = self.level_tree.get_children()
        if children:
            self.level_tree.delete(*children)
        for level in self._levels_desc():
            self.level_tree.insert(
                "",
                tk.END,
                iid=level.key,
                values=(level.name, level.level_type, f"{level.ppm:g}", level.color),
            )
        target_names = [lvl.name for lvl in self.ppm_levels]
        self.combo_target_level.configure(values=target_names)

    def _on_level_selected(self, event=None):
        selected = self.level_tree.selection()
        if not selected:
            return
        key = selected[0]
        self.selected_level_key = key
        level = next((lvl for lvl in self.ppm_levels if lvl.key == key), None)
        if not level:
            return
        self._set_entry(self.entry_level_name, level.name)
        self._set_entry(self.entry_level_ppm, level.ppm)
        self.combo_level_type.set(level.level_type)
        self.level_color_var.set(level.color)
        self.level_color_swatch.configure(bg=level.color)

    def choose_level_color(self):
        color = colorchooser.askcolor(initialcolor=self.level_color_var.get(), title="Katman rengi")[1]
        if color:
            self.level_color_var.set(color)
            self.level_color_swatch.configure(bg=color)

    def add_or_update_level(self):
        name = self.entry_level_name.get().strip()
        if not name:
            messagebox.showerror("PPM seviyesi", "Seviye adı boş olamaz.")
            return
        try:
            ppm = self._read_float(self.entry_level_ppm, "PPM", 1.0)
        except ValueError as exc:
            messagebox.showerror("PPM seviyesi", str(exc))
            return
        level_type = self.combo_level_type.get()
        color = self.level_color_var.get()
        key = self.selected_level_key or f"Custom_{name.translate(str.maketrans(' ', '_'))}"

        existing = next((lvl for lvl in self.ppm_levels if lvl.key == key or lvl.name == name), None)
        if existing:
            existing.name = name
            existing.ppm = ppm
            existing.level_type = level_type
            existing.color = color
        else:
            self.ppm_levels.append(PPMLevel(key=key, name=name, ppm=ppm, color=color, level_type=level_type))

        self.selected_level_key = None
        self._refresh_level_tree()
        self.calculate()

    def delete_level(self):
        if not self.selected_level_key:
            messagebox.showinfo("PPM seviyesi", "Silmek için listeden bir katman seçin.")
            return
        if len(self.ppm_levels) <= 1:
            messagebox.showwarning("PPM seviyesi", "En az bir seviye kalmalıdır.")
            return
        key = self.selected_level_key
        self.ppm_levels = [lvl for lvl in self.ppm_levels if lvl.key != key]
        self.selected_level_key = None
        self._refresh_level_tree()
        self.calculate()

    def save_project(self):
        if not self._commit_form_to_selected(show_errors=True):
            return
        path = filedialog.asksaveasfilename(
            title="Projeyi kaydet",
            defaultextension=".json",
            filetypes=[("CCTV Projesi", "*.json"), ("Tüm dosyalar", "*.*")],
        )
        if not path:
            return
        data = {
            "version": "2.0",
            "cameras": [asdict(cam) for cam in self.cameras],
            "plan_path": self.plan_path,
            "plan_width_m": self.plan_width_m,
            "auto_view_scale": self.auto_view_scale.get(),
            "target_point": asdict(self.target_point),
            "ppm_levels": [asdict(lvl) for lvl in self.ppm_levels],
            "selected_camera_index": self.selected_camera_index,
            "design_distance": self.design_distance_var.get(),
            "design_level": self.design_level_var.get(),
        }
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=4)
        self.status_var.set(f"Proje kaydedildi: {Path(path).name}")

    def load_project(self):
        path = filedialog.askopenfilename(
            title="Proje aç",
            filetypes=[("CCTV Projesi", "*.json"), ("Tüm dosyalar", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
            cam_list = data.get("cameras", [])
            if not cam_list:
                raise ValueError("Proje dosyasında kamera bulunamadı.")
            self.cameras = [CameraConfig(**cam) for cam in cam_list]
            self.selected_camera_index = min(data.get("selected_camera_index", 0), len(self.cameras) - 1)
            self.plan_path = data.get("plan_path", "")
            self.plan_width_m = float(data.get("plan_width_m", 45.0))
            self.auto_view_scale.set(bool(data.get("auto_view_scale", True)))
            target_data = data.get("target_point", {})
            self.target_point = TargetPoint(**target_data) if target_data else TargetPoint()
            levels_data = data.get("ppm_levels", [])
            if levels_data:
                self.ppm_levels = [PPMLevel(**lvl) for lvl in levels_data]

            self.design_distance_var.set(str(data.get("design_distance", "20.0")))
            self.design_level_var.set(str(data.get("design_level", "Ozel: TR Plaka")))

            self._set_entry(self.entry_plan_width, self.plan_width_m)
            self._set_entry(self.entry_target_name, self.target_point.name)
            self._set_entry(self.entry_design_distance, self.design_distance_var.get())
            self.lbl_plan_path.configure(text=Path(self.plan_path).name if self.plan_path else "Plan yok")

            self._refresh_camera_combo()
            self._refresh_level_tree()
            self._load_camera_to_form()
            self.calculate()
            self.status_var.set(f"Proje yüklendi: {Path(path).name}")
        except Exception as exc:
            messagebox.showerror("Proje yükleme", f"Proje dosyası açılamadı:\n{exc}")

    def export_csv(self):
        if not self.last_all_results:
            self.calculate(show_errors=True)
        path = filedialog.asksaveasfilename(
            title="CSV dışa aktar",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Tüm dosyalar", "*.*")],
        )
        if not path:
            return
        export_csv(path, self.last_all_results)
        self.status_var.set(f"CSV kaydedildi: {Path(path).name}")

    def export_png(self):
        path = filedialog.asksaveasfilename(
            title="PNG görsel dışa aktar",
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("Tüm dosyalar", "*.*")],
        )
        if not path:
            return
        export_png(path, self.root, self.canvas)
        self.status_var.set(f"PNG kaydedildi: {Path(path).name}")

    def export_pdf(self):
        if not self.last_all_results:
            self.calculate(show_errors=True)
        path = filedialog.asksaveasfilename(
            title="PDF rapor dışa aktar",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf"), ("Tüm dosyalar", "*.*")],
        )
        try:
            compliance_res = self.spec_window.last_compliance_result if getattr(self, "spec_window", None) else None
            export_pdf(
                path=path,
                cameras=self.cameras,
                lens_mode=self.lens_mode.get(),
                last_all_results=self.last_all_results,
                target_point=self.target_point,
                selected_level_name=self._selected_design_level().name,
                target_info_text=self.target_info_var.get(),
                last_compliance_result=compliance_res,
            )
            self.status_var.set(f"ASELSAN PDF Raporu kaydedildi: {Path(path).name}")
        except Exception as exc:
            messagebox.showerror("PDF dışa aktar", str(exc))

    def export_excel(self):
        if not self.last_all_results:
            self.calculate(show_errors=True)
        path = filedialog.asksaveasfilename(
            title="Excel (.xlsx) rapor dışa aktar",
            defaultextension=".xlsx",
            filetypes=[("Excel Çalışma Kitabı", "*.xlsx"), ("Tüm dosyalar", "*.*")],
        )
        if not path:
            return
        try:
            compliance_res = self.spec_window.last_compliance_result if getattr(self, "spec_window", None) else None
            export_excel(
                path=path,
                cameras=self.cameras,
                lens_mode=self.lens_mode.get(),
                last_all_results=self.last_all_results,
                target_point=self.target_point,
                selected_level_name=self._selected_design_level().name,
                last_compliance_result=compliance_res,
            )
            self.status_var.set(f"Excel kaydedildi: {Path(path).name}")
        except Exception as exc:
            messagebox.showerror("Excel dışa aktar", str(exc))
