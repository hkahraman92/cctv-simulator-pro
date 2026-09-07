"""
3D Viewshed, Line-of-Sight (LOS) Raymarching and DORI Occlusion Analysis Engine.

Performs:
1. Fast 3D raymarching over topographical DEM matrices.
2. Obstacle & hill ridge occlusion detection (Kör Nokta / Blind Spot Masking).
3. Earth curvature and atmospheric refraction correction for long-range surveillance (>3 km).
4. Continuous 3D Slant-Range DORI / Johnson criteria calculation across terrain cells.
5. Sightline cross-section elevation profile generation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from .terrain_loader import TerrainData
from .models import CameraConfig
from .config import SENSOR_DIMS_MM, RESOLUTIONS
from .atmosphere import band_for_camera, usable_range_m


# DORI PPM Thresholds (EN 62676-4:2015, Tablo B.1 — hedef düzleminde px/m)
PPM_IDENT = 250.0       # Identification / Teşhis
PPM_RECOG = 125.0       # Recognition / Tanıma
PPM_OBSERVE = 62.5      # Observation / Gözlem
PPM_DETECT = 25.0       # Detection / Algılama
PPM_MONITOR = 12.5      # Monitoring and control / İzleme (DORI'nin en düşük seviyesi)
PPM_OVERVIEW = PPM_MONITOR  # geriye dönük ad

# Zone Codes
ZONE_OUT_OF_FOV = 0
ZONE_OCCLUDED = 1       # Kör Nokta / Tepe Arkası Gölge
ZONE_DETECT = 2         # Algılama (>= 25 px/m)
ZONE_OBSERVE = 3        # Gözlem (>= 62.5 px/m)
ZONE_RECOG = 4          # Tanıma (>= 125 px/m)
ZONE_IDENT = 5          # Teşhis (>= 250 px/m)


@dataclass
class ViewshedResult:
    """Complete 3D Viewshed & Occlusion Analysis Output."""
    visibility_mask: np.ndarray      # 2D boolean array (True = visible, False = occluded or out of FOV)
    dori_grid: np.ndarray            # 2D int array of ZONE_* codes
    ppm_grid: np.ndarray             # 2D float array with px/m values
    slant_dist_grid: np.ndarray      # 2D float array of 3D distance from camera lens

    # Optical Center Elevation Profile (Kesit Grafiği)
    profile_dists_m: np.ndarray
    profile_terrain_elev_m: np.ndarray
    profile_ray_elev_m: np.ndarray
    profile_is_visible: np.ndarray

    # Telemetry & Statistics
    cam_x_m: float
    cam_y_m: float
    cam_ground_z_m: float
    cam_total_z_m: float
    mast_height_m: float
    hfov_deg: float
    vfov_deg: float
    pan_deg: float
    tilt_deg: float
    max_range_m: float
    optical_limit_m: float

    # Area Metrics
    fov_area_m2: float
    visible_area_m2: float
    occluded_area_m2: float
    coverage_pct: float
    max_los_reach_m: float

    # Atmosphere
    atmospheric_limit_m: float = 0.0     # range where path transmission hits tau_min
    visibility_km: float = 40.0


def calculate_3d_viewshed(terrain: TerrainData,
                          cam_x_m: float,
                          cam_y_m: float,
                          mast_height_m: float,
                          camera: CameraConfig,
                          lens_mode: str = "min",
                          pan_deg: float = 0.0,
                          tilt_deg: float = -5.0,
                          max_range_m: float = 2000.0,
                          earth_curvature: bool = True,
                          ray_step_m: float = 3.0,
                          visibility_km: float = 40.0,
                          weather: str = "",
                          focal_mm_override: Optional[float] = None,
                          with_profile: bool = True) -> ViewshedResult:
    """Computes comprehensive 3D viewshed, terrain occlusion, and DORI mapping.

    ``focal_mm_override`` sets an explicit focal length (mm) — for a PTZ preset
    at an intermediate zoom, instead of the min/max lens_mode ends.
    ``with_profile=False`` skips the (unused-in-batch) elevation cross-section.
    """
    rows, cols = terrain.rows, terrain.cols
    cell_size = terrain.cell_size_m

    # 1. Camera 3D Position
    cam_ground_z = terrain.get_elevation_at(cam_x_m, cam_y_m)
    cam_z = cam_ground_z + mast_height_m

    # 2. Camera Optics & FOV
    if focal_mm_override and focal_mm_override > 0:
        focal_mm = float(max(min(focal_mm_override, camera.focal_max_mm), camera.focal_min_mm))
    else:
        focal_mm = camera.focal_min_mm if lens_mode == "min" else camera.focal_max_mm
    sw, sh = SENSOR_DIMS_MM.get(camera.sensor_name, (5.6, 4.2))
    res_w, res_h = RESOLUTIONS.get(camera.resolution_name, (1920, 1080))

    is_thermal = ("LWIR" in camera.sensor_name.upper() or
                  "MWIR" in camera.sensor_name.upper() or
                  "TERMAL" in camera.model_name.upper())

    # Effective horizontal pixels: label resolution narrowed by the measured
    # MTF50/Nyquist ratio (cctv_iq). Default 1.0 keeps output unchanged.
    res_w = res_w * max(getattr(camera, "effective_px_ratio", 1.0), 0.05)

    # Floor PPM below which a cell is not worth showing. Non-thermal: the
    # EN 62676-4 Detection threshold (25 px/m) — the viewshed then covers exactly
    # the DORI-meaningful band (Detection and above), no arbitrary cut-off.
    min_detect_ppm = 1.3 if is_thermal else PPM_DETECT
    optical_limit_m = (focal_mm * res_w) / (sw * min_detect_ppm)

    # Fog / rain / haze cap the range where target contrast survives the path.
    atm_band = band_for_camera(camera.sensor_name, camera.model_name)
    atmospheric_limit_m = usable_range_m(optical_limit_m, visibility_km, atm_band, weather)

    # Effective raymarching range cannot exceed optical or atmospheric limits
    effective_max_range = min(max_range_m, optical_limit_m * 1.15, atmospheric_limit_m)

    hfov_deg = math.degrees(2.0 * math.atan((sw / 2.0) / focal_mm))
    vfov_deg = math.degrees(2.0 * math.atan((sh / 2.0) / focal_mm))
    half_hfov_rad = math.radians(hfov_deg / 2.0)

    # Pan angle in math radians (0 deg = +Y / North, 90 deg = +X / East)
    pan_rad = math.radians(pan_deg)
    opt_dir_x = math.sin(pan_rad)
    opt_dir_y = math.cos(pan_rad)

    # 3. Create Grid Coordinate Grids
    grid_x = terrain.origin_x + np.arange(cols) * cell_size
    grid_y = terrain.origin_y + np.arange(rows) * cell_size
    mesh_x, mesh_y = np.meshgrid(grid_x, grid_y)

    dx = mesh_x - cam_x_m
    dy = mesh_y - cam_y_m
    ground_dist = np.hypot(dx, dy)

    cell_azimuth_rad = np.arctan2(dx, dy)
    angle_diff_rad = np.arctan2(np.sin(cell_azimuth_rad - pan_rad),
                                np.cos(cell_azimuth_rad - pan_rad))

    in_fov_cone = (np.abs(angle_diff_rad) <= half_hfov_rad) & (ground_dist <= effective_max_range) & (ground_dist > 1.0)

    # 4. Initialize Output Grids
    vis_mask = np.zeros((rows, cols), dtype=bool)
    dori_grid = np.zeros((rows, cols), dtype=np.int32)
    ppm_grid = np.zeros((rows, cols), dtype=np.float32)
    slant_dist_grid = np.zeros((rows, cols), dtype=np.float32)

    # 5. Raymarching Radial Scan
    # Ray count so that even at the far edge the angular spacing lands < ~0.6
    # cell apart — otherwise diverging rays skip grid cells inside the cone and
    # coverage_pct reads low. Capped so the (R x S) work stays bounded.
    span_need = half_hfov_rad * 2.0 * effective_max_range / max(cell_size * 0.6, 0.5)
    num_rays = int(min(2200, max(180, hfov_deg * 4, span_need)))
    ray_angles = np.linspace(pan_rad - half_hfov_rad, pan_rad + half_hfov_rad, num_rays)

    R_EARTH = 6371000.0
    K_REFRACT = 0.13

    # Vertical framing: a ground cell is only *seen* if its line from the lens
    # falls between the bottom and top rays (tilt +/- vfov/2). Without this the
    # grid reports coverage the camera never frames (near dead zone, far sky).
    v_lo = math.radians(tilt_deg - vfov_deg / 2.0)     # bottom ray (steeper down)
    v_hi = math.radians(tilt_deg + vfov_deg / 2.0)     # top ray

    # 0.35-0.5 of a cell per step so a one-cell-wide ridge cannot fall between
    # two samples on a diagonal ray (the old 0.7-cell step could skip it).
    step_size = max(min(ray_step_m, cell_size * 0.5), cell_size * 0.35)
    num_steps = max(int(effective_max_range / step_size), 1)
    step_dists = np.arange(1, num_steps + 1) * step_size          # (S,)

    # Vectorised radial raymarch — same result as the old ray/step double loop,
    # ~10x faster. Each ray walks step_dists; a running horizon angle
    # (np.maximum.accumulate over prior optically-valid steps) decides LOS.
    d = step_dists[None, :]                                       # (1,S)
    cur_xs = cam_x_m + d * np.sin(ray_angles)[:, None]            # (R,S)
    cur_ys = cam_y_m + d * np.cos(ray_angles)[:, None]
    cc = ((cur_xs - terrain.origin_x) / cell_size).astype(np.intp)
    rr = ((cur_ys - terrain.origin_y) / cell_size).astype(np.intp)
    in_bounds = (cc >= 0) & (cc < cols) & (rr >= 0) & (rr < rows)
    valid = np.logical_and.accumulate(in_bounds, axis=1)          # ray stops at first exit

    rr_s = np.where(valid, rr, 0)
    cc_s = np.where(valid, cc, 0)
    elev = terrain.z_grid[rr_s, cc_s].astype(np.float64)
    curv_drop = (step_dists * step_dists) / (2.0 * R_EARTH) * (1.0 - K_REFRACT) if earth_curvature else 0.0
    dz = (elev - curv_drop) - cam_z
    tan_angle = dz / d
    slant = np.hypot(d, dz)
    ppm = (focal_mm * res_w) / (sw * np.maximum(slant, 1.0))
    ppm_ok = ppm >= min_detect_ppm

    # Terrain occlusion is physical — a ridge blocks the sightline whether or not
    # it sits inside the vertical frame, so the running horizon uses every valid
    # step, not just the framed ones.
    tan_for_max = np.where(valid & ppm_ok, tan_angle, -1e18)
    horizon_before = np.empty_like(tan_for_max)
    horizon_before[:, 0] = -1e18
    horizon_before[:, 1:] = np.maximum.accumulate(tan_for_max, axis=1)[:, :-1]

    elev_angle = np.arctan(tan_angle)                             # (-pi/2, pi/2)
    in_vfov = (elev_angle >= v_lo) & (elev_angle <= v_hi)
    framed = valid & ppm_ok & in_vfov
    visible = framed & (tan_angle >= horizon_before)

    zone = np.zeros(ppm.shape, dtype=np.int32)                    # ZONE_OUT_OF_FOV
    zone = np.where(framed & ~visible, ZONE_OCCLUDED, zone)       # framed but ridge-blocked
    zone = np.where(visible, ZONE_DETECT, zone)
    zone = np.where(visible & (ppm >= PPM_OBSERVE), ZONE_OBSERVE, zone)
    zone = np.where(visible & (ppm >= PPM_RECOG), ZONE_RECOG, zone)
    zone = np.where(visible & (ppm >= PPM_IDENT), ZONE_IDENT, zone)

    # Scatter back to the grid, ray-major then step-ascending -> last write wins,
    # matching the old loop's ordering.
    m = valid
    vr, vc = rr[m], cc[m]
    slant_dist_grid[vr, vc] = slant[m].astype(np.float32)
    ppm_grid[vr, vc] = ppm[m].astype(np.float32)
    dori_grid[vr, vc] = zone[m]
    vis_mask[vr, vc] = visible[m]

    # Narrow the analytic cone to what the camera can actually FRAME (bottom/top
    # ray), so coverage_pct is "of the frameable area" rather than of the whole
    # horizontal wedge including the near dead zone and the far sky.
    cell_dz = terrain.z_grid.astype(np.float64) - cam_z
    if earth_curvature:
        cell_dz -= (ground_dist ** 2) / (2.0 * R_EARTH) * (1.0 - K_REFRACT)
    cell_elev_angle = np.arctan2(cell_dz, np.maximum(ground_dist, 0.1))
    in_fov_cone &= (cell_elev_angle >= v_lo) & (cell_elev_angle <= v_hi)

    # Mask out anything not in the (now vertically-bounded) FOV cone
    dori_grid[~in_fov_cone] = ZONE_OUT_OF_FOV
    vis_mask[~in_fov_cone] = False

    # 6. Elevation profile along the central optical axis (skipped in batch runs
    # that never read the per-camera cross-section).
    if with_profile:
        prof_dists, prof_elevs, prof_coords = terrain.get_profile_between(
            cam_x_m, cam_y_m,
            cam_x_m + effective_max_range * opt_dir_x,
            cam_y_m + effective_max_range * opt_dir_y,
            num_samples=250
        )
        tilt_rad = math.radians(tilt_deg)
        prof_ray_z = cam_z + prof_dists * math.tan(tilt_rad)
        prof_vis = np.ones_like(prof_dists, dtype=bool)
        max_tan = -1e9
        for i, (d, el) in enumerate(zip(prof_dists, prof_elevs)):
            if d < 1.0:
                continue
            curv = ((d * d) / (2.0 * R_EARTH) * (1.0 - K_REFRACT)) if earth_curvature else 0.0
            tan_a = (el - curv - cam_z) / d
            if tan_a >= max_tan:
                max_tan = tan_a
                prof_vis[i] = True
            else:
                prof_vis[i] = False
    else:
        _empty = np.zeros(0, dtype=np.float64)
        prof_dists = prof_elevs = prof_ray_z = _empty
        prof_vis = np.zeros(0, dtype=bool)

    # 7. Compute Statistics
    cell_area = cell_size * cell_size
    fov_cells = int(np.sum(in_fov_cone))
    vis_cells = int(np.sum(vis_mask & in_fov_cone))
    occ_cells = int(np.sum((dori_grid == ZONE_OCCLUDED) & in_fov_cone))

    fov_area_m2 = fov_cells * cell_area
    visible_area_m2 = vis_cells * cell_area
    occluded_area_m2 = occ_cells * cell_area
    coverage_pct = (visible_area_m2 / max(fov_area_m2, 1.0)) * 100.0

    vis_dists = ground_dist[vis_mask & in_fov_cone]
    max_los_reach_m = float(np.max(vis_dists)) if len(vis_dists) > 0 else 0.0

    return ViewshedResult(
        visibility_mask=vis_mask,
        dori_grid=dori_grid,
        ppm_grid=ppm_grid,
        slant_dist_grid=slant_dist_grid,
        profile_dists_m=prof_dists,
        profile_terrain_elev_m=prof_elevs,
        profile_ray_elev_m=prof_ray_z,
        profile_is_visible=prof_vis,
        cam_x_m=cam_x_m,
        cam_y_m=cam_y_m,
        cam_ground_z_m=cam_ground_z,
        cam_total_z_m=cam_z,
        mast_height_m=mast_height_m,
        hfov_deg=hfov_deg,
        vfov_deg=vfov_deg,
        pan_deg=pan_deg,
        tilt_deg=tilt_deg,
        max_range_m=effective_max_range,
        optical_limit_m=optical_limit_m,
        fov_area_m2=fov_area_m2,
        visible_area_m2=visible_area_m2,
        occluded_area_m2=occluded_area_m2,
        coverage_pct=coverage_pct,
        max_los_reach_m=max_los_reach_m,
        atmospheric_limit_m=atmospheric_limit_m,
        visibility_km=visibility_km,
    )


# ── Çoklu kamera birleşik görüş alanı ────────────────────────────────────────

@dataclass
class CameraPlacement:
    """One camera pinned on the terrain for a combined-viewshed run."""
    x_m: float
    y_m: float
    mast_height_m: float
    camera: CameraConfig
    lens_mode: str = "min"
    pan_deg: float = 0.0
    tilt_deg: float = -5.0
    max_range_m: float = 2000.0
    label: str = ""
    focal_mm_override: Optional[float] = None   # explicit zoom (mm), overrides lens_mode


def placement_bounds_warnings(terrain: TerrainData,
                              placements: List["CameraPlacement"]) -> List[str]:
    """Non-fatal warnings for placements outside the terrain frame — their
    elevation is clamped to the nearest edge cell and the result is unreliable."""
    x0, y0 = terrain.origin_x, terrain.origin_y
    x1, y1 = x0 + terrain.width_m, y0 + terrain.height_m
    out = []
    for i, p in enumerate(placements):
        if not (x0 <= p.x_m <= x1 and y0 <= p.y_m <= y1):
            out.append(
                f"{p.label or f'yerleşim {i + 1}'}: ({p.x_m:.0f}, {p.y_m:.0f}) arazi "
                f"çerçevesi dışında [{x0:.0f}-{x1:.0f}] × [{y0:.0f}-{y1:.0f}] — "
                "zemin rakımı kenara sabitlendi, sonuç güvenilmez.")
    return out


@dataclass
class MultiViewshedResult:
    """Best-of-all-cameras DORI / visibility over the whole terrain grid."""
    dori_grid: np.ndarray             # ZONE_* — best level any camera achieves
    ppm_grid: np.ndarray             # best px/m from any camera
    visibility_mask: np.ndarray      # True where >= 1 camera has a clear framed LOS
    best_cam_grid: np.ndarray        # 1-based index of the strongest camera, 0 = none
    seen_count_grid: np.ndarray      # how many cameras cover the cell (redundancy)
    per_camera: List[ViewshedResult]
    labels: List[str]
    cell_size_m: float
    origin_x: float
    origin_y: float

    fov_area_m2: float               # union of every camera's frameable cone
    visible_area_m2: float
    occluded_area_m2: float          # in some cone, seen by nobody (terrain shadow)
    coverage_pct: float              # visible / union-cone
    overlap_area_m2: float           # cells seen by >= 2 cameras
    single_cover_area_m2: float      # cells seen by exactly 1 (no redundancy)
    pct_by_zone: Dict[str, float]    # ident/recog/observe/detect -> % of union cone


def calculate_multi_camera_viewshed(terrain: TerrainData,
                                    placements: List[CameraPlacement],
                                    *,
                                    earth_curvature: bool = True,
                                    visibility_km: float = 40.0,
                                    weather: str = "") -> Optional[MultiViewshedResult]:
    """Runs the authoritative single-camera engine for each placement and merges
    the grids: the combined map shows the best DORI level reachable at every
    ground cell, plus how many cameras overlap there."""
    if not placements:
        return None

    rows, cols = terrain.rows, terrain.cols
    per: List[ViewshedResult] = []
    for p in placements:
        per.append(calculate_3d_viewshed(
            terrain=terrain, cam_x_m=p.x_m, cam_y_m=p.y_m, mast_height_m=p.mast_height_m,
            camera=p.camera, lens_mode=p.lens_mode, pan_deg=p.pan_deg, tilt_deg=p.tilt_deg,
            max_range_m=p.max_range_m, earth_curvature=earth_curvature,
            visibility_km=visibility_km, weather=weather,
            focal_mm_override=p.focal_mm_override, with_profile=False,
        ))

    ppm = np.zeros((rows, cols), np.float32)
    best_cam = np.zeros((rows, cols), np.int32)
    vis = np.zeros((rows, cols), bool)
    seen = np.zeros((rows, cols), np.int32)
    union_cone = np.zeros((rows, cols), bool)

    for i, r in enumerate(per, 1):
        cam_vis = r.visibility_mask
        better = cam_vis & (r.ppm_grid > ppm)
        ppm = np.where(better, r.ppm_grid, ppm)
        best_cam = np.where(better, i, best_cam)
        vis |= cam_vis
        seen += cam_vis.astype(np.int32)
        union_cone |= (r.dori_grid != ZONE_OUT_OF_FOV)

    occ = union_cone & ~vis
    dori = np.zeros((rows, cols), np.int32)
    dori = np.where(occ, ZONE_OCCLUDED, dori)
    dori = np.where(vis, ZONE_DETECT, dori)
    dori = np.where(vis & (ppm >= PPM_OBSERVE), ZONE_OBSERVE, dori)
    dori = np.where(vis & (ppm >= PPM_RECOG), ZONE_RECOG, dori)
    dori = np.where(vis & (ppm >= PPM_IDENT), ZONE_IDENT, dori)

    cell_a = terrain.cell_size_m ** 2
    union_cells = int(np.count_nonzero(union_cone))
    denom = float(max(union_cells, 1))
    vis_cells = int(np.count_nonzero(vis))
    pct = {
        "ident": 100.0 * np.count_nonzero(dori == ZONE_IDENT) / denom,
        "recog": 100.0 * np.count_nonzero(dori >= ZONE_RECOG) / denom,
        "observe": 100.0 * np.count_nonzero(dori >= ZONE_OBSERVE) / denom,
        "detect": 100.0 * np.count_nonzero(dori >= ZONE_DETECT) / denom,
    }

    return MultiViewshedResult(
        dori_grid=dori, ppm_grid=ppm, visibility_mask=vis, best_cam_grid=best_cam,
        seen_count_grid=seen, per_camera=per,
        labels=[p.label or f"K{i}" for i, p in enumerate(placements, 1)],
        cell_size_m=terrain.cell_size_m, origin_x=terrain.origin_x, origin_y=terrain.origin_y,
        fov_area_m2=union_cells * cell_a,
        visible_area_m2=vis_cells * cell_a,
        occluded_area_m2=int(np.count_nonzero(occ)) * cell_a,
        coverage_pct=100.0 * vis_cells / denom,
        overlap_area_m2=int(np.count_nonzero(seen >= 2)) * cell_a,
        single_cover_area_m2=int(np.count_nonzero(seen == 1)) * cell_a,
        pct_by_zone=pct,
    )
