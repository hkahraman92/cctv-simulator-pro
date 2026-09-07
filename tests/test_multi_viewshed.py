"""Combined multi-camera viewshed: per-camera engine merged into one DORI map."""
from __future__ import annotations

import numpy as np
import pytest

from cctv_simulator.models import CameraConfig
from cctv_simulator.terrain_loader import TerrainData, generate_procedural_terrain
from cctv_simulator.viewshed_3d import (
    ZONE_OUT_OF_FOV,
    CameraPlacement,
    calculate_3d_viewshed,
    calculate_multi_camera_viewshed,
    placement_bounds_warnings,
)

CAM = CameraConfig(name="M", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                   focal_min_mm=6.0, focal_max_mm=30.0)


def _flat(n=80, cell=10.0):
    return TerrainData(z_grid=np.zeros((n, n), np.float32), cell_size_m=cell)


def test_none_without_placements():
    assert calculate_multi_camera_viewshed(_flat(), []) is None


def test_two_cameras_cover_more_than_one():
    terr = _flat()
    p1 = CameraPlacement(x_m=400, y_m=100, mast_height_m=8, camera=CAM, pan_deg=0, tilt_deg=-3, max_range_m=500, label="Kuzey")
    p2 = CameraPlacement(x_m=100, y_m=400, mast_height_m=8, camera=CAM, pan_deg=90, tilt_deg=-3, max_range_m=500, label="Doğu")

    one = calculate_multi_camera_viewshed(terr, [p1])
    two = calculate_multi_camera_viewshed(terr, [p1, p2])
    assert two.visible_area_m2 > one.visible_area_m2
    assert two.labels == ["Kuzey", "Doğu"]
    assert len(two.per_camera) == 2


def test_merge_equals_single_engine_for_one_camera():
    terr = generate_procedural_terrain("rolling_hills", grid_size=60, cell_size_m=10.0)
    p = CameraPlacement(x_m=300, y_m=300, mast_height_m=10, camera=CAM, lens_mode="max",
                        pan_deg=20, tilt_deg=-2, max_range_m=600)
    multi = calculate_multi_camera_viewshed(terr, [p])
    solo = calculate_3d_viewshed(terrain=terr, cam_x_m=300, cam_y_m=300, mast_height_m=10,
                                 camera=CAM, lens_mode="max", pan_deg=20, tilt_deg=-2, max_range_m=600)
    # same visible cells
    assert np.array_equal(multi.visibility_mask, solo.visibility_mask)
    # best_cam is 1 exactly where visible, 0 elsewhere
    assert np.array_equal(multi.best_cam_grid > 0, solo.visibility_mask)


def test_overlap_and_best_camera_tracking():
    terr = _flat()
    # two tele cameras pointed at the same patch from opposite sides (tele so the
    # optical limit actually reaches the middle band)
    a = CameraPlacement(x_m=400, y_m=200, mast_height_m=8, camera=CAM, lens_mode="max",
                        pan_deg=0, tilt_deg=-2, max_range_m=400)
    b = CameraPlacement(x_m=400, y_m=600, mast_height_m=8, camera=CAM, lens_mode="max",
                        pan_deg=180, tilt_deg=-2, max_range_m=400)
    res = calculate_multi_camera_viewshed(terr, [a, b])
    assert res.overlap_area_m2 > 0.0                 # they share a middle band
    assert res.seen_count_grid.max() == 2
    assert set(np.unique(res.best_cam_grid)) <= {0, 1, 2}
    # zone percentages are nested
    p = res.pct_by_zone
    assert p["detect"] >= p["observe"] >= p["recog"] >= p["ident"] - 1e-9


def test_focal_override_sits_between_lens_ends():
    terr = _flat()
    wide = calculate_3d_viewshed(terrain=terr, cam_x_m=400, cam_y_m=100, mast_height_m=8,
                                 camera=CAM, lens_mode="min", pan_deg=0, tilt_deg=-3, max_range_m=600)
    tele = calculate_3d_viewshed(terrain=terr, cam_x_m=400, cam_y_m=100, mast_height_m=8,
                                 camera=CAM, lens_mode="max", pan_deg=0, tilt_deg=-3, max_range_m=600)
    mid = calculate_3d_viewshed(terrain=terr, cam_x_m=400, cam_y_m=100, mast_height_m=8,
                                camera=CAM, focal_mm_override=15.0, pan_deg=0, tilt_deg=-3, max_range_m=600)
    assert tele.hfov_deg < mid.hfov_deg < wide.hfov_deg
    # override outside the range is clamped
    clamped = calculate_3d_viewshed(terrain=terr, cam_x_m=400, cam_y_m=100, mast_height_m=8,
                                    camera=CAM, focal_mm_override=999.0, pan_deg=0, tilt_deg=-3)
    assert clamped.hfov_deg == pytest.approx(tele.hfov_deg)


def test_with_profile_false_skips_cross_section_only():
    terr = _flat()
    kw = dict(terrain=terr, cam_x_m=400, cam_y_m=100, mast_height_m=8, camera=CAM,
              pan_deg=0, tilt_deg=-3, max_range_m=500)
    full = calculate_3d_viewshed(**kw, with_profile=True)
    lean = calculate_3d_viewshed(**kw, with_profile=False)
    assert np.array_equal(full.dori_grid, lean.dori_grid)
    assert full.profile_dists_m.size > 0 and lean.profile_dists_m.size == 0


def test_placement_bounds_warnings():
    terr = _flat(80, 10.0)   # 0..800 m
    inside = CameraPlacement(x_m=400, y_m=400, mast_height_m=8, camera=CAM, label="içeride")
    outside = CameraPlacement(x_m=-50, y_m=400, mast_height_m=8, camera=CAM, label="dışarıda")
    warns = placement_bounds_warnings(terr, [inside, outside])
    assert len(warns) == 1 and "dışarıda" in warns[0]


def test_combined_dori_is_best_of_cameras():
    terr = _flat()
    near = CameraPlacement(x_m=400, y_m=350, mast_height_m=6, camera=CAM, lens_mode="max",
                           pan_deg=0, tilt_deg=-2, max_range_m=300)
    far = CameraPlacement(x_m=400, y_m=50, mast_height_m=6, camera=CAM, lens_mode="min",
                          pan_deg=0, tilt_deg=-1, max_range_m=500)
    res = calculate_multi_camera_viewshed(terr, [near, far])
    # every visible cell's merged ppm is >= each contributing camera's ppm there
    for r in res.per_camera:
        m = r.visibility_mask
        assert np.all(res.ppm_grid[m] >= r.ppm_grid[m] - 1e-3)
    assert not np.any(res.dori_grid[~res.visibility_mask & (res.dori_grid > ZONE_OUT_OF_FOV)] == 99)
