"""API v1: optics bench verdict, measured image quality, 3D camera-eye frame."""
from __future__ import annotations

import math
from typing import Any, Dict

from .params import _camera_from, _levels_from, _require
from .protocol import APP_ERROR, INVALID_PARAMS, Context, RpcError
from .terrain_methods import _image, _num

PALETTES = ("auto", "day", "ir", "thermal_white", "thermal_black", "thermal_ironbow")
TARGETS = {"human": 1.8, "vehicle": 2.3, "object": 1.0}      # target heights, as the classic 3D view


def optics_bench(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Does the camera resolve ``task_ppm`` at ``distance_m``? Engine numbers + verdict reasons."""
    import dataclasses

    from ..calculations import calculate_for_camera, ground_distance_for_ppm
    from ..optics_bench import day_reasons, evaluate_requirement

    cam = _camera_from(params.get("camera", {}))
    mode = params.get("mode", "min")
    if mode not in ("min", "max"):
        raise RpcError(INVALID_PARAMS, "'mode' 'min' veya 'max' olmalı.")
    req_ppm = _num(params, "req_ppm", 250.0, lo=1, hi=5000)
    req_dist = _num(params, "distance_m", 20.0, lo=0.1, hi=20000)
    weather = str(params.get("weather", ""))
    result = calculate_for_camera(cam, mode, [])
    achieved, atm_limit, reasons = evaluate_requirement(cam, result, req_ppm, req_dist, weather)
    reach = ground_distance_for_ppm(result, req_ppm)
    dori = {name: ground_distance_for_ppm(result, ppm) for name, ppm in (
        ("identify", 250.0), ("recognize", 125.0), ("observe", 62.5), ("detect", 25.0))}
    return {
        "focal_mm": result.focal_mm, "hfov_deg": result.hfov_deg, "vfov_deg": result.vfov_deg,
        "top_ray_deg": result.top_ray_deg, "bottom_ray_deg": result.bottom_ray_deg,
        "dead_zone_m": result.dead_zone_m, "dead_zone_area_m2": result.dead_zone_area_m2,
        "dead_zone_half_width_m": result.dead_zone_left_m,
        "max_geom_dist_m": result.max_geom_dist_m, "res_width_px": result.res_width_px,
        "nominal_res_width_px": result.nominal_res_width_px, "effective_px_ratio": result.effective_px_ratio,
        "achieved_ppm": achieved, "required_ppm": req_ppm, "distance_m": req_dist,
        "atmospheric_limit_m": atm_limit if math.isfinite(atm_limit) else None,
        "reach_m": reach, "dori_reach_m": dori,
        "pass": not reasons, "day_pass": not day_reasons(reasons), "reasons": reasons,
        "ir_range_m": cam.ir_range_m, "camera": dataclasses.asdict(cam),
    }


def iq_measure(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Slanted-edge MTF of a photo (ISO 12233): k = MTF50 / Nyquist, effective resolution."""
    from ..cctv_iq import measure_file

    path = _require(params, "path", str)
    roi = params.get("roi")
    if roi is not None:
        if not (isinstance(roi, list) and len(roi) == 4 and all(isinstance(v, int) for v in roi)):
            raise RpcError(INVALID_PARAMS, "'roi' [x0, y0, x1, y1] tamsayı piksel olmalı.")
        roi = tuple(roi)
    try:
        res = measure_file(path, roi)
    except FileNotFoundError:
        raise RpcError(APP_ERROR, f"Dosya bulunamadı: {path}") from None
    except (ValueError, OSError) as exc:
        raise RpcError(APP_ERROR, f"Kenar ölçülemedi: {exc}") from None
    # arrays (frequency / MTF curve) as lists: the shell plots the curve
    out = {k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in res.items()}
    nominal_mp = params.get("nominal_mp")
    k = float(res.get("k", 1.0))
    if isinstance(nominal_mp, (int, float)) and nominal_mp > 0:
        out["effective_mp"] = float(nominal_mp) * k * k
    return out


def scene_render(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """The camera's own picture of a target: real FOV, sensor + MTF loss, palette, weather.

    Returns the frame plus crisp-overlay geometry (horizon, ground grid in
    frame pixels) and the HUD numbers, all from the perspective engine.
    """
    from ..atmosphere import WEATHER_PRESETS, band_for_camera, transmittance
    from ..models import DEFAULT_LEVELS
    from ..perspective_3d import (Perspective3DEngine, Point3D, generate_dori_ground_polygons,
                                  generate_ground_grid_lines)
    from ..scene_render import render_camera_frame

    cam = _camera_from(params.get("camera", {}))
    focal = _num(params, "focal_mm", cam.focal_min_mm, lo=cam.focal_min_mm, hi=cam.focal_max_mm)
    w = int(_num(params, "width", 960, lo=160, hi=2400))
    h = int(_num(params, "height", 540, lo=120, hi=1600))
    target = params.get("target", "human")
    if target not in TARGETS:
        raise RpcError(INVALID_PARAMS, "'target' human, vehicle ya da object olmalı.")
    dist = _num(params, "target_dist_m", 15.0, lo=1, hi=5000)
    lateral = _num(params, "lateral_m", 0.0, lo=-500, hi=500)
    palette = params.get("palette", "auto")
    if palette not in PALETTES:
        raise RpcError(INVALID_PARAMS, "Geçersiz palet.")
    weather = str(params.get("weather", "")) if params.get("weather") in WEATHER_PRESETS else ""
    vis_km = WEATHER_PRESETS.get(weather, 40.0)
    max_d = _num(params, "max_dist_m", 100.0, lo=10, hi=5000)

    engine = Perspective3DEngine(cam, focal_mm=focal, viewport_size=(w, h))
    if palette == "auto":
        palette = "thermal_white" if engine.is_thermal else "day"
    night = palette == "ir"
    th = TARGETS[target]
    ppm = engine.calculate_ppm_at_distance(dist, target_h_m=th)
    polys = generate_dori_ground_polygons(engine, list(DEFAULT_LEVELS), max_dist_m=max_d)
    frame, inset_zoom = render_camera_frame(
        engine, w=w, h=h, target_type=target, target_dist=dist, lateral_offset=lateral, ppm=ppm,
        palette=palette, k=max(cam.effective_px_ratio, 0.05), show_dori=bool(params.get("show_dori", True)),
        dori_polys=polys, ir_range_m=cam.ir_range_m if night else 0.0, fast=bool(params.get("fast", False)),
        visibility_km=vis_km, weather=weather)
    tau = transmittance(dist, vis_km, band_for_camera(cam.sensor_name, cam.model_name, night_ir=night), weather)
    dori_name, _badge, dori_desc = engine.get_dori_status(ppm, target_h_m=th)
    grid = []
    for line in generate_ground_grid_lines(engine, max_dist_m=max(dist * 1.4, 40.0)):
        p1, p2 = line["p1"], line["p2"]
        if p1.visible and p2.visible and p1.depth > 0.05 and p2.depth > 0.05:
            pm = line.get("p_mid")
            grid.append({"u1": p1.u, "v1": p1.v, "u2": p2.u, "v2": p2.v, "major": bool(line.get("is_major")),
                         "center": bool(line.get("is_center")), "type": line["type"],
                         "label": (f"{line['distance']:.0f} m" if line["type"] == "transverse" and pm
                                   and line.get("is_major") else ""),
                         "lu": pm.u if pm else None, "lv": pm.v if pm else None})
    head = engine.project_point(Point3D(lateral, dist, th))
    target_px = ppm * th
    return {
        "frame": _image(frame, max_px=max(w, h), fmt="JPEG"), "width": w, "height": h,
        "palette": palette, "horizon_v": engine.get_horizon_y(), "grid": grid,
        "target_head": {"u": head.u, "v": head.v, "visible": bool(head.visible and head.depth > 0.05)},
        "ppm": ppm, "target_px": target_px, "target_pct_h": target_px / max(engine.res_h, 1) * 100.0,
        "dori_name": dori_name.split(" (")[0], "dori_desc": dori_desc, "is_thermal": bool(engine.is_thermal),
        "hfov_deg": engine.hfov_deg, "transmittance": tau, "visibility_km": vis_km, "weather": weather,
        "inset_zoom": inset_zoom,
    }


def optics_project(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Every camera of a site plan at once: DORI distances, dead zones and which
    other cameras cover each dead zone, plus the check point verdict per camera."""
    from ..calculations import analyze_dead_zone_coverage, calculate_for_camera, target_analysis_for_result
    from ..models import TargetPoint

    raw = params.get("cameras")
    if not isinstance(raw, list) or not raw:
        raise RpcError(INVALID_PARAMS, "'cameras' en az bir kamera içermeli.")
    cams = [_camera_from(c) for c in raw]
    names = [c.name for c in cams]
    if len(set(names)) != len(names):
        raise RpcError(INVALID_PARAMS, "Kamera adları benzersiz olmalı (kör nokta örtüşmesi ada göre eşleşir).")
    focals = params.get("focals")          # optional per-camera focal (the slider), else lens end
    mode = params.get("mode", "min")
    if mode not in ("min", "max"):
        raise RpcError(INVALID_PARAMS, "'mode' 'min' veya 'max' olmalı.")
    import dataclasses
    levels = _levels_from(params.get("levels"))      # the project's own levels (custom ones included)
    results = {}
    for i, c in enumerate(cams):
        f = focals[i] if isinstance(focals, list) and i < len(focals) and isinstance(focals[i], (int, float)) else None
        if f is not None and c.focal_min_mm <= f <= c.focal_max_mm:
            c = dataclasses.replace(c, focal_min_mm=float(f), focal_max_mm=float(f))
            results[c.name] = [calculate_for_camera(c, "min", levels, with_recommendations=False)]
        else:
            results[c.name] = [calculate_for_camera(c, mode, levels, with_recommendations=False)]
    analyze_dead_zone_coverage(results, [r[0].camera for r in results.values()])

    tp = params.get("target")
    level_key = str(params.get("level_key", "Characterize"))
    level = next((lv for lv in levels if lv.key == level_key), None)
    if tp is not None and level is None:
        raise RpcError(INVALID_PARAMS, f"Bilinmeyen seviye: {level_key}")
    target = None
    if isinstance(tp, dict):
        target = TargetPoint(active=True, x_m=float(tp.get("x_m", 0.0)), y_m=float(tp.get("y_m", 0.0)),
                             required_level_key=level_key)
    out = []
    for name, (r,) in results.items():
        out.append({
            "name": name, "focal_mm": r.focal_mm, "hfov_deg": r.hfov_deg, "dead_zone_m": r.dead_zone_m,
            "max_geom_dist_m": r.max_geom_dist_m if math.isfinite(r.max_geom_dist_m) else None,
            "ground_distances": {k: (v if math.isfinite(v) else None) for k, v in r.ground_distances.items()},
            "dead_zone_covered_by": r.dead_zone_covered_by or [],
            "target": target_analysis_for_result(target, r, level) if target else None,
        })
    return {"cameras": out, "level": dataclasses.asdict(level) if level else None}


def plan_image(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Site plan picture (PNG / JPG / BMP / TIFF) for the CAD workspace background."""
    from PIL import Image, UnidentifiedImageError

    path = _require(params, "path", str)
    try:
        with Image.open(path) as im:
            im.load()
            w, h = im.size
            img = im.convert("RGB")
    except FileNotFoundError:
        raise RpcError(APP_ERROR, f"Dosya bulunamadı: {path}") from None
    except (UnidentifiedImageError, OSError) as exc:
        raise RpcError(APP_ERROR, f"Plan resmi okunamadı: {exc}") from None
    return {**_image(img, max_px=int(_num(params, "max_px", 2400, lo=200, hi=6000)), fmt="JPEG"),
            "source_width_px": w, "source_height_px": h, "aspect": h / w if w else 1.0}


# (field, lo, hi): every AnprSetup number the shell may send, range-checked by name
_ANPR_FIELDS = (("height_m", 0.3, 60), ("pole_x_m", -100, 100), ("lane_width_m", 2, 6), ("aim_y_m", 1, 500),
                ("plate_height_m", 0.2, 3), ("target_ppm", 20, 2000), ("max_h_deg", 1, 89), ("max_v_deg", 1, 89),
                ("speed_kmh", 1, 400), ("fps", 1, 240), ("exposure_s", 1e-6, 0.1), ("blur_px", 0.1, 20),
                ("max_y_m", 10, 1000))


def _anpr_setup(params: Dict[str, Any]):
    from ..anpr import AnprSetup

    raw = params.get("setup") or {}
    if not isinstance(raw, dict):
        raise RpcError(INVALID_PARAMS, "'setup' bir nesne olmalı.")
    vals: Dict[str, Any] = {}
    for name, lo, hi in _ANPR_FIELDS:
        if name in raw:
            vals[name] = _num(raw, name, lo=lo, hi=hi)
    if "lanes" in raw:
        vals["lanes"] = int(_num(raw, "lanes", lo=1, hi=8))
    if "min_frames" in raw:
        vals["min_frames"] = int(_num(raw, "min_frames", lo=1, hi=100))
    if raw.get("aim_x_m") is not None:
        vals["aim_x_m"] = _num(raw, "aim_x_m", lo=-100, hi=100)
    if "night_ir" in raw:
        vals["night_ir"] = bool(raw["night_ir"])
    return AnprSetup(**vals)


def _anpr_json(r) -> Dict[str, Any]:
    import dataclasses

    def fin(v):
        return v if v is None or math.isfinite(v) else None
    lanes = [{k: (fin(v) if isinstance(v, float) else v) for k, v in dataclasses.asdict(ln).items()} for ln in r.lanes]
    return {"focal_mm": r.focal_mm, "pan_deg": r.pan_deg, "tilt_deg": r.tilt_deg, "hfov_deg": r.hfov_deg,
            "vfov_deg": r.vfov_deg, "lanes_ok": r.lanes_ok, "lanes": lanes, "setup": dataclasses.asdict(r.setup)}


def anpr_analyse(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Plate reading on a straight road: readable zone per lane, frames at speed, motion blur, angles."""
    from ..anpr import analyse

    cam = _camera_from(params.get("camera", {}))
    focal = _num(params, "focal_mm", cam.focal_max_mm, lo=cam.focal_min_mm, hi=max(cam.focal_max_mm, cam.focal_min_mm))
    return _anpr_json(analyse(cam, focal, _anpr_setup(params)))


def anpr_optimize(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Best focal and aim point for the given pole: all lanes readable at the speed if possible."""
    from ..anpr import optimize

    cam = _camera_from(params.get("camera", {}))
    r = optimize(cam, _anpr_setup(params), progress=lambda d, n: ctx.progress(d, n, f"Odak {d}/{n}"))
    return _anpr_json(r)


METHODS = {
    "anpr.analyse": anpr_analyse,
    "anpr.optimize": anpr_optimize,
    "optics.project": optics_project,
    "plan.image": plan_image,
    "optics.bench": optics_bench,
    "iq.measure": iq_measure,
    "scene.render": scene_render,
}
