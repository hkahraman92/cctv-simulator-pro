import base64
import csv
import json
import os
import re
import urllib.request
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Dict, Any, Optional

from ..compliance import build_compliance_prompt, gemini_response_text, extract_json_object, rule_based_compliance, run_gemini_in_thread
from ..exporters import export_compliance_excel
from ..theme import is_themed, COLORS, StyledButton, fit_and_center_window, set_window_icon


class SpecAssistantWindow:
    def __init__(self, parent_app: Any):
        self.app = parent_app
        self.window = tk.Toplevel(self.app.root)
        self.window.title("Şartnameye Göre Kamera Seçimi ve Compliance Matrix")
        fit_and_center_window(self.window, default_w=1220, default_h=760, min_w=850, min_h=550, maximize=False)
        set_window_icon(self.window)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.loaded_file_path = ""
        self.loaded_file_mime = ""
        self.loaded_file_bytes = None
        self.last_compliance_result = None

        from ..errors import guarded_build
        self.build_ok = guarded_build(self.window, self._build_ui,
                                      "Şartname Asistanı")
        if not self.build_ok:
            return

    def close(self):
        self.app.spec_window = None
        self.window.destroy()

    def lift(self):
        self.window.lift()

    def _set_sash_pos(self, paned: ttk.PanedWindow, pos: int):
        try:
            if self.window.winfo_exists():
                paned.sashpos(0, pos)
        except Exception:
            pass

    def _build_ui(self):
        paned = ttk.PanedWindow(self.window, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        left = ttk.Frame(paned, padding=6, width=380)
        right = ttk.Frame(paned, padding=6)
        paned.add(left, weight=1)
        paned.add(right, weight=2)

        self.window.after(100, lambda: self._set_sash_pos(paned, 380))

        text_frame = ttk.LabelFrame(left, text=" Gelen Şartname / Teknik İsterler ", padding=6)
        text_frame.pack(fill=tk.BOTH, expand=True)
        self.spec_text = tk.Text(text_frame, wrap=tk.WORD, height=22, font=("Segoe UI", 9))
        self.spec_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        spec_scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.spec_text.yview)
        spec_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.spec_text.configure(yscrollcommand=spec_scroll.set)

        control_frame = ttk.LabelFrame(left, text=" Analiz ", padding=6)
        control_frame.pack(fill=tk.X, pady=(8, 0))
        StyledButton(control_frame, text="Şartname Dosyası Yükle", command=self.load_spec_file, bootstyle="secondary").grid(
            row=0, column=0, columnspan=2, sticky=tk.EW, pady=(0, 4)
        )
        ttk.Label(control_frame, text="Gemini model:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.spec_model_entry = ttk.Combobox(
            control_frame,
            values=["gemini-3.6-flash", "gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash-lite"],
            state="normal",
        )
        self.spec_model_entry.set("gemini-3.6-flash")
        self.spec_model_entry.grid(row=1, column=1, sticky=tk.EW, pady=2)
        ttk.Label(control_frame, text="API key:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.spec_api_key_entry = ttk.Entry(control_frame, show="*")
        self.spec_api_key_entry.grid(row=2, column=1, sticky=tk.EW, pady=2)
        StyledButton(control_frame, text="Gemini ile Analiz Et", command=self.analyze_spec_with_gemini, bootstyle="warning").grid(
            row=3, column=0, columnspan=2, sticky=tk.EW, pady=(6, 4)
        )
        StyledButton(control_frame, text="Kurallı Analiz Et", command=self.analyze_spec_rule_based, bootstyle="primary").grid(
            row=4, column=0, columnspan=2, sticky=tk.EW, pady=(0, 4)
        )
        StyledButton(control_frame, text="Matrix CSV Kaydet", command=self.export_compliance_csv, bootstyle="info-outline").grid(
            row=5, column=0, columnspan=2, sticky=tk.EW, pady=(0, 4)
        )
        StyledButton(control_frame, text="Matrix Excel (.xlsx) Kaydet", command=self.export_compliance_excel, bootstyle="success").grid(
            row=6, column=0, columnspan=2, sticky=tk.EW
        )
        control_frame.columnconfigure(1, weight=1)

        matrix_frame = ttk.LabelFrame(right, text=" Compliance Matrix ", padding=6)
        matrix_frame.pack(fill=tk.BOTH, expand=True)

        filter_bar = ttk.Frame(matrix_frame)
        filter_bar.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(filter_bar, text="Kamera Filtresi:").pack(side=tk.LEFT, padx=(0, 6))
        self._filter_all_label = "── Tüm Kameralar ──"
        self.camera_filter_var = tk.StringVar(value=self._filter_all_label)
        self.camera_filter_combo = ttk.Combobox(
            filter_bar,
            textvariable=self.camera_filter_var,
            state="readonly",
            width=60,
        )
        self.camera_filter_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.camera_filter_combo.bind("<<ComboboxSelected>>", self._on_camera_filter_changed)

        tree_container = ttk.Frame(matrix_frame)
        tree_container.pack(fill=tk.BOTH, expand=True)
        columns = ("profile", "requirement", "camera", "status", "evidence")
        self.compliance_tree = ttk.Treeview(tree_container, columns=columns, show="headings", height=18)
        headings = {
            "profile": "Profil",
            "requirement": "İster",
            "camera": "Kamera / Model",
            "status": "Durum",
            "evidence": "Kanıt / Not",
        }
        widths = {"profile": 150, "requirement": 220, "camera": 250, "status": 85, "evidence": 350}
        for column in columns:
            self.compliance_tree.heading(column, text=headings[column])
            self.compliance_tree.column(column, width=widths[column], anchor=tk.W)
        self.compliance_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        matrix_scroll = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.compliance_tree.yview)
        matrix_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.compliance_tree.configure(yscrollcommand=matrix_scroll.set)
        self.compliance_tree.tag_configure("Uyumlu", background=COLORS["treeview_uyumlu"])
        self.compliance_tree.tag_configure("Kısmi", background=COLORS["treeview_kismi"])
        self.compliance_tree.tag_configure("Uyumsuz", background=COLORS["treeview_uyumsuz"])
        self.compliance_tree.tag_configure("Bulunamadı", background=COLORS["treeview_bulunamadi"])

        summary_frame = ttk.LabelFrame(right, text=" Öneri ve Özet ", padding=6)
        summary_frame.pack(fill=tk.X, pady=(8, 0))
        self.compliance_summary_text = tk.Text(
            summary_frame,
            height=7,
            wrap=tk.WORD,
            font=("Segoe UI", 9),
            bg=COLORS["text_bg"],
            fg=COLORS["text_fg"],
            relief="flat",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#DEE2E6",
        )
        self.compliance_summary_text.pack(fill=tk.X)
        self.compliance_summary_text.configure(state=tk.DISABLED)

    def load_spec_file(self):
        path = filedialog.askopenfilename(
            parent=self.window,
            title="Şartname dosyası yükle",
            filetypes=[
                ("Şartname dosyaları", "*.txt *.md *.csv *.docx *.pdf"),
                ("Metin dosyaları", "*.txt *.md *.csv"),
                ("Word", "*.docx"),
                ("PDF", "*.pdf"),
                ("Tüm dosyalar", "*.*"),
            ],
        )
        if not path:
            return
        data = Path(path).read_bytes()
        suffix = Path(path).suffix.lower()
        self.loaded_file_path = path
        self.loaded_file_bytes = data
        self.loaded_file_mime = self._mime_for_spec_path(path)
        if suffix == ".docx":
            text = self._extract_docx_text(path)
        elif suffix == ".pdf":
            text = (
                f"[PDF yüklendi: {Path(path).name}]\n\n"
                "PDF dosyaları düz metin gibi gösterilmez. Gemini ile analiz edersen dosya doğrudan modele gönderilir. "
                "Kurallı analiz için PDF metnini buraya yapıştırman gerekir."
            )
        else:
            text = self._decode_text_bytes(data)
        if not text:
            messagebox.showerror("Şartname", "Dosya metni çıkarılamadı.", parent=self.window)
            return
        self.spec_text.delete("1.0", tk.END)
        self.spec_text.insert("1.0", text)

    def _decode_text_bytes(self, data: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "cp1254", "latin-1"):
            try:
                text = data.decode(encoding)
                if self._looks_like_binary_text(text):
                    continue
                return text
            except UnicodeDecodeError:
                continue
        return ""

    def _looks_like_binary_text(self, text: str) -> bool:
        if not text:
            return False
        sample = text[:2000]
        control_count = sum(1 for char in sample if ord(char) < 32 and char not in "\r\n\t")
        replacement_count = sample.count("\ufffd")
        return control_count > 20 or replacement_count > 10

    def _mime_for_spec_path(self, path: str) -> str:
        suffix = Path(path).suffix.lower()
        if suffix == ".pdf":
            return "application/pdf"
        if suffix == ".docx":
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if suffix == ".csv":
            return "text/csv"
        return "text/plain"

    def _extract_docx_text(self, path: str) -> str:
        try:
            with zipfile.ZipFile(path) as archive:
                xml = archive.read("word/document.xml").decode("utf-8")
        except Exception as exc:
            messagebox.showerror("DOCX", f"DOCX metni çıkarılamadı:\n{exc}", parent=self.window)
            return ""
        paragraphs = re.findall(r"<w:p[\s\S]*?</w:p>", xml)
        lines = []
        for paragraph in paragraphs:
            texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", paragraph)
            if texts:
                line = "".join(self._xml_unescape(text) for text in texts).strip()
                if line:
                    lines.append(line)
        return "\n".join(lines)

    def _xml_unescape(self, text: str) -> str:
        return (
            text.replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&amp;", "&")
            .replace("&quot;", '"')
            .replace("&apos;", "'")
        )

    def _get_spec_text(self) -> str:
        text = self.spec_text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showinfo("Şartname", "Önce şartname / teknik ister metni girin.", parent=self.window)
            return ""
        return text

    def analyze_spec_with_gemini(self):
        spec_text = self._get_spec_text()
        if not spec_text:
            return
        api_key = self.spec_api_key_entry.get().strip() or os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            messagebox.showinfo("Gemini API", "API key bulunamadı. Kurallı analiz çalıştırılıyor.", parent=self.window)
            self.analyze_spec_rule_based()
            return
        model = self.spec_model_entry.get().strip() or "gemini-2.0-flash"
        prompt = build_compliance_prompt(spec_text, self.app.camera_library)
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        parts = [{"text": prompt}]
        if self.loaded_file_bytes and self.loaded_file_mime == "application/pdf":
            parts.append(
                {
                    "inline_data": {
                        "mime_type": self.loaded_file_mime,
                        "data": base64.b64encode(self.loaded_file_bytes).decode("ascii"),
                    }
                }
            )
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.1},
        }
        api_request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )
        run_gemini_in_thread(
            self.window,
            api_request,
            on_success=self.apply_compliance_result,
            on_failure=self.analyze_spec_rule_based
        )

    def analyze_spec_rule_based(self):
        spec_text = self._get_spec_text()
        if not spec_text:
            return
        if self.loaded_file_mime == "application/pdf" and spec_text.startswith("[PDF yüklendi:"):
            messagebox.showinfo(
                "Kurallı analiz",
                "PDF dosyasından metin çıkarılamadı. PDF için Gemini ile analiz et veya şartname metnini kutuya yapıştır.",
                parent=self.window,
            )
            return
        result = rule_based_compliance(spec_text, self.app.camera_library)
        self.apply_compliance_result(result)

    def apply_compliance_result(self, result: Dict[str, Any]):
        self.last_compliance_result = result

        # Build unique camera list for filter combobox
        matrix = result.get("matrix", [])
        camera_names = list(dict.fromkeys(row.get("camera_model", "") for row in matrix))
        self.camera_filter_combo["values"] = [self._filter_all_label] + camera_names
        self.camera_filter_var.set(self._filter_all_label)

        self._populate_matrix(matrix)
        self._update_summary(result)

    def _on_camera_filter_changed(self, _event=None):
        if not self.last_compliance_result:
            return
        selected = self.camera_filter_var.get()
        matrix = self.last_compliance_result.get("matrix", [])
        if selected and selected != self._filter_all_label:
            matrix = [row for row in matrix if row.get("camera_model") == selected]
        self._populate_matrix(matrix)
        self._update_summary(self.last_compliance_result, camera_filter=selected)

    def _populate_matrix(self, matrix):
        for item in self.compliance_tree.get_children():
            self.compliance_tree.delete(item)
        for row in matrix:
            status = row.get("status", "Kısmi")
            self.compliance_tree.insert(
                "",
                tk.END,
                values=(
                    row.get("profile_name", ""),
                    row.get("requirement", ""),
                    row.get("camera_model", ""),
                    status,
                    row.get("evidence", ""),
                ),
                tags=(status,),
            )

    def _update_summary(self, result, camera_filter=None):
        self.compliance_summary_text.configure(state=tk.NORMAL)
        self.compliance_summary_text.delete("1.0", tk.END)

        scores = result.get("camera_scores", [])
        if camera_filter and camera_filter != self._filter_all_label:
            scores = [s for s in scores if s.get("camera_model") == camera_filter]

        lines = [f"ÖNERİ: {result.get('recommendation', '')}", "\nSKOR ÖZETİ:"]
        for score_item in scores[:12]:
            lines.append(
                f"- {score_item.get('profile_name', '')}: {score_item.get('camera_model', '')} "
                f"-> Skor: {score_item.get('score', 0)} ({score_item.get('verdict', '')}) | {score_item.get('notes', '')}"
            )
        if not scores:
            lines.append("Seçili kamera için skor bilgisi bulunamadı.")
        self.compliance_summary_text.insert("1.0", "\n".join(lines))
        self.compliance_summary_text.configure(state=tk.DISABLED)

    def export_compliance_csv(self):
        if not self.last_compliance_result or not self.last_compliance_result.get("matrix"):
            messagebox.showinfo("Matrix CSV", "Dışa aktarılacak analiz sonucu yok.", parent=self.window)
            return
        path = filedialog.asksaveasfilename(
            parent=self.window,
            title="Compliance matrix CSV kaydedin",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Tüm dosyalar", "*.*")],
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow(["Profil", "İster", "Kamera / Model", "Durum", "Kanıt / Not"])
            for row in self.last_compliance_result.get("matrix", []):
                writer.writerow(
                    [
                        row.get("profile_name", ""),
                        row.get("requirement", ""),
                        row.get("camera_model", ""),
                        row.get("status", ""),
                        row.get("evidence", ""),
                    ]
                )
        messagebox.showinfo("Matrix CSV", f"CSV kaydedildi:\n{Path(path).name}", parent=self.window)

    def export_compliance_excel(self):
        if not self.last_compliance_result or not self.last_compliance_result.get("matrix"):
            messagebox.showinfo("Matrix Excel", "Dışa aktarılacak analiz sonucu yok.", parent=self.window)
            return
        path = filedialog.asksaveasfilename(
            parent=self.window,
            title="Compliance matrix Excel kaydedin",
            defaultextension=".xlsx",
            filetypes=[("Excel Çalışma Kitabı", "*.xlsx"), ("Tüm dosyalar", "*.*")],
        )
        if not path:
            return
        try:
            export_compliance_excel(path, self.last_compliance_result)
            messagebox.showinfo("Matrix Excel", f"Excel kaydedildi:\n{Path(path).name}", parent=self.window)
        except Exception as exc:
            messagebox.showerror("Matrix Excel", str(exc), parent=self.window)
