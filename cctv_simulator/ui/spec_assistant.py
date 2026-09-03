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
        self.use_ollama_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(control_frame, text="Yerel model (Ollama) kullan",
                        variable=self.use_ollama_var).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))
        StyledButton(control_frame, text="Yapay Zeka ile Analiz Et", command=self.analyze_spec_with_gemini, bootstyle="warning").grid(
            row=4, column=0, columnspan=2, sticky=tk.EW, pady=(4, 4)
        )
        StyledButton(control_frame, text="Kurallı + Fizik Analizi", command=self.analyze_spec_rule_based, bootstyle="primary").grid(
            row=5, column=0, columnspan=2, sticky=tk.EW, pady=(0, 4)
        )

        ttk.Separator(control_frame).grid(row=6, column=0, columnspan=2, sticky=tk.EW, pady=4)
        ttk.Label(control_frame, text="İster şablonu:").grid(row=7, column=0, sticky=tk.W, pady=2)
        self.template_combo = ttk.Combobox(control_frame, state="readonly", values=[])
        self.template_combo.grid(row=7, column=1, sticky=tk.EW, pady=2)
        _tpl_row = ttk.Frame(control_frame)
        _tpl_row.grid(row=8, column=0, columnspan=2, sticky=tk.EW, pady=(0, 4))
        StyledButton(_tpl_row, text="Yükle", command=self.load_requirement_template, bootstyle="secondary-outline").pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        StyledButton(_tpl_row, text="Kaydet", command=self.save_requirement_template, bootstyle="secondary-outline").pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        StyledButton(_tpl_row, text="Sil", command=self.delete_requirement_template, bootstyle="danger-outline").pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))
        self._refresh_templates()

        ttk.Separator(control_frame).grid(row=9, column=0, columnspan=2, sticky=tk.EW, pady=4)
        StyledButton(control_frame, text="Matrix CSV Kaydet", command=self.export_compliance_csv, bootstyle="info-outline").grid(
            row=10, column=0, columnspan=2, sticky=tk.EW, pady=(0, 4)
        )
        StyledButton(control_frame, text="Matrix Excel (.xlsx) Kaydet", command=self.export_compliance_excel, bootstyle="success").grid(
            row=11, column=0, columnspan=2, sticky=tk.EW, pady=(0, 4)
        )
        StyledButton(control_frame, text="📄 EN 62676-4 Uygunluk Beyanı", command=self.export_compliance_statement, bootstyle="primary-outline").grid(
            row=12, column=0, columnspan=2, sticky=tk.EW
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
        self.compliance_tree.bind("<Double-1>", self._on_row_override)
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
            from ..spec_pdf import extract_text as _pdf_text
            text = _pdf_text(path)
            if not text:
                text = (
                    f"[PDF yüklendi: {Path(path).name}]\n\n"
                    "PDF metni çıkarılamadı (pypdf kurulu değil veya taranmış PDF). "
                    "Gemini ile analiz edersen dosya doğrudan modele gönderilir. "
                    "Kurallı analiz için metni buraya yapıştırın."
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

        if self.use_ollama_var.get():
            from ..compliance import analyze_with_ollama, ollama_available
            if not ollama_available():
                messagebox.showwarning("Ollama", "Yerel model (localhost:11434) yanıt vermiyor. Gemini/kurallı analize dönülüyor.", parent=self.window)
            else:
                self.compliance_summary_text.configure(state=tk.NORMAL)
                self.compliance_summary_text.delete("1.0", tk.END)
                self.compliance_summary_text.insert("1.0", "Yerel model analiz ediyor…")
                self.compliance_summary_text.configure(state=tk.DISABLED)
                self.window.update_idletasks()
                local_model = self.spec_model_entry.get().strip() if "gemini" not in self.spec_model_entry.get().lower() else "llama3.1"
                res = analyze_with_ollama(spec_text, self.app.camera_library, model=local_model)
                if res and res.get("matrix"):
                    self.apply_compliance_result(res)
                    return
                messagebox.showinfo("Ollama", "Yerel model geçerli JSON döndürmedi. Kurallı + fizik analizine dönülüyor.", parent=self.window)
                self.analyze_spec_rule_based()
                return

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
        self._row_by_iid = {}
        for row in matrix:
            eff_status = row.get("user_status") or row.get("status", "Kısmi")
            kind = row.get("evidence_kind", "")
            clause = row.get("standard_clause", "")
            conf = row.get("confidence")
            ev = row.get("evidence", "")
            prefix = f"[{clause}] " if clause and clause != "—" else ""
            suffix = f"  ·({kind})" if kind else ""
            if row.get("user_status"):
                suffix += f"  ✎ {row.get('user_note', 'elle geçersiz kılındı')}"
            status_disp = eff_status + (f" ·%{conf * 100:.0f}" if isinstance(conf, (int, float)) else "")
            iid = self.compliance_tree.insert(
                "",
                tk.END,
                values=(
                    row.get("profile_name", ""),
                    row.get("requirement", ""),
                    row.get("camera_model", ""),
                    status_disp,
                    prefix + ev + suffix,
                ),
                tags=(eff_status,),
            )
            self._row_by_iid[iid] = row

    def _on_row_override(self, _event=None):
        iid = self.compliance_tree.focus()
        row = getattr(self, "_row_by_iid", {}).get(iid)
        if row is None:
            return
        dlg = tk.Toplevel(self.window)
        dlg.title("İster kararını geçersiz kıl")
        dlg.transient(self.window)
        dlg.grab_set()
        ttk.Label(dlg, text=row.get("requirement", "")[:80], font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(10, 2))
        ttk.Label(dlg, text=f"Motor kararı: {row.get('status', '')} — {row.get('evidence', '')[:120]}",
                  wraplength=420, foreground="#666").pack(anchor="w", padx=10)
        vv = tk.StringVar(value=row.get("user_status") or row.get("status", "Kısmi"))
        vr = ttk.Frame(dlg)
        vr.pack(anchor="w", padx=10, pady=6)
        for opt in ("Uyumlu", "Kısmi", "Uyumsuz", "Bulunamadı"):
            ttk.Radiobutton(vr, text=opt, value=opt, variable=vv).pack(side=tk.LEFT, padx=4)
        nv = tk.StringVar(value=row.get("user_note", ""))
        ttk.Label(dlg, text="Gerekçe / not:").pack(anchor="w", padx=10)
        ttk.Entry(dlg, textvariable=nv, width=60).pack(fill=tk.X, padx=10, pady=(0, 8))

        def apply_override():
            if vv.get() == row.get("status") and not nv.get().strip():
                row.pop("user_status", None)
                row.pop("user_note", None)
            else:
                row["user_status"] = vv.get()
                row["user_note"] = nv.get().strip() or "elle geçersiz kılındı"
            dlg.destroy()
            self._recompute_scores()
            self._on_camera_filter_changed()

        def clear_override():
            row.pop("user_status", None)
            row.pop("user_note", None)
            dlg.destroy()
            self._recompute_scores()
            self._on_camera_filter_changed()

        br = ttk.Frame(dlg)
        br.pack(fill=tk.X, padx=10, pady=(0, 10))
        StyledButton(br, text="Uygula", command=apply_override, bootstyle="success").pack(side=tk.RIGHT, padx=4)
        StyledButton(br, text="Geçersiz kılmayı kaldır", command=clear_override, bootstyle="secondary-outline").pack(side=tk.RIGHT, padx=4)

    def _recompute_scores(self):
        """Re-derive camera_scores from the matrix, honouring manual overrides."""
        res = self.last_compliance_result
        if not res:
            return
        w_by_id = {r.get("id"): max(float(r.get("weight", 1)), 0.1) for r in res.get("requirements", [])}
        agg: Dict = {}
        for m in res.get("matrix", []):
            key = (m.get("profile_name", ""), m.get("camera_model", ""))
            w = w_by_id.get(m.get("requirement_id"), 1.0)
            st = m.get("user_status") or m.get("status", "Kısmi")
            d = agg.setdefault(key, {"passed": 0.0, "total": 0.0, "blocker": False})
            d["total"] += w
            if st == "Uyumlu":
                d["passed"] += w
            elif st == "Kısmi":
                d["passed"] += w * 0.5
            elif st == "Bulunamadı":
                d["passed"] += w * 0.15
            if st == "Uyumsuz" and (m.get("requirement_id", "")[-2:-1] == "D" or "type" in str(m.get("requirement", "")).lower()):
                d["blocker"] = True
        rows = []
        for (pname, model), d in agg.items():
            score = round(d["passed"] / max(d["total"], 1) * 100)
            if d["blocker"]:
                score = min(score, 40)
                verdict = "Uyumsuz"
            else:
                verdict = "Uyumlu" if score >= 82 else "Kısmi" if score >= 50 else "Uyumsuz"
            rows.append({"profile_name": pname, "camera_model": model, "score": score,
                         "verdict": verdict, "notes": f"{d['passed']:g}/{d['total']:g} ağırlık"})
        rows.sort(key=lambda r: r["score"], reverse=True)
        res["camera_scores"] = rows

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

        clar = result.get("clarification_questions", [])
        if clar:
            lines.append(f"\nAÇIKLAMA TALEPLERİ ({len(clar)} — nicel olmayan ister):")
            for q in clar[:8]:
                lines.append(f"- {q}")

        self.compliance_summary_text.insert("1.0", "\n".join(lines))
        self.compliance_summary_text.configure(state=tk.DISABLED)

    # ── requirement templates ──────────────────────────────────────────────
    def _refresh_templates(self):
        from .. import requirement_library as RL
        names = RL.list_templates()
        self.template_combo["values"] = names
        if names and not self.template_combo.get():
            self.template_combo.set(names[0])

    def save_requirement_template(self):
        from tkinter import simpledialog

        from .. import requirement_library as RL
        if not self.last_compliance_result or not self.last_compliance_result.get("requirements"):
            messagebox.showinfo("Şablon", "Önce bir analiz çalıştırın (kaydedilecek ister yok).", parent=self.window)
            return
        name = simpledialog.askstring("İster şablonu", "Şablon adı:", parent=self.window)
        if not name:
            return
        ok = RL.save_template(name, self.last_compliance_result["requirements"],
                              {"kaynak": Path(self.loaded_file_path).name if self.loaded_file_path else "elle"})
        if ok:
            self._refresh_templates()
            self.template_combo.set(name)
            messagebox.showinfo("Şablon", f"“{name}” kaydedildi.", parent=self.window)
        else:
            messagebox.showerror("Şablon", "Şablon kaydedilemedi.", parent=self.window)

    def load_requirement_template(self):
        from .. import requirement_library as RL
        from ..compliance import evaluate_dori_requirement, evaluate_rule_requirement
        name = self.template_combo.get().strip()
        tpl = RL.load_template(name) if name else None
        if not tpl:
            messagebox.showinfo("Şablon", "Yüklenecek şablon seçin.", parent=self.window)
            return
        requirements = tpl.get("requirements", [])
        if not requirements:
            messagebox.showinfo("Şablon", "Şablonda ister yok.", parent=self.window)
            return
        # re-evaluate the template's requirements against the current camera library
        items = [(n, m) for n, m in self.app.camera_library.items() if m]
        real = [(n, m) for n, m in items if m.get("stock_code")] or items
        matrix, rows = [], []
        for model_name, model in real:
            passed = total = 0.0
            for req in requirements:
                w = max(float(req.get("weight", 1)), 0.1)
                total += w
                if req.get("category") == "dori":
                    status, ev = evaluate_dori_requirement(model_name, model, req)
                    kind = "optik motor"
                else:
                    status, ev = evaluate_rule_requirement(model_name, model, req)
                    kind = "broşür"
                if status == "Uyumlu":
                    passed += w
                elif status == "Kısmi":
                    passed += w * 0.5
                matrix.append({
                    "profile_name": req.get("profile_name", tpl.get("name", "Şablon")),
                    "requirement_id": req.get("id", ""), "requirement": req.get("requirement", ""),
                    "camera_model": model_name, "status": status, "evidence": ev,
                    "evidence_kind": kind, "standard_clause": req.get("standard_clause", ""),
                    "confidence": req.get("confidence", 0.7), "spec_quote": req.get("spec_quote", ""),
                })
            score = round(passed / max(total, 1) * 100)
            rows.append({"profile_name": tpl.get("name", "Şablon"), "camera_model": model_name,
                         "score": score, "verdict": "Uyumlu" if score >= 82 else "Kısmi" if score >= 50 else "Uyumsuz",
                         "notes": f"{passed:g}/{total:g} ağırlık"})
        rows.sort(key=lambda r: r["score"], reverse=True)
        result = {
            "profiles": [{"id": "T1", "name": tpl.get("name", "Şablon"), "description": ""}],
            "requirements": requirements, "matrix": matrix, "camera_scores": rows,
            "recommendation": f"Şablon “{tpl.get('name', name)}” — en yüksek: "
                              + (f"{rows[0]['camera_model']} ({rows[0]['score']}/100)" if rows else "model yok"),
            "ambiguities": [], "clarification_questions": [],
        }
        self.apply_compliance_result(result)

    def delete_requirement_template(self):
        from .. import requirement_library as RL
        name = self.template_combo.get().strip()
        if not name:
            return
        if not messagebox.askyesno("Şablon sil", f"“{name}” silinsin mi?", parent=self.window):
            return
        RL.delete_template(name)
        self.template_combo.set("")
        self._refresh_templates()

    def export_compliance_statement(self):
        from ..compliance_report import build_statement
        if not self.last_compliance_result or not self.last_compliance_result.get("matrix"):
            messagebox.showinfo("Uygunluk Beyanı", "Önce bir analiz çalıştırın.", parent=self.window)
            return
        path = filedialog.asksaveasfilename(
            parent=self.window, title="EN 62676-4 Uygunluk Beyanı kaydet",
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Metin", "*.txt"), ("Tüm dosyalar", "*.*")],
        )
        if not path:
            return
        proj = ""
        try:
            proj = getattr(self.app, "_project_name", "") or ""
        except Exception:
            proj = ""
        md = build_statement(self.last_compliance_result, project_name=proj)
        try:
            Path(path).write_text(md, encoding="utf-8")
            messagebox.showinfo("Uygunluk Beyanı", f"Kaydedildi:\n{path}", parent=self.window)
        except OSError as exc:
            messagebox.showerror("Uygunluk Beyanı", f"Kaydedilemedi:\n{exc}", parent=self.window)

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
            writer.writerow(["Profil", "İster", "Standart", "Kamera / Model", "Durum",
                             "Kanıt türü", "Kanıt / Not", "Güven", "Şartname alıntısı", "Elle geçersiz kılma"])
            for row in self.last_compliance_result.get("matrix", []):
                conf = row.get("confidence")
                writer.writerow(
                    [
                        row.get("profile_name", ""),
                        row.get("requirement", ""),
                        row.get("standard_clause", ""),
                        row.get("camera_model", ""),
                        row.get("user_status") or row.get("status", ""),
                        row.get("evidence_kind", ""),
                        row.get("evidence", ""),
                        f"%{conf * 100:.0f}" if isinstance(conf, (int, float)) else "",
                        row.get("spec_quote", ""),
                        row.get("user_note", "") if row.get("user_status") else "",
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
