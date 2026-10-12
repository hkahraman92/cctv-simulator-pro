"""Mixed perimeter (fixed + PTZ) evaluation.

generate_perimeter_plan(..., ptz_camera=, ptz_every_n=) co-mounts a PTZ on
every Nth fixed pole. This module answers what that layout buys, on the
whole terrain grid, with the engines the single-camera tools already use:

  fixed poles  -> calculate_multi_camera_viewshed     (continuous coverage)
  each PTZ     -> ptz_schedule.evaluate_sweep          (patrol: revisit gaps)
  together     -> ptz_fusion.fuse_coverage             (CONTINUOUS / INTERMITTENT / NONE)
  alarm        -> ptz_fusion.cue_response per PTZ, fastest PTZ wins, and
                  detect_then_identify: seconds from a fixed-camera detection
                  to a PTZ holding the target at ``cue_target_ppm``.

Each PTZ's mount raster is computed once (mount_visibility cache) and shared
by its sweep and its cue map. No Tk.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from .models import CameraConfig
from .perimeter_planner import PerimeterPlanResult, PlacedCamera
from .ptz_fusion import (
    CueResponse,
    FusedCoverage,
    PTZCoverage,
    cue_response,
    detect_then_identify,
    fuse_coverage,
)
from .ptz_schedule import Rotation360, SectorPatrol, SweepResult, evaluate_sweep
from .terrain_loader import TerrainData
from .viewshed_3d import (
    PPM_IDENT,
    ZONE_DETECT,
    CameraPlacement,
    MultiViewshedResult,
    calculate_multi_camera_viewshed,
)


def plan_placements(plan: PerimeterPlanResult, camera: CameraConfig) -> List[CameraPlacement]:
    """The plan's fixed poles as viewshed placements, at the focal the planner
    actually used for each pole (corner guards included)."""
    return [CameraPlacement(
        x_m=c.x_m, y_m=c.y_m, mast_height_m=c.mast_height_m, camera=camera,
        pan_deg=c.pan_deg, tilt_deg=c.tilt_deg, max_range_m=c.effective_range_m,
        focal_mm_override=c.focal_mm, label=f"Direk {c.pole_id}")
        for c in plan.placed_cameras]


def patrol_for(ptz: PlacedCamera, speed_deg_s: float, end_dwell_s: float):
    """The sweep a planned PTZ runs: rotation for a 360 deg patrol, else a
    sector centred on its pan."""
    if ptz.patrol_span_deg >= 359.9:
        return Rotation360(speed_deg_s=speed_deg_s, tilt_deg=ptz.tilt_deg, focal_mm=ptz.focal_mm)
    half = ptz.patrol_span_deg / 2.0
    return SectorPatrol((ptz.pan_deg - half) % 360.0, (ptz.pan_deg + half) % 360.0,
                        speed_deg_s=speed_deg_s, end_dwell_s=end_dwell_s,
                        tilt_deg=ptz.tilt_deg, focal_mm=ptz.focal_mm)


@dataclass
class MixedPerimeterResult:
    static: Optional[MultiViewshedResult]   # fixed poles; None if the plan has none
    sweeps: List[SweepResult]               # one per PTZ, plan order
    fused: FusedCoverage
    cue_s: np.ndarray                       # fastest PTZ's response to a cue anywhere (+inf: none)
    detect_to_identify_s: np.ndarray        # cue_s where a fixed pole detects, +inf elsewhere
    cue_target_ppm: float
    detected_area_m2: float                 # fixed poles detect (>= ZONE_DETECT)
    identifiable_area_m2: float             # ... and some PTZ can then reach cue_target_ppm
    median_d2i_s: float
    p90_d2i_s: float
    worst_d2i_s: float
    ptz_camera_name: str = ""
    speed_deg_s: float = 0.0

    @property
    def identifiable_pct(self) -> float:
        """Share of the fixed-detected area a PTZ can follow up on."""
        return 100.0 * self.identifiable_area_m2 / self.detected_area_m2 if self.detected_area_m2 else 0.0


def evaluate_mixed_perimeter(terrain: TerrainData, plan: PerimeterPlanResult,
                             fixed_camera: CameraConfig, ptz_camera: CameraConfig, *,
                             speed_deg_s: float = 20.0, end_dwell_s: float = 2.0,
                             min_dwell_s: float = 0.5, cue_target_ppm: float = PPM_IDENT,
                             slew_speed_deg_s: float = 90.0, zoom_full_sweep_s: float = 3.0,
                             settle_s: float = 1.5, max_range_cap_m: float = 3000.0,
                             visibility_km: float = 40.0, weather: str = "",
                             earth_curvature: bool = True) -> MixedPerimeterResult:
    if not plan.ptz_cameras:
        raise ValueError("Planda PTZ yok: 'her N direkte bir PTZ' seçip yeniden dizin.")
    atm = dict(visibility_km=visibility_km, weather=weather)
    static = None
    if plan.placed_cameras:
        static = calculate_multi_camera_viewshed(
            terrain, plan_placements(plan, fixed_camera), earth_curvature=earth_curvature, **atm)

    sweeps: List[SweepResult] = []
    cues: List[CueResponse] = []
    for p in plan.ptz_cameras:
        # One raster per mast: the cue reach (tele) is the longer of the two
        # ranges; the sweep's optical limit cuts the wide view off anyway.
        rng = float(min(max(p.effective_range_m, 50.0), max_range_cap_m))
        sweeps.append(evaluate_sweep(
            terrain, p.x_m, p.y_m, p.mast_height_m, ptz_camera,
            patrol_for(p, speed_deg_s, end_dwell_s), max_range_m=rng,
            min_dwell_s=min_dwell_s, earth_curvature=earth_curvature, **atm))
        cues.append(cue_response(
            terrain, p.x_m, p.y_m, p.mast_height_m, ptz_camera,
            home_pan_deg=p.pan_deg, home_tilt_deg=p.tilt_deg, home_focal_mm=p.focal_mm,
            target_ppm=cue_target_ppm, slew_speed_deg_s=slew_speed_deg_s,
            zoom_full_sweep_s=zoom_full_sweep_s, settle_s=settle_s, max_range_m=rng,
            earth_curvature=earth_curvature, **atm))

    fused = fuse_coverage(static, [PTZCoverage.from_sweep(s, f"PTZ direk {p.pole_id}")
                                   for s, p in zip(sweeps, plan.ptz_cameras)],
                          terrain.cell_size_m)
    # The operator sends whichever PTZ gets there first.
    cue_s = np.minimum.reduce([c.response_s for c in cues])
    if static is not None:
        d2i = np.minimum.reduce([detect_then_identify(static, c) for c in cues])
        detected = static.dori_grid >= ZONE_DETECT
    else:
        d2i = np.full(cue_s.shape, np.inf)
        detected = np.zeros(cue_s.shape, bool)
    vals = d2i[np.isfinite(d2i)]
    cell_a = terrain.cell_size_m ** 2
    return MixedPerimeterResult(
        static=static, sweeps=sweeps, fused=fused, cue_s=cue_s, detect_to_identify_s=d2i,
        cue_target_ppm=cue_target_ppm,
        detected_area_m2=float(np.count_nonzero(detected)) * cell_a,
        identifiable_area_m2=float(vals.size) * cell_a,
        median_d2i_s=float(np.median(vals)) if vals.size else math.inf,
        p90_d2i_s=float(np.percentile(vals, 90)) if vals.size else math.inf,
        worst_d2i_s=float(vals.max()) if vals.size else math.inf,
        ptz_camera_name=getattr(ptz_camera, "name", "") or "",
        speed_deg_s=speed_deg_s,
    )
