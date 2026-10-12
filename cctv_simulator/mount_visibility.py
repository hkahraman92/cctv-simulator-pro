"""Per-mount 360-degree line-of-sight raster + cheap per-view masks.

Terrain occlusion seen from a mast depends only on WHERE the lens is (x, y,
mast height, earth curvature) -- not on where it points or how far it is
zoomed (see viewshed_3d.march_rays: the running horizon deliberately ignores
FOV and pixel density). So for a camera that changes its view -- a PTZ preset
tour, a sector patrol, a 360-degree sweep, slew-to-cue -- the expensive part
of calculate_3d_viewshed (the radial ray march) can be done ONCE over the full
circle, and every view becomes a few array comparisons on the cached result.

    mv = mount_visibility(terrain, x, y, mast, max_range_m=1500)   # cached
    view = view_mask(mv, camera, pan_deg=40, tilt_deg=-3, focal_mm=60)

The same building blocks as the authoritative engine are used --
march_rays (occlusion), view_limits (px/m floor, optical/atmospheric caps),
zone_codes (DORI classes), camera_optics (Kural 1) -- so the two differ only
in ray sampling: the engine casts rays inside one FOV, the raster round the
whole circle, and a cell keeps whichever ray sample lands in it last. On
flat ground they agree on >= 97 % of cells. On hills they disagree along
shadow edges, where BOTH are uncertain: against an exact eye -> cell-centre
sightline over the bilinear surface the raster was right on 89.5 % of framed
cells and the engine on 87.3 % (6 views, 400x400 @ 3 m). The test pins
"no less accurate than the engine" against that reference.

Measured (400x400 @ 3 m, 1 km range): raster ~200 ms once per mast, cache
hit ~10 us, ~10 ms per view vs ~20 ms for a full engine run.

No Tk. Pure numpy.
"""
from __future__ import annotations

import math
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from .models import CameraConfig
from .terrain_loader import TerrainData
from .viewshed_3d import (
    _K_REFRACT,
    _R_EARTH,
    march_rays,
    ray_step_size,
    view_focal_mm,
    view_limits,
    zone_codes,
)

_MAX_RAYS = 16384
_RAY_CHUNK = 512          # rays per march block: bounds the (rays x steps) temporaries


@dataclass
class MountVisibility:
    """Everything about the terrain as seen from one lens position that does
    not depend on pan, tilt or zoom. Arrays cover only ``window`` -- the
    terrain cells within ``max_range_m`` of the mast."""
    x_m: float
    y_m: float
    mast_height_m: float
    cam_z_m: float
    max_range_m: float
    earth_curvature: bool
    grid_shape: Tuple[int, int]               # full terrain (rows, cols)
    window: Tuple[slice, slice]               # rows, cols of the arrays below
    num_rays: int
    # sample recorded per cell (last write wins, ray-major / step-ascending,
    # exactly like calculate_3d_viewshed's scatter)
    written: np.ndarray                       # some in-range ray sample landed here
    los_clear: np.ndarray                     # ...and nothing earlier on its ray rose above it
    sample_elev_rad: np.ndarray               # elevation angle of that sample from the lens
    sample_slant_m: np.ndarray                # lens-to-sample 3D distance
    # cell-centre geometry (the engine's analytic FOV cone uses these)
    center_dist_m: np.ndarray
    center_az_rad: np.ndarray                 # 0 = +Y (north), clockwise
    center_elev_rad: np.ndarray


@dataclass
class ViewGrids:
    """One view (pan, tilt, focal) of a mount, on the full terrain grid."""
    visibility_mask: np.ndarray
    dori_grid: np.ndarray
    ppm_grid: np.ndarray
    pan_deg: float
    tilt_deg: float
    focal_mm: float
    hfov_deg: float
    vfov_deg: float
    effective_max_range_m: float
    truncated: bool          # the view reaches past the raster's max_range_m


def compute_mount_visibility(terrain: TerrainData, x_m: float, y_m: float, mast_height_m: float,
                             max_range_m: float = 2000.0, earth_curvature: bool = True,
                             ray_step_m: float = 3.0) -> MountVisibility:
    """March rays round the full circle from one mast (uncached)."""
    rows, cols, cell = terrain.rows, terrain.cols, terrain.cell_size_m
    cam_z = terrain.get_elevation_at(x_m, y_m) + mast_height_m

    step = ray_step_size(cell, ray_step_m)
    step_dists = np.arange(1, max(int(max_range_m / step), 1) + 1) * step
    # Same angular-density rule as the engine (< ~0.6 cell apart at the far
    # edge), applied to the whole circle.
    span_need = 2.0 * math.pi * max_range_m / max(cell * 0.6, 0.5)
    num_rays = int(min(_MAX_RAYS, max(720, span_need)))
    angles = np.linspace(0.0, 2.0 * math.pi, num_rays, endpoint=False)

    r0 = int(np.clip(math.floor((y_m - max_range_m - terrain.origin_y) / cell), 0, rows))
    r1 = int(np.clip(math.ceil((y_m + max_range_m - terrain.origin_y) / cell) + 1, 0, rows))
    c0 = int(np.clip(math.floor((x_m - max_range_m - terrain.origin_x) / cell), 0, cols))
    c1 = int(np.clip(math.ceil((x_m + max_range_m - terrain.origin_x) / cell) + 1, 0, cols))
    h, w = max(r1 - r0, 0), max(c1 - c0, 0)

    written = np.zeros((h, w), bool)
    los = np.zeros((h, w), bool)
    tan_g = np.zeros((h, w), np.float64)
    slant_g = np.zeros((h, w), np.float64)
    for start in range(0, num_rays, _RAY_CHUNK):
        m = march_rays(terrain, x_m, y_m, cam_z, angles[start:start + _RAY_CHUNK],
                       step_dists, earth_curvature)
        v = m.valid
        rr, cc = m.rr[v] - r0, m.cc[v] - c0
        written[rr, cc] = True
        los[rr, cc] = m.los_clear[v]
        tan_g[rr, cc] = m.tan_angle[v]
        slant_g[rr, cc] = m.slant[v]

    gx = terrain.origin_x + np.arange(c0, c1) * cell
    gy = terrain.origin_y + np.arange(r0, r1) * cell
    mx, my = np.meshgrid(gx, gy)
    dx, dy = mx - x_m, my - y_m
    dist = np.hypot(dx, dy)
    cell_dz = terrain.z_grid[r0:r1, c0:c1].astype(np.float64) - cam_z
    if earth_curvature:
        cell_dz -= (dist ** 2) / (2.0 * _R_EARTH) * (1.0 - _K_REFRACT)

    return MountVisibility(
        x_m=x_m, y_m=y_m, mast_height_m=mast_height_m, cam_z_m=cam_z,
        max_range_m=max_range_m, earth_curvature=earth_curvature,
        grid_shape=(rows, cols), window=(slice(r0, r1), slice(c0, c1)), num_rays=num_rays,
        written=written, los_clear=los,
        sample_elev_rad=np.arctan(tan_g), sample_slant_m=slant_g,
        center_dist_m=dist, center_az_rad=np.arctan2(dx, dy),
        center_elev_rad=np.arctan2(cell_dz, np.maximum(dist, 0.1)),
    )


# ── cache ────────────────────────────────────────────────────────────────────
_CACHE: "OrderedDict[tuple, tuple]" = OrderedDict()
_CACHE_MAX = 16


def mount_visibility(terrain: TerrainData, x_m: float, y_m: float, mast_height_m: float,
                     max_range_m: float = 2000.0, earth_curvature: bool = True,
                     ray_step_m: float = 3.0) -> MountVisibility:
    """Cached compute_mount_visibility. Keyed by the terrain object and its
    grid geometry (a recalibrated width or a new DEM is a different key) plus
    the mount; least-recently-used entries beyond 16 are dropped."""
    key = (id(terrain), id(terrain.z_grid), terrain.z_grid.shape, terrain.cell_size_m,
           terrain.origin_x, terrain.origin_y, float(x_m), float(y_m), float(mast_height_m),
           float(max_range_m), bool(earth_curvature), float(ray_step_m))
    hit = _CACHE.get(key)
    # ids can be reused once an object is freed: confirm it is the same terrain
    if hit is not None and hit[0]() is terrain and hit[1] is terrain.z_grid:
        _CACHE.move_to_end(key)
        return hit[2]
    mv = compute_mount_visibility(terrain, x_m, y_m, mast_height_m, max_range_m,
                                  earth_curvature, ray_step_m)
    _CACHE[key] = (weakref.ref(terrain), terrain.z_grid, mv)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return mv


def clear_mount_cache() -> None:
    _CACHE.clear()


# ── views ────────────────────────────────────────────────────────────────────
@dataclass
class ViewLayers:
    """Everything about a view EXCEPT where it points horizontally: one tilt
    and focal length, at every pan at once. A fixed view intersects it with
    one azimuth cone; a sweep (ptz_schedule) with the azimuths it passes."""
    visible: np.ndarray            # window: visible if the pan put the cell in the cone
    zone: np.ndarray               # window: ZONE_* under the same assumption
    ppm: np.ndarray                # window: px/m of the recorded sample (0 = none)
    in_frame: np.ndarray           # window: cell centre inside range + vertical frame
    center_az_rad: np.ndarray      # window: azimuth of each cell centre
    focal_mm: float
    hfov_deg: float
    vfov_deg: float
    effective_max_range_m: float
    truncated: bool


def view_layers(mv: MountVisibility, camera: CameraConfig, *, tilt_deg: float,
                focal_mm: Optional[float] = None, lens_mode: str = "min",
                max_range_m: Optional[float] = None, visibility_km: float = 40.0,
                weather: str = "") -> ViewLayers:
    """Pan-independent part of a view (see view_mask for the full rules)."""
    focal = view_focal_mm(camera, lens_mode, focal_mm)
    lim = view_limits(camera, focal, mv.max_range_m if max_range_m is None else max_range_m,
                      visibility_km, weather)
    optics = lim.optics
    eff = min(lim.effective_max_range_m, mv.max_range_m)
    v_lo = math.radians(tilt_deg - optics.vfov_deg / 2.0)
    v_hi = math.radians(tilt_deg + optics.vfov_deg / 2.0)

    ppm = np.where(mv.written, optics.ppm_at(np.maximum(mv.sample_slant_m, 1.0)), 0.0)
    framed = (mv.written & (ppm >= lim.min_detect_ppm)
              & (mv.sample_elev_rad >= v_lo) & (mv.sample_elev_rad <= v_hi))
    visible = framed & mv.los_clear
    zone = zone_codes(visible, framed, ppm)
    in_frame = ((mv.center_dist_m <= eff) & (mv.center_dist_m > 1.0)
                & (mv.center_elev_rad >= v_lo) & (mv.center_elev_rad <= v_hi))
    return ViewLayers(
        visible=visible & in_frame, zone=np.where(in_frame, zone, 0), ppm=ppm,
        in_frame=in_frame, center_az_rad=mv.center_az_rad, focal_mm=focal,
        hfov_deg=optics.hfov_deg, vfov_deg=optics.vfov_deg, effective_max_range_m=eff,
        truncated=lim.effective_max_range_m > mv.max_range_m,
    )


def embed(mv: MountVisibility, window_array: np.ndarray, dtype) -> np.ndarray:
    """Place a window-sized array on the full terrain grid (zeros elsewhere)."""
    full = np.zeros(mv.grid_shape, dtype)
    full[mv.window] = window_array
    return full


def view_mask(mv: MountVisibility, camera: CameraConfig, *, pan_deg: float, tilt_deg: float,
              focal_mm: Optional[float] = None, lens_mode: str = "min",
              max_range_m: Optional[float] = None, visibility_km: float = 40.0,
              weather: str = "") -> ViewGrids:
    """Visibility / DORI / px/m of one view, from the cached raster.

    Mirrors calculate_3d_viewshed: a cell is visible when its recorded sample
    has a clear sightline, sits inside the vertical frame and resolves at least
    the view's px/m floor, and the cell centre lies inside the horizontal FOV
    cone, the vertical frame and the (optical / atmospheric) effective range.
    """
    lay = view_layers(mv, camera, tilt_deg=tilt_deg, focal_mm=focal_mm, lens_mode=lens_mode,
                      max_range_m=max_range_m, visibility_km=visibility_km, weather=weather)
    pan = math.radians(pan_deg)
    d_az = np.arctan2(np.sin(lay.center_az_rad - pan), np.cos(lay.center_az_rad - pan))
    in_cone = np.abs(d_az) <= math.radians(lay.hfov_deg / 2.0)
    cone = in_cone & lay.in_frame
    return ViewGrids(
        visibility_mask=embed(mv, lay.visible & in_cone, bool),
        dori_grid=embed(mv, np.where(in_cone, lay.zone, 0), np.int32),
        ppm_grid=embed(mv, np.where(cone & mv.written, lay.ppm, 0.0), np.float32),
        pan_deg=pan_deg, tilt_deg=tilt_deg, focal_mm=lay.focal_mm,
        hfov_deg=lay.hfov_deg, vfov_deg=lay.vfov_deg,
        effective_max_range_m=lay.effective_max_range_m, truncated=lay.truncated,
    )
