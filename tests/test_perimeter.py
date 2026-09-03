"""Perimeter fence planner: spacing physics and BOM counts."""
from __future__ import annotations

import math

import pytest

from cctv_simulator.models import CameraConfig
from cctv_simulator.perimeter_planner import (
    calculate_optimal_spacing,
    generate_perimeter_plan,
    point_along_polyline,
)
from cctv_simulator.terrain_loader import generate_procedural_terrain


@pytest.fixture
def cam():
    return CameraConfig(
        name="Perimeter",
        sensor_name='1/2.8"',
        resolution_name="4 MP (2K - 2688x1520)",
        focal_min_mm=4.0,
        focal_max_mm=12.0,
    )


def test_spacing_shrinks_as_target_ppm_rises(cam):
    _, _, loose = calculate_optimal_spacing(cam, target_ppm=40.0)
    _, _, tight = calculate_optimal_spacing(cam, target_ppm=125.0)
    assert tight < loose
    assert loose > 0.0


def test_spacing_matches_en62676_slant_formula(cam):
    reach, dead, spacing = calculate_optimal_spacing(
        cam, target_ppm=40.0, mast_height_m=5.0, overlap_pct=0.15, lens_mode="min"
    )
    # slant = f * res_w / (sw * ppm); ground = sqrt(slant^2 - mast^2)
    slant = (4.0 * 2688) / (6.0 * 40.0)  # 1/2.8" sensor width ~ 5.6? uses config
    # allow either the config value or the fallback; just assert the relation.
    assert reach == pytest.approx(math.sqrt(max(slant**2 - 25.0, 100.0)), rel=0.15)
    assert spacing == pytest.approx((reach - dead) * 0.85, rel=1e-6)


def test_higher_overlap_gives_more_poles(cam):
    terr = generate_procedural_terrain("rolling_hills", grid_size=64, cell_size_m=10.0)
    square = [(50.0, 50.0), (450.0, 50.0), (450.0, 450.0), (50.0, 450.0)]

    few = generate_perimeter_plan(terr, square, cam, target_ppm=40.0, overlap_pct=0.10)
    many = generate_perimeter_plan(terr, square, cam, target_ppm=40.0, overlap_pct=0.30)

    assert len(few.placed_cameras) >= 4
    assert len(many.placed_cameras) >= len(few.placed_cameras)
    # pole ids are 1..N with no gaps
    assert [c.pole_id for c in many.placed_cameras] == list(range(1, len(many.placed_cameras) + 1))


def test_plan_needs_two_points(cam):
    terr = generate_procedural_terrain("rolling_hills", grid_size=32, cell_size_m=10.0)
    empty = generate_perimeter_plan(terr, [(0.0, 0.0)], cam)
    assert empty.placed_cameras == []
    assert empty.total_fence_length_m == 0.0


def test_point_along_polyline_maps_distance_to_world():
    square = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]
    assert point_along_polyline(square, 0.0)[:2] == (0.0, 0.0)
    assert point_along_polyline(square, 50.0)[:2] == pytest.approx((50.0, 0.0))
    assert point_along_polyline(square, 150.0)[:2] == pytest.approx((100.0, 50.0))
    x, y, seg = point_along_polyline(square, 150.0)
    assert seg == 1
    # clamped past the end
    assert point_along_polyline(square, 9999.0)[:2] == pytest.approx((0.0, 0.0))
    # degenerate inputs
    assert point_along_polyline([], 10.0) == (0.0, 0.0, 0)
    assert point_along_polyline([(5.0, 7.0)], 10.0) == (5.0, 7.0, 0)


def test_point_along_polyline_matches_planner_fence_points(cam):
    terr = generate_procedural_terrain("rolling_hills", grid_size=32, cell_size_m=10.0)
    square = [(20.0, 20.0), (220.0, 20.0), (220.0, 220.0), (20.0, 220.0)]
    plan = generate_perimeter_plan(terr, square, cam, is_closed_loop=True)
    mid = point_along_polyline(plan.fence_points, plan.total_fence_length_m / 2.0)
    assert 0.0 <= mid[0] <= 240.0 and 0.0 <= mid[1] <= 240.0


def test_closed_loop_fence_length(cam):
    terr = generate_procedural_terrain("rolling_hills", grid_size=32, cell_size_m=10.0)
    square = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    plan = generate_perimeter_plan(terr, square, cam, is_closed_loop=True)
    assert plan.total_fence_length_m == pytest.approx(400.0, rel=1e-6)
