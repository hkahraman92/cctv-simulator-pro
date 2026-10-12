"""Fixed cameras + PTZ together: per-level coverage class, and slew-to-cue time.

1. fuse_coverage -- for each DORI level, every ground cell is

     CONTINUOUS    some fixed camera reaches the level there (always watched),
     INTERMITTENT  only a PTZ (tour or sweep) reaches it; revisit = its gap,
     NONE          nobody reaches it.

   A cell a fixed camera already covers never shows a PTZ "revisit gap".
   With several PTZs the effective revisit is the smallest single-PTZ gap --
   a conservative upper bound (two PTZs' gaps may interleave and do better).
   For a preset tour the revisit grid is the tour's (any preset watching the
   cell); its DORI is the best preset's, so a level reached by only one
   preset is credited with the tour-wide revisit -- an approximation.

2. cue_response -- "tespitten teşhise süre": a detection anywhere (a fixed
   camera, analytics, a radar) cues the PTZ; how long until it holds the
   target at ``target_ppm`` (Identification 250 px/m by default)? Per cell:
   aim at the cell centre, zoom to the focal that resolves target_ppm at the
   cell's slant range (calculations.focal_for_ppm -- Kural 1), then the same
   slew/zoom/settle model preset tours use (ptz_tour.move_time_s). Cells the
   PTZ cannot identify at all are +inf with a reason code.

No Tk. Pure numpy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np

from .atmosphere import band_for_camera, usable_range_m
from .calculations import focal_for_ppm
from .models import CameraConfig
from .mount_visibility import mount_visibility
from .ptz_tour import move_time_s
from .terrain_loader import TerrainData
from .viewshed_3d import (
    PPM_IDENT,
    ZONE_DETECT,
    ZONE_IDENT,
    ZONE_OBSERVE,
    ZONE_RECOG,
    MultiViewshedResult,
)

LEVELS: Dict[str, int] = {"detect": ZONE_DETECT, "observe": ZONE_OBSERVE,
                          "recog": ZONE_RECOG, "ident": ZONE_IDENT}
NONE, INTERMITTENT, CONTINUOUS = 0, 1, 2


@dataclass
class PTZCoverage:
    """What one PTZ contributes: best DORI per cell and its revisit gap."""
    dori_grid: np.ndarray
    revisit_grid: np.ndarray          # s; +inf = never watched
    label: str = "PTZ"

    @classmethod
    def from_sweep(cls, result, label: str = "PTZ tarama") -> "PTZCoverage":
        return cls(result.dori_grid, result.revisit_grid, label)

    @classmethod
    def from_tour(cls, result, label: str = "PTZ turu") -> "PTZCoverage":
        return cls(result.combined.dori_grid, result.revisit_grid, label)


@dataclass
class FusedCoverage:
    status: Dict[str, np.ndarray]             # level -> NONE / INTERMITTENT / CONTINUOUS
    revisit: Dict[str, np.ndarray]            # level -> s (0 continuous, +inf none)
    continuous_area_m2: Dict[str, float]
    intermittent_area_m2: Dict[str, float]
    worst_intermittent_revisit_s: Dict[str, float]
    labels: List[str] = field(default_factory=list)


def fuse_coverage(static: Optional[MultiViewshedResult], ptzs: Sequence[PTZCoverage],
                  cell_size_m: float) -> FusedCoverage:
    grids = ([static.dori_grid] if static is not None else []) + [p.dori_grid for p in ptzs]
    if not grids:
        raise ValueError("fuse_coverage: no fixed or PTZ coverage given")
    shape = grids[0].shape
    if any(g.shape != shape for g in grids) or any(p.revisit_grid.shape != shape for p in ptzs):
        raise ValueError("fuse_coverage: all grids must come from the same terrain")

    cell_a = cell_size_m ** 2
    out = FusedCoverage({}, {}, {}, {}, {}, labels=[p.label for p in ptzs])
    for name, zone in LEVELS.items():
        continuous = static.dori_grid >= zone if static is not None else np.zeros(shape, bool)
        ptz_gap = np.full(shape, np.inf)
        for p in ptzs:
            reaches = (p.dori_grid >= zone) & np.isfinite(p.revisit_grid)
            ptz_gap = np.where(reaches, np.minimum(ptz_gap, p.revisit_grid), ptz_gap)
        intermittent = ~continuous & np.isfinite(ptz_gap)
        status = np.where(continuous, CONTINUOUS, np.where(intermittent, INTERMITTENT, NONE))
        revisit = np.where(continuous, 0.0, ptz_gap)
        out.status[name] = status.astype(np.int8)
        out.revisit[name] = revisit
        out.continuous_area_m2[name] = float(np.count_nonzero(continuous)) * cell_a
        out.intermittent_area_m2[name] = float(np.count_nonzero(intermittent)) * cell_a
        gaps = revisit[intermittent]
        out.worst_intermittent_revisit_s[name] = float(gaps.max()) if gaps.size else 0.0
    return out


# ── slew-to-cue ──────────────────────────────────────────────────────────────
CUE_OK, CUE_NO_LOS, CUE_LENS, CUE_TILT, CUE_ATMOSPHERE, CUE_OUT_OF_RANGE = 0, 1, 2, 3, 4, 5
CUE_REASONS = {
    CUE_OK: "ulaşılabilir",
    CUE_NO_LOS: "görüş hattı yok (arazi engeli)",
    CUE_LENS: "max odak yetersiz (hedef px/m'ye ulaşılamıyor)",
    CUE_TILT: "tilt sınırı dışında",
    CUE_ATMOSPHERE: "atmosferik menzil dışında",
    CUE_OUT_OF_RANGE: "analiz menzili dışında",
}


@dataclass
class CueResponse:
    response_s: np.ndarray        # s to hold the target at target_ppm; +inf = impossible
    reason: np.ndarray            # CUE_* code per cell
    focal_mm: np.ndarray          # focal the PTZ zooms to (nan where impossible)
    target_ppm: float
    reachable_area_m2: float
    mean_response_s: float
    p90_response_s: float
    worst_response_s: float


def cue_response(terrain: TerrainData, x_m: float, y_m: float, mast_height_m: float,
                 camera: CameraConfig, *, home_pan_deg: float, home_tilt_deg: float,
                 home_focal_mm: Optional[float] = None, target_ppm: float = PPM_IDENT,
                 slew_speed_deg_s: float = 90.0, zoom_full_sweep_s: float = 3.0,
                 settle_s: float = 1.5, tilt_min_deg: float = -90.0, tilt_max_deg: float = 20.0,
                 max_range_m: float = 2000.0, visibility_km: float = 40.0, weather: str = "",
                 earth_curvature: bool = True) -> CueResponse:
    """Seconds for a PTZ parked at (home pan/tilt/focal) to aim at each cell
    and hold it at ``target_ppm`` (see module docstring)."""
    mv = mount_visibility(terrain, x_m, y_m, mast_height_m, max_range_m=max_range_m,
                          earth_curvature=earth_curvature)
    home_focal = camera.focal_min_mm if home_focal_mm is None else home_focal_mm

    # Aim AND zoom for the cell centre. (The raster's recorded sample is the
    # last one written into the cell -- its far edge -- which over-zoomed by a
    # few percent.) The sightline verdict still comes from the raster.
    slant = np.maximum(mv.center_dist_m / np.cos(mv.center_elev_rad), 1.0)
    f_req = focal_for_ppm(camera, target_ppm, slant)
    f_use = np.maximum(f_req, camera.focal_min_mm)
    aim_pan = np.degrees(mv.center_az_rad)
    aim_tilt = np.degrees(mv.center_elev_rad)
    atm_cap = usable_range_m(math.inf, visibility_km,
                             band_for_camera(camera.sensor_name, camera.model_name), weather)

    reason = np.full(mv.written.shape, CUE_OK, np.int8)
    reason = np.where(slant > atm_cap, CUE_ATMOSPHERE, reason)
    reason = np.where((aim_tilt < tilt_min_deg) | (aim_tilt > tilt_max_deg), CUE_TILT, reason)
    reason = np.where(f_req > camera.focal_max_mm, CUE_LENS, reason)
    reason = np.where(~(mv.written & mv.los_clear), CUE_NO_LOS, reason)
    reason = np.where(mv.center_dist_m > mv.max_range_m, CUE_OUT_OF_RANGE, reason)
    ok = reason == CUE_OK

    slew, zoom, settle = move_time_s(aim_pan - home_pan_deg, aim_tilt - home_tilt_deg,
                                     f_use - home_focal, camera,
                                     slew_speed_deg_s=slew_speed_deg_s,
                                     zoom_full_sweep_s=zoom_full_sweep_s, settle_s=settle_s)
    resp = np.where(ok, slew + zoom + settle, np.inf)

    full_resp = np.full(mv.grid_shape, np.inf)
    full_reason = np.full(mv.grid_shape, CUE_OUT_OF_RANGE, np.int8)
    full_focal = np.full(mv.grid_shape, np.nan)
    full_resp[mv.window] = resp
    full_reason[mv.window] = reason
    full_focal[mv.window] = np.where(ok, f_use, np.nan)
    vals = resp[ok]
    return CueResponse(
        response_s=full_resp, reason=full_reason, focal_mm=full_focal, target_ppm=target_ppm,
        reachable_area_m2=float(vals.size) * terrain.cell_size_m ** 2,
        mean_response_s=float(vals.mean()) if vals.size else math.inf,
        p90_response_s=float(np.percentile(vals, 90)) if vals.size else math.inf,
        worst_response_s=float(vals.max()) if vals.size else math.inf,
    )


def detect_then_identify(static: MultiViewshedResult, cue: CueResponse) -> np.ndarray:
    """Seconds from a fixed-camera DETECTION to PTZ identification, per cell:
    the cue response where some fixed camera continuously detects, +inf
    elsewhere (nothing would trigger the cue there)."""
    return np.where(static.dori_grid >= ZONE_DETECT, cue.response_s, np.inf)
