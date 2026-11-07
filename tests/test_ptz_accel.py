"""PTZ trapezoidal slew profile: analytic values, a time-stepped check, and no change without accel."""
import numpy as np
import pytest

from cctv_simulator.models import CameraConfig
from cctv_simulator.ptz_tour import PTZPreset, PTZTour, _transition_s, axis_time_s, move_time_s

CAM = CameraConfig(focal_min_mm=4.8, focal_max_mm=153.0)


def _simulate(d, v, a, dt=1e-4):
    """Accelerate, cruise, brake just in time: integrate until the angle is covered."""
    pos = vel = t = 0.0
    while pos < d:
        brake = vel * vel / (2 * a)
        vel = max(vel - a * dt, dt * a) if d - pos <= brake else min(vel + a * dt, v)
        pos += vel * dt
        t += dt
    return t


@pytest.mark.parametrize("d", [2.0, 10.0, 45.0, 180.0])
def test_matches_a_time_stepped_motion(d):
    # Euler stepping catches the brake point only to ~1 %; the analytic values are checked exactly below
    assert axis_time_s(d, 90.0, 200.0) == pytest.approx(_simulate(d, 90.0, 200.0), rel=1.5e-2)


def test_analytic_regimes():
    # long move: d/v + v/a ; short move (never reaches v): 2*sqrt(d/a)
    assert float(axis_time_s(180.0, 90.0, 180.0)) == pytest.approx(180 / 90 + 90 / 180)
    assert float(axis_time_s(10.0, 90.0, 180.0)) == pytest.approx(2 * np.sqrt(10 / 180))
    # acceleration only ever slows a move down
    d = np.linspace(0, 360, 50)
    assert np.all(axis_time_s(d, 90.0, 120.0) >= axis_time_s(d, 90.0, None) - 1e-12)


def test_none_is_the_original_model_bit_for_bit():
    rng = np.random.default_rng(0)
    dp, dt, df = rng.uniform(-400, 400, 200), rng.uniform(-60, 60, 200), rng.uniform(-150, 150, 200)
    a = move_time_s(dp, dt, df, CAM, slew_speed_deg_s=90, zoom_full_sweep_s=3, settle_s=1.5)
    b = move_time_s(dp, dt, df, CAM, slew_speed_deg_s=90, zoom_full_sweep_s=3, settle_s=1.5, slew_accel_deg_s2=None)
    for x, y in zip(a, b):
        assert np.array_equal(x, y)


def test_tour_period_grows_with_finite_acceleration():
    presets = [PTZPreset("A", 0, -5), PTZPreset("B", 120, -5), PTZPreset("C", 240, -5)]
    base = PTZTour(0, 0, 8, CAM, presets=presets)
    slow = PTZTour(0, 0, 8, CAM, presets=presets, slew_accel_deg_s2=60.0)
    s0 = _transition_s(base, presets[0], presets[1])[0]
    s1 = _transition_s(slow, presets[0], presets[1])[0]
    # 120 deg < v^2/a = 135 deg: the move never reaches top speed -> 2*sqrt(d/a)
    assert s0 == pytest.approx(120 / 90) and s1 == pytest.approx(2 * np.sqrt(120 / 60))
