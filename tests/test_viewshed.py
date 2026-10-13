"""3D viewshed engine: LOS occlusion, DORI zones, atmosphere, invariants.

The raymarch was vectorised (was a ray x step Python double loop). This pins the
behaviour that matters; a full bit-diff against the old loop was done once
during the refactor (identical dori_grid / visibility_mask on 3 terrains).
"""
from __future__ import annotations

import numpy as np
import pytest

from cctv_simulator.models import CameraConfig
from cctv_simulator.terrain_loader import TerrainData, generate_procedural_terrain
from cctv_simulator.viewshed_3d import (
    ZONE_DETECT,
    ZONE_IDENT,
    ZONE_OCCLUDED,
    ZONE_OUT_OF_FOV,
    calculate_3d_viewshed,
)

CAM = CameraConfig(name="V", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                   focal_min_mm=6.0, focal_max_mm=30.0, pole_height_m=8.0)


def _flat(n=80, cell=10.0):
    return TerrainData(z_grid=np.zeros((n, n), np.float32), cell_size_m=cell)


def test_flat_ground_has_no_ridge_occlusion():
    terr = _flat()
    res = calculate_3d_viewshed(terrain=terr, cam_x_m=400, cam_y_m=100,
                                mast_height_m=8, camera=CAM, pan_deg=0, tilt_deg=-3,
                                max_range_m=600)
    assert not np.any(res.dori_grid == ZONE_OCCLUDED)
    assert res.visible_area_m2 > 0
    assert 0.0 <= res.coverage_pct <= 100.0


def test_dori_falls_off_with_distance_on_flat_ground():
    terr = _flat()
    res = calculate_3d_viewshed(terrain=terr, cam_x_m=400, cam_y_m=60,
                                mast_height_m=8, camera=CAM, lens_mode="max",
                                pan_deg=0, tilt_deg=-2, max_range_m=800)
    # along the optical axis (column ~40), zone codes should be non-increasing
    axis = res.dori_grid[:, 40]
    active = axis[axis > 0]
    assert active.size > 3
    assert np.all(np.diff(active.astype(int)) <= 0)


def test_wall_creates_a_shadow():
    terr = _flat(90, 8.0)
    terr.z_grid[42:45, :] = 40.0          # wall across the middle (east-west)
    # 30 mm lens so the optical/atmospheric range reaches past the wall
    res = calculate_3d_viewshed(terrain=terr, cam_x_m=360, cam_y_m=80,
                                mast_height_m=6, camera=CAM, lens_mode="max",
                                pan_deg=0, tilt_deg=-2, max_range_m=600)
    # a clear-ground run right behind the wall must contain occluded cells
    far = res.dori_grid[45:60, 40:50]
    assert np.any(far == ZONE_OCCLUDED)
    # and the wall's own near face is visible
    assert np.any(res.dori_grid[38:42, 40:50] > 0)


def test_fov_cone_masks_everything_outside():
    terr = _flat()
    res = calculate_3d_viewshed(terrain=terr, cam_x_m=400, cam_y_m=400,
                                mast_height_m=8, camera=CAM, pan_deg=0, tilt_deg=-3,
                                max_range_m=500)
    # directly behind the camera (south, smaller y -> smaller row) nothing is seen
    assert np.all(res.dori_grid[:38, :] == ZONE_OUT_OF_FOV)


def test_fog_shrinks_atmospheric_and_effective_range():
    terr = _flat()
    tele = CameraConfig(sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                        focal_min_mm=25.0, focal_max_mm=50.0, pole_height_m=8.0)
    clear = calculate_3d_viewshed(terrain=terr, cam_x_m=400, cam_y_m=40, mast_height_m=8,
                                  camera=tele, lens_mode="max", max_range_m=3000,
                                  visibility_km=40.0)
    fog = calculate_3d_viewshed(terrain=terr, cam_x_m=400, cam_y_m=40, mast_height_m=8,
                                camera=tele, lens_mode="max", max_range_m=3000,
                                visibility_km=0.3, weather="Yoğun sis")
    assert fog.atmospheric_limit_m < clear.atmospheric_limit_m
    assert fog.max_range_m < clear.max_range_m


def test_measured_k_shrinks_optical_limit():
    terr = _flat()
    import dataclasses
    soft = dataclasses.replace(CAM, effective_px_ratio=0.5)
    a = calculate_3d_viewshed(terrain=terr, cam_x_m=400, cam_y_m=40, mast_height_m=8, camera=CAM)
    b = calculate_3d_viewshed(terrain=terr, cam_x_m=400, cam_y_m=40, mast_height_m=8, camera=soft)
    assert b.optical_limit_m == pytest.approx(0.5 * a.optical_limit_m, rel=1e-6)


def test_central_profile_visible_near_camera():
    terr = generate_procedural_terrain("rolling_hills", grid_size=64, cell_size_m=10.0)
    res = calculate_3d_viewshed(terrain=terr, cam_x_m=300, cam_y_m=300, mast_height_m=10,
                                camera=CAM, pan_deg=30, tilt_deg=-2, max_range_m=500)
    assert res.profile_is_visible[:5].all()
    assert res.profile_dists_m[0] < res.profile_dists_m[-1]


def test_visible_cells_never_below_min_detect():
    terr = generate_procedural_terrain("ridge_and_valley", grid_size=70, cell_size_m=9.0)
    res = calculate_3d_viewshed(terrain=terr, cam_x_m=300, cam_y_m=200, mast_height_m=8,
                                camera=CAM, pan_deg=10, tilt_deg=-4, max_range_m=900)
    vis = res.visibility_mask
    # floor is now the EN 62676-4 Detection threshold (25 px/m)
    assert np.all(res.ppm_grid[vis] >= 25.0 - 1e-3)
    assert set(np.unique(res.dori_grid[vis]).tolist()) <= {ZONE_DETECT, 3, 4, ZONE_IDENT}


def test_vertical_fov_gates_the_grid():
    """tilt/vfov must actually bound the coverage — a steep-down camera frames
    only the near ground, a near-level one reaches far."""
    terr = _flat()
    steep = calculate_3d_viewshed(terrain=terr, cam_x_m=400, cam_y_m=100, mast_height_m=10,
                                  camera=CAM, pan_deg=0, tilt_deg=-35, max_range_m=600)
    level = calculate_3d_viewshed(terrain=terr, cam_x_m=400, cam_y_m=100, mast_height_m=10,
                                  camera=CAM, pan_deg=0, tilt_deg=-2, max_range_m=600)
    assert steep.max_los_reach_m < level.max_los_reach_m - 10.0
    assert steep.visible_area_m2 > 0.0


def test_near_dead_zone_is_out_of_frame():
    terr = _flat()
    res = calculate_3d_viewshed(terrain=terr, cam_x_m=400, cam_y_m=400, mast_height_m=12,
                                camera=CAM, lens_mode="max", pan_deg=0, tilt_deg=-2, max_range_m=500)
    # 10 m north of a 12 m mast, a -2 deg tele frame cannot include the ground
    assert res.dori_grid[41, 40] == ZONE_OUT_OF_FOV


@pytest.mark.parametrize("cell", [2.0, 5.0, 10.0])
@pytest.mark.parametrize("slope", [0.05, 0.08, 0.15])
def test_planar_down_slope_never_hides_itself(cell, slope):
    # Analytic baseline: a plane seen from above it has no occlusion. With
    # nearest-cell elevations the march saw terraces and marked 18-52 % of a
    # plain 8-15 % down-slope as ridge shadow.
    n = int(600 / cell)
    z = np.repeat((100.0 - slope * np.arange(n) * cell)[:, None], n, axis=1).astype(np.float32)
    vs = calculate_3d_viewshed(TerrainData(z_grid=z, cell_size_m=cell), 300.0, 50.0, 6.0, CAM,
                               pan_deg=0.0, tilt_deg=-4.0, max_range_m=400.0, lens_mode="min",
                               earth_curvature=False, with_profile=False)
    assert vs.visibility_mask.sum() > 20
    assert np.count_nonzero(vs.dori_grid == ZONE_OCCLUDED) == 0


def test_mount_raster_sees_all_of_a_planar_slope():
    from cctv_simulator.mount_visibility import compute_mount_visibility
    n, cell = 160, 5.0
    z = np.repeat((100.0 - 0.1 * np.arange(n) * cell)[:, None], n, axis=1).astype(np.float32)
    mv = compute_mount_visibility(TerrainData(z_grid=z, cell_size_m=cell), 400.0, 400.0, 6.0,
                                  max_range_m=300.0, earth_curvature=False)
    assert mv.written.sum() > 1000
    assert np.all(mv.los_clear[mv.written])
