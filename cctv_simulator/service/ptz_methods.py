"""API v1: PTZ on the terrain — continuous sweep (revisit), cue response
(alarm -> identify time), and the mixed fixed + PTZ perimeter."""
from __future__ import annotations

import math
from typing import Any, Dict

import numpy as np

from .params import _camera_from
from .protocol import APP_ERROR, INVALID_PARAMS, Context, RpcError
from .terrain_methods import _DISPLAY_PX, _image, _num, _overlay_size, _terrain_of, _zone_areas


def _weather(params):
    from ..atmosphere import WEATHER_PRESETS
    w = str(params.get("weather", "")) if params.get("weather") in WEATHER_PRESETS else ""
    return w, WEATHER_PRESETS.get(w, 40.0)


def _fin(v: float):
    return v if math.isfinite(v) else None


def ptz_sweep(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """A continuously sweeping PTZ (360° rotation or a sector patrol): revisit map and DORI where seen."""
    from .. import map_render
    from ..ptz_schedule import Rotation360, SectorPatrol, evaluate_sweep

    entry, t = _terrain_of(params)
    cam = _camera_from(params.get("camera", {}))
    mode = params.get("mode", "rotation")
    speed = _num(params, "speed_deg_s", 20.0, lo=0.5, hi=400)
    tilt = _num(params, "tilt_deg", -5.0, lo=-89, hi=30)
    focal = params.get("focal_mm")
    if focal is not None:
        focal = _num(params, "focal_mm", lo=cam.focal_min_mm, hi=cam.focal_max_mm)
    if mode == "rotation":
        sweep = Rotation360(speed_deg_s=speed, tilt_deg=tilt, focal_mm=focal)
    elif mode == "sector":
        sweep = SectorPatrol(start_pan_deg=_num(params, "start_pan_deg", 0.0) % 360.0,
                             end_pan_deg=_num(params, "end_pan_deg", 90.0) % 360.0, speed_deg_s=speed,
                             end_dwell_s=_num(params, "end_dwell_s", 2.0, lo=0, hi=600), tilt_deg=tilt, focal_mm=focal)
    else:
        raise RpcError(INVALID_PARAMS, "'mode' rotation ya da sector olmalı.")
    weather, vis = _weather(params)
    ctx.progress(0, 1, "Direk raster'ı ve tarama zamanlaması")
    res = evaluate_sweep(t, _num(params, "x_m"), _num(params, "y_m"), _num(params, "mast_m", 8.0, lo=0.5, hi=200),
                         cam, sweep, max_range_m=_num(params, "range_m", 1500.0, lo=50, hi=20000),
                         min_dwell_s=_num(params, "min_dwell_s", 0.5, lo=0, hi=60), visibility_km=vis, weather=weather)
    entry.results["sweep"] = res
    layer = params.get("layer", "revisit")
    max_px = int(_num(params, "max_px", _DISPLAY_PX, lo=200, hi=4096))
    if layer == "revisit":
        grid = np.where(res.seen_mask, res.revisit_grid, np.inf)
        overlay = map_render.zone_overlay(grid, _overlay_size(t, max_px), mode="revisit")
    elif layer == "dori":
        overlay = map_render.zone_overlay(res.dori_grid, _overlay_size(t, max_px), mode="dori")
    else:
        raise RpcError(INVALID_PARAMS, "'layer' revisit ya da dori olmalı.")
    return {
        "period_s": res.period_s, "focal_mm": res.focal_mm, "hfov_deg": res.hfov_deg, "vfov_deg": res.vfov_deg,
        "min_dwell_s": res.min_dwell_s, "seen_area_m2": res.seen_area_m2, "glimpse_area_m2": res.glimpse_area_m2,
        "continuous_area_m2": res.continuous_area_m2, "occluded_area_m2": res.occluded_area_m2,
        "mean_revisit_s": _fin(res.mean_revisit_s), "worst_revisit_s": _fin(res.worst_revisit_s),
        "truncated": bool(res.truncated), "zone_areas_m2": _zone_areas(np.where(res.seen_mask, res.dori_grid, 0),
                                                                        t.cell_size_m ** 2),
        "overlay": _image(overlay, max_px=max_px), "legend": map_render.legend(layer)[1],
    }


def ptz_cue(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Alarm -> identify: seconds for a parked PTZ to aim, zoom and hold every cell at the target px/m."""
    from .. import map_render
    from ..ptz_fusion import CUE_REASONS, cue_response

    entry, t = _terrain_of(params)
    cam = _camera_from(params.get("camera", {}))
    weather, vis = _weather(params)
    ctx.progress(0, 1, "Nişan + zoom süreleri")
    res = cue_response(t, _num(params, "x_m"), _num(params, "y_m"), _num(params, "mast_m", 8.0, lo=0.5, hi=200), cam,
                       home_pan_deg=_num(params, "home_pan_deg", 0.0) % 360.0,
                       home_tilt_deg=_num(params, "home_tilt_deg", -5.0, lo=-89, hi=30),
                       target_ppm=_num(params, "target_ppm", 250.0, lo=1, hi=2000),
                       slew_speed_deg_s=_num(params, "slew_deg_s", 90.0, lo=1, hi=600),
                       zoom_full_sweep_s=_num(params, "zoom_full_sweep_s", 3.0, lo=0, hi=60),
                       settle_s=_num(params, "settle_s", 1.5, lo=0, hi=30),
                       max_range_m=_num(params, "range_m", 1500.0, lo=50, hi=20000), visibility_km=vis, weather=weather)
    entry.results["cue"] = res
    max_px = int(_num(params, "max_px", _DISPLAY_PX, lo=200, hi=4096))
    overlay = map_render.zone_overlay(res.response_s, _overlay_size(t, max_px), mode="cue")
    codes, counts = np.unique(res.reason[~np.isfinite(res.response_s)], return_counts=True)
    reasons = sorted(({"reason": CUE_REASONS.get(int(c), str(c)), "area_m2": float(n) * t.cell_size_m ** 2}
                      for c, n in zip(codes, counts)), key=lambda r: -r["area_m2"])
    return {
        "target_ppm": res.target_ppm, "reachable_area_m2": res.reachable_area_m2,
        "mean_response_s": _fin(res.mean_response_s), "p90_response_s": _fin(res.p90_response_s),
        "worst_response_s": _fin(res.worst_response_s), "unreachable": reasons,
        "overlay": _image(overlay, max_px=max_px), "legend": map_render.legend("cue")[1],
    }


def perimeter_mixed(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """The last plan's fixed poles + co-mounted PTZs: continuous vs intermittent detection, detect -> identify."""
    from .. import map_render
    from ..perimeter_ptz import evaluate_mixed_perimeter

    entry, t = _terrain_of(params)
    if "plan" not in entry.results:
        raise RpcError(APP_ERROR, "Önce PTZ'li bir çit planı oluşturun.")
    plan, cam, kw, _fence = entry.results["plan"]
    ptz_cam = kw.get("ptz_camera")
    if ptz_cam is None or not plan.ptz_cameras:
        raise RpcError(APP_ERROR, "Planda PTZ yok: PTZ modeli ve 'her N direkte bir' seçip yeniden dizin.")
    ctx.progress(0, 1, f"{len(plan.placed_cameras)} sabit + {len(plan.ptz_cameras)} PTZ hesaplanıyor")
    try:
        res = evaluate_mixed_perimeter(t, plan, cam, ptz_cam,
                                       speed_deg_s=_num(params, "speed_deg_s", 20.0, lo=0.5, hi=400),
                                       end_dwell_s=_num(params, "end_dwell_s", 2.0, lo=0, hi=600),
                                       cue_target_ppm=_num(params, "cue_target_ppm", 250.0, lo=1, hi=2000),
                                       visibility_km=kw["visibility_km"], weather=kw["weather"])
    except ValueError as exc:
        raise RpcError(APP_ERROR, str(exc)) from None
    entry.results["mixed"] = res
    max_px = int(_num(params, "max_px", _DISPLAY_PX, lo=200, hi=4096))
    layer = params.get("layer", "fused")
    if layer == "fused":
        overlay = map_render.zone_overlay(res.fused.status["detect"].astype(float), _overlay_size(t, max_px), mode="fused")
    elif layer == "cue":
        overlay = map_render.zone_overlay(res.detect_to_identify_s, _overlay_size(t, max_px), mode="cue")
    else:
        raise RpcError(INVALID_PARAMS, "'layer' fused ya da cue olmalı.")
    cell = t.cell_size_m ** 2
    st = res.fused.status["detect"]
    return {
        "fixed_poles": len(plan.placed_cameras), "ptz_count": len(plan.ptz_cameras),
        "continuous_detect_m2": float((st == 2).sum() * cell), "intermittent_detect_m2": float((st == 1).sum() * cell),
        "worst_intermittent_revisit_s": _fin(res.fused.worst_intermittent_revisit_s.get("detect", math.inf)),
        "detected_area_m2": res.detected_area_m2, "identifiable_area_m2": res.identifiable_area_m2,
        "identifiable_pct": res.identifiable_pct, "median_d2i_s": _fin(res.median_d2i_s),
        "p90_d2i_s": _fin(res.p90_d2i_s), "worst_d2i_s": _fin(res.worst_d2i_s),
        "cue_target_ppm": res.cue_target_ppm, "ptz_detect_floor_ppm": res.ptz_detect_floor_ppm,
        "ptz_camera_name": res.ptz_camera_name,
        "overlay": _image(overlay, max_px=max_px), "legend": map_render.legend(layer)[1],
    }


METHODS = {
    "ptz.sweep": ptz_sweep,
    "ptz.cue": ptz_cue,
    "perimeter.mixed": perimeter_mixed,
}
