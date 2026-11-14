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


def _accel(params) -> "float | None":
    """Pan/tilt acceleration (deg/s^2); 0 / absent = instant top speed."""
    a = _num(params, "slew_accel_deg_s2", 0.0, lo=0, hi=5000)
    return a if a > 0 else None


def _opt_num(params, key: str, *, lo: float, hi: float) -> "float | None":
    """An optional number: absent / null = let the engine pick its default."""
    return None if params.get(key) is None else _num(params, key, lo=lo, hi=hi)


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
    from ..viewshed_3d import is_thermal_camera

    entry, t = _terrain_of(params)
    cam = _camera_from(params.get("camera", {}))
    weather, vis = _weather(params)
    ctx.progress(0, 1, "Nişan + zoom süreleri")
    res = cue_response(t, _num(params, "x_m"), _num(params, "y_m"), _num(params, "mast_m", 8.0, lo=0.5, hi=200), cam,
                       home_pan_deg=_num(params, "home_pan_deg", 0.0) % 360.0,
                       home_tilt_deg=_num(params, "home_tilt_deg", -5.0, lo=-89, hi=30),
                       # absent = the camera's own identification level (Johnson for a thermal)
                       target_ppm=_opt_num(params, "target_ppm", lo=1, hi=2000),
                       slew_speed_deg_s=_num(params, "slew_deg_s", 90.0, lo=1, hi=600),
                       zoom_full_sweep_s=_num(params, "zoom_full_sweep_s", 3.0, lo=0, hi=60),
                       settle_s=_num(params, "settle_s", 1.5, lo=0, hi=30),
                       max_range_m=_num(params, "range_m", 1500.0, lo=50, hi=20000), visibility_km=vis, weather=weather,
                       slew_accel_deg_s2=_accel(params))
    entry.results["cue"] = res
    max_px = int(_num(params, "max_px", _DISPLAY_PX, lo=200, hi=4096))
    overlay = map_render.zone_overlay(res.response_s, _overlay_size(t, max_px), mode="cue")
    codes, counts = np.unique(res.reason[~np.isfinite(res.response_s)], return_counts=True)
    reasons = sorted(({"reason": CUE_REASONS.get(int(c), str(c)), "area_m2": float(n) * t.cell_size_m ** 2}
                      for c, n in zip(codes, counts)), key=lambda r: -r["area_m2"])
    return {
        "target_ppm": res.target_ppm, "reachable_area_m2": res.reachable_area_m2,
        "mean_response_s": _fin(res.mean_response_s), "median_response_s": _fin(res.median_response_s),
        "p90_response_s": _fin(res.p90_response_s),
        "worst_response_s": _fin(res.worst_response_s), "unreachable": reasons,
        "thermal": is_thermal_camera(cam),
        "overlay": _image(overlay, max_px=max_px), "legend": map_render.legend("cue")[1],
    }


def ptz_tour(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """A PTZ preset tour: union coverage of the presets and the revisit time of every cell."""
    from .. import map_render
    from ..ptz_tour import PTZPreset, PTZTour, evaluate_ptz_tour

    entry, t = _terrain_of(params)
    cam = _camera_from(params.get("camera", {}))
    raw = params.get("presets")
    if not isinstance(raw, list) or len(raw) < 2:
        raise RpcError(INVALID_PARAMS, "Tur için en az iki preset gerekli.")
    presets = []
    for i, p in enumerate(raw):
        if not isinstance(p, dict):
            raise RpcError(INVALID_PARAMS, "Her preset bir nesne olmalı.")
        focal = p.get("focal_mm")
        if focal is not None and not (isinstance(focal, (int, float)) and cam.focal_min_mm <= focal <= cam.focal_max_mm):
            raise RpcError(INVALID_PARAMS, f"Preset {i + 1}: odak lens aralığının dışında.")
        presets.append(PTZPreset(name=str(p.get("name") or f"P{i + 1}"), pan_deg=_num(p, "pan_deg") % 360.0,
                                 tilt_deg=_num(p, "tilt_deg", -5.0, lo=-89, hi=30),
                                 focal_mm=float(focal) if focal is not None else None,
                                 dwell_s=_num(p, "dwell_s", 5.0, lo=0.1, hi=3600)))
    tour = PTZTour(x_m=_num(params, "x_m"), y_m=_num(params, "y_m"), mast_height_m=_num(params, "mast_m", 8.0, lo=0.5, hi=200),
                   camera=cam, presets=presets, max_range_m=_num(params, "range_m", 1500.0, lo=50, hi=20000),
                   slew_speed_deg_s=_num(params, "slew_deg_s", 90.0, lo=1, hi=600),
                   settle_s=_num(params, "settle_s", 1.5, lo=0, hi=30),
                   zoom_full_sweep_s=_num(params, "zoom_full_sweep_s", 3.0, lo=0, hi=60),
                   slew_accel_deg_s2=_accel(params))
    weather, vis = _weather(params)
    ctx.progress(0, 1, f"{len(presets)} preset hesaplanıyor")
    res = evaluate_ptz_tour(t, tour, visibility_km=vis, weather=weather)
    entry.results["tour"] = res
    max_px = int(_num(params, "max_px", _DISPLAY_PX, lo=200, hi=4096))
    layer = params.get("layer", "revisit")
    if layer == "revisit":
        overlay = map_render.zone_overlay(res.revisit_grid, _overlay_size(t, max_px), mode="revisit")
    elif layer == "dori":
        overlay = map_render.zone_overlay(res.combined.dori_grid, _overlay_size(t, max_px), mode="dori")
    else:
        raise RpcError(INVALID_PARAMS, "'layer' revisit ya da dori olmalı.")
    cell = t.cell_size_m ** 2
    seen = np.isfinite(res.revisit_grid)
    return {
        "tour_period_s": res.tour_period_s, "active_dwell_s": res.active_dwell_s, "slew_total_s": res.slew_total_s,
        "zoom_total_s": res.zoom_total_s, "settle_total_s": res.settle_total_s,
        "mean_revisit_s": _fin(res.mean_revisit_s), "worst_revisit_s": _fin(res.worst_revisit_s),
        "covered_area_m2": float(seen.sum() * cell), "preset_labels": res.preset_labels,
        "zone_areas_m2": _zone_areas(res.combined.dori_grid, cell),
        "overlay": _image(overlay, max_px=max_px), "legend": map_render.legend(layer)[1],
    }


def perimeter_mixed(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """The last plan's fixed poles + co-mounted PTZs: continuous vs intermittent detection, detect -> identify."""
    from .. import map_render
    from ..perimeter_ptz import evaluate_mixed_perimeter
    from ..viewshed_3d import ZONE_DETECT, is_thermal_camera

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
                                       cue_target_ppm=_opt_num(params, "cue_target_ppm", lo=1, hi=2000),
                                       visibility_km=kw["visibility_km"], weather=kw["weather"],
                                       fixed_reach_level=ZONE_DETECT)       # same detection as coverage / statement
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
        "fixed_poles": plan.pole_count, "ptz_count": len(plan.ptz_cameras),
        "continuous_detect_m2": float((st == 2).sum() * cell), "intermittent_detect_m2": float((st == 1).sum() * cell),
        "worst_intermittent_revisit_s": _fin(res.fused.worst_intermittent_revisit_s.get("detect", math.inf)),
        "detected_area_m2": res.detected_area_m2, "identifiable_area_m2": res.identifiable_area_m2,
        "identifiable_pct": res.identifiable_pct, "median_d2i_s": _fin(res.median_d2i_s),
        "p90_d2i_s": _fin(res.p90_d2i_s), "worst_d2i_s": _fin(res.worst_d2i_s),
        "cue_target_ppm": res.cue_target_ppm, "ptz_detect_floor_ppm": res.ptz_detect_floor_ppm,
        "ptz_camera_name": res.ptz_camera_name, "ptz_thermal": is_thermal_camera(ptz_cam),
        "overlay": _image(overlay, max_px=max_px), "legend": map_render.legend(layer)[1],
    }


def _playback_layers(t, x, y, mast, cam, sweep, max_range_m, vis, weather, max_px):
    """One PTZ's pan-independent pictures for the shell's live sweep: DORI
    where the line of sight is clear, shadow where terrain / buildings hide
    the ground inside the frame -- both over the mount's window, north-up."""
    from .. import map_render
    from ..design_tokens import rgba
    from ..mount_visibility import mount_visibility, view_layers
    from ..raster_smooth import smooth_overlay
    from ..viewshed_3d import ZONE_OCCLUDED

    mv = mount_visibility(t, x, y, mast, max_range_m=max_range_m)
    lay = view_layers(mv, cam, tilt_deg=sweep.tilt_deg, focal_mm=sweep.focal_mm, lens_mode=sweep.lens_mode,
                      max_range_m=max_range_m, visibility_km=vis, weather=weather)
    lit = np.zeros(lay.zone.shape + (4,), np.uint8)
    for lo, _hi, col, _txt in map_render.LAYER_BANDS["dori"][1]:
        if lo != ZONE_OCCLUDED:
            lit[(lay.zone == lo) & lay.visible] = tuple(col[:3]) + (215,)
    shadow = np.zeros_like(lit)
    shadow[lay.in_frame & ~lay.visible & (lay.zone == ZONE_OCCLUDED)] = rgba("bg-0", 185)
    rows, cols = mv.window
    cell = t.cell_size_m
    box = [cols.start * cell, rows.start * cell, cols.stop * cell, rows.stop * cell]   # x0, y0, x1, y1 (m)
    h, w = lit.shape[:2]
    k = max(1.0, min(4.0, max_px / max(h, w, 1)))
    size = (max(1, round(w * k)), max(1, round(h * k)))
    pics = [_image(smooth_overlay(np.flipud(a), size), max_px=max_px) for a in (lit, shadow)]
    cell_a = cell ** 2
    return {"box_m": box, "lit": pics[0], "shadow": pics[1], "hfov_deg": lay.hfov_deg,
            "reach_m": float(min(lay.effective_max_range_m, max_range_m)),
            "visible_area_m2": float(lay.visible.sum() * cell_a)}


def ptz_playback(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Live 360° / sector sweep for the shell: every PTZ of the last plan (or
    one given camera), at the focal and tilt the plan put it on. Pictures are
    computed once; the shell turns the cone itself (no round trip per frame).
    Pan motion is ``ptz_animation.pan_at`` (constant speed, instant reversal)."""
    from ..perimeter_ptz import _ptz_range, _sweep_reach, patrol_for
    from ..ptz_animation import sweep_period_s
    from ..ptz_schedule import Rotation360, SectorPatrol

    entry, t = _terrain_of(params)
    speed = _num(params, "speed_deg_s", 20.0, lo=0.5, hi=400)
    dwell = _num(params, "end_dwell_s", 2.0, lo=0, hi=600)
    max_px = int(_num(params, "max_px", 900, lo=100, hi=2048))
    items = []
    if params.get("source", "plan") == "plan":
        if "plan" not in entry.results:
            raise RpcError(APP_ERROR, "Önce PTZ'li bir plan oluşturun.")
        plan, _cam, kw, _fence = entry.results["plan"]
        ptz_cam = kw.get("ptz_camera")
        if ptz_cam is None or not plan.ptz_cameras:
            raise RpcError(APP_ERROR, "Planda PTZ yok: PTZ modeli ve 'her N direkte bir' seçip planı yeniden kurun.")
        weather, vis = kw["weather"], kw["visibility_km"]
        reach = _sweep_reach(ptz_cam, vis, weather)
        for p in plan.ptz_cameras:
            sw = patrol_for(p, speed, dwell)
            start = p.pan_deg if isinstance(sw, Rotation360) else sw.start_pan_deg
            items.append((f"PTZ {p.pole_id}", p.x_m, p.y_m, p.mast_height_m, ptz_cam, sw, start,
                          _ptz_range(p, reach, 3000.0), p.focal_mm, p.tilt_deg))
    else:
        cam = _camera_from(params.get("camera", {}))
        weather, vis = _weather(params)
        tilt = _num(params, "tilt_deg", -5.0, lo=-89, hi=30)
        focal = params.get("focal_mm")
        focal = cam.focal_min_mm if focal is None else _num(params, "focal_mm", lo=cam.focal_min_mm, hi=cam.focal_max_mm)
        if params.get("mode", "rotation") == "rotation":
            sw = Rotation360(speed_deg_s=speed, tilt_deg=tilt, focal_mm=focal)
            start = _num(params, "start_pan_deg", 0.0) % 360.0
        else:
            sw = SectorPatrol(start_pan_deg=_num(params, "start_pan_deg", 0.0) % 360.0,
                              end_pan_deg=_num(params, "end_pan_deg", 90.0) % 360.0, speed_deg_s=speed,
                              end_dwell_s=dwell, tilt_deg=tilt, focal_mm=focal)
            start = sw.start_pan_deg
        items.append(("PTZ", _num(params, "x_m"), _num(params, "y_m"), _num(params, "mast_m", 8.0, lo=0.5, hi=200),
                      cam, sw, start, _num(params, "range_m", 1500.0, lo=50, hi=20000), focal, tilt))
    out = []
    for i, (label, x, y, mast, cam, sw, start, rng, focal, tilt) in enumerate(items):
        ctx.progress(i, len(items), f"{label}: görünürlük ({i + 1}/{len(items)})")
        lay = _playback_layers(t, x, y, mast, cam, sw, rng, vis, weather, max_px)
        rotation = isinstance(sw, Rotation360)
        out.append({
            "label": label, "x_m": x, "y_m": y, "mast_m": mast, "focal_mm": focal, "tilt_deg": tilt,
            "mode": "rotation" if rotation else "sector", "start_pan_deg": start,
            "span_deg": 360.0 if rotation else sw.span_deg, "speed_deg_s": sw.speed_deg_s,
            "end_dwell_s": 0.0 if rotation else sw.end_dwell_s, "period_s": sweep_period_s(sw), **lay,
        })
    return {"ptz": out, "camera_name": (items[0][4].model_name or items[0][4].name) if items else ""}


METHODS = {
    "ptz.sweep": ptz_sweep,
    "ptz.playback": ptz_playback,
    "ptz.cue": ptz_cue,
    "ptz.tour": ptz_tour,
    "perimeter.mixed": perimeter_mixed,
}
