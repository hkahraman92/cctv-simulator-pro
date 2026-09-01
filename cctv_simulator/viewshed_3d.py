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


# DORI PPM Thresholds (EN 62676-4)
PPM_IDENT = 250.0
PPM_RECOG = 125.0
PPM_OBSERVE = 62.5
PPM_DETECT = 25.0
PPM_OVERVIEW = 20.0

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
                          ray_step_m: float = 3.0) -> ViewshedResult:
    """Computes comprehensive 3D viewshed, terrain occlusion, and DORI mapping."""
    rows, cols = terrain.rows, terrain.cols
    cell_size = terrain.cell_size_m

    # 1. Camera 3D Position
    cam_ground_z = terrain.get_elevation_at(cam_x_m, cam_y_m)
    cam_z = cam_ground_z + mast_height_m

    # 2. Camera Optics & FOV
    focal_mm = camera.focal_min_mm if lens_mode == "min" else camera.focal_max_mm
    sw, sh = SENSOR_DIMS_MM.get(camera.sensor_name, (5.6, 4.2))
    res_w, res_h = RESOLUTIONS.get(camera.resolution_name, (1920, 1080))

    is_thermal = ("LWIR" in camera.sensor_name.upper() or 
                  "MWIR" in camera.sensor_name.upper() or 
                  "TERMAL" in camera.model_name.upper())

    # Minimum PPM threshold for detection
    min_detect_ppm = 1.3 if is_thermal else PPM_OVERVIEW
    optical_limit_m = (focal_mm * res_w) / (sw * min_detect_ppm)

    # Effective raymarching range cannot exceed optical detection capability
    effective_max_range = min(max_range_m, optical_limit_m * 1.15)

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
    num_rays = max(180, int(hfov_deg * 4))
    ray_angles = np.linspace(pan_rad - half_hfov_rad, pan_rad + half_hfov_rad, num_rays)

    R_EARTH = 6371000.0
    K_REFRACT = 0.13

    step_size = max(ray_step_m, cell_size * 0.7)
    num_steps = int(effective_max_range / step_size)
    step_dists = np.arange(1, num_steps + 1) * step_size

    for angle in ray_angles:
        dir_x = math.sin(angle)
        dir_y = math.cos(angle)

        max_horizon_tan = -1e9

        cur_xs = cam_x_m + step_dists * dir_x
        cur_ys = cam_y_m + step_dists * dir_y

        for d, cur_x, cur_y in zip(step_dists, cur_xs, cur_ys):
            c = int((cur_x - terrain.origin_x) / cell_size)
            r = int((cur_y - terrain.origin_y) / cell_size)
            if c < 0 or c >= cols or r < 0 or r >= rows:
                break

            elev = float(terrain.z_grid[r, c])

            curv_drop = ((d * d) / (2.0 * R_EARTH) * (1.0 - K_REFRACT)) if earth_curvature else 0.0
            effective_elev = elev - curv_drop

            dz = effective_elev - cam_z
            tan_angle = dz / d

            slant_dist = math.hypot(d, dz)
            slant_dist_grid[r, c] = slant_dist

            ppm = (focal_mm * res_w) / (sw * max(slant_dist, 1.0))
            ppm_grid[r, c] = ppm

            # Check if within optical resolution threshold
            if ppm < min_detect_ppm:
                # Beyond camera optical detection capability -> Out of Range!
                dori_grid[r, c] = ZONE_OUT_OF_FOV
                vis_mask[r, c] = False
                continue

            if tan_angle >= max_horizon_tan:
                # Direct Line of Sight!
                max_horizon_tan = tan_angle
                vis_mask[r, c] = True

                if ppm >= PPM_IDENT:
                    dori_grid[r, c] = ZONE_IDENT
                elif ppm >= PPM_RECOG:
                    dori_grid[r, c] = ZONE_RECOG
                elif ppm >= PPM_OBSERVE:
                    dori_grid[r, c] = ZONE_OBSERVE
                elif ppm >= PPM_DETECT:
                    dori_grid[r, c] = ZONE_DETECT
                else:
                    dori_grid[r, c] = ZONE_DETECT
            else:
                # Occluded by preceding terrain ridge -> Blind Spot
                vis_mask[r, c] = False
                dori_grid[r, c] = ZONE_OCCLUDED

    # Mask out anything not in the FOV cone
    dori_grid[~in_fov_cone] = ZONE_OUT_OF_FOV
    vis_mask[~in_fov_cone] = False

    # 6. Extract Elevation Profile along the central optical axis
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
        eff_el = el - curv
        tan_a = (eff_el - cam_z) / d
        if tan_a >= max_tan:
            max_tan = tan_a
            prof_vis[i] = True
        else:
            prof_vis[i] = False

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
    )
