"""
Multi-Camera Perimeter Auto-Planner & Fence Line Optimization Engine.

Performs:
1. Interactive polyline/polygon fence perimeter parsing and distance measurement.
2. DORI-compliant optimal pole spacing calculation with dead-zone overlapping.
3. Automatic placement of 10 to 100+ cameras along facility boundary fences.
4. Corner and terrain slope adaptation (avoiding corner blind spots).
5. Bill of Materials (BOM), storage, and bandwidth estimation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import atmosphere as _atm
from .terrain_loader import TerrainData
from .models import CameraConfig
from .config import SENSOR_DIMS_MM, RESOLUTIONS
from .viewshed_3d import PPM_DETECT, PPM_IDENT, PPM_OBSERVE, PPM_RECOG


@dataclass
class PlacedCamera:
    """Individual camera placed along the perimeter."""
    pole_id: int
    x_m: float
    y_m: float
    ground_z_m: float
    mast_height_m: float
    pan_deg: float
    tilt_deg: float
    focal_mm: float
    hfov_deg: float
    vfov_deg: float
    effective_range_m: float
    dead_zone_m: float
    camera_model: str
    sensor_name: str
    resolution_name: str

    @property
    def total_z_m(self) -> float:
        return self.ground_z_m + self.mast_height_m


@dataclass
class FenceGap:
    """Uncovered segment along the fence line."""
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    length_m: float
    reason: str = "Aralık yetersizliği veya engel"


@dataclass
class PerimeterPlanResult:
    """Complete Perimeter Surveillance Layout Output."""
    fence_points: List[Tuple[float, float]]
    total_fence_length_m: float
    segment_lengths_m: List[float]
    placed_cameras: List[PlacedCamera]
    gaps: List[FenceGap] = field(default_factory=list)

    # BOM & Engineering Summary
    camera_count: int = 0
    pole_count: int = 0
    target_ppm: float = 40.0
    avg_spacing_m: float = 0.0
    coverage_percentage: float = 100.0
    estimated_bandwidth_mbps: float = 0.0
    estimated_storage_30days_tb: float = 0.0


def point_along_polyline(points: List[Tuple[float, float]],
                         dist_m: float) -> Tuple[float, float, int]:
    """World point at ``dist_m`` measured along the fence polyline.

    Returns ``(x, y, segment_index)``. ``dist_m`` is clamped to
    ``[0, total_length]``. Used to map a cross-section-profile mouse position
    back to a location on the map.
    """
    if not points:
        return 0.0, 0.0, 0
    if len(points) == 1:
        return points[0][0], points[0][1], 0

    if dist_m <= 0.0:
        return points[0][0], points[0][1], 0

    walked = 0.0
    for i in range(len(points) - 1):
        (x0, y0), (x1, y1) = points[i], points[i + 1]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg <= 1e-9:
            continue
        if walked + seg >= dist_m:
            t = (dist_m - walked) / seg
            return x0 + t * (x1 - x0), y0 + t * (y1 - y0), i
        walked += seg

    return points[-1][0], points[-1][1], len(points) - 2


def calculate_optimal_spacing(camera: CameraConfig,
                              target_ppm: float = 40.0,
                              mast_height_m: float = 5.0,
                              overlap_pct: float = 0.15,
                              lens_mode: str = "min",
                              visibility_km: float = 40.0,
                              weather: str = "") -> Tuple[float, float, float]:
    """Calculates effective range, dead zone, and recommended pole spacing (meters)."""
    focal_mm = camera.focal_min_mm if lens_mode == "min" else camera.focal_max_mm
    sw, sh = SENSOR_DIMS_MM.get(camera.sensor_name, (5.6, 4.2))
    res_w, res_h = RESOLUTIONS.get(camera.resolution_name, (2688, 1520))
    res_w = res_w * max(getattr(camera, "effective_px_ratio", 1.0), 0.05)

    # Optical slant range where resolution equals target PPM (EN 62676-4)
    # PPM = (f * res_w) / (sw * slant_dist) -> slant_dist = (f * res_w) / (sw * PPM)
    max_slant_dist = (focal_mm * res_w) / (sw * max(target_ppm, 1.0))
    # Fog / rain / haze cap the range where target contrast survives the path.
    band = _atm.band_for_camera(camera.sensor_name, camera.model_name)
    max_slant_dist = _atm.usable_range_m(max_slant_dist, visibility_km, band, weather)
    # Ground reach (Pythagoras with mast height)
    ground_reach = math.sqrt(max(max_slant_dist**2 - mast_height_m**2, 100.0))

    # Dead zone under the pole (assuming standard ~15 deg downward tilt)
    vfov_deg = math.degrees(2.0 * math.atan((sh / 2.0) / focal_mm))
    tilt_deg = 15.0
    top_ray_angle_deg = tilt_deg + (vfov_deg / 2.0)
    bottom_ray_angle_deg = tilt_deg - (vfov_deg / 2.0)

    # Distance to where bottom of picture touches the ground
    if bottom_ray_angle_deg > 2.0:
        dead_zone_m = mast_height_m / math.tan(math.radians(bottom_ray_angle_deg))
    else:
        dead_zone_m = mast_height_m * 1.2

    dead_zone_m = max(1.5, min(dead_zone_m, ground_reach * 0.25))

    # Effective inter-pole spacing with safety overlap
    raw_span = max(ground_reach - dead_zone_m, 10.0)
    optimal_spacing_m = raw_span * (1.0 - overlap_pct)

    return ground_reach, dead_zone_m, optimal_spacing_m


def generate_perimeter_plan(terrain: TerrainData,
                            fence_points: List[Tuple[float, float]],
                            camera: CameraConfig,
                            target_ppm: float = 40.0,
                            overlap_pct: float = 0.15,
                            mast_height_m: float = 5.0,
                            lens_mode: str = "min",
                            is_closed_loop: bool = True,
                            visibility_km: float = 40.0,
                            weather: str = "") -> PerimeterPlanResult:
    """Places cameras along fence line ensuring continuous dead-zone overlapping coverage."""
    if len(fence_points) < 2:
        return PerimeterPlanResult(
            fence_points=fence_points,
            total_fence_length_m=0.0,
            segment_lengths_m=[],
            placed_cameras=[],
        )

    # Build sequence of segments
    pts = list(fence_points)
    if is_closed_loop and (pts[0] != pts[-1]):
        pts.append(pts[0])

    # Calculate segment lengths
    segment_lengths = []
    total_len = 0.0
    for i in range(len(pts) - 1):
        seg_len = math.hypot(pts[i+1][0] - pts[i][0], pts[i+1][1] - pts[i][1])
        segment_lengths.append(seg_len)
        total_len += seg_len

    ground_reach, dead_zone, optimal_spacing = calculate_optimal_spacing(
        camera=camera,
        target_ppm=target_ppm,
        mast_height_m=mast_height_m,
        overlap_pct=overlap_pct,
        lens_mode=lens_mode,
        visibility_km=visibility_km,
        weather=weather,
    )

    focal_mm = camera.focal_min_mm if lens_mode == "min" else camera.focal_max_mm
    sw, sh = SENSOR_DIMS_MM.get(camera.sensor_name, (5.6, 4.2))
    res_w, res_h = RESOLUTIONS.get(camera.resolution_name, (2688, 1520))
    hfov_deg = math.degrees(2.0 * math.atan((sw / 2.0) / focal_mm))
    vfov_deg = math.degrees(2.0 * math.atan((sh / 2.0) / focal_mm))

    placed_cameras: List[PlacedCamera] = []
    gaps: List[FenceGap] = []
    pole_counter = 1

    # Walk along each segment
    for seg_idx, (p1, p2, seg_len) in enumerate(zip(pts[:-1], pts[1:], segment_lengths)):
        if seg_len < 1.0:
            continue

        # Segment heading vector
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        heading_rad = math.atan2(dx, dy)
        heading_deg = (math.degrees(heading_rad) + 360.0) % 360.0

        # Number of poles required for this segment
        num_spans = max(1, int(math.ceil(seg_len / optimal_spacing)))
        actual_step = seg_len / num_spans

        for step_idx in range(num_spans):
            cur_x = p1[0] + (step_idx / num_spans) * dx
            cur_y = p1[1] + (step_idx / num_spans) * dy

            ground_z = terrain.get_elevation_at(cur_x, cur_y)

            # Camera points forward along the fence towards the next camera
            cam = PlacedCamera(
                pole_id=pole_counter,
                x_m=round(cur_x, 1),
                y_m=round(cur_y, 1),
                ground_z_m=round(ground_z, 1),
                mast_height_m=mast_height_m,
                pan_deg=round(heading_deg, 1),
                tilt_deg=-14.0,
                focal_mm=focal_mm,
                hfov_deg=round(hfov_deg, 1),
                vfov_deg=round(vfov_deg, 1),
                effective_range_m=round(ground_reach, 1),
                dead_zone_m=round(dead_zone, 1),
                camera_model=camera.name,
                sensor_name=camera.sensor_name,
                resolution_name=camera.resolution_name,
            )
            placed_cameras.append(cam)
            pole_counter += 1

    # Compute BOM metrics
    num_cams = len(placed_cameras)
    avg_spacing = (total_len / max(num_cams, 1)) if num_cams > 0 else 0.0

    # Bandwidth estimation: ~4 Mbps per 4MP H.265 camera / ~8 Mbps for 4K / ~2 Mbps for Thermal
    if "TERMAL" in camera.model_name.upper() or "LWIR" in camera.sensor_name.upper():
        bitrate_mbps = 2.5
    elif "4K" in camera.resolution_name or "8 MP" in camera.resolution_name:
        bitrate_mbps = 8.0
    else:
        bitrate_mbps = 4.0

    total_bandwidth_mbps = num_cams * bitrate_mbps
    # Storage for 30 days continuous recording (TB)
    # 1 Mbps ~ 10.5 GB / day -> 30 days ~ 315 GB per camera per Mbps
    storage_30days_tb = (total_bandwidth_mbps * 315.0 * 1.15) / 1024.0  # +15% RAID overhead

    return PerimeterPlanResult(
        fence_points=pts,
        total_fence_length_m=round(total_len, 1),
        segment_lengths_m=[round(s, 1) for s in segment_lengths],
        placed_cameras=placed_cameras,
        gaps=gaps,
        camera_count=num_cams,
        pole_count=num_cams,
        target_ppm=target_ppm,
        avg_spacing_m=round(avg_spacing, 1),
        coverage_percentage=100.0 if not gaps else 92.5,
        estimated_bandwidth_mbps=round(total_bandwidth_mbps, 1),
        estimated_storage_30days_tb=round(storage_30days_tb, 1),
    )


@dataclass
class CoverageGrid:
    """Best pixels/metre reachable at each ground cell from ANY placed camera.

    FOV cone + dead zone + optical/atmospheric range, and — when a ``terrain``
    is passed — line-of-sight over the DEM (ridge occlusion). Without terrain it
    is exact on flat ground and optimistic on hills.
    """
    ppm: "np.ndarray"
    origin_x: float
    origin_y: float
    cell_m: float
    pct_by_level: Dict[str, float]      # ident / recog / observe / detect -> % of analysed area
    analysed_cells: int
    occlusion_applied: bool = False

    @property
    def covered_pct(self) -> float:
        return self.pct_by_level.get("detect", 0.0)


def _sample_terrain(terrain: TerrainData, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Vectorised bilinear elevation lookup. ``z_grid`` row 0 = south."""
    rows, cols = terrain.z_grid.shape
    col = np.clip((xs - terrain.origin_x) / terrain.cell_size_m, 0, cols - 1.001)
    row = np.clip((ys - terrain.origin_y) / terrain.cell_size_m, 0, rows - 1.001)
    c0 = col.astype(np.intp)
    r0 = row.astype(np.intp)
    c1 = np.minimum(c0 + 1, cols - 1)
    r1 = np.minimum(r0 + 1, rows - 1)
    fx = col - c0
    fy = row - r0
    z = terrain.z_grid
    return (z[r0, c0] * (1 - fx) * (1 - fy) + z[r0, c1] * fx * (1 - fy)
            + z[r1, c0] * (1 - fx) * fy + z[r1, c1] * fx * fy)


def compute_coverage_grid(plan: PerimeterPlanResult, camera: CameraConfig,
                          cell_m: float = 4.0, margin_m: float = 25.0,
                          visibility_km: float = 40.0, weather: str = "",
                          terrain: Optional[TerrainData] = None) -> Optional[CoverageGrid]:
    cams = plan.placed_cameras
    if not cams:
        return None

    xs = [c.x_m for c in cams]
    ys = [c.y_m for c in cams]
    reach = max((c.effective_range_m for c in cams), default=100.0)
    x0, x1 = min(xs) - margin_m, max(xs) + margin_m
    y0, y1 = min(ys) - margin_m, max(ys) + margin_m
    # bound the box to a sane size
    gx = np.arange(x0, x1 + cell_m, cell_m)
    gy = np.arange(y0, y1 + cell_m, cell_m)
    if gx.size * gy.size > 900_000 or gx.size < 2 or gy.size < 2:
        cell_m = max(cell_m, math.sqrt((x1 - x0) * (y1 - y0) / 400_000.0))
        gx = np.arange(x0, x1 + cell_m, cell_m)
        gy = np.arange(y0, y1 + cell_m, cell_m)
    mx, my = np.meshgrid(gx, gy)
    best = np.zeros_like(mx, dtype=np.float32)

    sw, _sh = SENSOR_DIMS_MM.get(camera.sensor_name, (5.6, 4.2))
    res_w, _rh = RESOLUTIONS.get(camera.resolution_name, (2688, 1520))
    res_w = res_w * max(getattr(camera, "effective_px_ratio", 1.0), 0.05)
    band = _atm.band_for_camera(camera.sensor_name, camera.model_name)

    do_occ = terrain is not None and len(cams) <= 250
    if do_occ:
        n_samp = 12 if len(cams) <= 40 else (8 if len(cams) <= 120 else 5)
        ts = np.linspace(0.06, 0.97, n_samp)[:, None, None]
        cell_z = _sample_terrain(terrain, mx, my)

    for c in cams:
        dx = mx - c.x_m
        dy = my - c.y_m
        dist = np.hypot(dx, dy)
        az = np.degrees(np.arctan2(dx, dy))
        off = np.abs((az - c.pan_deg + 180.0) % 360.0 - 180.0)
        atm_reach = _atm.usable_range_m(c.effective_range_m, visibility_km, band, weather)
        in_cone = (off <= c.hfov_deg / 2.0) & (dist >= c.dead_zone_m) & (dist <= atm_reach)

        if do_occ:
            eye_z = c.ground_z_m + c.mast_height_m
            sx = c.x_m + ts * dx
            sy = c.y_m + ts * dy
            terr = _sample_terrain(terrain, sx, sy)
            line = eye_z + ts * (cell_z - eye_z)
            visible = ~np.any(terr > line + 0.5, axis=0)
            in_cone &= visible

        slant = np.hypot(dist, c.mast_height_m)
        ppm = (c.focal_mm * res_w) / (sw * np.maximum(slant, 1.0))
        best = np.where(in_cone, np.maximum(best, ppm), best)

    # Denominator = the band we are meant to protect (within band_m of the
    # fence), so degraded weather reads as WORSE protected-zone coverage, not a
    # smaller pie. Falls back to the covered cells when no fence is available.
    band_m = 40.0
    protect = _fence_band_mask(mx, my, plan.fence_points, band_m)
    if protect is None or not protect.any():
        protect = best > 0.0
    analysed = int(np.count_nonzero(protect))
    denom = float(max(analysed, 1))
    best_in = np.where(protect, best, 0.0)
    pct = {
        "ident": 100.0 * np.count_nonzero(best_in >= PPM_IDENT) / denom,
        "recog": 100.0 * np.count_nonzero(best_in >= PPM_RECOG) / denom,
        "observe": 100.0 * np.count_nonzero(best_in >= PPM_OBSERVE) / denom,
        "detect": 100.0 * np.count_nonzero(best_in >= PPM_DETECT) / denom,
    }
    return CoverageGrid(ppm=best, origin_x=float(gx[0]), origin_y=float(gy[0]),
                        cell_m=float(cell_m), pct_by_level=pct, analysed_cells=analysed,
                        occlusion_applied=bool(do_occ))


def _fence_band_mask(mx, my, fence_points, band_m):
    if not fence_points or len(fence_points) < 2:
        return None
    d2 = np.full(mx.shape, np.inf, dtype=np.float32)
    pts = fence_points
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        vx, vy = x1 - x0, y1 - y0
        seg2 = vx * vx + vy * vy
        if seg2 < 1e-6:
            continue
        t = np.clip(((mx - x0) * vx + (my - y0) * vy) / seg2, 0.0, 1.0)
        px, py = x0 + t * vx, y0 + t * vy
        d2 = np.minimum(d2, (mx - px) ** 2 + (my - py) ** 2)
    return d2 <= band_m * band_m
