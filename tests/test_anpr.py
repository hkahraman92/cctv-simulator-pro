"""ANPR geometry: readable zone, frames vs speed, motion blur, plate angles, IR, optimiser."""
from __future__ import annotations

import math
from dataclasses import replace

import pytest

from cctv_simulator.anpr import SAMPLE_M, AnprSetup, analyse, optimize
from cctv_simulator.calculations import camera_optics
from cctv_simulator.models import CameraConfig

CAM = CameraConfig(focal_min_mm=8.0, focal_max_mm=32.0, ir_range_m=40.0)


def gantry(**kw):
    """Camera straight above lane 1's centre, at plate height unless told otherwise."""
    base = dict(height_m=0.5, pole_x_m=1.75, lanes=1, lane_width_m=3.5, aim_y_m=30.0, aim_x_m=1.75,
                plate_height_m=0.5, speed_kmh=60.0, exposure_s=1 / 1000)
    base.update(kw)
    return AnprSetup(**base)


def test_head_on_zone_ends_where_the_plate_drops_below_target():
    r = analyse(CAM, 16.0, gantry())
    ln = r.lanes[0]
    reach = camera_optics(CAM, 16.0).distance_for_ppm(143.0)     # head-on: slant = along the road
    assert ln.zone_y1_m == pytest.approx(reach, abs=SAMPLE_M + 1e-6)
    assert ln.ppm_min == pytest.approx(143.0, rel=0.01) and ln.h_angle_max_deg == pytest.approx(0.0)
    assert r.hfov_deg == pytest.approx(camera_optics(CAM, 16.0).hfov_deg)


def test_frames_and_max_speed_follow_the_zone_length():
    s = gantry(speed_kmh=72.0, fps=25.0, min_frames=4)
    ln = analyse(CAM, 16.0, s).lanes[0]
    assert ln.frames == pytest.approx(ln.zone_len_m / 20.0 * 25.0)
    assert ln.max_speed_kmh == pytest.approx(ln.zone_len_m * 25.0 / 4 * 3.6)


def test_motion_along_the_line_of_sight_does_not_blur_and_a_high_mount_does():
    level = analyse(CAM, 16.0, gantry()).lanes[0]
    assert level.blur_px_max == pytest.approx(0.0, abs=1e-9)
    high = analyse(CAM, 16.0, gantry(height_m=8.0, aim_y_m=25.0, speed_kmh=120.0)).lanes[0]
    assert high.blur_px_max > 0
    # at the longest allowed exposure the worst blur is exactly the limit
    at_limit = analyse(CAM, 16.0, gantry(height_m=8.0, aim_y_m=25.0, speed_kmh=120.0, exposure_s=high.max_exposure_s)).lanes[0]
    assert at_limit.blur_px_max == pytest.approx(2.0, rel=1e-6)


def test_a_pole_far_to_the_side_starts_reading_only_past_the_angle_limit():
    s = gantry(height_m=0.5, pole_x_m=-15.0, aim_y_m=60.0, max_h_deg=30.0)
    ln = analyse(CAM, 32.0, s).lanes[0]
    first = (1.75 + 15.0) / math.tan(math.radians(30.0))
    assert ln.zone_y0_m is not None and ln.zone_y0_m >= first - 1e-6
    assert ln.h_angle_max_deg <= 30.0 + 1e-9


def test_night_ir_range_cuts_the_zone():
    day = analyse(CAM, 16.0, gantry()).lanes[0]
    night = analyse(CAM, 16.0, gantry(night_ir=True)).lanes[0]
    assert night.zone_y1_m <= 40.0 + 1e-6 < day.zone_y1_m


def test_too_fast_for_the_zone_is_named():
    ln = analyse(CAM, 32.0, gantry(height_m=6.0, aim_y_m=40.0, speed_kmh=250.0, min_frames=20, exposure_s=1e-5)).lanes[0]
    assert not ln.ok and any("kare" in r for r in ln.reasons)


def test_optimizer_finds_a_passing_aim_and_is_never_worse_than_a_given_one():
    s = AnprSetup(height_m=6.0, pole_x_m=-1.5, lanes=2, speed_kmh=90.0)
    given = analyse(CAM, 16.0, replace(s, aim_y_m=10.0))
    best = optimize(CAM, s, focals=[8.0, 16.0, 32.0])
    assert best.lanes_ok == 2 and best.lanes_ok >= given.lanes_ok
    assert all(ln.frames >= s.min_frames and ln.blur_px_max <= s.blur_px + 1e-9 for ln in best.lanes)


def test_an_impossible_setup_reports_reasons_per_lane():
    wide = CameraConfig(focal_min_mm=2.8, focal_max_mm=2.8)
    r = optimize(wide, AnprSetup(height_m=10.0, pole_x_m=-8.0, lanes=3, speed_kmh=130.0))
    assert r.lanes_ok < 3 and all(ln.reasons for ln in r.lanes if not ln.ok)
