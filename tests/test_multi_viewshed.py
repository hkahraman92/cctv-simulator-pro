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


def _ptz_views(camera_id):
    # two presets of one PTZ on the same mast, frames overlapping (pan 0 vs 20)
    return [CameraPlacement(x_m=400, y_m=200, mast_height_m=8, camera=CAM, lens_mode="min",
                            pan_deg=pan, tilt_deg=-4, max_range_m=400, label=f"P{pan}",
                            camera_id=camera_id)
            for pan in (0.0, 20.0)]


def test_views_of_one_camera_count_once():
    # BUGFIX: presets of one PTZ used to count as separate cameras.
    terr = _flat()
    as_cameras = calculate_multi_camera_viewshed(terr, _ptz_views(None))
    as_views = calculate_multi_camera_viewshed(terr, _ptz_views("ptz-1"))
    assert as_cameras.overlap_area_m2 > 0.0          # the frames do overlap...
    assert as_views.overlap_area_m2 == 0.0           # ...but it is one camera
    assert as_views.seen_count_grid.max() == 1
    assert as_views.camera_count == 1 and as_cameras.camera_count == 2
    assert as_views.single_cover_area_m2 == as_views.visible_area_m2
    # what is SEEN (and how well) is unchanged -- only redundancy is.
    assert np.array_equal(as_views.visibility_mask, as_cameras.visibility_mask)
    assert np.array_equal(as_views.ppm_grid, as_cameras.ppm_grid)
    assert np.array_equal(as_views.dori_grid, as_cameras.dori_grid)
    assert np.array_equal(as_views.best_cam_grid, as_cameras.best_cam_grid)


def test_fixed_camera_plus_ptz_counts_two():
    terr = _flat()
    fixed = CameraPlacement(x_m=400, y_m=200, mast_height_m=8, camera=CAM, lens_mode="min",
                            pan_deg=10, tilt_deg=-4, max_range_m=400, label="Sabit")
    res = calculate_multi_camera_viewshed(terr, [fixed] + _ptz_views("ptz-1"))
    assert res.camera_count == 2
    assert res.seen_count_grid.max() == 2            # fixed + PTZ, never 3


def test_ptz_tour_has_no_self_overlap():
    from cctv_simulator.ptz_tour import PTZPreset, PTZTour, evaluate_ptz_tour
    tour = PTZTour(x_m=400, y_m=200, mast_height_m=8, camera=CAM, max_range_m=400,
                   presets=[PTZPreset("A", 0.0, -4.0, "min"), PTZPreset("B", 20.0, -4.0, "min")])
    res = evaluate_ptz_tour(_flat(), tour)
    assert res.combined.camera_count == 1
    assert res.combined.overlap_area_m2 == 0.0
    assert res.combined.seen_count_grid.max() == 1


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


# ── merge keeps each camera's own classification (thermal = Johnson) ─────────
def _thermal_cam():
    from cctv_simulator.config import DEFAULT_CAMERA_LIBRARY
    m = DEFAULT_CAMERA_LIBRARY["ASELSAN UMA Fix 55mm Termal Kamera"]
    return CameraConfig(name="T", model_name="ASELSAN UMA Fix 55mm Termal Kamera", sensor_name=m["sensor_name"],
                        resolution_name=m["resolution_name"], focal_min_mm=m["focal_min_mm"],
                        focal_max_mm=m["focal_max_mm"])


def _per_camera_max(mv):
    out = np.zeros_like(mv.dori_grid)
    for r in mv.per_camera:
        out = np.maximum(out, np.where(r.visibility_mask, r.dori_grid, 0))
    return out


def test_thermal_multi_merge_is_the_per_camera_maximum():
    """BUGFIX guard: the merge re-thresholded px/m with EN constants and lost every Johnson
    recognition / identification class of a thermal camera."""
    from cctv_simulator.viewshed_3d import ZONE_OBSERVE, ZONE_OCCLUDED, ZONE_RECOG
    terr = _flat(100, 10.0)
    cam = _thermal_cam()
    pls = [CameraPlacement(x_m=300, y_m=500, mast_height_m=8, camera=cam, pan_deg=90, tilt_deg=-1, max_range_m=700),
           CameraPlacement(x_m=700, y_m=500, mast_height_m=8, camera=cam, pan_deg=270, tilt_deg=-1, max_range_m=700)]
    mv = calculate_multi_camera_viewshed(terr, pls)
    per = np.concatenate([np.unique(r.dori_grid) for r in mv.per_camera])
    assert (per == ZONE_RECOG).any() or (per == ZONE_OBSERVE).any()        # the cameras do reach Johnson classes
    expect = _per_camera_max(mv)
    vis = mv.visibility_mask
    assert np.array_equal(mv.dori_grid[vis], expect[vis])
    assert (mv.dori_grid[vis] >= ZONE_OBSERVE).any()
    assert ((mv.dori_grid == ZONE_OCCLUDED) == (~vis & (mv.dori_grid != 0))).all()


def test_mixed_fleet_takes_each_cameras_own_classes():
    from cctv_simulator.viewshed_3d import ZONE_DETECT
    terr = _flat(100, 10.0)
    th, vis_cam = _thermal_cam(), CAM
    pls = [CameraPlacement(x_m=300, y_m=500, mast_height_m=8, camera=th, pan_deg=90, tilt_deg=-1, max_range_m=700),
           CameraPlacement(x_m=420, y_m=500, mast_height_m=8, camera=vis_cam, pan_deg=90, tilt_deg=-3, max_range_m=300)]
    mv = calculate_multi_camera_viewshed(terr, pls)
    vis = mv.visibility_mask
    assert np.array_equal(mv.dori_grid[vis], _per_camera_max(mv)[vis])
    # where only the thermal sees a cell its Johnson class survives (EN px/m alone would say Detection)
    only_th = mv.per_camera[0].visibility_mask & ~mv.per_camera[1].visibility_mask
    assert (mv.dori_grid[only_th] > ZONE_DETECT).any()


def test_non_thermal_merge_is_unchanged_by_the_zone_code_merge():
    """The old rule (re-threshold the merged best px/m) and the per-camera max agree for visible cameras,
    with and without the monitor floor."""
    from cctv_simulator.viewshed_3d import PPM_DETECT, PPM_IDENT, PPM_OBSERVE, PPM_RECOG, ZONE_DETECT, ZONE_IDENT, \
        ZONE_MONITOR, ZONE_OBSERVE, ZONE_RECOG
    terr = generate_procedural_terrain("ridge_and_valley", 120, 10.0)
    pls = [CameraPlacement(x_m=400 + 150 * i, y_m=500, mast_height_m=8, camera=CAM, pan_deg=40 * i, tilt_deg=-4,
                           max_range_m=400) for i in range(4)]
    for mon in (False, True):
        mv = calculate_multi_camera_viewshed(terr, pls, monitor_floor=mon)
        vis, ppm = mv.visibility_mask, mv.ppm_grid
        old = np.where(vis, ZONE_DETECT, 0)
        if mon:
            old = np.where(vis & (ppm < PPM_DETECT), ZONE_MONITOR, old)
        for z, th in ((ZONE_OBSERVE, PPM_OBSERVE), (ZONE_RECOG, PPM_RECOG), (ZONE_IDENT, PPM_IDENT)):
            old = np.where(vis & (ppm >= th), z, old)
        assert np.array_equal(mv.dori_grid[vis], old[vis])
