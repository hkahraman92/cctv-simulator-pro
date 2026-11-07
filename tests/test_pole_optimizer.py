"""Fewest poles for a fence-coverage goal (spacing search + per-pole focal optimisation)."""
from __future__ import annotations

import numpy as np

from cctv_simulator.focal_optimizer import fence_roi
from cctv_simulator.models import CameraConfig
from cctv_simulator.perimeter_planner import generate_perimeter_plan
from cctv_simulator.pole_optimizer import minimize_poles
from cctv_simulator.terrain_loader import TerrainData
from cctv_simulator.viewshed_3d import ZONE_DETECT

CAM = CameraConfig(name="V", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                   focal_min_mm=2.8, focal_max_mm=12.0, pole_height_m=6.0)
FENCE = [(100.0, 300.0), (700.0, 300.0)]
KW = dict(target_ppm=40.0, overlap_pct=0.15, mast_height_m=6.0, lens_mode="min", is_closed_loop=False)


def _flat(n=160, cell=5.0):
    return TerrainData(z_grid=np.zeros((n, n), np.float32), cell_size_m=cell)


def test_spacing_override_sets_the_pole_count_and_none_is_unchanged():
    terr = _flat()
    base = generate_perimeter_plan(terr, FENCE, CAM, **KW)
    same = generate_perimeter_plan(terr, FENCE, CAM, spacing_m=None, **KW)
    assert [c.x_m for c in base.placed_cameras] == [c.x_m for c in same.placed_cameras]
    sparse = generate_perimeter_plan(terr, FENCE, CAM, spacing_m=base.avg_spacing_m * 3, **KW)
    assert len(sparse.placed_cameras) < len(base.placed_cameras)


def test_reachable_goal_gets_fewer_poles_than_the_planner():
    terr = _flat()
    res = minimize_poles(terr, FENCE, CAM, plan_kwargs=KW, goal_frac=0.8, level=ZONE_DETECT, n_focals=5)
    assert res.base.meets                          # the planner's spacing already passes...
    assert res.met and res.best.optimised_frac >= 0.8
    assert res.best.fixed_poles < res.base.fixed_poles   # ...and a sparser layout still does
    assert all(t.optimised_frac >= t.planner_frac - 1e-9 for t in res.trials)
    passing = [t for t in res.trials if t.meets]
    assert res.best.fixed_poles == min(t.fixed_poles for t in passing)
    roi = fence_roi(terr, FENCE, closed=False)
    assert res.roi_area_m2 == float(roi.sum()) * terr.cell_size_m ** 2


def test_goal_above_the_planner_goes_denser():
    res = minimize_poles(_flat(), FENCE, CAM, plan_kwargs=KW, goal_frac=0.9, level=ZONE_DETECT, n_focals=5)
    assert not res.base.meets
    assert res.met and res.best.fixed_poles > res.base.fixed_poles
    # and it is the sparsest of the passing trials, not just any dense one
    assert res.best.fixed_poles == min(t.fixed_poles for t in res.trials if t.meets)


def test_impossible_goal_reports_best_effort_and_says_so():
    res = minimize_poles(_flat(), FENCE, CAM, plan_kwargs=KW, goal_frac=1.01, n_focals=4, max_trials=5)
    assert not res.met and res.best is not None and res.notes
    assert res.best.optimised_frac == max(t.optimised_frac for t in res.trials)
