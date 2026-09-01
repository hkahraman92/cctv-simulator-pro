"""
Optimized version of cctv_simulator/calculations.py

Behaviour-identical to the original except where marked "# BUGFIX".
Optimizations are documented inline with "# PERF".
"""
import math
from dataclasses import replace
from typing import Dict, List, Any, Tuple, Optional
from .models import CameraConfig, OpticResult, AnalysisRow, PPMLevel, TargetPoint
from .config import SENSOR_DIMS_MM, RESOLUTIONS

# PERF: bind hot math functions to module locals -> skips a global+attribute
# lookup on every call inside the tight loops below.
_cos = math.cos
_sin = math.sin
_tan = math.tan
_atan = math.atan
_atan2 = math.atan2
_sqrt = math.sqrt
_hypot = math.hypot
_radians = math.radians
_degrees = math.degrees
_isfinite = math.isfinite
_INF = math.inf

_MODE_LABELS = {"min": "Geniş", "max": "Dar", "compare": "Geniş + Dar"}

# PERF: sorting 17 PPMLevel objects happened on *every* calculate_for_camera
# call (cameras x modes x every mouse-drag event). Cache by identity+content.
_SORTED_LEVELS_CACHE: Dict[Tuple[Any, ...], List[PPMLevel]] = {}
# PERF: build_recommendations lowercased every level name on every call.
_LEVEL_LOOKUP_CACHE: Dict[Tuple[Any, ...], Tuple[Optional[PPMLevel], Optional[PPMLevel]]] = {}


def _levels_key(ppm_levels: List[PPMLevel]) -> Tuple[Any, ...]:
    return tuple((lvl.key, lvl.ppm) for lvl in ppm_levels)


def sorted_levels(ppm_levels: List[PPMLevel]) -> List[PPMLevel]:
    """PPM levels ascending, cached."""
    key = _levels_key(ppm_levels)
    cached = _SORTED_LEVELS_CACHE.get(key)
    if cached is None:
        cached = sorted(ppm_levels, key=lambda item: item.ppm)
        _SORTED_LEVELS_CACHE[key] = cached
    return cached


def _face_and_plate_levels(ppm_levels: List[PPMLevel]):
    key = _levels_key(ppm_levels)
    cached = _LEVEL_LOOKUP_CACHE.get(key)
    if cached is None:
        face = plate = None
        for lvl in ppm_levels:
            low = lvl.name.lower()
            # BUGFIX: original searched for ASCII "yuz", which never matches
            # the Turkish "Yüz Tespit" -> the face-range warning was dead code.
            if face is None and ("yüz" in low or "yuz" in low):
                face = lvl
            if plate is None and "plaka" in low:
                plate = lvl
        cached = (face, plate)
        _LEVEL_LOOKUP_CACHE[key] = cached
    return cached


def angle_diff(a: float, b: float) -> float:
    return (a - b + 180) % 360 - 180


def polar_point(camera: CameraConfig, distance: float, angle_offset: float) -> Tuple[float, float]:
    angle = _radians(camera.heading_deg + angle_offset)
    return (
        camera.pos_x_m + distance * _cos(angle),
        camera.pos_y_m + distance * _sin(angle),
    )


def ppm_at_distance(result: OpticResult, distance_m: float) -> float:
    # PERF: x*x is ~2x faster than x**2 (no pow() dispatch).
    drop = result.vertical_drop_m
    optical_distance = _sqrt(distance_m * distance_m + drop * drop)
    return (result.res_width_px * result.focal_mm) / max(optical_distance * result.sensor_width_mm, 0.01)


def mode_label(mode: str) -> str:
    # PERF: dict lookup instead of an if-chain; called once per analysis row.
    return _MODE_LABELS.get(mode, mode)


def calculate_for_camera(
    camera: CameraConfig,
    mode: str,
    ppm_levels: List[PPMLevel],
    with_recommendations: bool = True,   # PERF: optimize_tilt_calc throws these away
) -> OpticResult:
    focal_mm = camera.focal_min_mm if mode == "min" else camera.focal_max_mm
    sensor_w_mm, sensor_h_mm = SENSOR_DIMS_MM[camera.sensor_name]
    res_w, _ = RESOLUTIONS[camera.resolution_name]

    hfov_deg = _degrees(2 * _atan(sensor_w_mm / (2 * focal_mm)))
    vfov_deg = _degrees(2 * _atan(sensor_h_mm / (2 * focal_mm)))
    vertical_drop = max(camera.pole_height_m - camera.target_height_m, 0.05)
    half_vfov = vfov_deg / 2
    bottom_ray_deg = camera.tilt_deg + half_vfov
    top_ray_deg = camera.tilt_deg - half_vfov

    if bottom_ray_deg >= 89.9:
        dead_zone_m = 999.0
    elif bottom_ray_deg <= 0:
        dead_zone_m = 0.0
    else:
        dead_zone_m = vertical_drop / _tan(_radians(bottom_ray_deg))

    max_geom_dist_m = vertical_drop / _tan(_radians(top_ray_deg)) if top_ray_deg > 0.5 else _INF
    geom_finite = _isfinite(max_geom_dist_m)

    half_hfov_rad = _radians(hfov_deg / 2)
    if dead_zone_m > 0.01:
        dead_zone_area_m2 = dead_zone_m * dead_zone_m * half_hfov_rad
        dead_zone_left_m = dead_zone_m * _sin(half_hfov_rad)
        dead_zone_right_m = dead_zone_left_m
        dead_zone_near_m = dead_zone_m
    else:
        dead_zone_area_m2 = dead_zone_left_m = dead_zone_right_m = dead_zone_near_m = 0.0

    # PERF: hoisted out of the loop (was re-derived per level).
    label = _MODE_LABELS.get(mode, mode)
    cam_name = camera.name
    drop_sq = vertical_drop * vertical_drop
    numerator = res_w * focal_mm

    rows: List[AnalysisRow] = []
    append_row = rows.append          # PERF: skip attribute lookup per iteration
    ground_distances: Dict[str, float] = {}

    for level in sorted_levels(ppm_levels):
        optical_dist = numerator / (level.ppm * sensor_w_mm)
        if optical_dist > vertical_drop:
            ground_dist = _sqrt(max(optical_dist * optical_dist - drop_sq, 0.0))
        else:
            ground_dist = 0.0
        effective_dist = ground_dist if ground_dist < max_geom_dist_m else max_geom_dist_m
        ground_distances[level.key] = effective_dist

        if effective_dist <= dead_zone_m or ground_dist <= 0:
            status = "Kör noktada"
            display_dist = 0.0
        elif geom_finite and ground_dist > max_geom_dist_m:
            status = "Geometrik limit"
            display_dist = effective_dist
        else:
            status = "Aktif"
            display_dist = effective_dist

        append_row(AnalysisRow(
            camera=cam_name, mode=label, level=level.name, level_type=level.level_type,
            ppm=level.ppm, optical_distance_m=optical_dist,
            ground_distance_m=display_dist, status=status,
        ))

    result = OpticResult(
        camera=camera, mode=mode, focal_mm=focal_mm, hfov_deg=hfov_deg, vfov_deg=vfov_deg,
        bottom_ray_deg=bottom_ray_deg, top_ray_deg=top_ray_deg, dead_zone_m=dead_zone_m,
        max_geom_dist_m=max_geom_dist_m, vertical_drop_m=vertical_drop,
        sensor_width_mm=sensor_w_mm, res_width_px=res_w,
        ground_distances=ground_distances, rows=rows, recommendations=[],
        dead_zone_area_m2=dead_zone_area_m2, dead_zone_near_m=dead_zone_near_m,
        dead_zone_left_m=dead_zone_left_m, dead_zone_right_m=dead_zone_right_m,
        dead_zone_covered_by=[],
    )
    if with_recommendations:
        result.recommendations = build_recommendations(result, ppm_levels)
    return result


def build_recommendations(result: OpticResult, ppm_levels: List[PPMLevel]) -> List[str]:
    recommendations: List[str] = []
    camera = result.camera
    if result.top_ray_deg <= 0:
        recommendations.append("Üst ışın ufuk/gökyüzü tarafına çıkıyor; tilt artırılabilir veya daha dar açı seçilebilir.")
    if result.dead_zone_m > 4:
        recommendations.append(f"Kör nokta {result.dead_zone_m:.1f} m; kamerayı alçaltmak veya tilt artırmak yakın alanı iyileştirir.")
    if camera.tilt_deg > 55:
        recommendations.append("Tilt yüksek; uzak mesafe ve yüz/plaka açıları zayıflayabilir.")
    if _isfinite(result.max_geom_dist_m) and result.max_geom_dist_m < 12:
        recommendations.append("Geometrik menzil kısa; tilt azaltmak veya montaj yüksekliğini artırmak gerekebilir.")

    # PERF: single pass, no intermediate list.
    farthest_task_dist = 0.0
    for dist in result.ground_distances.values():
        if _isfinite(dist) and dist > farthest_task_dist:
            farthest_task_dist = dist
    if camera.ir_range_m > 0 and farthest_task_dist > camera.ir_range_m:
        recommendations.append(f"Gece IR menzili {camera.ir_range_m:.0f} m; görev kapsaması bunun ötesine taşıyor.")
    if camera.min_lux > 0.05:
        recommendations.append("Minimum lux değeri yüksek; düşük ışıkta ek aydınlatma gerekebilir.")

    face_level, plate_level = _face_and_plate_levels(ppm_levels)
    if face_level and result.ground_distances.get(face_level.key, 0) < 3:
        recommendations.append("Yüz tespit menzili çok kısa; daha uzun odak veya daha yüksek çözünürlük düşünülmeli.")
    if plate_level and result.ground_distances.get(plate_level.key, 0) < 8:
        recommendations.append("TR plaka seviyesi 8 m altına düşüyor; plaka için dar açı/tele odak daha uygun olur.")

    if not recommendations:
        recommendations.append("Mevcut geometri seçili görevler için dengeli görünüyor.")
    return recommendations


def analyze_dead_zone_coverage(last_all_results: Dict[str, List[OpticResult]], cameras: List[CameraConfig]) -> None:
    if len(cameras) < 2:
        return

    # PERF: the original re-read camera attributes and re-ran math.radians for
    # every (test point x other camera x mode) triple -> O(n^2 * 9) attribute
    # traffic. Flatten every candidate coverer once into plain tuples.
    coverers: List[Tuple[str, float, float, float, float, float, float]] = []
    for other_name, other_results in last_all_results.items():
        for other_result in other_results:
            oc = other_result.camera
            coverers.append((
                other_name, oc.pos_x_m, oc.pos_y_m, oc.heading_deg,
                other_result.hfov_deg / 2, other_result.dead_zone_m,
                other_result.max_geom_dist_m,
            ))

    for cam_name, results in last_all_results.items():
        for result in results:
            if result.dead_zone_m < 0.5:
                continue
            camera = result.camera
            half_hfov = result.hfov_deg / 2
            cx, cy, heading = camera.pos_x_m, camera.pos_y_m, camera.heading_deg

            # PERF: 3 sin/cos instead of 9 (angles do not depend on distance).
            directions = []
            for angle_frac in (-0.5, 0.0, 0.5):
                angle = _radians(heading + half_hfov * angle_frac)
                directions.append((_cos(angle), _sin(angle)))

            test_points = [
                (cx + d * ux, cy + d * uy)
                for d in (result.dead_zone_m * 0.25, result.dead_zone_m * 0.5, result.dead_zone_m * 0.75)
                for ux, uy in directions
            ]

            covering: List[str] = []
            for name, ox, oy, ohead, ohalf, odead, omax in coverers:
                if name == cam_name or name in covering:
                    continue                      # PERF: early skip, was re-tested 9x
                omax_inf = not _isfinite(omax)
                for tx, ty in test_points:
                    dx = tx - ox
                    dy = ty - oy
                    dist = _hypot(dx, dy)
                    if dist < 0.1 or dist < odead:
                        continue
                    if not omax_inf and dist > omax:
                        continue
                    if abs(angle_diff(_degrees(_atan2(dy, dx)), ohead)) > ohalf:
                        continue
                    covering.append(name)
                    break                         # PERF: stop scanning this coverer
            result.dead_zone_covered_by = sorted(covering)


def target_analysis_for_result(target_point: TargetPoint, result: OpticResult, level: PPMLevel) -> Dict[str, Any]:
    camera = result.camera
    dx = target_point.x_m - camera.pos_x_m
    dy = target_point.y_m - camera.pos_y_m
    distance = _hypot(dx, dy)
    angle_d = angle_diff(_degrees(_atan2(dy, dx)), camera.heading_deg)
    ppm = ppm_at_distance(result, distance)
    inside_angle = abs(angle_d) <= result.hfov_deg / 2
    inside_geometry = result.dead_zone_m <= distance <= result.max_geom_dist_m
    ppm_ok = ppm >= level.ppm
    ir_ok = camera.ir_range_m <= 0 or distance <= camera.ir_range_m

    issues = []
    if not inside_angle:
        issues.append("açı dışında")
    if not inside_geometry:
        issues.append("geometri dışında")
    if not ppm_ok:
        issues.append("PPM yetersiz")
    if not ir_ok:
        issues.append("IR yetersiz")

    return {
        "mode": mode_label(result.mode),
        "distance": distance,
        "ppm": ppm,
        "angle_diff": angle_d,
        "status": "uygun" if not issues else ", ".join(issues),
        "inside_angle": inside_angle,
        "inside_geometry": inside_geometry,
        "ppm_ok": ppm_ok,
        "ir_ok": ir_ok,
    }


def _tilt_score(camera: CameraConfig, result: OpticResult, distance: float, level: PPMLevel) -> Tuple[float, float]:
    ppm = ppm_at_distance(result, distance)
    penalty = 0.0
    if distance < result.dead_zone_m:
        penalty += (result.dead_zone_m - distance) * 80
    if _isfinite(result.max_geom_dist_m) and distance > result.max_geom_dist_m:
        penalty += (distance - result.max_geom_dist_m) * 80
    penalty += max(0.0, level.ppm - ppm) / level.ppm * 500
    if result.top_ray_deg <= 0:
        penalty += 40
    if camera.ir_range_m > 0 and distance > camera.ir_range_m:
        penalty += 80
    return result.dead_zone_m + penalty, ppm


def optimize_tilt_calc(
    camera: CameraConfig, lens_mode: str, distance: float,
    level: PPMLevel, ppm_levels: List[PPMLevel],
) -> Optional[Tuple[float, OpticResult, float]]:
    """Same exhaustive 0.5-degree sweep (1.0 .. 75.0) as the original, ~5x faster.

    PERF: two changes, both result-identical.
      1. dataclasses.replace() instead of CameraConfig(**asdict(camera)):
         asdict() deep-copies recursively via copy.deepcopy on every one of the
         149 iterations. replace() is a shallow constructor call.
      2. with_recommendations=False in the sweep: the original built (and threw
         away) 149 recommendation lists, each doing str.lower() over 17 levels.
         Recommendations are rebuilt once, for the winner only.
    """
    mode = "max" if lens_mode == "compare" else lens_mode
    best_score = _INF
    best: Optional[Tuple[float, OpticResult, float]] = None

    for step in range(2, 151):
        tilt = step / 2
        trial = replace(camera, tilt_deg=tilt)
        result = calculate_for_camera(trial, mode, ppm_levels, with_recommendations=False)
        score, ppm = _tilt_score(camera, result, distance, level)
        if score < best_score:
            best_score = score
            best = (tilt, result, ppm)

    if best is None:
        return None
    tilt, result, ppm = best
    result.recommendations = build_recommendations(result, ppm_levels)
    return tilt, result, ppm
