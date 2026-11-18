"""API v1: data-uncertainty (sensitivity) analysis of a plan's coverage.

``perimeter.sensitivity`` re-runs the coverage report's authoritative engine on perturbed copies
of the terrain data (DEM error field, building / tree height error; model and assumptions in
``cctv_simulator/sensitivity.py``) and answers "how much of the protected area is held no matter
how wrong the data is". Same plan, band, level and weather settings as ``perimeter.coverage``
(its helpers are reused, not copied); every number is a count over engine grids.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

import numpy as np

from .protocol import APP_ERROR, INVALID_PARAMS, Context, RpcError
from .terrain_methods import (_DISPLAY_PX, _band_params, _image, _levels_table, _num, _overlay_size, _plan_roi,
                              _target_level, _terrain_of)


def _opt_num(params: Dict[str, Any], key: str, *, lo: float, hi: float) -> Optional[float]:
    return None if params.get(key) is None else _num(params, key, lo=lo, hi=hi)


def _source_grid(entry, t) -> Optional[np.ndarray]:
    """Height source per obstacle cell of ``t`` (``sensitivity.SRC_*``), rebuilt from what the surface was
    made of: OSM features re-rasterised per source (the taller feature wins an overlap, exactly as in
    ``surface_obstacles.rasterize``; ties go to the more certain source), measured raster cells from the
    kind grid. A surface without sources (a saved region's) returns what it can: unknown cells are
    treated as class defaults by the analysis."""
    from .. import sensitivity as sens
    from .. import surface_obstacles as obs

    if t.obstacle_grid is None:
        return None
    h = np.asarray(t.obstacle_grid)
    grid = np.zeros(h.shape, np.uint8)
    feats = (entry.obstacle_src or {}).get("features")
    if feats:
        for code, name in ((sens.SRC_DEFAULT, "default"), (sens.SRC_LEVELS, "levels"), (sens.SRC_TAGGED, "tagged")):
            sub = [f for f in feats if f.source == name]
            if sub:
                lay = obs.rasterize(t, sub)
                grid[(lay.height_grid > 0) & (lay.height_grid == h)] = code
    if t.obstacle_kind is not None:
        grid[(np.asarray(t.obstacle_kind) == obs.KIND_SURFACE) & (h > 0)] = sens.SRC_MEASURED
    return grid


def perimeter_sensitivity(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Coverage of the last plan under DEM / building-height error: certain / uncertain / never shares
    of the protected area, the spread of the share over ``n_runs`` data draws (run 0 = the data as
    is), per-level ranges, the assumptions used and a CERTAIN / UNCERTAIN / NEVER picture."""
    from .. import map_render
    from .. import sensitivity as sens
    from ..focal_optimizer import full_reach_placements
    from ..viewshed_3d import (PPM_DETECT, ZONE_DETECT, ZONE_IDENT, ZONE_MONITOR, ZONE_OBSERVE, ZONE_RECOG,
                               is_thermal_camera)

    entry, t = _terrain_of(params)
    if "plan" not in entry.results:
        raise RpcError(APP_ERROR, "Önce bir çit planı oluşturun.")
    plan, cam, kw, fence = entry.results["plan"]
    if not plan.placed_cameras:
        raise RpcError(APP_ERROR, "Planda kamera yok.")
    n_runs = int(_num(params, "n_runs", 12, lo=4, hi=sens.MAX_RUNS))
    if n_runs != params.get("n_runs", 12):
        raise RpcError(INVALID_PARAMS, "'n_runs' tam sayı olmalı.")
    seed = int(_num(params, "seed", 0, lo=0, hi=2 ** 31 - 1))
    sigma = _opt_num(params, "dem_sigma_m", lo=0.0, hi=100.0)
    corr = _opt_num(params, "dem_corr_m", lo=1.0, hi=5000.0)
    scale = _num(params, "obstacle_uncertainty", 1.0, lo=0.0, hi=3.0)
    is_area = kw.get("line_mode") == "area"
    band, inside = (0.0, None) if is_area else _band_params(params)
    roi = _plan_roi(t, fence, kw, band, inside)
    if not roi.any():
        raise RpcError(APP_ERROR, "Korunan alan arazinin dışında; hassasiyet hesaplanacak hücre yok.")
    label, ppm, _zone = _target_level(params, plan, kw, cam, kw.get("ptz_camera"))
    monitor_run = not is_thermal_camera(cam) and ppm < PPM_DETECT - 1e-9
    want = params.get("level", "plan")
    if want == "plan":                 # the coverage report's ``target`` class "held": fixed cameras, px/m + visible
        def held(mv):
            return (mv.ppm_grid >= ppm - 1e-6) & mv.visibility_mask
    else:
        zone = _levels_table()[want]

        def held(mv, zone=zone):
            return mv.dori_grid >= zone
    named = (("identify", ZONE_IDENT), ("recognize", ZONE_RECOG), ("observe", ZONE_OBSERVE), ("detect", ZONE_DETECT))
    zones = [z for _n, z in named] + ([ZONE_MONITOR] if monitor_run else [])
    # the very placements perimeter.coverage / band_area score (each pole out to its Detection reach)
    placements = full_reach_placements(plan, cam, ZONE_MONITOR if monitor_run else ZONE_DETECT)

    t0 = time.perf_counter()
    try:
        res = sens.coverage_sensitivity(
            t, placements, roi=roi, held=held, levels=zones, n_runs=n_runs, seed=seed, dem_sigma_m=sigma,
            dem_corr_m=corr, obstacle_scale=scale, source_grid=_source_grid(entry, t),
            refine_factor=entry.refine_factor, visibility_km=kw["visibility_km"], weather=kw["weather"],
            monitor_floor=monitor_run, progress=lambda d, n, msg: ctx.progress(d, n, msg))
    except ValueError as exc:
        raise RpcError(APP_ERROR, str(exc)) from None
    elapsed = time.perf_counter() - t0

    max_px = int(_num(params, "max_px", _DISPLAY_PX, lo=200, hi=4096))
    overlay = map_render.zone_overlay(res.class_grid, _overlay_size(t, max_px), mode="sensitivity")
    levels_out = {name: res.level_shares[z] for name, z in named}
    if monitor_run:
        levels_out["monitor"] = res.level_shares[ZONE_MONITOR]
    line = res.summary_line()
    entry.results["sensitivity"] = {
        "line": line, "label": label, "n_runs": res.n_runs, "certain": res.certain_share,
        "uncertain": res.uncertain_share, "never": res.never_share, "nominal": res.nominal_share,
        "share_min": res.share_min, "share_max": res.share_max, "levels": levels_out,
        "assumptions": res.assumptions, "measured": res.measured}
    return {
        "n_runs": res.n_runs, "seed": res.seed, "level_label": label, "target_ppm": ppm,
        "certain": res.certain_share, "uncertain": res.uncertain_share, "never": res.never_share,
        "nominal": res.nominal_share, "share_min": res.share_min, "share_max": res.share_max,
        "share_p10": res.share_p10, "share_p90": res.share_p90, "run_shares": res.run_shares,
        "levels": levels_out, "assumptions": res.assumptions, "line": line,
        "band_area_m2": res.roi_cells * res.cell_area_m2, "measured": res.measured, "monitor_floor": monitor_run,
        "overlay": _image(overlay, max_px=min(4096, 2 * max_px)),
        "legend": map_render.legend("sensitivity")[1], "elapsed_s": elapsed,
    }


METHODS = {"perimeter.sensitivity": perimeter_sensitivity}
