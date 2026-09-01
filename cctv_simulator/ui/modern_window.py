"""
Modern optics workbench UI for the CCTV simulator.

A dark, CAD-style single-camera workbench: parameter rail on the left, a live
top-down FOV / DORI plan on the right, live status and EN 62676-4 compliance
cards along the bottom.

All optics come from cctv_simulator.calculations - this module draws, it does
not re-derive any physics.

Requires: customtkinter >= 5.2, Pillow (for true alpha-blended DORI zones;
degrades to stipple shading without it).
"""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont
from typing import Dict, List, Optional, Tuple

import customtkinter as ctk

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:  # pragma: no cover - alpha compositing is optional
    Image = ImageDraw = ImageTk = None

from ..config import SENSOR_DIMS_MM, RESOLUTIONS
from ..database import load_camera_library
from ..models import CameraConfig, OpticResult
from ..calculations import calculate_for_camera, ppm_at_distance


# ─────────────────────────────────────────────────────────────────────────────
# Design tokens
# ─────────────────────────────────────────────────────────────────────────────

BG        = "#1E1E2E"   # application ground
PANEL     = "#2A2A3C"   # panels, cards
PANEL_HI  = "#33334A"   # raised / hover
FIELD     = "#232333"   # input wells
STROKE    = "#3A3A4E"   # hairline separators
STROKE_HI = "#4A4A63"

TEXT      = "#E6E6F0"
TEXT_DIM  = "#8A8AA3"
TEXT_MUTE = "#5F5F7A"

CYAN      = "#00E5FF"   # primary accent - interactive, active, measured
AMBER     = "#FFB300"   # secondary accent - attention, Recognition band

# EN 62676-4 task bands
C_IDENT   = "#00E676"   # Teşhis / Identification
C_RECOG   = "#FFB300"   # Tanıma / Recognition
C_DETECT  = "#FF4D6D"   # Algılama / Detection
C_DEAD    = "#FF4D6D"   # dead zone hatch

# EN 62676-4 pixel density thresholds (px/m across the target)
PPM_IDENT   = 250.0
PPM_RECOG   = 125.0
PPM_OBSERVE = 62.5
PPM_DETECT  = 25.0

BAND_ALPHA   = 38       # fill alpha of a DORI band  (0-255)
BAND_EDGE_A  = 170      # alpha of the band's leading edge

TASKS = {
    "Teşhis (Identification)": PPM_IDENT,
    "Tanıma (Recognition)":    PPM_RECOG,
    "Gözlem (Observation)":    PPM_OBSERVE,
    "Algılama (Detection)":    PPM_DETECT,
}


def _pick_font(*candidates: str) -> str:
    """First installed family from candidates, else Tk's default."""
    try:
        available = {name.lower() for name in tkfont.families()}
    except Exception:
        return candidates[-1]
    for name in candidates:
        if name.lower() in available:
            return name
    return candidates[-1]


def _hex_rgb(value: str) -> Tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _mix(fg: str, bg: str, alpha: float) -> str:
    """Flatten fg over bg at alpha - for widgets that cannot do real alpha."""
    fr, fg_, fb = _hex_rgb(fg)
    br, bg_, bb = _hex_rgb(bg)
    return "#%02X%02X%02X" % (
        round(fr * alpha + br * (1 - alpha)),
        round(fg_ * alpha + bg_ * (1 - alpha)),
        round(fb * alpha + bb * (1 - alpha)),
    )


def ground_distance_for_ppm(result: OpticResult, ppm: float) -> float:
    """Ground range at which the camera still resolves `ppm` px/m.

    Same relation the engine uses in calculate_for_camera - restated here
    against the already-computed OpticResult so the two can never drift.
    """
    if ppm <= 0:
        return 0.0
    optical = (result.res_width_px * result.focal_mm) / (ppm * result.sensor_width_mm)
    drop = result.vertical_drop_m
    if optical <= drop:
        return 0.0
    ground = math.sqrt(optical * optical - drop * drop)
    return min(ground, result.max_geom_dist_m)


# ─────────────────────────────────────────────────────────────────────────────
# Small composed widgets
# ─────────────────────────────────────────────────────────────────────────────

class SectionLabel(ctk.CTkFrame):
    """Uppercase micro-heading with a hairline rule running to the edge."""

    def __init__(self, master, text: str, font_ui: str):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            self, text=text.upper(), text_color=TEXT_MUTE,
            font=(font_ui, 10, "bold"), anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        rule = ctk.CTkFrame(self, height=1, fg_color=STROKE)
        rule.grid(row=0, column=1, sticky="ew")


class Field(ctk.CTkFrame):
    """Label + slider + live numeric readout, on one row."""

    def __init__(self, master, label: str, unit: str, lo: float, hi: float,
                 value: float, step: float, on_change, font_ui: str, font_mono: str):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self._unit = unit
        self._step = step
        self._on_change = on_change
        self.var = tk.DoubleVar(value=value)

        ctk.CTkLabel(self, text=label, text_color=TEXT_DIM,
                     font=(font_ui, 11), anchor="w").grid(row=0, column=0, sticky="w")
        self.readout = ctk.CTkLabel(
            self, text=self._fmt(value), text_color=TEXT,
            font=(font_mono, 12, "bold"), anchor="e",
        )
        self.readout.grid(row=0, column=1, sticky="e")

        steps = max(int(round((hi - lo) / step)), 1)
        self.slider = ctk.CTkSlider(
            self, from_=lo, to=hi, number_of_steps=steps, variable=self.var,
            command=self._changed, height=12,
            fg_color=FIELD, progress_color=CYAN, button_color=CYAN,
            button_hover_color="#66F0FF", border_width=0,
        )
        self.slider.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(3, 0))

    def _fmt(self, value: float) -> str:
        digits = 0 if self._step >= 1 else (1 if self._step >= 0.1 else 2)
        return f"{value:.{digits}f} {self._unit}".strip()

    def _changed(self, value):
        self.readout.configure(text=self._fmt(float(value)))
        self._on_change()

    def get(self) -> float:
        return float(self.var.get())

    def set(self, value: float):
        self.var.set(value)
        self.readout.configure(text=self._fmt(float(value)))


class Select(ctk.CTkFrame):
    """Label above a flat option menu."""

    def __init__(self, master, label: str, values: List[str], value: str,
                 on_change, font_ui: str):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text=label, text_color=TEXT_DIM,
                     font=(font_ui, 11), anchor="w").grid(row=0, column=0, sticky="w")
        self.var = tk.StringVar(value=value)
        self.menu = ctk.CTkOptionMenu(
            self, values=values, variable=self.var, command=lambda _v: on_change(),
            height=28, corner_radius=6, font=(font_ui, 11),
            fg_color=FIELD, button_color=FIELD, button_hover_color=PANEL_HI,
            text_color=TEXT, dropdown_fg_color=PANEL, dropdown_hover_color=PANEL_HI,
            dropdown_text_color=TEXT, dropdown_font=(font_ui, 11),
            dynamic_resizing=False, anchor="w",
        )
        self.menu.grid(row=1, column=0, sticky="ew", pady=(3, 0))

    def get(self) -> str:
        return self.var.get()


class StatCard(ctk.CTkFrame):
    """Bottom-bar readout: accent spine, micro-label, mono value, footnote."""

    def __init__(self, master, label: str, accent: str, font_ui: str, font_mono: str):
        super().__init__(master, fg_color=PANEL, corner_radius=8)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._accent = accent

        self.spine = ctk.CTkFrame(self, width=3, fg_color=accent, corner_radius=2)
        self.spine.grid(row=0, column=0, rowspan=3, sticky="ns", padx=(8, 10), pady=10)

        ctk.CTkLabel(self, text=label.upper(), text_color=TEXT_MUTE,
                     font=(font_ui, 9, "bold"), anchor="w").grid(
            row=0, column=1, sticky="w", pady=(10, 0), padx=(0, 12))
        self.value = ctk.CTkLabel(self, text="—", text_color=TEXT,
                                  font=(font_mono, 20, "bold"), anchor="w")
        self.value.grid(row=1, column=1, sticky="w", padx=(0, 12))
        self.note = ctk.CTkLabel(self, text="", text_color=TEXT_DIM,
                                 font=(font_ui, 10), anchor="w",
                                 justify="left", wraplength=170)
        self.note.grid(row=2, column=1, sticky="nw", pady=(0, 10), padx=(0, 12))

    def update_values(self, value: str, note: str = "", accent: Optional[str] = None):
        self.value.configure(text=value, text_color=accent or TEXT)
        self.note.configure(text=note)
        self.spine.configure(fg_color=accent or self._accent)


# ─────────────────────────────────────────────────────────────────────────────
# Plan-view renderer
# ─────────────────────────────────────────────────────────────────────────────

class DoriPlanView:
    """Top-down FOV plan. Camera sits bottom-centre, optical axis points up.

    The shaded DORI bands are composited in RGBA with Pillow and blitted as a
    single canvas image, so overlapping zones and the grid beneath them blend
    the way they would in a CAD viewport. Without Pillow the same bands are
    drawn as stippled polygons.
    """

    PAD_L, PAD_R, PAD_T, PAD_B = 52, 16, 16, 52

    def __init__(self, canvas: tk.Canvas, font_ui: str, font_mono: str):
        self.canvas = canvas
        self.font_ui = font_ui
        self.font_mono = font_mono
        self._photo = None          # keep a reference or Tk drops the image
        self.geometry: Dict[str, float] = {}

    # ── coordinate helpers ────────────────────────────────────────────────
    def _world_to_px(self, lateral_m: float, forward_m: float) -> Tuple[float, float]:
        g = self.geometry
        return (g["ox"] + lateral_m * g["scale"], g["oy"] - forward_m * g["scale"])

    def _polar_px(self, radius_m: float, angle_rad: float) -> Tuple[float, float]:
        return self._world_to_px(radius_m * math.sin(angle_rad),
                                 radius_m * math.cos(angle_rad))

    def px_to_world(self, px: float, py: float) -> Tuple[float, float]:
        g = self.geometry
        if not g:
            return 0.0, 0.0
        return ((px - g["ox"]) / g["scale"], (g["oy"] - py) / g["scale"])

    @staticmethod
    def _nice_step(span: float) -> float:
        raw = span / 8.0
        for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
            if step >= raw:
                return float(step)
        return 2000.0

    def _arc_points(self, radius_m: float, half_rad: float, segments: int = 24):
        return [self._polar_px(radius_m, -half_rad + 2 * half_rad * i / segments)
                for i in range(segments + 1)]

    def _wedge(self, near_m: float, far_m: float, half_rad: float,
               segments: int = 24) -> List[Tuple[float, float]]:
        """Annular sector between two ranges, as a device-space polygon."""
        return (self._arc_points(near_m, half_rad, segments)
                + self._arc_points(far_m, half_rad, segments)[::-1])

    # ── main draw ─────────────────────────────────────────────────────────
    def draw(self, result: OpticResult, req_ppm: float, req_dist: float):
        cv = self.canvas
        cv.delete("all")
        w = cv.winfo_width()
        h = cv.winfo_height()
        if w < 160 or h < 160:
            return

        plot = (self.PAD_L, self.PAD_T, w - self.PAD_R, h - self.PAD_B)
        plot_w = plot[2] - plot[0]
        plot_h = plot[3] - plot[1]

        half_rad = math.radians(result.hfov_deg / 2.0)
        d_ident = ground_distance_for_ppm(result, PPM_IDENT)
        d_recog = ground_distance_for_ppm(result, PPM_RECOG)
        d_detect = ground_distance_for_ppm(result, PPM_DETECT)

        span = max(d_detect * 1.10, req_dist * 1.15, result.dead_zone_m * 2.0, 8.0)
        if math.isfinite(result.max_geom_dist_m):
            span = min(span, max(result.max_geom_dist_m * 1.20, 8.0))
        lateral = max(span * math.sin(half_rad), span * 0.10)

        scale = min(plot_h / span, (plot_w / 2.0) / lateral)
        self.geometry = {
            "ox": plot[0] + plot_w / 2.0, "oy": plot[3],
            "scale": scale, "span": span, "lateral": lateral,
            "half_rad": half_rad, "plot": plot,
        }

        bands = [
            ("TEŞHİS",   C_IDENT,  result.dead_zone_m, d_ident),
            ("TANIMA",   C_RECOG,  d_ident,            d_recog),
            ("ALGILAMA", C_DETECT, d_recog,            d_detect),
        ]
        bands = [b for b in bands if b[3] > b[2] + 1e-6]

        rastered = False
        if Image is not None:
            try:
                self._blit(plot, plot_w, plot_h, span, half_rad, bands)
                rastered = True
            except Exception as exc:
                # A raster failure must not blank the workbench. Report once,
                # then draw the same bands with vector primitives.
                if not getattr(self, "_blit_failed", False):
                    self._blit_failed = True
                    try:
                        from ..errors import report
                        report(exc, "DORI raster katmani", show=False)
                    except Exception:
                        pass
        if not rastered:
            self._rings_vector(span, half_rad)
            for _name, color, near, far in bands:
                cv.create_polygon(self._wedge(near, far, half_rad),
                                  fill=color, outline="", stipple="gray12")

        self._draw_frame_and_ruler(span, half_rad)
        self._draw_fov_edges(result, span, half_rad)
        self._draw_limits(result, half_rad, span)
        self._draw_requirement(result, req_ppm, req_dist)
        self._draw_camera()
        self._draw_legend(bands, plot)

    # ── raster layer: range rings + alpha-blended DORI bands ──────────────
    def _blit(self, plot, plot_w, plot_h, span, half_rad, bands):
        g = self.geometry
        x0, y0 = plot[0], plot[1]
        size = (max(int(plot_w), 1), max(int(plot_h), 1))
        img = Image.new("RGBA", size, (*_hex_rgb(BG), 255))
        draw = ImageDraw.Draw(img, "RGBA")
        shift = lambda pt: (pt[0] - x0, pt[1] - y0)

        # iso-range rings: the physically meaningful grid for a conic FOV
        minor = (*_hex_rgb(PANEL), 255)
        major = (*_hex_rgb(STROKE), 255)
        step = self._nice_step(span)
        index = 1
        while index * step <= span * 1.02:
            radius = index * step
            draw.line([shift(p) for p in self._arc_points(radius, half_rad, 40)],
                      fill=major if index % 5 == 0 else minor, width=1)
            index += 1
        # radial spokes
        for frac in (-1.0, -0.5, 0.0, 0.5, 1.0):
            end = shift(self._polar_px(span, half_rad * frac))
            draw.line([shift((g["ox"], g["oy"])), end],
                      fill=major if frac == 0.0 else minor, width=1)

        # DORI bands, each composited as its own translucent sheet
        for _name, color, near, far in bands:
            sheet = Image.new("RGBA", size, (0, 0, 0, 0))
            ImageDraw.Draw(sheet, "RGBA").polygon(
                [shift(p) for p in self._wedge(near, far, half_rad)],
                fill=(*_hex_rgb(color), BAND_ALPHA))
            img = Image.alpha_composite(img, sheet)
            draw = ImageDraw.Draw(img, "RGBA")
            draw.line([shift(p) for p in self._arc_points(far, half_rad, 40)],
                      fill=(*_hex_rgb(color), BAND_EDGE_A), width=2)

        # master= is not optional here. Without it PIL builds the image against
        # tkinter._default_root, which in a frozen build is not necessarily the
        # interpreter this canvas belongs to; Tcl then reports
        #   TclError: image "pyimageNN" doesn't exist
        # Keep the previous photo alive until create_image has consumed the new
        # one, so a GC pass cannot delete the image mid-call.
        previous = self._photo
        self._photo = ImageTk.PhotoImage(img, master=self.canvas)
        self.canvas.create_image(x0, y0, image=self._photo, anchor="nw")
        del previous

    def _rings_vector(self, span: float, half_rad: float):
        step = self._nice_step(span)
        index = 1
        while index * step <= span * 1.02:
            self.canvas.create_line(self._arc_points(index * step, half_rad, 40),
                                    fill=STROKE if index % 5 == 0 else PANEL)
            index += 1

    # ── vector overlays ───────────────────────────────────────────────────
    def _draw_frame_and_ruler(self, span: float, half_rad: float):
        cv = self.canvas
        g = self.geometry
        x0, y0, x1, y1 = g["plot"]
        cv.create_rectangle(x0, y0, x1, y1, outline=STROKE, width=1)

        step = self._nice_step(span)
        index = 1
        while index * step <= span * 1.02:
            radius = index * step
            py = g["oy"] - radius * g["scale"]
            if py < y0 + 6:
                break
            major = index % 5 == 0
            cv.create_line(x0 - (7 if major else 4), py, x0, py,
                           fill=STROKE_HI if major else STROKE)
            cv.create_text(x0 - 10, py, text=f"{radius:g}", anchor="e",
                           fill=TEXT_DIM if major else TEXT_MUTE,
                           font=(self.font_mono, 9))
            index += 1
        cv.create_text(x0 - 10, y1 - 2, text="m", anchor="se",
                       fill=TEXT_MUTE, font=(self.font_ui, 9))

    def _draw_fov_edges(self, result: OpticResult, span: float, half_rad: float):
        cv = self.canvas
        g = self.geometry
        for sign in (-1, 1):
            end = self._polar_px(span, sign * half_rad)
            cv.create_line(g["ox"], g["oy"], end[0], end[1], fill=CYAN, width=1)
        anchor_pt = self._polar_px(span * 0.62, half_rad)
        cv.create_text(anchor_pt[0] + 8, anchor_pt[1], anchor="w", fill=CYAN,
                       font=(self.font_mono, 10),
                       text=f"HFOV {result.hfov_deg:.1f}°")

    def _draw_limits(self, result: OpticResult, half_rad: float, span: float):
        cv = self.canvas
        if result.dead_zone_m > 0.05:
            dead = min(result.dead_zone_m, span)
            cv.create_polygon(self._wedge(0.0, dead, half_rad),
                              fill="", outline=C_DEAD, width=1, dash=(3, 3))
            edge = self._polar_px(dead, -half_rad)
            cv.create_text(edge[0] - 14, edge[1] - 6, anchor="e", fill=C_DEAD,
                           font=(self.font_mono, 9, "bold"),
                           text=f"KÖR {result.dead_zone_m:.1f} m")
        if math.isfinite(result.max_geom_dist_m) and result.max_geom_dist_m <= span:
            cv.create_line(self._arc_points(result.max_geom_dist_m, half_rad, 40),
                           fill=AMBER, width=1, dash=(6, 4))
            tip = self._polar_px(result.max_geom_dist_m, half_rad * 0.98)
            cv.create_text(tip[0] + 8, tip[1], anchor="w", fill=AMBER,
                           font=(self.font_mono, 9, "bold"),
                           text=f"GEOM. LİMİT {result.max_geom_dist_m:.1f} m")

    def _draw_requirement(self, result, req_ppm: float, req_dist: float):
        cv = self.canvas
        g = self.geometry
        if req_dist <= 0 or req_dist > g["span"]:
            return
        achieved = ppm_at_distance(result, req_dist)
        ok = (achieved >= req_ppm
              and result.dead_zone_m <= req_dist <= result.max_geom_dist_m)
        color = C_IDENT if ok else C_DETECT
        x, y = self._world_to_px(0, req_dist)
        cv.create_oval(x - 7, y - 7, x + 7, y + 7, outline=color, width=2)
        cv.create_line(x - 13, y, x + 13, y, fill=color, width=1)
        cv.create_line(x, y - 13, x, y + 13, fill=color, width=1)
        cv.create_text(x + 18, y, anchor="w", fill=color,
                       font=(self.font_mono, 10, "bold"),
                       text=f"HEDEF {req_dist:.1f} m · {achieved:.0f} px/m")

    def _draw_camera(self):
        cv = self.canvas
        g = self.geometry
        x, y = g["ox"], g["oy"]
        cv.create_oval(x - 16, y - 16, x + 16, y + 16, outline=_mix(CYAN, BG, 0.30))
        cv.create_polygon([x, y - 12, x - 8, y + 7, x + 8, y + 7],
                          fill=CYAN, outline=BG, width=1)
        cv.create_text(x, y + 24, text="KAMERA", fill=TEXT_DIM,
                       font=(self.font_ui, 9, "bold"))

    def _draw_legend(self, bands, plot):
        """Boxed key, top-right inside the plot. Carries the band ranges so the
        wedge itself stays free of labels."""
        cv = self.canvas
        if not bands:
            return
        x0, y0, x1, y1 = plot
        rows = [(name, color, f"{near:.1f} – {far:.1f} m")
                for name, color, near, far in bands]
        width = 226
        height = 14 + len(rows) * 17
        bx1, by0 = x1 - 12, y0 + 12
        bx0, by1 = bx1 - width, by0 + height
        cv.create_rectangle(bx0, by0, bx1, by1, fill=PANEL, outline=STROKE)
        y = by0 + 14
        for name, color, span_text in rows:
            cv.create_rectangle(bx0 + 12, y - 5, bx0 + 22, y + 5,
                                fill=_mix(color, PANEL, BAND_ALPHA / 255.0),
                                outline=color)
            cv.create_text(bx0 + 30, y, anchor="w", fill=TEXT_DIM,
                           font=(self.font_mono, 9), text=name)
            cv.create_text(bx0 + 110, y, anchor="w", fill=TEXT,
                           font=(self.font_mono, 9), text=span_text)
            y += 17


# ─────────────────────────────────────────────────────────────────────────────
# Application window
# ─────────────────────────────────────────────────────────────────────────────

class _WorkbenchBody:
    """Everything the workbench is, built into `self`.

    Mixed into a CTk root (standalone launch) or a CTkToplevel (opened from
    the classic dual-view application), because only the shell differs.
    """

    MIN_W, MIN_H = 1280, 880
    RENDER_MS = 16          # coalesce slider bursts into one frame

    def _build_workbench(self):
        if tk._default_root is None:
            try:
                tk._default_root = self.winfo_toplevel()
            except Exception:
                tk._default_root = self

        ctk.set_appearance_mode("dark")

        self.title("CCTV Optics Workbench — EN 62676-4")
        self.geometry(f"{self.MIN_W}x{self.MIN_H}")
        self.minsize(self.MIN_W, self.MIN_H)

        self.font_ui = _pick_font("Segoe UI", "Inter", "Ubuntu", "DejaVu Sans", "Arial")
        self.font_mono = _pick_font("JetBrains Mono", "Cascadia Mono", "Consolas",
                                    "DejaVu Sans Mono", "Courier New")

        try:
            self.camera_library = load_camera_library()
        except Exception:
            self.camera_library = {"Özel kamera": {}}

        self.camera = CameraConfig()
        self.result: Optional[OpticResult] = None
        self._render_job = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)

        self._build_topbar()
        self._build_rail()
        self._build_viewport()
        self._build_statusbar()

        self.after(60, self.request_render)

    def seed_from(self, camera: CameraConfig):
        """Adopt the geometry of a camera from the classic application."""
        if camera.sensor_name in SENSOR_DIMS_MM:
            self.sel_sensor.var.set(camera.sensor_name)
        if camera.resolution_name in RESOLUTIONS:
            self.sel_res.var.set(camera.resolution_name)
        for value, field in ((camera.focal_min_mm, self.f_focal_min),
                             (camera.focal_max_mm, self.f_focal_max),
                             (camera.pole_height_m, self.f_pole),
                             (camera.tilt_deg, self.f_tilt),
                             (camera.target_height_m, self.f_target),
                             (camera.ir_range_m, self.f_ir),
                             (camera.min_lux, self.f_lux)):
            lo, hi = field.slider.cget("from_"), field.slider.cget("to")
            field.set(min(max(float(value), lo), hi))
        self.title(f"CCTV Optics Workbench — {camera.name}")
        self.request_render()

    def _cancel_render(self):
        if self._render_job is not None:
            try:
                self.after_cancel(self._render_job)
            except Exception:
                pass
            self._render_job = None

    # ── chrome ────────────────────────────────────────────────────────────
    def _build_topbar(self):
        bar = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0, height=52)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(2, weight=1)

        mark = ctk.CTkFrame(bar, width=4, height=22, fg_color=CYAN, corner_radius=2)
        mark.grid(row=0, column=0, padx=(18, 10), pady=15)
        ctk.CTkLabel(bar, text="CCTV OPTICS WORKBENCH", text_color=TEXT,
                     font=(self.font_ui, 13, "bold")).grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(bar, text="EN 62676-4 · DORI", text_color=TEXT_MUTE,
                     font=(self.font_mono, 11)).grid(row=0, column=2, sticky="w", padx=14)

        self.lens_mode = ctk.CTkSegmentedButton(
            bar, values=["GENİŞ", "DAR"], command=lambda _v: self.request_render(),
            height=30, corner_radius=6, font=(self.font_ui, 11, "bold"),
            fg_color=FIELD, selected_color=CYAN, selected_hover_color="#66F0FF",
            unselected_color=FIELD, unselected_hover_color=PANEL_HI,
            text_color=TEXT, text_color_disabled=TEXT_MUTE,
        )
        self.lens_mode.set("GENİŞ")
        self.lens_mode.grid(row=0, column=3, padx=(0, 18))

        ctk.CTkFrame(self, height=1, fg_color=STROKE, corner_radius=0).grid(
            row=0, column=0, columnspan=2, sticky="sew")

    def _build_rail(self):
        rail = ctk.CTkScrollableFrame(
            self, width=320, fg_color=PANEL, corner_radius=0,
            scrollbar_button_color=STROKE, scrollbar_button_hover_color=STROKE_HI,
        )
        rail.grid(row=1, column=0, rowspan=2, sticky="nsw")
        rail.grid_columnconfigure(0, weight=1)
        self._rail = rail
        row = 0

        def section(title: str):
            nonlocal row
            widget = SectionLabel(rail, title, self.font_ui)
            widget.grid(row=row, column=0, sticky="ew", pady=(14 if row else 4, 8))
            row += 1

        def add(widget, pad=(0, 9)):
            nonlocal row
            widget.grid(row=row, column=0, sticky="ew", padx=(0, 4), pady=pad)
            row += 1

        section("Kamera")
        models = list(self.camera_library.keys()) or ["Özel kamera"]
        self.sel_model = Select(rail, "Hazır model", models, models[0],
                                self._apply_library_model, self.font_ui)
        add(self.sel_model)

        section("Optik")
        sensors = list(SENSOR_DIMS_MM.keys())
        self.sel_sensor = Select(rail, "Sensör formatı", sensors,
                                 self.camera.sensor_name if self.camera.sensor_name in sensors else sensors[0],
                                 self.request_render, self.font_ui)
        add(self.sel_sensor)
        resolutions = list(RESOLUTIONS.keys())
        self.sel_res = Select(rail, "Çözünürlük", resolutions,
                              self.camera.resolution_name if self.camera.resolution_name in resolutions else resolutions[0],
                              self.request_render, self.font_ui)
        add(self.sel_res)
        self.f_focal_min = Field(rail, "Odak — geniş uç", "mm", 1.0, 60.0,
                                 self.camera.focal_min_mm, 0.1, self.request_render,
                                 self.font_ui, self.font_mono)
        add(self.f_focal_min)
        self.f_focal_max = Field(rail, "Odak — dar uç", "mm", 2.0, 400.0,
                                 self.camera.focal_max_mm, 0.5, self.request_render,
                                 self.font_ui, self.font_mono)
        add(self.f_focal_max)

        section("Geometri")
        self.f_pole = Field(rail, "Montaj yüksekliği", "m", 1.0, 30.0,
                            self.camera.pole_height_m, 0.1, self.request_render,
                            self.font_ui, self.font_mono)
        add(self.f_pole)
        self.f_tilt = Field(rail, "Aşağı eğim (tilt)", "°", 0.0, 75.0,
                            self.camera.tilt_deg, 0.5, self.request_render,
                            self.font_ui, self.font_mono)
        add(self.f_tilt)
        self.f_target = Field(rail, "Hedef yüksekliği", "m", 0.2, 3.0,
                              self.camera.target_height_m, 0.1, self.request_render,
                              self.font_ui, self.font_mono)
        add(self.f_target)

        section("Sahne")
        self.f_ir = Field(rail, "IR menzili", "m", 0.0, 300.0,
                          self.camera.ir_range_m, 1.0, self.request_render,
                          self.font_ui, self.font_mono)
        add(self.f_ir)

        self.f_lux = Field(rail, "Minimum aydınlatma", "lux", 0.0, 1.0,
                           self.camera.min_lux, 0.01, self.request_render,
                           self.font_ui, self.font_mono)
        add(self.f_lux, pad=(0, 16))

    def _build_viewport(self):
        holder = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        holder.grid(row=1, column=1, sticky="nsew", padx=(1, 0))
        holder.grid_columnconfigure(0, weight=1)
        holder.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(holder, fg_color="transparent", height=30)
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 6))
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(head, text="GÖRÜŞ ALANI · KUŞ BAKIŞI", text_color=TEXT_DIM,
                     font=(self.font_ui, 10, "bold")).grid(row=0, column=0, sticky="w")
        self.cursor_var = tk.StringVar(value="")
        ctk.CTkLabel(head, textvariable=self.cursor_var, text_color=CYAN,
                     font=(self.font_mono, 10), anchor="e").grid(row=0, column=1, sticky="e")

        self.canvas = tk.Canvas(holder, bg=BG, highlightthickness=0, bd=0)
        self.canvas.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 14))
        self.canvas.bind("<Configure>", lambda _e: self.request_render(delay=80))
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda _e: self.cursor_var.set(""))

        self.plan = DoriPlanView(self.canvas, self.font_ui, self.font_mono)

    def _build_requirement_strip(self, parent) -> ctk.CTkFrame:
        """The design requirement lives beside the verdict it produces, not
        buried at the bottom of the parameter rail."""
        strip = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=8, height=48)
        strip.grid_propagate(False)
        strip.grid_columnconfigure(5, weight=1)

        ctk.CTkFrame(strip, width=3, height=20, fg_color=AMBER, corner_radius=2).grid(
            row=0, column=0, padx=(10, 10), pady=14)
        ctk.CTkLabel(strip, text="ŞARTNAME", text_color=TEXT_MUTE,
                     font=(self.font_ui, 9, "bold")).grid(row=0, column=1, padx=(0, 14))

        self.sel_task = ctk.CTkOptionMenu(
            strip, values=list(TASKS.keys()), command=lambda _v: self.request_render(),
            width=210, height=28, corner_radius=6, font=(self.font_ui, 11),
            fg_color=FIELD, button_color=FIELD, button_hover_color=PANEL_HI,
            text_color=TEXT, dropdown_fg_color=PANEL, dropdown_hover_color=PANEL_HI,
            dropdown_text_color=TEXT, dropdown_font=(self.font_ui, 11),
            dynamic_resizing=False, anchor="w",
        )
        self.sel_task.set("Teşhis (Identification)")
        self.sel_task.grid(row=0, column=2, padx=(0, 18))

        ctk.CTkLabel(strip, text="MESAFE", text_color=TEXT_MUTE,
                     font=(self.font_ui, 9, "bold")).grid(row=0, column=3, padx=(0, 10))
        self.req_dist_var = tk.DoubleVar(value=12.0)
        self.slider_req = ctk.CTkSlider(
            strip, from_=1.0, to=200.0, number_of_steps=398,
            variable=self.req_dist_var, command=self._req_dist_changed, height=12,
            fg_color=FIELD, progress_color=AMBER, button_color=AMBER,
            button_hover_color="#FFCC55", border_width=0,
        )
        self.slider_req.configure(width=320)
        self.slider_req.grid(row=0, column=4, padx=(0, 16))
        self.lbl_req_dist = ctk.CTkLabel(strip, text="12.0 m", text_color=TEXT,
                                         font=(self.font_mono, 12, "bold"), width=64,
                                         anchor="e")
        self.lbl_req_dist.grid(row=0, column=6, padx=(0, 14))
        return strip

    def _req_dist_changed(self, value):
        self.lbl_req_dist.configure(text=f"{float(value):.1f} m")
        self.request_render()

    def _build_statusbar(self):
        holder = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        holder.grid(row=2, column=1, sticky="ew", padx=18, pady=(0, 16))
        holder.grid_columnconfigure(0, weight=1)

        self._build_requirement_strip(holder).grid(
            row=0, column=0, sticky="ew", pady=(0, 10))

        bar = ctk.CTkFrame(holder, fg_color="transparent", height=118)
        bar.grid(row=1, column=0, sticky="ew")
        bar.grid_propagate(False)
        for column in range(5):
            bar.grid_columnconfigure(column, weight=1, uniform="cards")

        specs = [
            ("card_fov",     "Görüş açısı",        CYAN),
            ("card_dead",    "Kör nokta",          C_DETECT),
            ("card_limit",   "Geometrik limit",    AMBER),
            ("card_ppm",     "Hedefte çözünürlük", CYAN),
            ("card_verdict", "EN 62676-4 uygunluk", C_IDENT),
        ]
        for column, (attr, label, accent) in enumerate(specs):
            card = StatCard(bar, label, accent, self.font_ui, self.font_mono)
            card.grid(row=0, column=column, sticky="nsew",
                      padx=(0 if column == 0 else 6, 0), pady=2)
            setattr(self, attr, card)

    # ── behaviour ─────────────────────────────────────────────────────────
    def _apply_library_model(self):
        entry = self.camera_library.get(self.sel_model.get()) or {}
        if entry.get("sensor_name") in SENSOR_DIMS_MM:
            self.sel_sensor.var.set(entry["sensor_name"])
        if entry.get("resolution_name") in RESOLUTIONS:
            self.sel_res.var.set(entry["resolution_name"])
        for key, field in (("focal_min_mm", self.f_focal_min),
                           ("focal_max_mm", self.f_focal_max),
                           ("pole_height_m", self.f_pole),
                           ("tilt_deg", self.f_tilt),
                           ("ir_range_m", self.f_ir)):
            if isinstance(entry.get(key), (int, float)):
                field.set(float(entry[key]))
        self.request_render()

    def request_render(self, delay: int = None):
        """Coalesce a burst of parameter changes into a single repaint."""
        if self._render_job is not None:
            return
        self._render_job = self.after(self.RENDER_MS if delay is None else delay,
                                      self._render)

    def _render(self):
        self._render_job = None
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return

        self.camera.sensor_name = self.sel_sensor.get()
        self.camera.resolution_name = self.sel_res.get()
        self.camera.focal_min_mm = self.f_focal_min.get()
        self.camera.focal_max_mm = self.f_focal_max.get()
        self.camera.pole_height_m = self.f_pole.get()
        self.camera.tilt_deg = self.f_tilt.get()
        self.camera.target_height_m = self.f_target.get()
        self.camera.ir_range_m = self.f_ir.get()
        self.camera.min_lux = self.f_lux.get()

        mode = "min" if self.lens_mode.get() == "GENİŞ" else "max"
        self.result = calculate_for_camera(self.camera, mode, [])

        req_ppm = TASKS[self.sel_task.get()]
        req_dist = float(self.req_dist_var.get())
        self.plan.draw(self.result, req_ppm, req_dist)
        self._update_cards(req_ppm, req_dist)

    def _update_cards(self, req_ppm: float, req_dist: float):
        r = self.result
        self.card_fov.update_values(
            f"{r.hfov_deg:.1f}°",
            f"dikey {r.vfov_deg:.1f}° · {r.focal_mm:g} mm", CYAN)

        if r.dead_zone_m < 0.05:
            self.card_dead.update_values("YOK", "kamera dibi kapsanıyor", C_IDENT)
        else:
            self.card_dead.update_values(
                f"{r.dead_zone_m:.1f} m",
                f"{r.dead_zone_area_m2:.0f} m² · yanal ±{r.dead_zone_left_m:.1f} m",
                C_DETECT if r.dead_zone_m > 4 else AMBER)

        if math.isfinite(r.max_geom_dist_m):
            self.card_limit.update_values(
                f"{r.max_geom_dist_m:.1f} m",
                f"üst ışın {r.top_ray_deg:+.1f}°", AMBER)
        else:
            self.card_limit.update_values("AÇIK", "üst ışın ufkun üzerinde", CYAN)

        achieved = ppm_at_distance(r, req_dist)
        self.card_ppm.update_values(
            f"{achieved:.0f} px/m",
            f"{req_dist:.1f} m · gereken {req_ppm:g}",
            C_IDENT if achieved >= req_ppm else C_DETECT)

        reasons = []
        if achieved < req_ppm:
            reasons.append("piksel yoğunluğu yetersiz")
        if req_dist < r.dead_zone_m:
            reasons.append("kör noktada")
        if req_dist > r.max_geom_dist_m:
            reasons.append("geometrik limit ötesinde")
        if self.camera.ir_range_m > 0 and req_dist > self.camera.ir_range_m:
            reasons.append("IR menzili dışında")
        if self.camera.min_lux > 0.05 and (self.camera.ir_range_m <= 0
                                           or req_dist > self.camera.ir_range_m):
            reasons.append("düşük ışıkta yetersiz")

        task = self.sel_task.get().split(" (")[0]
        if reasons:
            self.card_verdict.update_values("UYGUN DEĞİL", " · ".join(reasons), C_DETECT)
        else:
            self.card_verdict.update_values(
                "UYGUN", f"{task} · {req_dist:.1f} m", C_IDENT)

    def _on_motion(self, event):
        if not self.result or not self.plan.geometry:
            return
        x0, y0, x1, y1 = self.plan.geometry["plot"]
        if not (x0 <= event.x <= x1 and y0 <= event.y <= y1):
            self.cursor_var.set("")
            return
        lateral, forward = self.plan.px_to_world(event.x, event.y)
        if forward < 0:
            self.cursor_var.set("")
            return
        distance = math.hypot(lateral, forward)
        ppm = ppm_at_distance(self.result, distance)
        half = self.result.hfov_deg / 2.0
        angle = math.degrees(math.atan2(lateral, max(forward, 1e-6)))
        inside = abs(angle) <= half and self.result.dead_zone_m <= distance <= self.result.max_geom_dist_m
        task = ("teşhis" if ppm >= PPM_IDENT else
                "tanıma" if ppm >= PPM_RECOG else
                "gözlem" if ppm >= PPM_OBSERVE else
                "algılama" if ppm >= PPM_DETECT else "kapsama dışı")
        state = task if inside else "görüş alanı dışında"
        self.cursor_var.set(
            f"{distance:6.1f} m   {angle:+5.1f}°   {ppm:6.0f} px/m   {state}")


class ModernOpticsWorkbench(_WorkbenchBody, ctk.CTk):
    """Standalone window - owns its own Tk root."""

    def __init__(self):
        super().__init__(fg_color=BG)
        self._build_workbench()


class OpticsWorkbenchWindow(_WorkbenchBody, tk.Toplevel):
    """Child window - opened from the classic dual-view application."""

    def __init__(self, master, on_close=None):
        if tk._default_root is None and master is not None:
            try:
                tk._default_root = master.winfo_toplevel()
            except Exception:
                tk._default_root = master
        super().__init__(master, bg=BG)
        if tk._default_root is None:
            tk._default_root = self
        self._on_close = on_close
        self._render_job = None
        # Bind the close protocol BEFORE building. If the build raises, the
        # window would otherwise sit there empty with no way to shut it.
        self.protocol("WM_DELETE_WINDOW", self.close)
        from ..errors import guarded_build
        self.build_ok = guarded_build(self, self._build_workbench,
                                      "Optik Tezgâhı")
        if not self.build_ok:
            return
        self.after(100, self.lift)
        self.after(150, self.request_render)

    def close(self):
        try:
            self._cancel_render()
        except Exception:
            pass
        if callable(self._on_close):
            try:
                self._on_close()
            except Exception:
                pass
        self.destroy()


def launch(initial_model: str = None):
    """Entry point for the standalone workbench."""
    wb = ModernOpticsWorkbench()
    if initial_model and initial_model in wb.camera_library:
        wb.sel_model.var.set(initial_model)
        wb._apply_library_model()
    wb.mainloop()


if __name__ == "__main__":
    launch()
