"""Terrain-aware pole placement: poles may slide along the fence."""
import math

import numpy as np

from cctv_simulator import surface_obstacles as obs
from cctv_simulator.focal_optimizer import (apply_layout, band_area, fence_roi, fence_shifts, mounts_from_plan,
                                            optimize_layout)
from cctv_simulator.models import CameraConfig
from cctv_simulator.perimeter_planner import generate_perimeter_plan
from cctv_simulator.terrain_loader import TerrainData
from cctv_simulator.viewshed_3d import ZONE_DETECT

CAM = CameraConfig(focal_min_mm=2.8, focal_max_mm=12.0)


def _site(with_walls: bool):
    t = TerrainData(np.zeros((120, 120)), cell_size_m=5.0, is_measured=True)
    if not with_walls:
        return t
    h = np.zeros((120, 120)); k = np.zeros((120, 120), np.uint8)
    # buildings right in front of every pole of the straight fence (y = 300 m)
    for x0 in range(10, 110, 8):
        h[58:61, x0:x0 + 2] = 25.0; k[58:61, x0:x0 + 2] = obs.KIND_BUILDING
    return obs.with_obstacles(t, obs.ObstacleLayer(height_grid=h, kind_grid=k, counts={}))


def test_shifts_follow_the_fence_and_stop_at_its_ends():
    fence = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0)]
    s = fence_shifts(fence, 98.0, 0.0, 5.0, 2)          # near the corner
    assert (93.0, 0.0) in s and (100.0, 3.0) in s and (100.0, 8.0) in s
    assert all(abs(y) < 1e-9 or abs(x - 100.0) < 1e-9 for x, y in s)      # on the polyline
    assert fence_shifts(fence, 0.0, 0.0, 5.0, 1) == [(5.0, 0.0)]          # no shift past the start


def test_no_alternatives_is_the_old_optimiser():
    t = _site(False)
    fence = [(50.0, 300.0), (550.0, 300.0)]
    plan = generate_perimeter_plan(t, fence, CAM, target_ppm=40, mast_height_m=6, is_closed_loop=False)
    roi = fence_roi(t, fence, False)
    a = optimize_layout(t, mounts_from_plan(plan, CAM), roi=roi, n_focals=4)
    b = optimize_layout(t, mounts_from_plan(plan, CAM, position_step_m=0.0, position_steps=0), roi=roi, n_focals=4)
    assert [(c.focal_mm, c.tilt_deg, c.pan_deg) for c in a.choices] == [(c.focal_mm, c.tilt_deg, c.pan_deg) for c in b.choices]
    assert all(c.moved_m == 0 for c in a.choices)


def test_sliding_poles_past_buildings_wins_band_coverage_and_the_engine_agrees():
    t = _site(True)
    fence = [(50.0, 300.0), (550.0, 300.0)]
    plan = generate_perimeter_plan(t, fence, CAM, target_ppm=40, mast_height_m=6, is_closed_loop=False, spacing_m=40.0)
    roi = fence_roi(t, fence, False)
    fixed = optimize_layout(t, mounts_from_plan(plan, CAM), roi=roi, n_focals=4)
    moved = optimize_layout(t, mounts_from_plan(plan, CAM, position_step_m=5.0, position_steps=2), roi=roi, n_focals=4)
    assert moved.after_m2[ZONE_DETECT] >= fixed.after_m2[ZONE_DETECT]            # never worse
    assert any(c.moved_m > 0 for c in moved.choices)
    new_plan = apply_layout(plan, moved, CAM, 40.0, terrain=t)
    for c, ch in zip(new_plan.placed_cameras, moved.choices):
        assert math.isclose(c.x_m, round(ch.x_m, 2)) and math.isclose(c.y_m, round(ch.y_m, 2))
    # the authoritative engine sees the gain too
    _, a0 = band_area(t, apply_layout(plan, fixed, CAM, 40.0, terrain=t), CAM, roi, ZONE_DETECT)
    _, a1 = band_area(t, new_plan, CAM, roi, ZONE_DETECT)
    assert a1 >= a0
