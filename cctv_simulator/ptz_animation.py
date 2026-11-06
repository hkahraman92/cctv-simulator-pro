"""Animated PTZ sweep: where the camera looks *now*, over terrain and buildings.

``ptz_schedule.evaluate_sweep`` answers "over a full period, how long is
each cell unwatched". This module answers the operator's question -- "show me
the camera turning" -- with one frame per instant:

* the current field of view, every cell coloured by its DORI class when the
  line of sight is clear, and dark where terrain or a building hides it
  (the shadow moves with the camera, so ridges and blocks read at a glance);
* a fading trail of what the sweep has just passed (radar persistence);
* a faint memory of everything seen so far in this playback.

Visibility comes from the cached 360-degree mount raster
(``mount_visibility`` + ``view_layers``) -- the same rules as the sweep
evaluator and the fixed-view engine (Kural 1). A frame is a handful of array
operations on the mount's window (~1-3 ms for 300x300 cells); nothing is
re-marched while playing.

Pan motion: ``Rotation360`` turns clockwise at constant speed;
``SectorPatrol`` goes start -> end (clockwise), dwells, comes back, dwells,
with instantaneous turnarounds -- the same constant-speed model as
``ptz_schedule.sweep_timing``.

No Tk. Colours are passed in (the UI maps tokens), so this stays testable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

from .models import CameraConfig
from .mount_visibility import mount_visibility, view_layers
from .ptz_schedule import Rotation360, SectorPatrol, Sweep
from .terrain_loader import TerrainData
from .viewshed_3d import ZONE_OCCLUDED

RGBA = Tuple[int, int, int, int]


def sweep_period_s(sweep: Sweep) -> float:
    if isinstance(sweep, Rotation360):
        return 360.0 / max(sweep.speed_deg_s, 1e-6)
    span = sweep.span_deg
    return 2.0 * span / max(sweep.speed_deg_s, 1e-6) + 2.0 * max(sweep.end_dwell_s, 0.0)


def pan_at(sweep: Sweep, t_s: float, start_pan_deg: float = 0.0) -> float:
    """Pan azimuth (deg, clockwise from north) at time ``t_s``."""
    if isinstance(sweep, Rotation360):
        return (start_pan_deg + sweep.speed_deg_s * t_s) % 360.0
    assert isinstance(sweep, SectorPatrol)
    span, w, dwell = sweep.span_deg, max(sweep.speed_deg_s, 1e-6), max(sweep.end_dwell_s, 0.0)
    leg = span / w
    t = t_s % sweep_period_s(sweep)
    if t < leg:                                   # start -> end
        off = w * t
    elif t < leg + dwell:                         # dwell at the end
        off = span
    elif t < 2 * leg + dwell:                     # end -> start
        off = span - w * (t - leg - dwell)
    else:                                         # dwell at the start
        off = 0.0
    return (sweep.start_pan_deg + off) % 360.0


@dataclass
class Frame:
    t_s: float
    pan_deg: float
    rgba: np.ndarray          # (rows, cols, 4) uint8 on the mount WINDOW, z_grid orientation
    window: Tuple[slice, slice]
    lit_area_m2: float        # visible in the current frame
    shadow_area_m2: float     # in the current frame but hidden by terrain / buildings
    seen_area_m2: float       # visible at least once since playback started


class SweepPlayback:
    """Precomputes the pan-independent layers once; ``frame(t)`` is cheap."""

    def __init__(self, terrain: TerrainData, x_m: float, y_m: float, mast_height_m: float,
                 camera: CameraConfig, sweep: Sweep, *, colors: Dict[int, RGBA],
                 shadow_rgba: RGBA, max_range_m: float = 1500.0, visibility_km: float = 40.0,
                 weather: str = "", trail_s: Optional[float] = None, memory_alpha: int = 46,
                 start_pan_deg: float = 0.0):
        self.sweep = sweep
        self.start_pan_deg = start_pan_deg
        self.mv = mount_visibility(terrain, x_m, y_m, mast_height_m, max_range_m=max_range_m)
        lay = view_layers(self.mv, camera, tilt_deg=sweep.tilt_deg, focal_mm=sweep.focal_mm,
                          lens_mode=sweep.lens_mode, max_range_m=max_range_m,
                          visibility_km=visibility_km, weather=weather)
        self.layers = lay
        self.hfov_deg = lay.hfov_deg
        self.reach_m = lay.effective_max_range_m
        self.period_s = sweep_period_s(sweep)
        self.az_deg = np.degrees(lay.center_az_rad) % 360.0
        self.visible = lay.visible
        self.shadow = lay.in_frame & ~lay.visible & (lay.zone == ZONE_OCCLUDED)
        self.cell_area = terrain.cell_size_m ** 2
        # Trail: by default the time the cone needs to move one FOV width.
        speed = max(sweep.speed_deg_s, 1e-6)
        self.trail_s = trail_s if trail_s is not None else max(0.6, 1.5 * lay.hfov_deg / speed)
        self.memory_alpha = memory_alpha
        base = np.zeros(lay.zone.shape + (4,), np.uint8)
        for zone, rgba in colors.items():
            base[(lay.zone == zone) & lay.visible] = rgba
        self.base = base
        self.shadow_rgba = np.array(shadow_rgba, np.uint8)
        self.reset()

    def reset(self):
        self.last_lit = np.full(self.visible.shape, -np.inf)
        self.ever = np.zeros(self.visible.shape, bool)

    def in_cone(self, pan_deg: float) -> np.ndarray:
        d = (self.az_deg - pan_deg + 180.0) % 360.0 - 180.0
        return np.abs(d) <= self.hfov_deg / 2.0

    def frame(self, t_s: float) -> Frame:
        pan = pan_at(self.sweep, t_s, self.start_pan_deg)
        cone = self.in_cone(pan)
        lit = cone & self.visible
        shade = cone & self.shadow
        self.last_lit[lit] = t_s
        self.ever |= lit

        age = t_s - self.last_lit                               # inf where never lit
        fade = np.where(np.isfinite(age), np.exp(-np.maximum(age, 0.0) / self.trail_s), 0.0)
        alpha_base = self.base[..., 3].astype(np.float32)
        alpha = np.maximum(alpha_base * fade, np.where(self.ever, self.memory_alpha, 0))
        out = self.base.copy()
        out[..., 3] = np.where(self.ever, alpha, 0).astype(np.uint8)
        out[lit] = self.base[lit]                               # full strength in the cone
        out[shade] = self.shadow_rgba
        return Frame(
            t_s=t_s, pan_deg=pan, rgba=out, window=self.mv.window,
            lit_area_m2=float(lit.sum()) * self.cell_area,
            shadow_area_m2=float(shade.sum()) * self.cell_area,
            seen_area_m2=float(self.ever.sum()) * self.cell_area,
        )


def sight_profile(terrain: TerrainData, x_m: float, y_m: float, mast_height_m: float,
                  pan_deg: float, reach_m: float, n: int = 160):
    """Ground + surface profile along the current pan and which samples the
    lens sees (running max elevation angle over the SURFACE, like
    march_rays without earth curvature -- a picture, not the authority).
    Returns (dist, ground, surface, visible, cam_z)."""
    a = math.radians(pan_deg)
    d = np.linspace(0.0, max(reach_m, 10.0), n)
    xs, ys = x_m + d * math.sin(a), y_m + d * math.cos(a)
    surf = np.array([terrain.get_elevation_at(x, y) for x, y in zip(xs, ys)])
    ground = (np.array([terrain.ground_elevation_at(x, y) for x, y in zip(xs, ys)])
              if terrain.has_obstacles else surf)
    cam_z = terrain.ground_elevation_at(x_m, y_m) + mast_height_m
    ang = np.arctan2(surf - cam_z, np.maximum(d, 0.5))
    horizon = np.maximum.accumulate(np.concatenate(([-np.inf], ang[:-1])))
    visible = ang >= horizon - 1e-9
    visible[0] = True
    return d, ground, surf, visible, cam_z
