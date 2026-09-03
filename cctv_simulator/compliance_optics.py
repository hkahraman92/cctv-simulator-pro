"""Physics-backed spec review.

Pulls DORI / pixel-density / range requirements out of the spec text
("30 m'de teşhis", "125 PPM @ 40 m", "plaka 25 m") and checks each candidate
camera model against them with the actual optic engine
(``calculations.calculate_for_camera``) instead of a datasheet keyword match.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

from .calculations import calculate_for_camera, ground_distance_for_ppm
from .compliance_standards import DORI_PPM, clause_for, dori_ppm_for_task
from .config import RESOLUTIONS, SENSOR_DIMS_MM
from .models import DEFAULT_LEVELS, CameraConfig

# A standard review geometry when the spec gives none. Modest pole so slant
# range ~ ground range; the pixel density is dominated by focal/sensor/res.
_STD_POLE_M = 3.0
_STD_TARGET_M = 1.6

_TASK_WORDS = r"(teşhis|kimlik|tanı(?:ma)?|gözlem|takip|algıla(?:ma)?|tespit|izleme|kontrol|plaka|anpr|lpr|yüz\s*tanıma|yüz\s*teşhis|yüz\s*tespit)"
# distance: "30 m", "30m", "30 metre", "30 metrede", "30 metrelik" — but not "30 mm"
_DIST = r"(\d+(?:[.,]\d+)?)\s*(?:metre\w{0,5}|m)(?![a-zğüşıöçA-ZĞÜŞİÖÇ])"
_PPM = r"(\d+(?:[.,]\d+)?)\s*(?:ppm|px/m|piksel\s*/\s*metre|piksel/m)"


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def extract_dori_requirements(spec_text: str, profile_id: str = "P1",
                              profile_name: str = "Genel kamera isterleri") -> List[Dict[str, Any]]:
    reqs: List[Dict[str, Any]] = []
    seen: set = set()
    rid = 1

    def add(task_key: str, ppm: float, dist: float, quote: str):
        nonlocal rid
        key = (task_key, round(ppm, 1), round(dist, 1))
        if key in seen or dist <= 0 or dist > 5000 or ppm <= 0:
            return
        seen.add(key)
        clause, clause_desc = clause_for("dori")
        reqs.append({
            "id": f"{profile_id}-D{rid}",
            "profile_id": profile_id,
            "profile_name": profile_name,
            "category": "dori",
            "requirement": f"{dist:g} m mesafede {task_key} (≥ {ppm:g} px/m)",
            "task": task_key,
            "required_ppm": ppm,
            "distance_m": dist,
            "weight": 5,
            "confidence": 0.9,
            "spec_quote": quote.strip().replace("\n", " ")[:180],
            "standard_clause": clause,
            "standard_desc": clause_desc,
        })
        rid += 1

    # "<task> ... <dist> m"  and  "<dist> m ... <task>"
    for m in re.finditer(_TASK_WORDS + r"[^.\n]{0,40}?" + _DIST, spec_text, re.IGNORECASE):
        hit = dori_ppm_for_task(m.group(1))
        if hit:
            add(hit[0], hit[1], _num(m.group(2)), m.group(0))
    for m in re.finditer(_DIST + r"[^.\n]{0,40}?" + _TASK_WORDS, spec_text, re.IGNORECASE):
        hit = dori_ppm_for_task(m.group(2))
        if hit:
            add(hit[0], hit[1], _num(m.group(1)), m.group(0))

    # explicit "<ppm> PPM ... <dist> m" (either order)
    for m in re.finditer(_PPM + r"[^.\n]{0,40}?" + _DIST, spec_text, re.IGNORECASE):
        add(f"{_num(m.group(1)):g} px/m", _num(m.group(1)), _num(m.group(2)), m.group(0))
    for m in re.finditer(_DIST + r"[^.\n]{0,40}?" + _PPM, spec_text, re.IGNORECASE):
        add(f"{_num(m.group(2)):g} px/m", _num(m.group(2)), _num(m.group(1)), m.group(0))

    return reqs


def _best_key(want: str, options: List[str]) -> Optional[str]:
    w = want.strip().lower()
    for o in options:
        if o.lower() == w:
            return o
    for o in options:
        if w and (w in o.lower() or o.lower() in w):
            return o
    return None


def camera_from_model(model: Dict[str, Any]) -> Optional[CameraConfig]:
    """CameraConfig from a camera-library entry, or None if it lacks optics."""
    fmin = model.get("focal_min_mm")
    if not isinstance(fmin, (int, float)):
        return None
    fmin = float(fmin)
    fmax = float(model.get("focal_max_mm", fmin) or fmin)
    if fmax < fmin:
        fmin, fmax = fmax, fmin

    sensor = _best_key(str(model.get("sensor_name", "")), list(SENSOR_DIMS_MM)) or '1/2.8"'
    res = _best_key(str(model.get("resolution_name", "")), list(RESOLUTIONS)) or "4 MP (2K - 2688x1520)"
    return CameraConfig(
        name=str(model.get("model_name", "model")),
        model_name=str(model.get("model_name", "model")),
        sensor_name=sensor,
        resolution_name=res,
        focal_min_mm=fmin,
        focal_max_mm=fmax,
        pole_height_m=_STD_POLE_M,
        target_height_m=_STD_TARGET_M,
        tilt_deg=6.0,
        ir_range_m=float(model.get("ir_range_m", 0.0) or 0.0),
        effective_px_ratio=float(model.get("effective_px_ratio", 1.0) or 1.0),
    )


def evaluate_dori_requirement(model_name: str, model: Dict[str, Any],
                              req: Dict[str, Any], mount_height_m: float = _STD_POLE_M
                              ) -> Tuple[str, str]:
    cam = camera_from_model(model)
    if cam is None:
        return "Bulunamadı", "Broşürde odak/sensör verisi yok — optik doğrulama yapılamadı."
    cam.pole_height_m = max(mount_height_m, cam.target_height_m + 0.3)

    need_ppm = float(req["required_ppm"])
    need_dist = float(req["distance_m"])
    # Aim the camera at the target range so the whole frame does not clip the
    # ground short of it — this is a resolution question, not a placement one.
    drop = max(cam.pole_height_m - cam.target_height_m, 0.05)
    cam.tilt_deg = max(math.degrees(math.atan(drop / need_dist)), 0.2)

    tele = calculate_for_camera(cam, "max", DEFAULT_LEVELS)
    wide = calculate_for_camera(cam, "min", DEFAULT_LEVELS)
    reach_tele = ground_distance_for_ppm(tele, need_ppm)
    reach_wide = ground_distance_for_ppm(wide, need_ppm)

    geo = f"{cam.focal_min_mm:g}-{cam.focal_max_mm:g} mm lens, {cam.pole_height_m:g} m direk"
    px = f"{need_ppm:g} px/m"
    if reach_tele >= need_dist:
        margin = reach_tele / need_dist
        if reach_wide >= need_dist:
            return "Uyumlu", f"{geo}: her odakta {need_dist:g} m'de ≥ {px} sağlanıyor (dar uçta {reach_tele:.0f} m'ye kadar)."
        return "Uyumlu", f"{geo}: dar uçta {reach_tele:.0f} m menzilde {px}, {need_dist:g} m için {margin:.1f}× pay. Geniş açıda {reach_wide:.0f} m."
    if reach_tele >= need_dist * 0.8:
        return "Kısmi", f"{geo}: dar uçta yalnız {reach_tele:.0f} m'de {px} (gereken {need_dist:g} m). Sınırda."
    return "Uyumsuz", f"{geo}: dar uçta {reach_tele:.0f} m'de {px} — {need_dist:g} m'de {px} sağlanamıyor (fizik)."


assert DORI_PPM  # import sanity
