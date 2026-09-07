"""PTZ preset-tour coverage and revisit-time analysis.

A PTZ camera does not see everything at once: it cycles through a list of
presets, dwelling at each. This module runs the authoritative viewshed engine
once per preset (same lens position, same mount) and adds the temporal layer:

- combined coverage — the best DORI level any preset ever achieves at a cell
  (:func:`viewshed_3d.calculate_multi_camera_viewshed`),
- revisit time — the longest stretch a covered cell goes *unwatched* during one
  tour period, from the preset dwell times and the pan/tilt slew between them.

No Tk. Pure functions + numpy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from .models import CameraConfig
from .terrain_loader import TerrainData
from .viewshed_3d import (
    CameraPlacement,
    MultiViewshedResult,
    calculate_multi_camera_viewshed,
)


@dataclass
class PTZPreset:
    name: str
    pan_deg: float
    tilt_deg: float = -5.0            # < 0 = down
    lens_mode: str = "max"           # presets are usually zoomed in
    dwell_s: float = 5.0


@dataclass
class PTZTour:
    x_m: float
    y_m: float
    mast_height_m: float
    camera: CameraConfig
    presets: List[PTZPreset] = field(default_factory=list)
    max_range_m: float = 2000.0
    slew_speed_deg_s: float = 90.0    # typical mid-range PTZ pan/tilt speed


@dataclass
class PTZTourResult:
    combined: MultiViewshedResult    # union of every preset (best DORI per cell)
    revisit_grid: np.ndarray         # seconds; +inf where no preset ever covers
    tour_period_s: float
    active_dwell_s: float            # sum of preset dwell (excludes slew)
    slew_total_s: float
    mean_revisit_s: float            # over cells covered at least once
    worst_revisit_s: float
    continuous_area_m2: float        # always in some preset's frame (revisit ~ 0)
    intermittent_area_m2: float      # covered but with a revisit gap
    never_seen_area_m2: float        # inside the union cone, no preset frames it
    preset_labels: List[str]


def _tour_timeline(tour: PTZTour):
    """(start, end, preset_index) windows over one period, plus the period."""
    n = len(tour.presets)
    speed = max(tour.slew_speed_deg_s, 1.0)
    windows = []
    t = 0.0
    for i, ps in enumerate(tour.presets):
        windows.append((t, t + ps.dwell_s, i))
        t += ps.dwell_s
        nxt = tour.presets[(i + 1) % n]
        d_pan = abs((nxt.pan_deg - ps.pan_deg + 180.0) % 360.0 - 180.0)
        d_tilt = abs(nxt.tilt_deg - ps.tilt_deg)
        t += max(d_pan, d_tilt) / speed
    return windows, t


def _revisit_for_mask(mask: int, windows, period: float) -> float:
    covering = [w for w in windows if (mask >> w[2]) & 1]
    if not covering:
        return math.inf
    covering.sort()
    worst = 0.0
    for j, (_s, e, _i) in enumerate(covering):
        nxt_start = covering[(j + 1) % len(covering)][0]
        gap = (nxt_start - e) % period
        worst = max(worst, gap)
    return worst


def evaluate_ptz_tour(terrain: TerrainData, tour: PTZTour, *,
                      earth_curvature: bool = True, visibility_km: float = 40.0,
                      weather: str = "") -> Optional[PTZTourResult]:
    if not tour.presets:
        return None

    placements = [
        CameraPlacement(
            x_m=tour.x_m, y_m=tour.y_m, mast_height_m=tour.mast_height_m,
            camera=tour.camera, lens_mode=ps.lens_mode, pan_deg=ps.pan_deg,
            tilt_deg=ps.tilt_deg, max_range_m=tour.max_range_m, label=ps.name,
        )
        for ps in tour.presets
    ]
    combined = calculate_multi_camera_viewshed(
        terrain, placements, earth_curvature=earth_curvature,
        visibility_km=visibility_km, weather=weather,
    )
    assert combined is not None

    windows, period = _tour_timeline(tour)

    # per-cell bitmask of which presets frame it
    mask = np.zeros(combined.visibility_mask.shape, dtype=np.int64)
    for i, r in enumerate(combined.per_camera):
        mask |= (r.visibility_mask.astype(np.int64) << i)

    revisit = np.full(mask.shape, np.inf, dtype=np.float64)
    for m in np.unique(mask):
        if m == 0:
            continue
        revisit[mask == m] = _revisit_for_mask(int(m), windows, period)

    cell_a = terrain.cell_size_m ** 2
    covered = np.isfinite(revisit)
    cov_vals = revisit[covered]
    active_dwell = sum(ps.dwell_s for ps in tour.presets)

    return PTZTourResult(
        combined=combined,
        revisit_grid=revisit,
        tour_period_s=period,
        active_dwell_s=active_dwell,
        slew_total_s=period - active_dwell,
        mean_revisit_s=float(cov_vals.mean()) if cov_vals.size else 0.0,
        worst_revisit_s=float(cov_vals.max()) if cov_vals.size else 0.0,
        continuous_area_m2=float(np.count_nonzero(covered & (revisit < 1e-6)) * cell_a),
        intermittent_area_m2=float(np.count_nonzero(covered & (revisit >= 1e-6)) * cell_a),
        never_seen_area_m2=float(np.count_nonzero(~covered & (combined.dori_grid != 0)) * cell_a),
        preset_labels=[ps.name for ps in tour.presets],
    )
