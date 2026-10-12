"""Continuous PTZ sweeps -- sector patrol and 360-degree rotation.

A preset tour (ptz_tour) dwells at a few discrete views. A sweeping PTZ moves
continuously at one tilt and zoom, so every ground cell is in frame for a
short while once (or twice) per period. Both numbers per cell follow in
closed form from the cell's azimuth -- no per-angle masks:

- revisit_s: the longest stretch the cell goes UNWATCHED in one period,
- look_s: how long one look lasts. A cell that crosses the frame faster
  than min_dwell_s (operator / analytics minimum) is only a *glimpse*, not
  coverage: a 3-degree tele frame swept at 90 deg/s holds a cell ~30 ms.

Sector patrol a -> b -> a (span S clockwise from a, speed w, dwell t_e at each
end), cell at offset u from a, half-HFOV h. Its frame window in axis space is
[lo, hi] = [u - h, u + h] clipped to [0, S]:

    gap after the outbound pass  = 2 (S - hi) / w + t_e      (0 if hi = S)
    gap after the return pass    = 2 lo / w + t_e            (0 if lo = 0)
    revisit = max(both); look = (hi - lo) / w, or the merged window
    2 (hi - lo) / w + t_e for a cell at a sector end; continuous if the
    sector is narrower than the frame.

360-degree rotation at w: look = 2h / w, revisit = (360 - 2h) / w for all.

Visibility (sightline, vertical frame, range, px/m) comes from the mount's
cached 360-degree raster (mount_visibility.view_layers), so the sweep uses
exactly the same rules as a fixed view. Assumes constant slew speed (no
acceleration ramps) -- the same assumption ptz_tour makes.

No Tk. Pure numpy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Union

import numpy as np

from .models import CameraConfig
from .mount_visibility import embed, mount_visibility, view_layers
from .terrain_loader import TerrainData
from .viewshed_3d import ZONE_OCCLUDED


@dataclass
class SectorPatrol:
    """Pan back and forth between two azimuths (clockwise from ``start``)."""
    start_pan_deg: float
    end_pan_deg: float
    speed_deg_s: float = 20.0
    end_dwell_s: float = 0.0            # pause at each end before turning back
    tilt_deg: float = -5.0
    focal_mm: Optional[float] = None    # explicit zoom; else lens_mode end
    lens_mode: str = "min"

    @property
    def span_deg(self) -> float:
        return (self.end_pan_deg - self.start_pan_deg) % 360.0


@dataclass
class Rotation360:
    """Continuous rotation in one direction."""
    speed_deg_s: float = 20.0
    tilt_deg: float = -5.0
    focal_mm: Optional[float] = None
    lens_mode: str = "min"


Sweep = Union[SectorPatrol, Rotation360]


@dataclass
class SweepResult:
    revisit_grid: np.ndarray     # s; +inf where the cell is never reliably seen
    look_grid: np.ndarray        # s per look; +inf = always in frame; 0 = never in frame
    dori_grid: np.ndarray        # ZONE_* where reliably seen; OCCLUDED where swept but blocked
    seen_mask: np.ndarray        # swept, visible, each look >= min_dwell_s
    glimpse_mask: np.ndarray     # swept and visible, but each look < min_dwell_s
    period_s: float
    focal_mm: float
    hfov_deg: float
    vfov_deg: float
    min_dwell_s: float
    seen_area_m2: float
    glimpse_area_m2: float
    continuous_area_m2: float    # seen and never unwatched (revisit 0)
    occluded_area_m2: float
    mean_revisit_s: float        # over seen cells
    worst_revisit_s: float
    truncated: bool              # the view reaches past the mount raster's range


def sweep_timing(az_deg: np.ndarray, hfov_deg: float, sweep: Sweep):
    """Closed-form (swept, revisit_s, look_s, period_s) per cell azimuth.

    ``az_deg``: azimuth of each cell, degrees clockwise from north.
    """
    w = max(float(sweep.speed_deg_s), 1e-6)
    h = hfov_deg / 2.0
    az = np.mod(az_deg, 360.0)
    if isinstance(sweep, Rotation360) or sweep.span_deg <= 0.0:
        period = 360.0 / w
        swept = np.ones(az.shape, bool)
        if 2.0 * h >= 360.0:
            return swept, np.zeros(az.shape), np.full(az.shape, np.inf), period
        look = 2.0 * h / w
        return swept, np.full(az.shape, period - look), np.full(az.shape, look), period

    span, te = sweep.span_deg, max(float(sweep.end_dwell_s), 0.0)
    period = 2.0 * span / w + 2.0 * te
    u = np.mod(az - sweep.start_pan_deg, 360.0)
    u = np.where(u > span + h, u - 360.0, u)          # just before the start -> negative offset
    swept = (u >= -h) & (u <= span + h)
    lo = np.clip(u - h, 0.0, span)
    hi = np.clip(u + h, 0.0, span)
    at_start, at_end = lo <= 0.0, hi >= span
    gap_start = np.where(at_start, 0.0, 2.0 * lo / w + te)
    gap_end = np.where(at_end, 0.0, 2.0 * (span - hi) / w + te)
    revisit = np.maximum(gap_start, gap_end)
    one_pass = (hi - lo) / w
    look = np.where(at_start & at_end, np.inf,
                    np.where(at_start | at_end, 2.0 * one_pass + te, one_pass))
    return swept, revisit, look, period


def evaluate_sweep(terrain: TerrainData, x_m: float, y_m: float, mast_height_m: float,
                   camera: CameraConfig, sweep: Sweep, *, max_range_m: float = 2000.0,
                   min_dwell_s: float = 0.5, earth_curvature: bool = True,
                   visibility_km: float = 40.0, weather: str = "") -> SweepResult:
    """Revisit / look-time / DORI maps of one sweeping PTZ."""
    mv = mount_visibility(terrain, x_m, y_m, mast_height_m, max_range_m=max_range_m,
                          earth_curvature=earth_curvature)
    lay = view_layers(mv, camera, tilt_deg=sweep.tilt_deg, focal_mm=sweep.focal_mm,
                      lens_mode=sweep.lens_mode, visibility_km=visibility_km, weather=weather)
    swept, revisit, look, period = sweep_timing(np.degrees(lay.center_az_rad), lay.hfov_deg, sweep)

    framed_vis = lay.visible & swept
    seen = framed_vis & (look >= min_dwell_s)
    glimpse = framed_vis & (look < min_dwell_s)
    occluded = swept & (lay.zone == ZONE_OCCLUDED)
    dori = np.where(seen, lay.zone, np.where(occluded, ZONE_OCCLUDED, 0))
    revisit_w = np.where(seen, revisit, np.inf)

    cell_a = terrain.cell_size_m ** 2
    vals = revisit_w[seen]
    revisit_full = np.full(mv.grid_shape, np.inf)      # outside the raster: never seen
    revisit_full[mv.window] = revisit_w
    return SweepResult(
        revisit_grid=revisit_full,
        look_grid=embed(mv, np.where(framed_vis, look, 0.0), np.float64),
        dori_grid=embed(mv, dori, np.int32),
        seen_mask=embed(mv, seen, bool),
        glimpse_mask=embed(mv, glimpse, bool),
        period_s=period, focal_mm=lay.focal_mm, hfov_deg=lay.hfov_deg, vfov_deg=lay.vfov_deg,
        min_dwell_s=min_dwell_s,
        seen_area_m2=float(np.count_nonzero(seen)) * cell_a,
        glimpse_area_m2=float(np.count_nonzero(glimpse)) * cell_a,
        continuous_area_m2=float(np.count_nonzero(seen & (revisit <= 1e-9))) * cell_a,
        occluded_area_m2=float(np.count_nonzero(occluded)) * cell_a,
        mean_revisit_s=float(vals.mean()) if vals.size else math.inf,
        worst_revisit_s=float(vals.max()) if vals.size else math.inf,
        truncated=lay.truncated,
    )
