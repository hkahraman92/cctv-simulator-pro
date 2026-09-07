"""PTZ preset-tour coverage + revisit-time analysis."""
from __future__ import annotations

import math

import numpy as np
import pytest

from cctv_simulator.models import CameraConfig
from cctv_simulator.ptz_tour import PTZPreset, PTZTour, evaluate_ptz_tour, _tour_timeline
from cctv_simulator.terrain_loader import TerrainData

CAM = CameraConfig(name="PTZ", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                   focal_min_mm=6.0, focal_max_mm=120.0)


def _flat(n=90, cell=10.0):
    return TerrainData(z_grid=np.zeros((n, n), np.float32), cell_size_m=cell)


def test_none_without_presets():
    tour = PTZTour(x_m=400, y_m=400, mast_height_m=10, camera=CAM, presets=[])
    assert evaluate_ptz_tour(_flat(), tour) is None


def test_tour_period_includes_dwell_slew_and_settle():
    tour = PTZTour(x_m=0, y_m=0, mast_height_m=8, camera=CAM, slew_speed_deg_s=90.0,
                   settle_s=1.5, zoom_full_sweep_s=3.0, presets=[
        PTZPreset("A", pan_deg=0, lens_mode="max", dwell_s=5),
        PTZPreset("B", pan_deg=90, lens_mode="max", dwell_s=5),
    ])
    windows, period = _tour_timeline(tour)
    # 2x5 dwell + 2x(90/90) slew + 2x1.5 settle + 0 zoom (same lens end) = 15 s
    assert period == pytest.approx(15.0)
    assert len(windows) == 2


def test_zoom_transition_adds_time():
    base = dict(x_m=0, y_m=0, mast_height_m=8, camera=CAM, slew_speed_deg_s=90.0,
                settle_s=0.0, zoom_full_sweep_s=4.0)
    no_zoom = PTZTour(**base, presets=[
        PTZPreset("A", pan_deg=0, focal_mm=6, dwell_s=5),
        PTZPreset("B", pan_deg=0, focal_mm=6, dwell_s=5)])
    full_zoom = PTZTour(**base, presets=[
        PTZPreset("A", pan_deg=0, focal_mm=6, dwell_s=5),      # wide
        PTZPreset("B", pan_deg=0, focal_mm=120, dwell_s=5)])   # full tele
    _, p0 = _tour_timeline(no_zoom)
    _, p1 = _tour_timeline(full_zoom)
    # one full min->max sweep each way => +2 * 4 s
    assert p1 == pytest.approx(p0 + 8.0)


def test_more_presets_cover_more_area():
    terr = _flat()
    one = PTZTour(x_m=450, y_m=450, mast_height_m=12, camera=CAM, max_range_m=400, presets=[
        PTZPreset("K", pan_deg=0, tilt_deg=-3, lens_mode="min", dwell_s=6),
    ])
    four = PTZTour(x_m=450, y_m=450, mast_height_m=12, camera=CAM, max_range_m=400, presets=[
        PTZPreset("K", pan_deg=0, tilt_deg=-3, lens_mode="min", dwell_s=6),
        PTZPreset("D", pan_deg=90, tilt_deg=-3, lens_mode="min", dwell_s=6),
        PTZPreset("G", pan_deg=180, tilt_deg=-3, lens_mode="min", dwell_s=6),
        PTZPreset("B", pan_deg=270, tilt_deg=-3, lens_mode="min", dwell_s=6),
    ])
    r1 = evaluate_ptz_tour(terr, one)
    r4 = evaluate_ptz_tour(terr, four)
    assert r4.combined.visible_area_m2 > r1.combined.visible_area_m2
    assert r4.tour_period_s > r1.tour_period_s


def test_single_preset_revisit_is_period_minus_dwell():
    terr = _flat()
    tour = PTZTour(x_m=450, y_m=200, mast_height_m=12, camera=CAM, max_range_m=350,
                   slew_speed_deg_s=90.0, presets=[
        PTZPreset("tek", pan_deg=0, tilt_deg=-3, lens_mode="min", dwell_s=8),
    ])
    res = evaluate_ptz_tour(terr, tour)
    # one preset: slew back to itself is 0 deg -> period == dwell -> revisit ~ 0
    assert res.tour_period_s == pytest.approx(8.0)
    covered = np.isfinite(res.revisit_grid)
    assert np.all(res.revisit_grid[covered] < 1e-6)   # always framed


def test_two_opposite_presets_have_revisit_gap():
    terr = _flat()
    tour = PTZTour(x_m=450, y_m=450, mast_height_m=12, camera=CAM, max_range_m=350,
                   slew_speed_deg_s=180.0, presets=[
        PTZPreset("kuzey", pan_deg=0, tilt_deg=-3, lens_mode="min", dwell_s=10),
        PTZPreset("güney", pan_deg=180, tilt_deg=-3, lens_mode="min", dwell_s=10),
    ])
    res = evaluate_ptz_tour(terr, tour)
    # a cell only the north preset sees waits: period - its dwell window
    covered = np.isfinite(res.revisit_grid)
    assert res.worst_revisit_s > 5.0
    assert res.worst_revisit_s <= res.tour_period_s + 1e-6
    assert res.mean_revisit_s > 0.0
    assert res.combined.visible_area_m2 > 0.0
    assert np.any(covered)


def test_revisit_inf_where_never_covered():
    terr = _flat()
    tour = PTZTour(x_m=100, y_m=100, mast_height_m=10, camera=CAM, max_range_m=300, presets=[
        PTZPreset("dar", pan_deg=45, tilt_deg=-3, lens_mode="min", dwell_s=5),
    ])
    res = evaluate_ptz_tour(terr, tour)
    # behind the camera is never covered -> inf
    assert np.isinf(res.revisit_grid).any()
