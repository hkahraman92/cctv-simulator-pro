import tkinter as tk
from tkinter import ttk, messagebox
from typing import Dict, Any, Optional, Tuple

from ..database import (
    camera_db_extended_field_specs,
    camera_db_missing_fields,
    has_camera_db_value,
    read_camera_library_json,
    write_camera_library_json,
    load_camera_library,
    model_descriptor,
)
from ..config import get_admin_password, set_admin_password
from ..theme import is_themed, COLORS, StyledButton, fit_and_center_window, set_window_icon


class CameraDatabaseWindow:
    def __init__(self, parent_app: Any):
        self.app = parent_app
        self.window = tk.Toplevel(self.app.root)
        self.window.title("Kamera Veritabanı ve Eksik Veri Tamamlama")
        fit_and_center_window(self.window, default_w=1320, default_h=820, min_w=900, min_h=600, maximize=False)
        set_window_icon(self.window)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.selected_model_name = ""
        self.entries: Dict[str, tk.Widget] = {}
        self.texts: Dict[str, tk.Text] = {}
        self.status_var = tk.StringVar(value="Hazır")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *args: self.refresh_tree())

        from ..errors import guarded_build
        self.build_ok = guarded_build(self.window, self._build_ui,
                                      "Kamera Veritabanı")
        if not self.build_ok:
            return

    def close(self):
        self.app.camera_db_window = None
        self.window.destroy()

    def lift(self):
        self.window.lift()

    def _set_sash_pos(self, paned: ttk.PanedWindow, pos: int):
        try:
            if self.window.winfo_exists():
                paned.sashpos(0, pos)
        except Exception:
            pass

    def _change_admin_password(self):
        dialog = tk.Toplevel(self.window)
        dialog.title("Admin Şifresi Değiştir")
        dialog.transient(self.window)
        dialog.grab_set()

        fit_and_center_window(dialog, default_w=400, default_h=250, min_w=360, min_h=220, maximize=False)

        ttk.Label(
            dialog,
            text="🔑 Yönetici Şifresi Değiştirme",
            font=("Segoe UI", 10, "bold"),
            foreground=COLORS["accent"],
        ).pack(anchor=tk.W, padx=16, pady=(16, 6))

        frame = ttk.Frame(dialog)
        frame.pack(fill=tk.X, padx=16, pady=4)

        ttk.Label(frame, text="Mevcut Şifre:").grid(row=0, column=0, sticky=tk.W, pady=3, padx=(0, 6))
        old_entry = ttk.Entry(frame, show="*")
        old_entry.grid(row=0, column=1, sticky=tk.EW, pady=3)

        ttk.Label(frame, text="Yeni Şifre:").grid(row=1, column=0, sticky=tk.W, pady=3, padx=(0, 6))
        new_entry = ttk.Entry(frame, show="*")
        new_entry.grid(row=1, column=1, sticky=tk.EW, pady=3)

        ttk.Label(frame, text="Yeni Şifre (Tekrar):").grid(row=2, column=0, sticky=tk.W, pady=3, padx=(0, 6))
        confirm_entry = ttk.Entry(frame, show="*")
        confirm_entry.grid(row=2, column=1, sticky=tk.EW, pady=3)
        frame.columnconfigure(1, weight=1)

        btn_row = ttk.Frame(dialog)
        btn_row.pack(fill=tk.X, padx=16, pady=16)

        def save_pass():
            old_p = old_entry.get().strip()
            new_p = new_entry.get().strip()
            conf_p = confirm_entry.get().strip()

            if old_p != get_admin_password():
                messagebox.showerror("Hata", "Mevcut şifre hatalı!", parent=dialog)
                return
            if not new_p:
                messagebox.showerror("Hata", "Yeni şifre boş olamaz!", parent=dialog)
                return
            if new_p != conf_p:
                messagebox.showerror("Hata", "Yeni şifreler birbiriyle eşleşmiyor!", parent=dialog)
                return

            set_admin_password(new_p)
            messagebox.showinfo("Başarılı", "Yönetici şifresi başarıyla değiştirildi.", parent=dialog)
            dialog.destroy()

        StyledButton(btn_row, text="Kaydet", command=save_pass, bootstyle="success").pack(side=tk.RIGHT, padx=(4, 0))
        StyledButton(btn_row, text="İptal", command=dialog.destroy, bootstyle="secondary-outline").pack(side=tk.RIGHT)

    def _build_ui(self):
        paned = ttk.PanedWindow(self.window, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        left = ttk.Frame(paned, padding=6, width=420)
        right = ttk.Frame(paned, padding=6)
        paned.add(left, weight=1)
        paned.add(right, weight=2)

        # Ensure sash is placed so left camera list is clearly visible on any resolution
        self.window.after(100, lambda: self._set_sash_pos(paned, 440))

        search_row = ttk.Frame(left)
        search_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(search_row, text="Ara:").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Entry(search_row, textvariable=self.search_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        columns = ("stock", "status", "missing")
        self.tree = ttk.Treeview(left, columns=columns, show="tree headings", height=24)
        self.tree.heading("#0", text="Kamera Modeli / Ürün")
        self.tree.heading("stock", text="Stok Kodu")
        self.tree.heading("status", text="Durum")
        self.tree.heading("missing", text="Eksik Alanlar")

        self.tree.column("#0", width=220, anchor=tk.W)
        self.tree.column("stock", width=110, anchor=tk.W)
        self.tree.column("status", width=95, anchor=tk.CENTER)
        self.tree.column("missing", width=180, anchor=tk.W)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=tree_scroll.set)

        self.tree.tag_configure("critical", background=COLORS["treeview_uyumsuz"])
        self.tree.tag_configure("partial", background=COLORS["treeview_kismi"])
        self.tree.tag_configure("complete", background=COLORS["treeview_uyumlu"])
        self.tree.bind("<<TreeviewSelect>>", self._on_selected)

        top_info = ttk.Frame(right)
        top_info.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(
            top_info,
            text="Excel'den aktarılan 500+ model broşür verisi düzenlenebilir ve yeni model eklenebilir.",
            font=("Segoe UI", 9, "bold"),
            foreground=COLORS["accent"],
        ).pack(side=tk.LEFT)
        StyledButton(top_info, text="🔑 Şifre Değiştir", command=self._change_admin_password, bootstyle="warning-outline").pack(
            side=tk.RIGHT, padx=(4, 0)
        )
        StyledButton(top_info, text="Aktif Kameradan Yeni Kayıt Aç", command=self._new_from_active, bootstyle="info-outline").pack(
            side=tk.RIGHT
        )
        ttk.Label(right, textvariable=self.status_var, foreground="#263238").pack(fill=tk.X, pady=(0, 6))

        # ── Action buttons MUST be packed BEFORE the notebook ──
        # Pack with side=BOTTOM first so they are always visible at the bottom.
        action_row = ttk.Frame(right)
        action_row.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
        StyledButton(action_row, text="Kaydet / Güncelle", command=self.save_model, bootstyle="success").pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4)
        )
        StyledButton(action_row, text="Aktif Kameraya Uygula", command=self._apply_to_active, bootstyle="info").pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=4
        )
        StyledButton(action_row, text="JSON'dan Kaldır", command=self.delete_model, bootstyle="danger-outline").pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=4
        )
        StyledButton(action_row, text="Formu Temizle", command=self.clear_form, bootstyle="secondary-outline").pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0)
        )

        self.db_notebook = ttk.Notebook(right)
        self.db_notebook.pack(fill=tk.BOTH, expand=True)

        basic_tab = ttk.Frame(self.db_notebook, padding=8)
        optic_tab = ttk.Frame(self.db_notebook, padding=8)
        video_tab = ttk.Frame(self.db_notebook, padding=8)
        ai_audio_tab = ttk.Frame(self.db_notebook, padding=8)
        physical_tab = ttk.Frame(self.db_notebook, padding=8)
        raw_tab = ttk.Frame(self.db_notebook, padding=8)

        self.db_notebook.add(basic_tab, text="Temel")
        self.db_notebook.add(optic_tab, text="Optik/DORI")
        self.db_notebook.add(video_tab, text="Video/Ağ")
        self.db_notebook.add(ai_audio_tab, text="AI/Ses/Alarm")
        self.db_notebook.add(physical_tab, text="Fiziksel/Çevre")
        self.db_notebook.add(raw_tab, text="Ham Broşür")

        self._build_basic_tab(basic_tab)
        self._build_optic_tab(optic_tab)
        self._build_video_tab(video_tab)
        self._build_ai_audio_tab(ai_audio_tab)
        self._build_physical_tab(physical_tab)
        self._build_raw_tab(raw_tab)

        preferred = self.app.combo_camera_model.get() if hasattr(self.app, "combo_camera_model") else ""
        self.refresh_tree(select_name=preferred)
        if preferred in self.app.camera_library and self.app.camera_library.get(preferred):
            self.load_model_to_form(preferred)
        else:
            self.clear_form()

    def _db_entry(self, parent: ttk.Frame, row: int, key: str, label: str, width: int = 20) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=3, padx=(0, 8))
        entry = ttk.Entry(parent, width=width)
        entry.grid(row=row, column=1, sticky=tk.EW, pady=3)
        self.entries[key] = entry
        return entry

    def _db_combo(self, parent: ttk.Frame, row: int, key: str, label: str, values: list) -> ttk.Combobox:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=3, padx=(0, 8))
        combo = ttk.Combobox(parent, values=values, state="normal")
        combo.grid(row=row, column=1, sticky=tk.EW, pady=3)
        self.entries[key] = combo
        return combo

    def _db_multi_combo(self, parent: ttk.Frame, row: int, key: str, label: str, options: list) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=3, padx=(0, 8))
        container = ttk.Frame(parent)
        container.grid(row=row, column=1, sticky=tk.EW, pady=3)
        entry = ttk.Entry(container)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        btn = StyledButton(
            container,
            text="Çoklu Seç (✓)",
            command=lambda: self._open_multi_select_dialog(key, label, entry, options),
            bootstyle="info-outline",
        )
        btn.pack(side=tk.RIGHT, padx=(4, 0))
        self.entries[key] = entry
        return entry

    def _open_multi_select_dialog(self, key: str, label: str, entry_widget: ttk.Entry, options: list):
        dialog = tk.Toplevel(self.window)
        dialog.title(f"Çoklu Seçim: {label}")
        dialog.transient(self.window)
        dialog.grab_set()

        fit_and_center_window(dialog, default_w=500, default_h=500, min_w=420, min_h=380, maximize=False)

        current_val = entry_widget.get().strip()
        current_items = [item.strip() for item in current_val.split(",") if item.strip()]

        ttk.Label(
            dialog,
            text=f"Lütfen '{label}' için istediğiniz seçenekleri işaretleyin:",
            font=("Segoe UI", 9, "bold"),
            wraplength=460,
        ).pack(side=tk.TOP, anchor=tk.W, padx=14, pady=(14, 6))

        # Pack bottom action buttons FIRST so they are ALWAYS anchored to the bottom of the window
        btn_row = ttk.Frame(dialog)
        btn_row.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=14)

        vars_dict = {}

        def apply_selection():
            selected = [opt for opt, var in vars_dict.items() if var.get()]
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, ", ".join(selected))
            dialog.destroy()

        StyledButton(btn_row, text="Tamam / Uygula", command=apply_selection, bootstyle="success").pack(side=tk.RIGHT, padx=(6, 0))
        StyledButton(btn_row, text="İptal", command=dialog.destroy, bootstyle="secondary-outline").pack(side=tk.RIGHT)

        # Pack middle scrollable checkbox area in remaining space
        scroll_frame = ttk.Frame(dialog)
        scroll_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=14, pady=6)

        canvas = tk.Canvas(scroll_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(scroll_frame, orient="vertical", command=canvas.yview)
        inner_frame = ttk.Frame(canvas)

        inner_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        for opt in options:
            if not opt:
                continue
            is_checked = any(opt.casefold() in item.casefold() or item.casefold() in opt.casefold() for item in current_items)
            var = tk.BooleanVar(value=is_checked)
            vars_dict[opt] = var
            cb = ttk.Checkbutton(inner_frame, text=opt, variable=var)
            cb.pack(anchor=tk.W, pady=3, padx=4)

        for item in current_items:
            matching = [opt for opt in options if opt and (opt.casefold() in item.casefold() or item.casefold() in opt.casefold())]
            if not matching and item:
                var = tk.BooleanVar(value=True)
                vars_dict[item] = var
                cb = ttk.Checkbutton(inner_frame, text=f"{item} (Özel)", variable=var)
                cb.pack(anchor=tk.W, pady=3, padx=4)

    def _db_text(self, parent: ttk.Frame, row: int, key: str, label: str, height: int = 4) -> tk.Text:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.NW, pady=3, padx=(0, 8))
        text = tk.Text(parent, height=height, wrap=tk.WORD, font=("Arial", 9))
        text.grid(row=row, column=1, sticky=tk.NSEW, pady=3)
        scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=text.yview)
        scroll.grid(row=row, column=2, sticky=tk.NS, pady=3)
        text.configure(yscrollcommand=scroll.set)
        self.texts[key] = text
        return text

    def _build_basic_tab(self, tab: ttk.Frame):
        row = 0
        self._db_entry(tab, row, "model_name", "Model adı (ana anahtar):", width=28)
        row += 1
        self._db_entry(tab, row, "stock_code", "Stok kodu:")
        row += 1
        self._db_entry(tab, row, "product_name", "Ürün adı:")
        row += 1
        self._db_combo(
            tab,
            row,
            "sensor_name",
            "Sensör boyutu:",
            list(self.app.SENSOR_DIMS_MM.keys()),
        )
        row += 1
        self._db_combo(
            tab,
            row,
            "resolution_name",
            "Çözünürlük:",
            list(self.app.RESOLUTIONS.keys()),
        )
        row += 1
        self._db_entry(tab, row, "focal_min_mm", "Min odak (mm):")
        row += 1
        self._db_entry(tab, row, "focal_max_mm", "Max odak (mm):")
        row += 1
        self._db_entry(tab, row, "ir_range_m", "IR mesafesi (m):")
        row += 1
        self._db_entry(tab, row, "min_lux", "Minimum lux:")
        row += 1
        self._db_combo(
            tab,
            row,
            "camera_type",
            "Kamera tipi (Sabit/Dome/PTZ):",
            ["", "Sabit (Bullet)", "Sabit (Dome)", "Varifokal (Bullet)", "Varifokal (Dome)", "PTZ / Speed Dome", "Taret (Turret)"],
        )
        row += 1
        self._db_multi_combo(
            tab,
            row,
            "usage_purpose",
            "Kullanım amacı (Çoklu):",
            ["Dış Ortam Çevre Güvenliği", "İç Ortam Genel İzleme", "Plaka Tanıma (PTS / ANPR)", "Gece Görüşü / Uzak Mesafeli", "Kritik Tesis / Vandalizm", "Giriş / Çıkış Kontrolü"],
        )
        row += 1
        self._db_text(tab, row, "overview", "Özet paragraf:", height=4)
        row += 1
        self._db_text(tab, row, "highlights", "Öne çıkan özellikler:", height=4)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(11, weight=1)
        tab.rowconfigure(12, weight=1)

    def _build_optic_tab(self, tab: ttk.Frame):
        row = 0
        self._db_entry(tab, row, "max_fps", "Maks. fps:")
        row += 1
        self._db_combo(tab, row, "lens_type", "Lens tipi:", ["", "Varifokal (Motorize)", "Varifokal (Manuel)", "Sabit (Fixed)", "Fish-eye / Panoramik"])
        row += 1
        self._db_entry(tab, row, "aperture_f_number", "Apertür (F/N):")
        row += 1
        self._db_entry(tab, row, "horizontal_fov_deg", "Yatay FOV (°):")
        row += 1
        self._db_entry(tab, row, "vertical_fov_deg", "Dikey FOV (°):")
        row += 1
        self._db_entry(tab, row, "diagonal_fov_deg", "Diyagonal FOV (°):")
        row += 1
        self._db_entry(tab, row, "lens_mount", "Lens montajı:")
        row += 1
        self._db_entry(tab, row, "pan_range_deg", "Pan aralığı (°):")
        row += 1
        self._db_entry(tab, row, "tilt_range_deg", "Tilt aralığı (°):")
        row += 1
        self._db_entry(tab, row, "rotate_range_deg", "Rotate aralığı (°):")
        row += 1
        self._db_entry(tab, row, "dori_detect_m", "DORI Detect (m):")
        row += 1
        self._db_entry(tab, row, "dori_observe_m", "DORI Observe (m):")
        row += 1
        self._db_entry(tab, row, "dori_recognize_m", "DORI Recognize (m):")
        row += 1
        self._db_entry(tab, row, "dori_identify_m", "DORI Identify (m):")
        row += 1
        self._db_entry(tab, row, "shutter_speed", "Enstantane hızı:")
        row += 1
        self._db_combo(tab, row, "day_night", "Day & Night mod:", ["", "ICR Otomatik (Filtreli)", "Elektronik Day/Night", "ColorVu / Full Color"])
        row += 1
        self._db_entry(tab, row, "sn_ratio_db", "S/N oranı (dB):")
        row += 1
        self._db_combo(tab, row, "wdr", "WDR / HDR:", ["", "120 dB WDR", "140 dB WDR", "True WDR", "DWDR", "WDR Yok"])
        row += 1
        self._db_combo(tab, row, "illuminator_type", "Aydınlatıcı tipi:", ["", "IR LED", "Smart IR", "Beyaz Işık / LED", "Laser IR"])
        row += 1
        self._db_entry(tab, row, "white_light_range_m", "Beyaz ışık menzili (m):")
        row += 1
        self._db_entry(tab, row, "illuminator_wavelength_nm", "Dalga boyu (nm):")
        row += 1
        self._db_entry(tab, row, "smart_illumination", "Smart IR / Light:")
        row += 1
        self._db_text(tab, row, "image_enhancements", "Görüntü iyileştirme:", height=3)
        row += 1
        self._db_entry(tab, row, "privacy_masking", "Privacy masking:")
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(22, weight=1)

    def _build_video_tab(self, tab: ttk.Frame):
        row = 0
        self._db_multi_combo(tab, row, "codec", "Kodlama / Codec (Çoklu):", ["H.265+", "H.265", "H.264+", "H.264", "MJPEG", "JPEG"])
        row += 1
        self._db_multi_combo(tab, row, "smart_codec", "Smart Codec (Çoklu):", ["Smart H.265+", "Smart H.264+", "Akıllı Akış (Smart Stream)", "Dinamik GOP"])
        row += 1
        self._db_entry(tab, row, "bitrate_control", "Bitrate kontrolü:")
        row += 1
        self._db_entry(tab, row, "video_bitrate", "Video bitrate:")
        row += 1
        self._db_entry(tab, row, "main_stream", "Ana akış:")
        row += 1
        self._db_entry(tab, row, "sub_stream", "Alt akış:")
        row += 1
        self._db_entry(tab, row, "third_stream", "Üçüncü akış:")
        row += 1
        self._db_entry(tab, row, "ethernet_interface", "Ethernet:")
        row += 1
        self._db_text(tab, row, "network_protocols", "Ağ protokolleri:", height=5)
        row += 1
        self._db_combo(tab, row, "onvif", "ONVIF:", ["", "Profile S / G / T", "Profile S / G", "Profile S", "Destekliyor"])
        row += 1
        self._db_entry(tab, row, "standards_api", "CGI / SDK / API:")
        row += 1
        self._db_entry(tab, row, "live_view_users", "Eşzamanlı kullanıcı:")
        row += 1
        self._db_text(tab, row, "cyber_security", "Siber güvenlik:", height=4)
        row += 1
        self._db_combo(tab, row, "internal_storage", "Dahili depolama:", ["", "MicroSD (256 GB)", "MicroSD (512 GB)", "MicroSD (128 GB)", "Dahili SD Kart Slotu"])
        row += 1
        self._db_entry(tab, row, "network_storage", "Ağ depolama:")
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(8, weight=1)
        tab.rowconfigure(12, weight=1)

    def _build_ai_audio_tab(self, tab: ttk.Frame):
        row = 0
        self._db_text(tab, row, "basic_analytics", "Temel analitikler:", height=4)
        row += 1
        self._db_text(tab, row, "ai_analytics", "Derin öğrenme / AI:", height=4)
        row += 1
        self._db_multi_combo(
            tab,
            row,
            "target_classification",
            "İnsan / Araç Ayrımı (Çoklu):",
            ["İnsan Sınıflandırma", "Araç Sınıflandırma", "Bisiklet/Motosiklet Sınıflandırma", "Hedef Filtreleme (AI)"],
        )
        row += 1
        self._db_multi_combo(
            tab,
            row,
            "perimeter_protection",
            "Çevre Güvenliği (Çoklu):",
            ["Sınır İhlali (Line Crossing)", "Bölge İhlali (Intrusion)", "Bölgeye Giriş (Region Entrance)", "Bölgeden Çıkış (Region Exiting)"],
        )
        row += 1
        self._db_multi_combo(
            tab,
            row,
            "object_tracking",
            "Nesne Takibi (Çoklu):",
            ["Otomatik Hedef Takibi (Auto Tracking)", "Terk Edilmiş Eşya Tespiti", "Kayıp Eşya Tespiti", "Hızlı Hareket Algılama"],
        )
        row += 1
        self._db_multi_combo(
            tab,
            row,
            "face_detection",
            "Yüz Algılama / Tanıma (Çoklu):",
            ["Yüz Algılama (Face Detection)", "Yüz Yakalama (Face Capture)", "Yüz Tanıma (Veritabanlı)", "Yüz Öznitelik Analizi"],
        )
        row += 1
        self._db_multi_combo(
            tab,
            row,
            "anpr_lpr",
            "ANPR / LPR Plaka (Çoklu):",
            ["Dahili Plaka Tanıma (ANPR)", "Plaka Okuma ve Veritabanı", "Taşıt Rengi Tespiti", "PTS Yazılım Uyumluluğu"],
        )
        row += 1
        self._db_multi_combo(
            tab,
            row,
            "people_counting",
            "Kişi Sayma (Çoklu):",
            ["Kişi Sayma (Giriş/Çıkış)", "Yoğunluk Analizi", "Kuyruk Yönetimi", "Sosyal Mesafe Uyarısı"],
        )
        row += 1
        self._db_multi_combo(
            tab,
            row,
            "heat_map",
            "Heat Map (Çoklu):",
            ["Isı Haritası (Kişi Yoğunluğu)", "Hareketsizlik Analizi", "Zaman Bazlı Harita"],
        )
        row += 1
        self._db_text(tab, row, "analytics_notes", "Analitik / YZ notu:", height=4)
        row += 1
        self._db_entry(tab, row, "audio_compression", "Ses sıkıştırma:")
        row += 1
        self._db_multi_combo(
            tab,
            row,
            "built_in_audio",
            "Dahili Ses (Çoklu):",
            ["Dahili Mikrofon", "Dahili Hoparlör", "İki Yönlü Ses Konuşma", "Sesli İkaz Uyarısı"],
        )
        row += 1
        self._db_multi_combo(
            tab,
            row,
            "audio_io",
            "Audio I/O (Çoklu):",
            ["1 Kanal Ses Girişi", "1 Kanal Ses Çıkışı", "RCA Giriş/Çıkış", "Line-in / Line-out"],
        )
        row += 1
        self._db_multi_combo(
            tab,
            row,
            "alarm_io",
            "Alarm I/O (Çoklu):",
            ["1 Giriş / 1 Çıkış", "2 Giriş / 2 Çıkış", "4 Giriş / 2 Çıkış", "Röle Çıkışı"],
        )
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        tab.rowconfigure(9, weight=1)

    def _build_physical_tab(self, tab: ttk.Frame):
        row = 0
        self._db_entry(tab, row, "power_supply", "Güç beslemesi:")
        row += 1
        self._db_entry(tab, row, "power_consumption", "Güç tüketimi:")
        row += 1
        self._db_entry(tab, row, "temperature_min_c", "Sıcaklık min (°C):")
        row += 1
        self._db_entry(tab, row, "temperature_max_c", "Sıcaklık max (°C):")
        row += 1
        self._db_entry(tab, row, "humidity", "Nem:")
        row += 1
        self._db_combo(tab, row, "ip_rating", "IP koruma:", ["", "IP67", "IP66", "IP68", "IP65", "IP54", "Dış Ortam"])
        row += 1
        self._db_combo(tab, row, "ik_rating", "IK koruma:", ["", "IK10", "IK08", "IK09", "Darbe Korumasız"])
        row += 1
        self._db_entry(tab, row, "surge_protection", "Aşırı gerilim:")
        row += 1
        self._db_entry(tab, row, "housing_material", "Kasa malzemesi:")
        row += 1
        self._db_entry(tab, row, "dimensions", "Boyutlar:")
        row += 1
        self._db_entry(tab, row, "weight", "Ağırlık:")
        row += 1
        self._db_text(tab, row, "mechanical_drawings", "Boyutsal çizim notu:", height=4)
        row += 1
        self._db_text(tab, row, "certificates", "Sertifikalar:", height=4)
        row += 1
        self._db_text(tab, row, "notes", "Genel not:", height=4)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(11, weight=1)
        tab.rowconfigure(12, weight=1)
        tab.rowconfigure(13, weight=1)

    def _build_raw_tab(self, tab: ttk.Frame):
        row = 0
        self._db_text(tab, row, "brochure_title", "Broşür başlığı:", height=4)
        row += 1
        self._db_entry(tab, row, "source_sheet", "Kaynak sayfa:")
        row += 1
        self._db_entry(tab, row, "raw_sensor", "Ham sensör:")
        row += 1
        self._db_entry(tab, row, "raw_resolution", "Ham çözünürlük:")
        row += 1
        self._db_entry(tab, row, "raw_focal", "Ham lens:")
        row += 1
        self._db_entry(tab, row, "raw_ir", "Ham IR:")
        row += 1
        self._db_text(tab, row, "raw_light_sensitivity", "Ham ışık hassasiyeti:", height=5)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(0, weight=1)
        tab.rowconfigure(6, weight=1)

    def refresh_tree(self, select_name: Optional[str] = None):
        selected = select_name or self.selected_model_name
        for item in self.tree.get_children():
            self.tree.delete(item)

        query = self.search_var.get().strip().casefold()
        rows = []
        for name, model in self.app.camera_library.items():
            if name == "Özel kamera" or not model:
                continue
            descriptor = model_descriptor(name, model)
            if query and query not in descriptor:
                continue
            missing_req, missing_imp = camera_db_missing_fields(model, fallback_name=name)
            if missing_req:
                status = "Kritik eksik"
                tag = "critical"
                order = 0
            elif missing_imp:
                status = "Tamamlanabilir"
                tag = "partial"
                order = 1
            else:
                status = "Tam"
                tag = "complete"
                order = 2
            missing = missing_req + missing_imp
            missing_text = ", ".join(missing[:3])
            if len(missing) > 3:
                missing_text += f" +{len(missing) - 3}"
            rows.append((order, name.casefold(), name, model, status, tag, missing_text))

        for _, __, name, model, status, tag, missing_text in sorted(rows):
            self.tree.insert(
                "",
                tk.END,
                iid=name,
                text=name,
                values=(model.get("stock_code", ""), status, missing_text),
                tags=(tag,),
            )
        if selected in self.tree.get_children():
            self.tree.selection_set(selected)
            self.tree.see(selected)

    def _on_selected(self, event=None):
        selected = self.tree.selection()
        if not selected:
            return
        self.load_model_to_form(selected[0])

    def load_model_to_form(self, model_name: str):
        model = self.app.camera_library.get(model_name, {})
        if not model:
            return
        self.selected_model_name = model_name
        self._set_form_value("model_name", model.get("model_name", model_name) or model_name)
        for key, entry in self.entries.items():
            if key == "model_name":
                continue
            self._set_form_value(key, model.get(key, ""))
        for key, text_widget in self.texts.items():
            text_widget.delete("1.0", tk.END)
            text_widget.insert("1.0", str(model.get(key, "")))

        missing_req, missing_imp = camera_db_missing_fields(model, fallback_name=model_name)
        if missing_req:
            message = "Kritik eksik: " + ", ".join(missing_req)
        elif missing_imp:
            message = "Tamamlanabilir: " + ", ".join(missing_imp)
        else:
            message = "Kayıt tam görünüyor."
        self.status_var.set(message)

    def _set_form_value(self, key: str, value: Any):
        widget = self.entries.get(key)
        if widget is None:
            return
        widget.delete(0, tk.END)
        widget.insert(0, "" if value is None else str(value))

    def clear_form(self):
        self.selected_model_name = ""
        for entry in self.entries.values():
            entry.delete(0, tk.END)
        for text_widget in self.texts.values():
            text_widget.delete("1.0", tk.END)
        self.tree.selection_remove(self.tree.selection())
        self.status_var.set("Yeni kayıt için model adı girin.")

    def _new_from_active(self):
        if not self.app._commit_form_to_selected(show_errors=True):
            return
        camera = self.app.cameras[self.app.selected_camera_index]
        self.clear_form()
        self._set_form_value("model_name", camera.model_name if camera.model_name != "Özel kamera" else camera.name)
        self._set_form_value("sensor_name", camera.sensor_name)
        self._set_form_value("resolution_name", camera.resolution_name)
        self._set_form_value("focal_min_mm", f"{camera.focal_min_mm:g}")
        self._set_form_value("focal_max_mm", f"{camera.focal_max_mm:g}")
        self._set_form_value("ir_range_m", f"{camera.ir_range_m:g}")
        self._set_form_value("min_lux", f"{camera.min_lux:g}")
        self._set_form_value("source_sheet", "Manuel veri girişi")
        self.status_var.set("Aktif kamera değerleri forma aktarıldı; kaydetmeden kütüphaneye eklenmez.")

    def save_model(self):
        try:
            model_name, model = self._form_to_model()
            old_name = self.selected_model_name
            if old_name != model_name and model_name in self.app.camera_library:
                overwrite = messagebox.askyesno(
                    "Kamera veritabanı",
                    f"{model_name} zaten var. Bu kaydın üzerine yazılsın mı?",
                    parent=self.window,
                )
                if not overwrite:
                    return
            data = read_camera_library_json()
            if old_name and old_name != model_name and old_name in data:
                del data[old_name]
            data[model_name] = model
            write_camera_library_json(data)
            self.app.camera_library = load_camera_library()

            for camera in self.app.cameras:
                if camera.model_name == old_name:
                    camera.model_name = model_name
            self.app._refresh_camera_model_values(selected=model_name)
            self.refresh_tree(select_name=model_name)
            self.load_model_to_form(model_name)
            self.app.status_var.set(f"Kamera verisi kaydedildi: {model_name}")
            messagebox.showinfo("Kamera veritabanı", "Kayıt kaydedildi ve kütüphane güncellendi.", parent=self.window)
        except ValueError as exc:
            messagebox.showerror("Kamera veritabanı", str(exc), parent=self.window)
        except Exception as exc:
            messagebox.showerror("Kamera veritabanı", f"Kayıt kaydedilemedi:\n{exc}", parent=self.window)

    def _form_to_model(self) -> Tuple[str, Dict[str, Any]]:
        model_name = self.entries["model_name"].get().strip()
        if not model_name:
            raise ValueError("Model adı boş olamaz.")
        old_name = self.selected_model_name
        source_name = old_name or model_name
        model = dict(self.app.camera_library.get(source_name, {}) or {})
        model["model_name"] = model_name
        numeric_keys = {
            "max_fps",
            "focal_min_mm",
            "focal_max_mm",
            "ir_range_m",
            "min_lux",
            "horizontal_fov_deg",
            "vertical_fov_deg",
            "diagonal_fov_deg",
            "dori_detect_m",
            "dori_observe_m",
            "dori_recognize_m",
            "dori_identify_m",
            "sn_ratio_db",
            "white_light_range_m",
            "illuminator_wavelength_nm",
            "live_view_users",
            "temperature_min_c",
            "temperature_max_c",
        }

        for key, widget in self.entries.items():
            if key == "model_name":
                continue
            raw = widget.get().strip()
            if not raw:
                model.pop(key, None)
                continue
            if key in numeric_keys:
                try:
                    value = float(raw.replace(",", "."))
                except ValueError as exc:
                    raise ValueError(f"{key} sayısal olmalı.") from exc
                model[key] = int(value) if value.is_integer() else value
            else:
                model[key] = raw

        for key, text_widget in self.texts.items():
            raw = text_widget.get("1.0", tk.END).strip()
            if not raw:
                model.pop(key, None)
            else:
                model[key] = raw
        return model_name, model

    def _apply_to_active(self):
        if not self.selected_model_name or self.selected_model_name not in self.app.camera_library:
            messagebox.showinfo("Kamera veritabanı", "Önce bir kamera kaydı seçin.", parent=self.window)
            return
        self.app.combo_camera_model.set(self.selected_model_name)
        self.app.apply_camera_model()

    def delete_model(self):
        if not self.selected_model_name:
            messagebox.showinfo("Kamera veritabanı", "Silmek için bir kayıt seçin.", parent=self.window)
            return
        name = self.selected_model_name
        data = read_camera_library_json()
        if name not in data and name in self.app.camera_library:
            messagebox.showwarning(
                "Kamera veritabanı",
                "Bu hazır şablon modeldir ve JSON veritabanında saklanmamaktadır; doğrudan silinemez.",
                parent=self.window,
            )
            return
        confirm = messagebox.askyesno(
            "Kamera veritabanı",
            f"{name} model kaydı JSON veritabanından silinsin mi?",
            parent=self.window,
        )
        if not confirm:
            return
        if name in data:
            del data[name]
            write_camera_library_json(data)
        self.app.camera_library = load_camera_library()
        self.app._refresh_camera_model_values()
        self.clear_form()
        self.refresh_tree()
        messagebox.showinfo("Kamera veritabanı", f"{name} kaydı silindi.", parent=self.window)
