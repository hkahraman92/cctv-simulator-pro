"""EN 62676-4 optics bench verdict: does a camera resolve a task at a distance?

The one place that decides pass / fail for "required px/m at a distance", so
the classic workbench (``ui/modern_window``), the core service and the new
shell can never disagree. All physics comes from the optic engine
(``ppm_at_distance`` / ``ground_distance_for_ppm``, Rule 1); this only
combines it with weather, IR range and light.

No Tk.
"""
from __future__ import annotations

from typing import List, Tuple

from .atmosphere import WEATHER_PRESETS, band_for_camera, usable_range_m
from .calculations import ground_distance_for_ppm, ppm_at_distance
from .models import CameraConfig, OpticResult

# EN 62676-4 DORI tasks (px/m across the target)
TASKS = {
    "Teşhis (Identification)": 250.0,
    "Tanıma (Recognition)": 125.0,
    "Gözlem (Observation)": 62.5,
    "Algılama (Detection)": 25.0,
}

# Reasons that only hold after dark start with this. The bench answers day and
# night at once; a camera that resolves the task in daylight but not under IR
# must not read as a plain "fails" (27 px/m against 25 looked like a bug).
NIGHT = "gece: "


def day_reasons(reasons: List[str]) -> List[str]:
    """The reasons that also fail in daylight."""
    return [r for r in reasons if not r.startswith(NIGHT)]


def evaluate_requirement(camera: CameraConfig, result: OpticResult, req_ppm: float, req_dist: float,
                         weather: str = "") -> Tuple[float, float, List[str]]:
    """(achieved px/m, atmospheric range limit, failure reasons) at ``req_dist``.

    An empty reason list means the requirement is met day and night; reasons
    prefixed ``NIGHT`` fail only after dark (``day_reasons`` drops them). The
    atmospheric limit is +inf in clear air (visibility >= 20 km).
    """
    achieved = ppm_at_distance(result, req_dist)
    vis_km = WEATHER_PRESETS.get(weather, 40.0)
    atm_limit = float("inf")
    if vis_km < 20.0:
        band = band_for_camera(camera.sensor_name, camera.model_name)
        atm_limit = usable_range_m(ground_distance_for_ppm(result, req_ppm), vis_km, band, weather)

    reasons: List[str] = []
    if achieved < req_ppm:
        reasons.append("piksel yoğunluğu yetersiz")
    if req_dist > atm_limit:
        reasons.append(f"atmosferik menzil aşıldı ({weather})")
    if req_dist < result.dead_zone_m:
        reasons.append("kör noktada")
    if req_dist > result.max_geom_dist_m:
        reasons.append("geometrik limit ötesinde")
    if camera.ir_range_m > 0 and req_dist > camera.ir_range_m:
        reasons.append(f"{NIGHT}IR menzili dışında (IR {camera.ir_range_m:.0f} m)")
    if camera.min_lux > 0.05 and (camera.ir_range_m <= 0 or req_dist > camera.ir_range_m):
        reasons.append(f"{NIGHT}düşük ışıkta yetersiz (en az {camera.min_lux:g} lux)")
    return achieved, atm_limit, reasons
