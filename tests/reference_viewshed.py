"""Slow, simple, independent reference for ``viewshed_3d.calculate_3d_viewshed``.

Tests only. It implements the engine's DOCUMENTED model with a different
method so the two can be compared cell by cell:

* the engine marches radial rays and lets the last ray sample that lands in a
  cell decide its class (a scatter);
* the reference takes EVERY grid node as a target and casts one straight line
  of sight from the lens to it, testing that line against the bilinear surface
  by dense sampling (<= ``step_cells`` of a cell apart) PLUS the exact surface
  heights where the line crosses a node line (the bilinear surface only bends
  there, and on a node line it is linear between two nodes, so the height is
  exact -- this is what a one-cell wall needs).

Model conventions copied from the engine (they are definitions, not physics we
re-derive): a cell's location is its grid node ``origin + (c, r) * cell``; the
lens stands on the BARE ground (``ground_elevation_at``) + mast; the surface
(``z_grid``, ground + obstacles) is what occludes and what the target stands
on; the target point is ``target_h`` above the node; earth curvature +
refraction drop ``d^2 / 2R * (1 - k)`` is applied to the terrain at ground
distance ``d`` along the line; the head (not the foot) is what must clear the
horizon of the ground before it and what the vertical frame is tested on;
px/m is ``ppm_at(slant)`` with the slant to the GROUND point.

Optics (FOV, px/m, range limits, zone thresholds) come from the engine's own
building blocks (``camera_optics`` via ``view_limits``, ``zone_codes``): the
reference is independent in geometry and occlusion, not in optics.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np

from cctv_simulator.viewshed_3d import (
    in_horizontal_cone,
    range_ok,
    vertical_ok,
    PPM_DETECT,
    PPM_IDENT,
    PPM_OBSERVE,
    PPM_RECOG,
    ZONE_DETECT,
    ZONE_IDENT,
    ZONE_MONITOR,
    ZONE_OBSERVE,
    ZONE_OCCLUDED,
    ZONE_OUT_OF_FOV,
    ZONE_RECOG,
    current_target_height,
    view_focal_mm,
    view_limits,
    zone_codes,
)

R_EARTH = 6371000.0
K_REFRACT = 0.13
_TOL = 1e-9          # tan units: absorbs float rounding on a plane that grazes itself


@dataclass
class RefView:
    dori_grid: np.ndarray
    visibility_mask: np.ndarray
    ppm_grid: np.ndarray
    in_cone: np.ndarray            # analytic frameable cone (engine's in_fov_cone)
    cell_size_m: float
    visible_area_m2: float
    occluded_area_m2: float
    fov_area_m2: float


def _curv(d, earth_curvature: bool):
    return d * d / (2.0 * R_EARTH) * (1.0 - K_REFRACT) if earth_curvature else 0.0


def bilinear(z: np.ndarray, origin_x: float, origin_y: float, cell: float, x, y) -> np.ndarray:
    """Surface height at points (the engine's node-convention bilinear, edges clamped)."""
    rows, cols = z.shape
    fc = np.clip((np.asarray(x, np.float64) - origin_x) / cell, 0.0, cols - 1.0)
    fr = np.clip((np.asarray(y, np.float64) - origin_y) / cell, 0.0, rows - 1.0)
    c0 = np.minimum(np.floor(fc).astype(np.intp), max(cols - 2, 0))
    r0 = np.minimum(np.floor(fr).astype(np.intp), max(rows - 2, 0))
    fx, fy = fc - c0, fr - r0
    c1 = np.minimum(c0 + 1, cols - 1)
    r1 = np.minimum(r0 + 1, rows - 1)
    return (z[r0, c0] * (1 - fx) + z[r0, c1] * fx) * (1 - fy) + (z[r1, c0] * (1 - fx) + z[r1, c1] * fx) * fy


def horizon_tan(z: np.ndarray, origin_x: float, origin_y: float, cell: float,
                cam_x: float, cam_y: float, cam_z: float,
                tx: np.ndarray, ty: np.ndarray, *, earth_curvature: bool = True,
                step_cells: float = 0.05, budget: int = 3_000_000) -> np.ndarray:
    """Highest tan(elevation angle from the lens) of the bilinear surface along
    the straight line from the lens to each target ``(tx, ty)``, over the open
    interval before the target. Dense samples + exact node-line crossings."""
    z = np.asarray(z, np.float64)
    rows, cols = z.shape
    tx = np.asarray(tx, np.float64)
    ty = np.asarray(ty, np.float64)
    out = np.full(tx.shape, -1e18)
    dist = np.hypot(tx - cam_x, ty - cam_y)
    order = np.argsort(dist)
    fcam_c = (cam_x - origin_x) / cell
    fcam_r = (cam_y - origin_y) / cell
    pos = 0
    n = len(order)
    while pos < n:
        # chunk of cells with similar distance; S fixed by the farthest one
        s_need = int(math.ceil(dist[order[min(n - 1, pos)]] / (step_cells * cell)))
        m = max(1, budget // max(s_need, 2))
        idx = order[pos:pos + m]
        pos += m
        d = dist[idx]
        S = max(int(math.ceil(d.max() / (step_cells * cell))), 2)
        t = (np.arange(1, S, dtype=np.float64) / S)[None, :]          # open interval (0, 1)
        sx = cam_x + t * (tx[idx] - cam_x)[:, None]
        sy = cam_y + t * (ty[idx] - cam_y)[:, None]
        fc = np.clip((sx - origin_x) / cell, 0.0, cols - 1.0)
        fr = np.clip((sy - origin_y) / cell, 0.0, rows - 1.0)
        c0 = np.minimum(np.floor(fc).astype(np.intp), max(cols - 2, 0))
        r0 = np.minimum(np.floor(fr).astype(np.intp), max(rows - 2, 0))
        fx, fy = fc - c0, fr - r0
        c1 = np.minimum(c0 + 1, cols - 1)
        r1 = np.minimum(r0 + 1, rows - 1)
        elev = (z[r0, c0] * (1 - fx) + z[r0, c1] * fx) * (1 - fy) + (z[r1, c0] * (1 - fx) + z[r1, c1] * fx) * fy
        s = t * d[:, None]
        tan = (elev - _curv(s, earth_curvature) - cam_z) / s
        hor = tan.max(axis=1)

        # exact heights where the line crosses a node line
        tfc = (tx[idx] - origin_x) / cell
        tfr = (ty[idx] - origin_y) / cell
        for line_is_col in (True, False):
            a0 = fcam_c if line_is_col else fcam_r
            a1 = tfc if line_is_col else tfr
            b0 = fcam_r if line_is_col else fcam_c
            b1 = tfr if line_is_col else tfc
            lo = np.minimum(a0, a1)
            hi = np.maximum(a0, a1)
            k0 = np.floor(lo) + 1.0
            jmax = int(np.maximum(np.floor(hi) - np.floor(lo), 0).max()) + 1
            for j in range(jmax + 1):
                k = k0 + j
                with np.errstate(divide="ignore", invalid="ignore"):
                    tc = (k - a0) / (a1 - a0)
                ok = (k > lo) & (k < hi) & np.isfinite(tc) & (tc > 1e-9) & (tc < 1.0 - 1e-9)
                if not ok.any():
                    continue
                tc = np.where(ok, tc, 0.5)
                other = b0 + (b1 - b0) * tc
                n_other = rows if line_is_col else cols
                o0 = np.clip(np.floor(other), 0, max(n_other - 2, 0)).astype(np.intp)
                fo = np.clip(other - o0, 0.0, 1.0)
                o1 = np.minimum(o0 + 1, n_other - 1)
                kk = np.clip(k, 0, (cols if line_is_col else rows) - 1).astype(np.intp)
                if line_is_col:
                    h = z[o0, kk] + (z[o1, kk] - z[o0, kk]) * fo
                else:
                    h = z[kk, o0] + (z[kk, o1] - z[kk, o0]) * fo
                sc = tc * d
                ct = np.where(ok, (h - _curv(sc, earth_curvature) - cam_z) / np.maximum(sc, 1e-9), -1e18)
                hor = np.maximum(hor, ct)
        out[idx] = hor
    return out


def _single_pass(terrain, cam_x, cam_y, cam_z, camera, lens_mode, pan_deg, tilt_deg, max_range_m,
                 earth_curvature, visibility_km, weather, focal_mm_override, monitor_floor,
                 target_h, step_cells, offset=(0.0, 0.0)):
    rows, cols, cell = terrain.rows, terrain.cols, terrain.cell_size_m
    focal = view_focal_mm(camera, lens_mode, focal_mm_override)
    lim = view_limits(camera, focal, max_range_m, visibility_km, weather, monitor_floor)
    optics = lim.optics
    half_h = math.radians(optics.hfov_deg / 2.0)
    pan = math.radians(pan_deg)
    v_lo = math.radians(tilt_deg - optics.vfov_deg / 2.0)
    v_hi = math.radians(tilt_deg + optics.vfov_deg / 2.0)

    xs = terrain.origin_x + np.arange(cols) * cell
    ys = terrain.origin_y + np.arange(rows) * cell
    gx, gy = np.meshgrid(xs + offset[0] * cell, ys + offset[1] * cell)
    z = np.asarray(terrain.z_grid, np.float64)
    zt = z if offset == (0.0, 0.0) else bilinear(z, terrain.origin_x, terrain.origin_y, cell, gx, gy)
    dx, dy = gx - cam_x, gy - cam_y
    gd = np.hypot(dx, dy)
    az = np.arctan2(dx, dy)
    diff = np.arctan2(np.sin(az - pan), np.cos(az - pan))
    # The cone rule is a shared DEFINITION, not occlusion geometry: boundary ties (nodes exactly on a
    # cone edge / the range ring / a frame edge) are half-open, the same rule as the engine
    # (viewshed_3d "Boundary ties"); every non-tie node keeps the plain inclusive test.
    in_range = range_ok(gd, lim.effective_max_range_m, dx, dy)
    cone = in_horizontal_cone(diff, gd, half_h, in_range) & in_range
    dz = zt - cam_z - _curv(gd, earth_curvature)
    cone &= vertical_ok(np.arctan2(dz, np.maximum(gd, 0.1)), v_lo, v_hi, dx, dy)

    dori = np.zeros((rows, cols), np.int32)
    vis = np.zeros((rows, cols), bool)
    ppm_g = np.zeros((rows, cols), np.float64)

    # px/m and framing need no line of sight: prune the (expensive) LOS to
    # cells that are framed and dense enough -- the rest cannot be visible or
    # "occluded" (zone_codes: occluded needs framed).
    slant = np.hypot(gd, dz)
    ppm = optics.ppm_at(np.maximum(slant, 1.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        head_tan = dz / gd + (target_h / gd if target_h > 0 else 0.0)
    elev = np.arctan(head_tan)
    framed = cone & (ppm >= lim.min_detect_ppm) & vertical_ok(elev, v_lo, v_hi, dx, dy)
    fr_, fc_ = np.nonzero(framed)
    visible = np.zeros((rows, cols), bool)
    if fr_.size:
        hor = horizon_tan(z, terrain.origin_x, terrain.origin_y, cell, cam_x, cam_y, cam_z,
                          gx[fr_, fc_], gy[fr_, fc_], earth_curvature=earth_curvature, step_cells=step_cells)
        visible[fr_, fc_] = head_tan[fr_, fc_] >= hor - _TOL
    zone = zone_codes(visible, framed, ppm, lim.thermal, lim.monitor)
    dori[cone] = zone[cone]
    vis[cone] = visible[cone]
    ppm_g[cone] = ppm[cone]
    return dori, vis, ppm_g, cone, lim


def reference_view(terrain, cam_x_m: float, cam_y_m: float, mast_height_m: float, camera, *,
                   lens_mode: str = "min", pan_deg: float = 0.0, tilt_deg: float = -5.0,
                   max_range_m: float = 2000.0, earth_curvature: bool = True,
                   visibility_km: float = 40.0, weather: str = "",
                   focal_mm_override: Optional[float] = None, monitor_floor: bool = False,
                   target_h: Optional[float] = None, step_cells: float = 0.05,
                   target_offset_cells=(0.0, 0.0)) -> RefView:
    """Reference counterpart of ``calculate_3d_viewshed`` (same arguments)."""
    if target_h is None:
        target_h = current_target_height()
    cam_z = terrain.ground_elevation_at(cam_x_m, cam_y_m) + mast_height_m
    args = (terrain, cam_x_m, cam_y_m, cam_z, camera, lens_mode, pan_deg, tilt_deg, max_range_m,
            earth_curvature, visibility_km, weather, focal_mm_override)
    off = (float(target_offset_cells[0]), float(target_offset_cells[1]))
    dori, vis, ppm, cone, lim = _single_pass(*args, False, target_h, step_cells, off)
    if monitor_floor and not lim.thermal:
        # engine: the default run EXTENDED -- the monitor run only fills cells the
        # default run left at 0, and only with MONITOR / OCCLUDED codes
        d2, v2, p2, c2, _ = _single_pass(*args, True, target_h, step_cells, off)
        take = (dori == ZONE_OUT_OF_FOV) & (d2 != ZONE_OUT_OF_FOV) & (d2 < ZONE_DETECT)
        dori = np.where(take, d2, dori).astype(np.int32)
        vis = vis | (take & v2)
        ppm = np.where(take, p2, ppm)
        cone = c2          # the engine returns the extended run's cone
    ca = terrain.cell_size_m ** 2
    return RefView(dori, vis, ppm, cone, terrain.cell_size_m,
                   visible_area_m2=float(np.count_nonzero(vis & cone)) * ca,
                   occluded_area_m2=float(np.count_nonzero((dori == ZONE_OCCLUDED) & cone)) * ca,
                   fov_area_m2=float(np.count_nonzero(cone)) * ca)


@dataclass
class RefMulti:
    dori_grid: np.ndarray
    ppm_grid: np.ndarray
    visibility_mask: np.ndarray
    seen_count_grid: np.ndarray
    union_cone: np.ndarray
    per_camera: List[RefView]
    cell_size_m: float
    visible_area_m2: float
    zone_area_m2: Dict[int, float]


def reference_multi(terrain, placements: Sequence, *, earth_curvature: bool = True,
                    visibility_km: float = 40.0, weather: str = "", target_h: Optional[float] = None,
                    step_cells: float = 0.05) -> RefMulti:
    """Per-camera reference merged by best ZONE among cameras that see the cell;
    each camera classes the cell on its own thresholds (Johnson for a
    thermal); redundancy counts PHYSICAL cameras (views sharing ``camera_id`` once)."""
    rows, cols = terrain.rows, terrain.cols
    views = [reference_view(terrain, p.x_m, p.y_m, p.mast_height_m, p.camera, lens_mode=p.lens_mode,
                            pan_deg=p.pan_deg, tilt_deg=p.tilt_deg, max_range_m=p.max_range_m,
                            earth_curvature=earth_curvature, visibility_km=visibility_km,
                            weather=weather, focal_mm_override=p.focal_mm_override,
                            target_h=target_h, step_cells=step_cells) for p in placements]
    ppm = np.zeros((rows, cols))
    vis = np.zeros((rows, cols), bool)
    union = np.zeros((rows, cols), bool)
    by_cam: Dict[object, np.ndarray] = {}
    for i, (p, v) in enumerate(zip(placements, views), 1):
        ppm = np.where(v.visibility_mask & (v.ppm_grid > ppm), v.ppm_grid, ppm)
        vis |= v.visibility_mask
        union |= v.dori_grid != ZONE_OUT_OF_FOV
        key = p.camera_id if p.camera_id is not None else ("view", i)
        by_cam[key] = by_cam.get(key, np.zeros((rows, cols), bool)) | v.visibility_mask
    seen = np.zeros((rows, cols), np.int32)
    for m in by_cam.values():
        seen += m
    # best zone: the finest class any camera that SEES the cell gives it (each
    # camera classed on its own thresholds -- Johnson for a thermal)
    best = np.zeros((rows, cols), np.int32)
    for v in views:
        best = np.where(v.visibility_mask, np.maximum(best, v.dori_grid), best)
    dori = np.zeros((rows, cols), np.int32)
    dori[union & ~vis] = ZONE_OCCLUDED
    dori[vis] = best[vis]
    ca = terrain.cell_size_m ** 2
    zones = {z: float(np.count_nonzero(dori == z)) * ca
             for z in (ZONE_OCCLUDED, ZONE_DETECT, ZONE_OBSERVE, ZONE_RECOG, ZONE_IDENT)}
    return RefMulti(dori, ppm, vis, seen, union, views, terrain.cell_size_m,
                    float(np.count_nonzero(vis)) * ca, zones)


def boundary_distance(grid: np.ndarray, max_r: int = 4) -> np.ndarray:
    """Per cell: smallest Chebyshev radius r (1..max_r) whose (2r+1)^2 window
    holds a different class; ``max_r + 1`` if none. Edges replicate (the grid
    border is not a class boundary)."""
    g = np.asarray(grid)
    out = np.full(g.shape, max_r + 1, np.int32)
    for r in range(max_r, 0, -1):
        p = np.pad(g, r, mode="edge")
        diff = np.zeros(g.shape, bool)
        for dy in range(2 * r + 1):
            for dx in range(2 * r + 1):
                diff |= p[dy:dy + g.shape[0], dx:dx + g.shape[1]] != g
        out = np.where(diff, r, out)
    return out
