"""Site acceptance test sheet ("saha kabul foyu") of a plan.

Coverage numbers come from a model; the only way to know they hold on site is
to measure after installation. For every FIXED camera of the plan the sheet says
where to stand a test target (a person or a 0.5 m board) and what the image must
show there: the engine's px/m at that node, the DORI level, and the pixel count
a 1.8 m person / 0.5 m board / 0.52 m plate must span, with a tolerance band.
The commissioning engineer fills in "measured px" and ticks pass / fail.

Nothing is derived here (Rule 1): every candidate point is a NODE of the terrain
grid that the authoritative multi-camera run (``MultiViewshedResult.per_camera``,
the run ``perimeter.coverage`` scores) says is visible, and its px/m / DORI /
slant are that run's node values, read back unchanged. PTZ cameras get a
patrol-settings row only (they are verified at their presets).

Point selection, per camera (``points_per_camera`` takes them in this order):
  A  the farthest visible node near the optical axis (|off-axis| <= HFOV/6,
     widened to HFOV/4 and then to any bearing, said in the note) that still
     holds the target px/m;
  B  a node near the frame edge (0.6-0.95 of the half-FOV) at about half the
     camera's farthest target-holding distance, still holding the target;
  C  the farthest visible node at Detection (the edge of the camera's reach);
  B2 the same as B on the opposite side of the frame;
  A2 near the axis at about half of A's distance;
  B3 near the frame edge at about 30 % of the farthest target distance.
Nodes inside the protected area (ROI) are preferred; a point outside it says so.
A camera that holds the target at no visible node has NO target point: that is
a finding and the row says it. No Tk.
"""
from __future__ import annotations

import csv
import datetime as _dt
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .viewshed_3d import ZONE_DETECT

PERSON_M = 1.8
BOARD_M = 0.5
PLATE_M = 0.52
PLATE_PPM = 143.0          # TR plate / ANPR density (models.DEFAULT_LEVELS "ANPR_Alg"): a plate row only above it
MAX_POINTS = 6
_ORDER = ("A", "B", "C", "B2", "A2", "B3")
_KIND_TEXT = {
    "A": "Eksen, hedefi tutan en uzak", "B": "Kadraj kenarı, orta menzil", "C": "Algılama sınırı (en uzak)",
    "B2": "Karşı kenar, orta menzil", "A2": "Eksen, orta menzil", "B3": "Kadraj kenarı, yakın",
}
_ARROW = "▲"          # ▲ marks a point in a data-dependent cell


@dataclass
class CheckPoint:
    point_id: str                       # "D12-K5-A"
    kind: str                           # A / B / C / B2 / A2 / B3
    kind_text: str
    x_m: float
    y_m: float
    lat: Optional[float]
    lon: Optional[float]
    node: Tuple[int, int]               # (row, col) of the terrain grid
    distance_m: float                   # ground distance from the pole
    slant_m: float                      # engine slant distance to the node (lens to ground node)
    bearing_deg: float                  # compass bearing from the pole
    off_axis_deg: float
    dz_m: float                         # node elevation minus pole ground elevation
    ppm: float                          # engine node value, unchanged
    level: str                          # DORI level the node reaches
    level_code: int
    px_person: float
    px_board: float
    px_plate: Optional[float]
    in_roi: bool
    uncertain: bool = False             # inside a data-dependent (sensitivity) cell
    note: str = ""

    def band(self, px: Optional[float], tol: float) -> Optional[Tuple[float, float]]:
        return None if px is None else (px * (1.0 - tol), px * (1.0 + tol))


@dataclass
class CameraRow:
    index: int                          # 1-based, plan.placed_cameras order
    pole_id: int
    model: str
    focal_mm: float
    mast_m: float
    pan_deg: float
    tilt_deg: float
    hfov_deg: float
    x_m: float
    y_m: float
    lat: Optional[float]
    lon: Optional[float]
    ground_z_m: float
    is_ptz: bool = False
    patrol_span_deg: float = 0.0
    range_m: float = 0.0
    points: List[CheckPoint] = field(default_factory=list)
    note: str = ""
    no_target_point: bool = False

    @property
    def cam_id(self) -> str:
        return f"D{self.pole_id}-" + (f"PTZ{self.index}" if self.is_ptz else f"K{self.index}")


@dataclass
class AcceptanceSheet:
    header: Dict[str, str]
    rows: List[CameraRow]
    method: List[str]
    tolerance: float
    person_height_m: float
    target_ppm: float
    level_label: str
    measured: bool
    has_latlon: bool
    sens_marks: bool = False            # per-point marks were possible (the sensitivity result carried its grid)

    @property
    def fixed_rows(self) -> List[CameraRow]:
        return [r for r in self.rows if not r.is_ptz]

    @property
    def points(self) -> List[CheckPoint]:
        return [p for r in self.rows for p in r.points]

    def summary(self) -> Dict[str, Any]:
        fixed = self.fixed_rows
        return {"cameras": len(fixed), "ptz": len(self.rows) - len(fixed), "points": len(self.points),
                "no_target_cameras": [r.cam_id for r in fixed if r.no_target_point],
                "cameras_without_target_point": sum(1 for r in fixed if r.no_target_point),
                "uncertain_points": sum(1 for p in self.points if p.uncertain),
                "measured": self.measured, "has_latlon": self.has_latlon}


# ------------------------------------------------------------------ selection
def _wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


def _pick(cand: np.ndarray, free: np.ndarray, in_roi: np.ndarray, keys: Sequence[np.ndarray]) -> Optional[int]:
    """Index of the best candidate: protected-area nodes first, then lexicographic ``keys`` (first = primary)."""
    m = cand & free
    if not m.any():
        return None
    inside = m & in_roi
    if inside.any():
        m = inside
    idx = np.flatnonzero(m)
    order = np.lexsort(tuple(k[idx] for k in reversed(keys)))
    return int(idx[order[0]])


def _select(kinds: Sequence[str], arr: Dict[str, np.ndarray], target_ppm: float, hfov: float):
    """Return ({kind: (index, note)}, [notes]) over the candidate arrays of ONE camera."""
    d, off = arr["d"], arr["off"]
    aoff = np.abs(off)
    held = arr["ppm"] >= target_ppm - 1e-6              # visible already: only visible nodes are candidates
    detect = arr["dori"] >= ZONE_DETECT
    in_roi = arr["roi"]
    free = np.ones(d.shape, bool)
    half = max(hfov / 2.0, 1e-6)
    got: Dict[str, Tuple[int, str]] = {}
    notes: List[str] = []
    dmax = float(d[held].max()) if held.any() else 0.0

    def take(kind, i, note=""):
        if i is not None:
            got[kind] = (i, note)
            free[i] = False
        else:
            notes.append(f"{kind} noktası bulunamadı ({_KIND_TEXT[kind].lower()}: uygun görünür düğüm yok)")

    def axis_pick(near_d=None):
        for frac, tag in ((hfov / 6.0, ""), (hfov / 4.0, "eksenden biraz uzak"), (math.inf, "eksen dışı")):
            cand = held & (aoff <= frac)
            keys = [-d, aoff] if near_d is None else [np.abs(d - near_d), aoff]
            i = _pick(cand, free, in_roi, keys)
            if i is not None:
                return i, (f"{tag} (eksenden {aoff[i]:.0f}°)" if tag else "")
        return None, ""

    def edge_pick(target_d, side=0):
        for lo, hi in ((0.6, 0.95), (0.4, 1.0)):
            cand = held & (aoff >= lo * half) & (aoff <= hi * half)
            if side:
                cand = cand & (np.sign(off) == side)
            i = _pick(cand, free, in_roi, [np.abs(d - target_d), -aoff])
            if i is not None:
                return i
        return None

    edge_side = 0
    for kind in kinds:
        if kind == "A":
            i, nt = axis_pick()
            take("A", i, nt)
        elif kind == "C":
            i = _pick(detect, free, in_roi, [-d, aoff])
            take("C", i)
        elif kind == "A2":
            ref = got.get("A")
            i, nt = axis_pick(near_d=0.5 * (d[ref[0]] if ref else dmax))
            take("A2", i, nt)
        elif kind in ("B", "B2", "B3"):
            if kind == "B":
                i = edge_pick(0.5 * dmax)
                if i is not None:
                    edge_side = int(np.sign(off[i])) or 1
            elif kind == "B2":
                i = edge_pick(0.5 * dmax, side=-edge_side if edge_side else 0)
            else:
                i = edge_pick(0.3 * dmax, side=edge_side)
            take(kind, i)
    return got, notes


# ------------------------------------------------------------------ build
def _levels(camera) -> Dict[int, str]:
    from .plan_statement import levels_for
    return {z: label for z, _k, label in levels_for(camera, monitor=True)}


def _fmt_ppm(v: float) -> str:
    return f"{v:g}".replace(".", ",")


def build_acceptance(terrain, plan, camera, *, mv, placements: Optional[Sequence] = None,
                     roi: Optional[np.ndarray] = None, target_ppm: float, level: str = "plan",
                     points_per_camera: int = 3, tolerance: float = 0.15, sensitivity: Optional[Dict] = None,
                     project: str = "", weather: str = "", visibility_km: Optional[float] = None,
                     person_height_m: float = PERSON_M, target_height_m: Optional[float] = None,
                     band_text: str = "", ptz_camera=None, date: Optional[_dt.datetime] = None) -> AcceptanceSheet:
    """Acceptance sheet of ``plan``'s fixed cameras from the engine result ``mv``.

    ``mv``: the ``MultiViewshedResult`` of the plan's fixed poles (``focal_optimizer.band_area`` /
    ``perimeter.coverage``; ``per_camera[i]`` is ``plan.placed_cameras[i]``). ``roi``: the protected
    area mask (candidates inside it are preferred). ``level``: the label of the requirement
    (``target_ppm`` is its px/m). ``sensitivity``: ``perimeter.sensitivity``'s result; its ``class_grid``
    marks the points in data-dependent cells."""
    from .geo_export import GeoFrame

    if not 1 <= int(points_per_camera) <= MAX_POINTS:
        raise ValueError(f"Kamera başına nokta sayısı 1-{MAX_POINTS} aralığında olmalı.")
    if not 0.0 < tolerance < 1.0:
        raise ValueError("Tolerans 0 ile 1 arasında olmalı (ör. 0,15 = ±%15).")
    cams = list(plan.placed_cameras)
    per = list(getattr(mv, "per_camera", []) or [])
    if len(per) != len(cams):
        raise ValueError("Kapsama sonucu planla uyuşmuyor (kamera sayısı farklı): kapsamayı yeniden hesaplayın.")
    if placements is not None and len(placements) != len(cams):
        raise ValueError("Yerleşimler planla uyuşmuyor.")
    frame = GeoFrame(terrain)
    ll = (lambda x, y: frame.latlon(x, y)) if frame.has_latlon or frame.exact else (lambda x, y: (None, None))
    cell, ox, oy = float(terrain.cell_size_m), float(terrain.origin_x), float(terrain.origin_y)
    zlabel = _levels(camera)
    kinds = _ORDER[:int(points_per_camera)]
    tol = float(tolerance)

    grid = None
    sens_marks = False
    if sensitivity is not None and sensitivity.get("class_grid") is not None:
        g = np.asarray(sensitivity["class_grid"])
        if g.shape == mv.dori_grid.shape:
            grid, sens_marks = g, True

    rows: List[CameraRow] = []
    for i, (c, r) in enumerate(zip(cams, per), 1):
        lat, lon = ll(c.x_m, c.y_m)
        row = CameraRow(index=i, pole_id=c.pole_id, model=c.camera_model, focal_mm=c.focal_mm, mast_m=c.mast_height_m,
                        pan_deg=c.pan_deg % 360.0, tilt_deg=c.tilt_deg, hfov_deg=float(r.hfov_deg), x_m=c.x_m,
                        y_m=c.y_m, lat=lat, lon=lon, ground_z_m=c.ground_z_m)
        r0, r1, c0, c1 = r.window
        vis = r.visibility_window if r1 > r0 and c1 > c0 else np.zeros((0, 0), bool)
        rr, cc = np.nonzero(vis)
        if rr.size == 0:
            row.no_target_point = True
            row.note = "Bu kamera hiçbir yeri görmüyor (görüş hattı kapalı): hedef px/m tutulmuyor — bulgu."
            rows.append(row)
            continue
        gr, gc = r0 + rr, c0 + cc
        x = ox + gc * cell
        y = oy + gr * cell
        dx, dy = x - c.x_m, y - c.y_m
        arr = {"d": np.hypot(dx, dy), "off": _wrap180(np.degrees(np.arctan2(dx, dy)) - c.pan_deg),
               "ppm": r.ppm_window[rr, cc].astype(np.float64), "dori": r.dori_window[rr, cc],
               "roi": roi[gr, gc] if roi is not None else np.ones(rr.size, bool)}
        got, notes = _select(kinds, arr, target_ppm, row.hfov_deg)
        if "A" in kinds and "A" not in got and not (arr["ppm"] >= target_ppm - 1e-6).any():
            row.no_target_point = True
            row.note = (f"Bu kamera {_fmt_ppm(target_ppm)} px/m hedefini görünür hiçbir düğümde tutmuyor — bulgu: "
                        "direk yüksekliği, eğim ya da odak yeniden gözden geçirilmeli.")
            notes = [n for n in notes if not n.startswith(("A ", "B", "A2"))]
        elif notes:
            row.note = "; ".join(notes)
        for kind in kinds:
            if kind not in got:
                continue
            j, nt = got[kind]
            ppm = float(r.ppm_window[rr[j], cc[j]])
            code = int(r.dori_window[rr[j], cc[j]])
            px_p, px_b = ppm * person_height_m, ppm * BOARD_M
            plate = ppm * PLATE_M if ppm >= PLATE_PPM else None
            plat, plon = ll(float(x[j]), float(y[j]))
            z = float(terrain.z_grid[gr[j], gc[j]])
            pt = CheckPoint(
                point_id=f"{row.cam_id}-{kind}", kind=kind, kind_text=_KIND_TEXT[kind], x_m=float(x[j]), y_m=float(y[j]),
                lat=plat, lon=plon, node=(int(gr[j]), int(gc[j])), distance_m=float(arr["d"][j]),
                slant_m=float(r.slant_window[rr[j], cc[j]]), bearing_deg=float(np.degrees(np.arctan2(dx[j], dy[j])) % 360.0),
                off_axis_deg=float(arr["off"][j]), dz_m=z - float(c.ground_z_m), ppm=ppm,
                level=zlabel.get(code, "—"), level_code=code, px_person=px_p, px_board=px_b, px_plate=plate,
                in_roi=bool(arr["roi"][j]), note=nt)
            if roi is not None and not pt.in_roi:
                pt.note = (pt.note + "; " if pt.note else "") + "korunan alan dışında"
            if grid is not None and int(grid[gr[j], gc[j]]) == 2:        # sensitivity.CLASS_UNCERTAIN
                pt.uncertain = True
            row.points.append(pt)
        if not row.points and not row.note:
            row.no_target_point = True
            row.note = "Bu kamera için görünür nokta bulunamadı."
        rows.append(row)

    for k, c in enumerate(getattr(plan, "ptz_cameras", []) or [], 1):
        lat, lon = ll(c.x_m, c.y_m)
        span = getattr(c, "patrol_span_deg", 0.0)
        rows.append(CameraRow(
            index=k, pole_id=c.pole_id, model=c.camera_model, focal_mm=c.focal_mm, mast_m=c.mast_height_m,
            pan_deg=c.pan_deg % 360.0, tilt_deg=c.tilt_deg, hfov_deg=c.hfov_deg, x_m=c.x_m, y_m=c.y_m, lat=lat, lon=lon,
            ground_z_m=c.ground_z_m, is_ptz=True, patrol_span_deg=span, range_m=c.effective_range_m,
            note=("PTZ: kabul presetlerde doğrulanır — ev konumu ve devriye ayarları: "
                  f"yön {c.pan_deg % 360:.0f}° (devriye merkezi), {'360° dönüş' if span >= 359.9 else f'{span:.0f}° sektör'}, "
                  f"eğim {c.tilt_deg:.1f}°, odak {c.focal_mm:.1f} mm (geniş uç), teşhis erişimi {c.effective_range_m:.0f} m.")))

    return _sheet(terrain, plan, camera, rows, frame, sens_marks, sensitivity, tol, person_height_m, target_ppm, level,
                  project, weather, visibility_km, target_height_m, band_text, ptz_camera, date)


def _sheet(terrain, plan, camera, rows, frame, sens_marks, sens, tol, person, target_ppm, level, project, weather, vis,
           target_h, band_text, ptz_camera, date) -> AcceptanceSheet:
    measured = bool(getattr(terrain, "is_measured", False))
    n_fixed = sum(1 for r in rows if not r.is_ptz)
    n_ptz = len(rows) - n_fixed
    when = (date or _dt.datetime.now()).strftime("%d.%m.%Y")
    src = getattr(terrain, "source_note", "") or getattr(terrain, "name", "") or "arazi"
    head: Dict[str, str] = {
        "Belge": "Saha kabul föyü — kurulum sonrası ölçüm",
        "Proje": project or "—",
        "Tarih": when,
        "Plan özeti": f"{n_fixed} sabit kamera" + (f" + {n_ptz} PTZ" if n_ptz else "") + f" · {plan.pole_count} direk"
                      + f" · çit {plan.total_fence_length_m:.0f} m",
        "Kamera": f"{getattr(camera, 'model_name', '') or getattr(camera, 'name', '')} · "
                  f"{getattr(camera, 'sensor_name', '')} · {getattr(camera, 'resolution_name', '')}",
        "Hedef seviye": (f"{_fmt_ppm(target_ppm)} px/m" if level.replace(" ", "") == f"{_fmt_ppm(target_ppm)}px/m"
                         else f"{level} — {_fmt_ppm(target_ppm)} px/m"),
        "Korunan alan": band_text or "—",
        "Arazi": (f"ÖLÇÜLMÜŞ DEM — {src}" if measured else
                  f"TEMSİLİ arazi — ölçüm değil, noktalar bağlayıcı değil ({src})"),
        "Hedef boyu": (f"{target_h:.1f} m insan: görüş hattı ve kadraj başa göre".replace(".", ",", 1)
                       if target_h else "zemin noktası (insan boyu modellenmedi)"),
        "Hava / görüş": f"{weather or 'Berrak hava'}" + (f" (görüş {vis:g} km)".replace(".", ",") if vis else ""),
        "Tolerans": f"±%{tol * 100:g} (kabul bandı = beklenen px × (1 ± tolerans))".replace(".", ","),
        "Test hedefleri": f"kişi {person:g} m boy · pano {BOARD_M:g} m genişlik · TR plaka {PLATE_M:g} m (yalnız ≥ {PLATE_PPM:g} px/m)".replace(".", ","),
    }
    if not frame.has_latlon and not frame.exact:
        head["Konum"] = "Arazinin coğrafi konumu yok: yalnız yerel metre koordinatları (KML üretilemez)."
    if sens is not None:
        n_u = sum(1 for r in rows for p in r.points if p.uncertain)
        line = str(sens.get("line") or "")
        if sens_marks:
            head["Veri hassasiyeti"] = (f"{line} {_ARROW} ile işaretli {n_u} nokta 'veriye bağlı' hücrede: DEM / bina "
                                        "yüksekliği hatasında hedefi tutmama olasılığı yüksek, sahada ilk onlar başarısız olabilir.").strip()
        else:
            head["Veri hassasiyeti"] = f"{line} (sonuç ızgarası yok: noktalar işaretlenemedi — hassasiyeti yeniden çalıştırın)".strip()
    method = [
        "Noktaya 0,5 m genişliğinde bir pano ya da bir kişi koyun; kameraya dönük, dik ve tam görünür dursun.",
        "Gündüz, kameranın bu föyde yazılı odak uzaklığında ve dijital zoom kapalıyken, tam çözünürlükte bir durağan görüntü alın.",
        "Görüntüde panonun piksel genişliğini (kişide boyunu) sayın ve 'Ölçülen px' sütununa yazın.",
        "Ölçülen değer 'Kabul bandı' içindeyse G (geçti), dışındaysa K (kaldı) yazın; gerekirse 'Not' sütununa ekleyin.",
        "Nokta başarısızsa önce direk yüksekliğini, eğimi ve odak uzaklığını kontrol edin; sonra noktanın koordinatını "
        "(hücreyi) raporlayın: model o hücrede yanılmıştır.",
        f"{_ARROW} işareti: nokta veriye bağlı bir hücrede (DEM / bina yüksekliği belirsizliği) — başarısızlık orada en olasıdır."
        if sens is not None else "",
    ]
    return AcceptanceSheet(header=head, rows=rows, method=[m for m in method if m], tolerance=tol, person_height_m=person,
                           target_ppm=target_ppm, level_label=level, measured=measured,
                           has_latlon=frame.has_latlon or frame.exact, sens_marks=sens_marks)


# ------------------------------------------------------------------ tabular form
_COLS = ["Direk", "Kamera", "Model", "Odak (mm)", "Direk yük. (m)", "Yön (°)", "Eğim (°)", "HFOV (°)", "Kamera X (m)",
         "Kamera Y (m)", "Kamera enlem", "Kamera boylam", "Nokta", "Nokta türü", "Veriye bağlı", "X (m)", "Y (m)",
         "Enlem", "Boylam", "Mesafe (m)", "Yön (°)", "Yükseklik farkı (m)", "Beklenen px/m", "DORI",
         "Kişi px", "Kişi px alt", "Kişi px üst", "Pano px", "Pano px alt", "Pano px üst",
         "Plaka px", "Plaka px alt", "Plaka px üst", "Ölçülen px", "Sonuç (G/K)", "Not"]


def _n(v: Optional[float], nd: int = 1) -> str:
    return "" if v is None else f"{v:.{nd}f}"


def table_rows(sheet: AcceptanceSheet) -> List[List[str]]:
    """Flat rows for CSV / XLSX: one per check point; a camera with none (a finding, a PTZ) gets one row."""
    out: List[List[str]] = []
    tol = sheet.tolerance
    for r in sheet.rows:
        cam = [str(r.pole_id), r.cam_id, r.model, _n(r.focal_mm), _n(r.mast_m), _n(r.pan_deg, 0), _n(r.tilt_deg),
               _n(r.hfov_deg), _n(r.x_m), _n(r.y_m), _n(r.lat, 6), _n(r.lon, 6)]
        if not r.points:
            out.append(cam + [""] * (len(_COLS) - len(cam) - 1) + [r.note])
            continue
        for p in r.points:
            def trio(px):
                b = p.band(px, tol)
                return [_n(px, 0), _n(b[0], 0), _n(b[1], 0)] if b else ["", "", ""]
            note = "; ".join(x for x in (p.note, r.note if p is r.points[0] else "") if x)
            out.append(cam + [p.point_id, p.kind_text, _ARROW if p.uncertain else "", _n(p.x_m), _n(p.y_m),
                              _n(p.lat, 6), _n(p.lon, 6), _n(p.distance_m), _n(p.bearing_deg, 0), _n(p.dz_m),
                              _n(p.ppm, 2), p.level] + trio(p.px_person) + trio(p.px_board) + trio(p.px_plate)
                       + ["", "", note])
    return out


# ------------------------------------------------------------------ writers
def write_csv(path: str | Path, sheet: AcceptanceSheet) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        for k, v in sheet.header.items():
            w.writerow([f"# {k}", v])
        for m in sheet.method:
            w.writerow(["# Yöntem", m])
        w.writerow([])
        w.writerow(_COLS)
        w.writerows(table_rows(sheet))


def write_xlsx(path: str | Path, sheet: AcceptanceSheet) -> None:
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise RuntimeError("Excel aktarımı için 'openpyxl' kütüphanesi gerekli.") from None
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Kabul föyü"
    bold = Font(bold=True)
    for k, v in sheet.header.items():
        ws.append([k, v])
        ws.cell(ws.max_row, 1).font = bold
    ws.append([])
    ws.append(["Yöntem"])
    ws.cell(ws.max_row, 1).font = bold
    for i, m in enumerate(sheet.method, 1):
        ws.append([f"{i}.", m])
    ws.append([])
    ws.append(_COLS)
    top = ws.max_row
    fill = PatternFill("solid", fgColor="002D62")
    for c in ws[top]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = fill
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    for row in table_rows(sheet):
        ws.append(row)
    ws.freeze_panes = ws.cell(top + 1, 14)
    for i, name in enumerate(_COLS, 1):
        ws.column_dimensions[get_column_letter(i)].width = 44 if name == "Not" else 26 if name == "Model" else 12
    wb.save(path)


def _pdf_glyphs_ok(font_name: str, chars: str) -> bool:
    try:
        from reportlab.pdfbase import pdfmetrics
        face = pdfmetrics.getFont(font_name).face
        return all(ord(ch) in face.charToGlyph for ch in chars)
    except Exception:  # noqa: BLE001 -- the built-in Helvetica has no charToGlyph: assume no
        return False


def write_pdf(path: str | Path, sheet: AcceptanceSheet) -> None:
    """Landscape A4 sheet (ReportLab, the engineering report's look); plain-text PDF without ReportLab."""
    from . import exporters as ex

    now = _dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    if not ex._REPORTLAB_AVAILABLE:
        lines = [f"{k}: {v}" for k, v in sheet.header.items()] + [""] + [f"{i}. {m}" for i, m in enumerate(sheet.method, 1)] + [""]
        for r in sheet.rows:
            lines.append(f"{r.cam_id} · {r.model} · {r.focal_mm:.1f} mm · direk {r.mast_m:.1f} m · yön {r.pan_deg:.0f}° · eğim {r.tilt_deg:.1f}°")
            for p in r.points:
                b = p.band(p.px_person, sheet.tolerance)
                lines.append(f"   {p.point_id}{' ' + _ARROW if p.uncertain else ''}  {p.distance_m:.0f} m  {p.ppm:.1f} px/m  "
                             f"{p.level}  kişi {b[0]:.0f}-{b[1]:.0f} px  Ölçülen: ____  Sonuç: ____")
            if r.note:
                lines.append("   " + r.note)
        ex.write_simple_pdf(str(path), "SAHA KABUL FÖYÜ", lines)
        return

    from reportlab.lib import colors as C
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ex._ensure_reportlab_fonts()
    F, FB = ex._FONT_FAMILY, ex._FONT_FAMILY_BOLD
    arrow = _ARROW if _pdf_glyphs_ok(F, _ARROW) else "(!)"
    tick = "G / K"
    esc = ex._esc
    W, H = landscape(A4)
    M = 28
    pw = W - 2 * M
    navy, light, grid = C.HexColor("#002D62"), C.HexColor("#F4F6F9"), C.HexColor("#CFD8DC")

    class _Canvas(ex.rl_canvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._pages: list = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            n = len(self._pages)
            for st in self._pages:
                self.__dict__.update(st)
                self.setFillColor(grid)
                self.rect(M, 30, pw, 0.75, fill=True, stroke=False)
                self.setFont(FB, 7)
                self.setFillColor(navy)
                self.drawString(M, 20, "SAHA KABUL FÖYÜ")
                self.setFont(F, 7)
                self.setFillColor(C.HexColor("#546E7A"))
                self.drawCentredString(W / 2, 20, f"Hazırlanma: {now}")
                self.drawRightString(W - M, 20, f"Sayfa {self._pageNumber} / {n}")
                super().showPage()
            super().save()

    S = {
        "title": ParagraphStyle("T", fontName=FB, fontSize=15, leading=18, textColor=C.white),
        "sec": ParagraphStyle("H", fontName=FB, fontSize=10.5, leading=13, textColor=navy, spaceBefore=6, spaceAfter=3),
        "c": ParagraphStyle("C", fontName=F, fontSize=7, leading=8.6, textColor=C.HexColor("#212529")),
        "cb": ParagraphStyle("CB", fontName=FB, fontSize=7, leading=8.6, textColor=C.HexColor("#212529")),
        "k": ParagraphStyle("K", fontName=FB, fontSize=8, leading=10, textColor=C.HexColor("#212529")),
        "v": ParagraphStyle("V", fontName=F, fontSize=8, leading=10, textColor=C.HexColor("#212529")),
        "h": ParagraphStyle("Hd", fontName=FB, fontSize=7, leading=8.6, textColor=C.white, alignment=1),
        "cam": ParagraphStyle("Cm", fontName=FB, fontSize=7.4, leading=9, textColor=navy),
        "m": ParagraphStyle("M", fontName=F, fontSize=8, leading=10.5, textColor=C.HexColor("#212529")),
    }
    story: list = []
    banner = Table([[Paragraph("SAHA KABUL FÖYÜ — KURULUM SONRASI ÖLÇÜM", S["title"])]], colWidths=[pw])
    banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), navy), ("TOPPADDING", (0, 0), (-1, -1), 8),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("LEFTPADDING", (0, 0), (-1, -1), 10)]))
    story += [banner, Spacer(1, 6)]

    items = list(sheet.header.items())[1:]                      # "Belge" is the banner
    half = (len(items) + 1) // 2
    cols = [items[:half], items[half:]]
    cells = []
    for i in range(half):
        line = []
        for col in cols:
            if i < len(col):
                line += [Paragraph(esc(col[i][0]), S["k"]), Paragraph(esc(col[i][1]), S["v"])]
            else:
                line += [Paragraph("", S["k"]), Paragraph("", S["v"])]
        cells.append(line)
    kv = Table(cells, colWidths=[pw * 0.10, pw * 0.40, pw * 0.10, pw * 0.40])
    kv.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), light), ("BACKGROUND", (2, 0), (2, -1), light),
                            ("BOX", (0, 0), (-1, -1), 0.75, navy), ("INNERGRID", (0, 0), (-1, -1), 0.25, grid),
                            ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                            ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(kv)
    if not sheet.measured:
        warn = Table([[Paragraph("UYARI — Arazi ölçülmüş değil (temsili): bu föydeki noktalar bir model çıktısıdır, "
                                 "bağlayıcı değildir; gerçek DEM ile yeniden hesaplanmalıdır.", S["cb"])]], colWidths=[pw])
        warn.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), C.HexColor("#F8D7DA")),
                                  ("BOX", (0, 0), (-1, -1), 1, C.HexColor("#842029")), ("TOPPADDING", (0, 0), (-1, -1), 4),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
        story += [Spacer(1, 4), warn]
    method = Table([[Paragraph("Ölçüm yöntemi ve başarısız noktada ne yapılır", S["sec"])]] +
                   [[Paragraph(esc(f"{i}. {m}"
                                   .replace(_ARROW, arrow)), S["m"])] for i, m in enumerate(sheet.method, 1)], colWidths=[pw])
    method.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.75, navy), ("BACKGROUND", (0, 0), (-1, -1), light),
                                ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                                ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    story += [Spacer(1, 6), method, Spacer(1, 8)]

    has_plate = any(p.px_plate is not None for p in sheet.points)
    head = ["Nokta", "Konum (yerel m · enlem / boylam)", "Mesafe\n(m)", "Yön\n(°)", "Δh\n(m)", "px/m", "DORI",
            f"Kişi {sheet.person_height_m:g} m\nbeklenen px (bant)", "Pano 0,5 m\nbeklenen px (bant)"]
    widths = [58, 118, 32, 28, 28, 32, 56, 78, 78]
    if has_plate:
        head.append("Plaka 0,52 m\nbeklenen px (bant)")
        widths.append(78)
    head += ["Ölçülen\npx", f"Sonuç\n{tick}", "Not"]
    widths += [44, 38, 0]
    widths[-1] = pw - sum(widths)

    def band(p, px):
        b = p.band(px, sheet.tolerance)
        return f"{px:.0f} ({b[0]:.0f}–{b[1]:.0f})" if b else "—"

    data = [[Paragraph(esc(h).replace("\n", "<br/>"), S["h"]) for h in head]]
    spans, nosplit = [], []
    for r in sheet.rows:
        ident = (f"<b>{esc(r.cam_id)}</b> · {esc(r.model)} · odak {r.focal_mm:.1f} mm · direk {r.mast_m:.1f} m · "
                 f"yön {r.pan_deg:.0f}° · eğim {r.tilt_deg:.1f}° · HFOV {r.hfov_deg:.0f}° · "
                 f"({r.x_m:.0f}, {r.y_m:.0f})" + (f" · {r.lat:.5f}, {r.lon:.5f}" if r.lat is not None else ""))
        start = len(data)
        data.append([Paragraph(ident, S["cam"])] + [""] * (len(head) - 1))
        spans.append(("SPAN", (0, start), (-1, start)))
        if r.note and (not r.points or r.no_target_point or r.is_ptz):
            data.append([Paragraph(esc(r.note), S["c"])] + [""] * (len(head) - 1))
            spans.append(("SPAN", (0, len(data) - 1), (-1, len(data) - 1)))
        for p in r.points:
            loc = f"{p.x_m:.0f}, {p.y_m:.0f}" + (f"<br/>{p.lat:.6f}, {p.lon:.6f}" if p.lat is not None else "")
            row = [Paragraph(esc(p.point_id) + (f" {arrow}" if p.uncertain else "") + f"<br/>{esc(p.kind_text)}", S["c"]),
                   Paragraph(loc, S["c"]), Paragraph(f"{p.distance_m:.0f}", S["c"]), Paragraph(f"{p.bearing_deg:.0f}", S["c"]),
                   Paragraph(f"{p.dz_m:+.1f}", S["c"]), Paragraph(f"{p.ppm:.1f}", S["cb"]), Paragraph(esc(p.level), S["c"]),
                   Paragraph(band(p, p.px_person), S["c"]), Paragraph(band(p, p.px_board), S["c"])]
            if has_plate:
                row.append(Paragraph(band(p, p.px_plate), S["c"]))
            row += [Paragraph("", S["c"]), Paragraph("", S["c"]), Paragraph(esc(p.note), S["c"])]
            data.append(row)
        nosplit.append(("NOSPLIT", (0, start), (-1, len(data) - 1)))
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), navy), ("BOX", (0, 0), (-1, -1), 0.5, navy),
             ("INNERGRID", (0, 0), (-1, -1), 0.25, grid), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
             ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
    for a, b in ((s[1][1], s[2][1]) for s in spans):
        style.append(("BACKGROUND", (0, a), (-1, a), light))
    t.setStyle(TableStyle(style + spans + nosplit))
    story.append(t)

    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=M, rightMargin=M, topMargin=28, bottomMargin=40,
                            title="Saha kabul föyü")
    doc.build(story, canvasmaker=_Canvas)


def build_kml(sheet: AcceptanceSheet, terrain, *, name: str = "Saha kabul noktaları") -> str:
    """Placemarks: every pole of a fixed camera and its check points (geo_export's frame, so they sit on the plan's KML)."""
    from xml.sax.saxutils import escape

    from . import geo_export as g

    f = g.GeoFrame(terrain)
    f.latlon(0.0, 0.0)                              # fails loudly (same message as the other KML exports) with no position
    styles = g._STYLES + ('<Style id="chk"><IconStyle><color>ff3ac0f0</color><scale>0.8</scale>'
                          '<Icon><href>http://maps.google.com/mapfiles/kml/shapes/target.png</href></Icon></IconStyle>'
                          '<LabelStyle><scale>0.7</scale></LabelStyle></Style>'
                          '<Style id="chku"><IconStyle><color>ff2020e0</color><scale>0.9</scale>'
                          '<Icon><href>http://maps.google.com/mapfiles/kml/shapes/caution.png</href></Icon></IconStyle>'
                          '<LabelStyle><scale>0.7</scale></LabelStyle></Style>')
    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
             f"<name>{escape(name)}</name><description>{escape(g._source_line(terrain, f))}</description>", styles,
             "<Folder><name>Direkler</name>"]
    seen = set()
    for r in sheet.fixed_rows:
        if r.pole_id in seen:
            continue
        seen.add(r.pole_id)
        parts.append(g._placemark(f"D{r.pole_id}", "pole", g._point(f, r.x_m, r.y_m),
                                  f"{r.model} · {r.focal_mm:.1f} mm · yön {r.pan_deg:.0f}° · eğim {r.tilt_deg:.1f}°"))
    parts.append("</Folder><Folder><name>Kabul noktaları</name>")
    for r in sheet.fixed_rows:
        for p in r.points:
            b = p.band(p.px_person, sheet.tolerance)
            desc = (f"{p.kind_text} · mesafe {p.distance_m:.0f} m · yön {p.bearing_deg:.0f}° · {p.ppm:.1f} px/m · {p.level} · "
                    f"kişi {p.px_person:.0f} px (bant {b[0]:.0f}–{b[1]:.0f}) · pano {p.px_board:.0f} px"
                    + (" · VERİYE BAĞLI HÜCRE" if p.uncertain else ""))
            parts.append(g._placemark(p.point_id, "chku" if p.uncertain else "chk", g._point(f, p.x_m, p.y_m), desc))
    parts.append("</Folder></Document></kml>")
    return "\n".join(parts)


def build_dxf(sheet: AcceptanceSheet, terrain) -> str:
    """DXF R12 in local metres: POINT + circle + label per check point, poles on their own layer."""
    from . import geo_export as g

    W, H = terrain.width_m, terrain.height_m
    th = max(1.5, min(W, H) / 250.0)
    d = g._Dxf()
    d.g(0, "SECTION"); d.g(2, "HEADER")
    d.g(9, "$ACADVER"); d.g(1, "AC1009")
    d.g(9, "$INSUNITS"); d.g(70, 6)
    d.g(9, "$EXTMIN"); d.g(10, 0.0); d.g(20, 0.0); d.g(30, 0.0)
    d.g(9, "$EXTMAX"); d.g(10, float(W)); d.g(20, float(H)); d.g(30, 0.0)
    d.g(0, "ENDSEC")
    d.g(0, "SECTION"); d.g(2, "TABLES")
    d.g(0, "TABLE"); d.g(2, "LTYPE"); d.g(70, 1)
    d.g(0, "LTYPE"); d.g(2, "CONTINUOUS"); d.g(70, 0); d.g(3, "Solid line"); d.g(72, 65); d.g(73, 0); d.g(40, 0.0)
    d.g(0, "ENDTAB")
    layers = (("DIREK", 3), ("KABUL", 2), ("KABUL_VERIYE_BAGLI", 1), ("ETIKET", 7))
    d.g(0, "TABLE"); d.g(2, "LAYER"); d.g(70, len(layers))
    for nm, col in layers:
        d.g(0, "LAYER"); d.g(2, nm); d.g(70, 0); d.g(62, col); d.g(6, "CONTINUOUS")
    d.g(0, "ENDTAB"); d.g(0, "ENDSEC")
    d.g(0, "SECTION"); d.g(2, "ENTITIES")
    seen = set()
    for r in sheet.fixed_rows:
        if r.pole_id not in seen:
            seen.add(r.pole_id)
            d.circle("DIREK", (r.x_m, r.y_m), th * 0.6)
            d.text("ETIKET", (r.x_m + th, r.y_m + th), th, g._dxf_ascii(f"D{r.pole_id}"))
        for p in r.points:
            layer = "KABUL_VERIYE_BAGLI" if p.uncertain else "KABUL"
            d.g(0, "POINT"); d.g(8, layer)
            d.g(10, float(p.x_m)); d.g(20, float(p.y_m)); d.g(30, 0.0)
            d.circle(layer, (p.x_m, p.y_m), th * 0.4)
            d.text("ETIKET", (p.x_m + th * 0.6, p.y_m + th * 0.6), th * 0.7,
                   g._dxf_ascii(f"{p.point_id} {p.ppm:.0f}px/m"))
    d.g(0, "ENDSEC"); d.g(0, "EOF")
    return d.out.getvalue()


FORMATS = ("csv", "pdf", "xlsx", "kml", "dxf")


def write_acceptance(path: str | Path, sheet: AcceptanceSheet, fmt: str, terrain=None) -> None:
    """Write one format. KML needs ``terrain`` with a real location (ValueError otherwise, as the other KML exports)."""
    if fmt == "csv":
        write_csv(path, sheet)
    elif fmt == "xlsx":
        write_xlsx(path, sheet)
    elif fmt == "pdf":
        write_pdf(path, sheet)
    elif fmt == "kml":
        Path(path).write_text(build_kml(sheet, terrain), encoding="utf-8")
    elif fmt == "dxf":
        Path(path).write_text(build_dxf(sheet, terrain), encoding="ascii", newline="\r\n")
    else:
        raise ValueError(f"Biçim şunlardan biri olmalı: {', '.join(FORMATS)}")
