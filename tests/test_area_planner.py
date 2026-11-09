"""Area goal: polygon ROI, fewest cameras over it, engine-verified share, downstream reuse."""
from __future__ import annotations

import numpy as np
import pytest

from cctv_simulator import surface_obstacles as obs
from cctv_simulator.area_planner import candidate_sites, plan_area, polygon_mask
from cctv_simulator.focal_optimizer import band_area
from cctv_simulator.models import CameraConfig
from cctv_simulator.network_plan import plan_network
from cctv_simulator.terrain_loader import TerrainData
from cctv_simulator.viewshed_3d import ZONE_DETECT, ZONE_OBSERVE, ZONE_RECOG

CAM = CameraConfig(focal_min_mm=2.8, focal_max_mm=12.0)
SQUARE = [(100.0, 100.0), (220.0, 100.0), (220.0, 200.0), (100.0, 200.0)]


def flat(n=200, cell=2.0):
    return TerrainData(np.zeros((n, n)), cell_size_m=cell)


def test_polygon_mask_matches_the_polygon_area_and_handles_concave_shapes():
    t = flat()
    m = polygon_mask(t, SQUARE)
    assert m.sum() * 4.0 == pytest.approx(120 * 100, rel=0.03)
    # z_grid row 0 = south: the square's rows sit at y = 100..200 m -> rows 50..100
    assert m[75, 80] and not m[30, 80] and not m[75, 20]
    ell = [(100.0, 100.0), (300.0, 100.0), (300.0, 160.0), (160.0, 160.0), (160.0, 300.0), (100.0, 300.0)]
    m2 = polygon_mask(t, ell)
    assert m2.sum() * 4.0 == pytest.approx(200 * 60 + 60 * 140, rel=0.03)
    assert not m2[115, 115]                     # the notch of the L (x = y = 230 m) is outside


def test_candidate_sites_respect_the_cap_and_the_interior_switch():
    s_all, _ = candidate_sites(SQUARE, 10.0, interior=True, max_sites=30)
    s_edge, _ = candidate_sites(SQUARE, 10.0, interior=False, max_sites=500)
    assert len(s_all) <= 30
    assert len(s_edge) == 44                    # 440 m of boundary every 10 m
    xs = [x for x, _ in s_edge]
    assert min(xs) == pytest.approx(100.0) and max(xs) == pytest.approx(220.0)


def test_area_plan_meets_the_goal_by_the_authoritative_engine():
    t = flat()
    rep = plan_area(t, SQUARE, CAM, level=ZONE_OBSERVE, goal_frac=0.9, mast_height_m=6.0)
    assert rep.met and rep.engine_frac >= 0.9
    plan = rep.plan
    assert plan.line_mode == "area" and plan.gaps == []
    assert plan.pole_count == len({c.pole_id for c in plan.placed_cameras}) <= plan.camera_count
    per_pole = np.bincount([c.pole_id for c in plan.placed_cameras])
    assert per_pole.max() <= 2
    # the reported share is exactly what the multi-camera engine says
    roi = polygon_mask(t, SQUARE)
    mv, _ = band_area(t, plan, CAM, roi, ZONE_DETECT)            # the coverage run: full Algılama reach
    assert rep.engine_frac == pytest.approx(((mv.dori_grid >= ZONE_OBSERVE) & roi).sum() / roi.sum())
    assert plan.coverage_percentage == pytest.approx(rep.engine_frac * 100, abs=0.06)


def test_a_lower_goal_never_needs_more_cameras_and_pruning_keeps_it_lean():
    t = flat()
    lo = plan_area(t, SQUARE, CAM, level=ZONE_OBSERVE, goal_frac=0.6, mast_height_m=6.0)
    hi = plan_area(t, SQUARE, CAM, level=ZONE_OBSERVE, goal_frac=0.9, mast_height_m=6.0)
    assert lo.plan.camera_count <= hi.plan.camera_count
    # every camera of the pruned layout is needed for the goal (estimate frame)
    assert lo.estimate_frac >= 0.6 and lo.steps and lo.steps[-1].frac >= 0.6


def test_a_wall_across_the_area_puts_cameras_on_both_sides():
    z = np.zeros((200, 200))
    z[74:76, 55:106] = 25.0                     # wall along y = 150 m, x = 110..210 m (inside the square)
    t = TerrainData(z, cell_size_m=2.0)
    rep = plan_area(t, SQUARE, CAM, level=ZONE_DETECT, goal_frac=0.85, mast_height_m=6.0, interior=False)
    assert rep.met
    ys = {c.y_m for c in rep.plan.placed_cameras}
    assert min(ys) < 150 < max(ys)


def test_sites_inside_a_building_are_skipped():
    bare = flat()
    h = np.zeros_like(bare.z_grid)
    h[45:105, 45:115] = 30.0                    # a hall covering the whole square + margin
    surf = obs.with_obstacles(bare, obs.ObstacleLayer(height_grid=h, kind_grid=(h > 0).astype(np.uint8), counts={}))
    rep = plan_area(surf, SQUARE, CAM, level=ZONE_DETECT, goal_frac=0.5, mast_height_m=6.0)
    assert rep.sites_skipped_obstacle > 0
    assert rep.sites_considered == 0 and rep.plan.camera_count == 0 and not rep.met
    assert any("bina" in n for n in rep.notes)


def test_an_unreachable_goal_is_reported_not_faked():
    t = flat()
    rep = plan_area(t, SQUARE, CAM, level=ZONE_RECOG, goal_frac=0.95, mast_height_m=6.0, max_cameras=4)
    assert not rep.met and rep.plan.camera_count <= 4
    assert rep.engine_frac < 0.95 and any("tutmadı" in n for n in rep.notes)


def test_too_small_or_empty_polygons_are_refused():
    t = flat()
    with pytest.raises(ValueError):
        plan_area(t, [(0.0, 0.0), (10.0, 0.0)], CAM)
    with pytest.raises(ValueError):
        plan_area(t, [(1000.0, 1000.0), (1010.0, 1000.0), (1010.0, 1010.0)], CAM)


def test_an_interior_pole_beyond_copper_gets_its_own_cabinet_not_a_warning():
    from dataclasses import replace

    from cctv_simulator.perimeter_planner import PerimeterPlanResult, PlacedCamera

    ring = [(0.0, 0.0), (400.0, 0.0), (400.0, 400.0), (0.0, 400.0), (0.0, 0.0)]
    cam = PlacedCamera(pole_id=1, x_m=200.0, y_m=200.0, ground_z_m=0.0, mast_height_m=6.0, pan_deg=0.0, tilt_deg=-5.0,
                       focal_mm=4.0, hfov_deg=80.0, vfov_deg=45.0, effective_range_m=30.0, dead_zone_m=3.0,
                       camera_model="x", sensor_name="", resolution_name="")
    edge = replace(cam, pole_id=2, x_m=50.0, y_m=0.0)
    plan = PerimeterPlanResult(fence_points=ring, total_fence_length_m=1600.0, segment_lengths_m=[400.0] * 4,
                               placed_cameras=[cam, replace(cam, pan_deg=180.0), edge], camera_count=3, pole_count=2,
                               line_mode="area")
    net = plan_network(plan, max_copper_m=90.0)
    assert net.warnings == []
    cabinets = [s for s in net.switches if s.on_pole]
    assert len(cabinets) == 1 and cabinets[0].ports_used == 2 and (cabinets[0].x_m, cabinets[0].y_m) == (200.0, 200.0)
    assert net.fibre_m >= 200.0 * 1.1 - 1e-6           # the trench from the edge to the cabinet is fibre
    assert net.longest_copper_m <= 90.0 + 1e-6 and net.cameras == 3


def test_network_counts_the_trench_to_an_interior_pole():
    t = flat()
    rep = plan_area(t, SQUARE, CAM, level=ZONE_OBSERVE, goal_frac=0.9, mast_height_m=6.0)
    net = plan_network(rep.plan, max_copper_m=95.0)
    inner = [c for c in rep.plan.placed_cameras if 102 < c.x_m < 218 and 102 < c.y_m < 198]
    if inner:                                   # the plan used interior poles: their trench is in the copper
        assert any("hendek" in a for a in net.assumptions)
    assert net.cameras == rep.plan.camera_count


def test_hole_areas_are_8_connected_patches():
    from cctv_simulator.area_planner import hole_areas

    m = np.zeros((8, 8), bool)
    m[1:3, 1:3] = True            # 4 cells
    m[3, 3] = True                # touches diagonally -> same hole
    m[6, 0:5] = True              # separate, 5 cells
    lab, areas = hole_areas(m, 4.0)
    assert sorted(areas) == [20.0, 20.0]
    assert lab[1, 1] == lab[3, 3] != lab[6, 0] and (lab > 0).sum() == m.sum()
    assert hole_areas(np.zeros((3, 3), bool), 1.0)[1] == []


def test_redundancy_goal_counts_cells_held_by_k_cameras():
    """min_cameras=2: the reported share is of cells TWO cameras hold at the
    level (engine, per camera) -- more cameras than the single-cover plan."""
    from cctv_simulator.area_planner import plan_area
    from cctv_simulator.models import CameraConfig
    from cctv_simulator.terrain_loader import TerrainData
    from cctv_simulator.viewshed_3d import ZONE_OBSERVE

    t = TerrainData(z_grid=np.zeros((150, 150)), cell_size_m=2.0)
    cam = CameraConfig(focal_min_mm=2.8, focal_max_mm=12.0)
    poly = [(100.0, 100.0), (200.0, 100.0), (200.0, 180.0), (100.0, 180.0)]
    one = plan_area(t, poly, cam, level=ZONE_OBSERVE, goal_frac=0.8, mast_height_m=6.0)
    two = plan_area(t, poly, cam, level=ZONE_OBSERVE, goal_frac=0.8, mast_height_m=6.0, min_cameras=2)
    assert one.met and two.met and len(two.plan.placed_cameras) > len(one.plan.placed_cameras)
    from cctv_simulator.area_planner import polygon_mask
    from cctv_simulator.focal_optimizer import band_area
    from cctv_simulator.viewshed_3d import ZONE_DETECT
    roi = polygon_mask(t, poly)
    mv, _ = band_area(t, two.plan, cam, roi, ZONE_DETECT)
    n = sum((r.dori_grid >= ZONE_OBSERVE).astype(int) for r in mv.per_camera)
    assert two.engine_frac == pytest.approx(float(((n >= 2) & roi).sum()) / roi.sum())
    assert any("Yedeklilik" in s for s in two.notes)
