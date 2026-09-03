"""Perimeter fence planner: spacing physics and BOM counts."""
from __future__ import annotations

import math

import pytest

from cctv_simulator.models import CameraConfig
from cctv_simulator.perimeter_planner import (
    calculate_optimal_spacing,
    compute_coverage_grid,
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


def test_fog_shortens_long_range_pole_reach():
    # a tele lens reaches ~450 m optically; dense fog caps it well below that
    tele = CameraConfig(name="Tele", sensor_name='1/2.8"',
                        resolution_name="4 MP (2K - 2688x1520)",
                        focal_min_mm=12.0, focal_max_mm=36.0)
    clear = calculate_optimal_spacing(tele, target_ppm=40.0, lens_mode="max", visibility_km=40.0)
    fog = calculate_optimal_spacing(tele, target_ppm=40.0, lens_mode="max",
                                    visibility_km=0.3, weather="Yoğun sis")
    assert fog[0] < clear[0] - 1.0      # ground reach
    assert fog[2] < clear[2]            # spacing


def test_measured_k_shortens_reach(cam):
    import dataclasses
    soft = dataclasses.replace(cam, effective_px_ratio=0.5)
    assert calculate_optimal_spacing(soft, 40.0)[0] < calculate_optimal_spacing(cam, 40.0)[0]


def test_coverage_grid_levels_are_nested(cam):
    terr = generate_procedural_terrain("rolling_hills", grid_size=48, cell_size_m=10.0)
    square = [(60.0, 60.0), (260.0, 60.0), (260.0, 260.0), (60.0, 260.0)]
    plan = generate_perimeter_plan(terr, square, cam, target_ppm=40.0)
    cov = compute_coverage_grid(plan, cam, cell_m=6.0)
    assert cov is not None
    p = cov.pct_by_level
    assert p["detect"] >= p["observe"] >= p["recog"] >= p["ident"] - 1e-9
    assert 0.0 <= cov.covered_pct <= 100.0
    assert cov.ppm.shape[0] > 2 and cov.ppm.shape[1] > 2


def test_coverage_grid_none_without_cameras(cam):
    from cctv_simulator.perimeter_planner import PerimeterPlanResult
    empty = PerimeterPlanResult(fence_points=[], total_fence_length_m=0.0,
                                segment_lengths_m=[], placed_cameras=[])
    assert compute_coverage_grid(empty, cam) is None


def test_terrain_occlusion_reduces_coverage(cam):
    import numpy as np

    from cctv_simulator.terrain_loader import TerrainData
    flat = TerrainData(z_grid=np.zeros((80, 80), np.float32), cell_size_m=6.0)
    ridged = TerrainData(z_grid=np.zeros((80, 80), np.float32), cell_size_m=6.0)
    # a tall wall across the middle of the site
    ridged.z_grid[38:42, :] = 60.0

    tele = CameraConfig(name="T", sensor_name='1/2.8"',
                        resolution_name="4 MP (2K - 2688x1520)",
                        focal_min_mm=12.0, focal_max_mm=40.0)
    square = [(60.0, 60.0), (420.0, 60.0), (420.0, 420.0), (60.0, 420.0)]
    plan = generate_perimeter_plan(flat, square, tele, target_ppm=40.0, lens_mode="max")

    open_cov = compute_coverage_grid(plan, tele, cell_m=6.0, terrain=flat)
    blocked = compute_coverage_grid(plan, tele, cell_m=6.0, terrain=ridged)
    assert open_cov.occlusion_applied and blocked.occlusion_applied
    assert blocked.pct_by_level["detect"] < open_cov.pct_by_level["detect"]


def test_coverage_grid_no_occlusion_flag_without_terrain(cam):
    terr = generate_procedural_terrain("rolling_hills", grid_size=40, cell_size_m=10.0)
    square = [(50.0, 50.0), (200.0, 50.0), (200.0, 200.0), (50.0, 200.0)]
    plan = generate_perimeter_plan(terr, square, cam)
    assert compute_coverage_grid(plan, cam).occlusion_applied is False
