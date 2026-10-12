"""Continuous PTZ sweeps: closed-form revisit / look time vs a time simulation."""
from __future__ import annotations

import math

import numpy as np
import pytest

from cctv_simulator.models import CameraConfig
from cctv_simulator.mount_visibility import clear_mount_cache, mount_visibility, view_mask
from cctv_simulator.ptz_schedule import Rotation360, SectorPatrol, evaluate_sweep, sweep_timing
from cctv_simulator.terrain_loader import TerrainData, generate_procedural_terrain

CAM = CameraConfig(name="PTZ", sensor_name='1/2.8"', resolution_name="2 MP (1080p - 1920x1080)",
                   focal_min_mm=4.3, focal_max_mm=129.0)
DT = 0.002


def _axis(sweep, t):
    """Pan of the optical axis at times t (degrees)."""
    w = sweep.speed_deg_s
    if isinstance(sweep, Rotation360):
        return np.mod(w * t, 360.0)
    s, te = sweep.span_deg, sweep.end_dwell_s
    p = 2 * s / w + 2 * te
    tt = np.mod(t, p)
    out_t = s / w
    phi = np.where(tt < out_t, w * tt,                               # outbound
          np.where(tt < out_t + te, s,                               # dwell at end
          np.where(tt < 2 * out_t + te, s - w * (tt - out_t - te),   # return
                   0.0)))                                            # dwell at start
    return np.mod(sweep.start_pan_deg + phi, 360.0)


def _simulate(sweep, az, hfov, period):
    """Longest unwatched gap and shortest look over one period, by brute force."""
    t = np.arange(0.0, 2 * period, DT)
    pan = _axis(sweep, t)
    d = np.abs((az - pan + 180.0) % 360.0 - 180.0)
    inview = d <= hfov / 2.0 + 1e-9
    seg = inview[int(period / DT): int(2 * period / DT)]           # one steady period
    if not seg.any():
        return None, None
    if seg.all():
        return 0.0, math.inf
    ring = np.concatenate([seg, seg])                               # circular period
    runs, cur, val = [], 0, ring[0]
    for v in ring:
        if v == val:
            cur += 1
        else:
            runs.append((val, cur))
            val, cur = v, 1
    runs.append((val, cur))
    gaps = [n * DT for v, n in runs if not v]
    looks = [n * DT for v, n in runs[1:-1] if v]                    # interior = complete runs
    return max(gaps), min(looks)


@pytest.mark.parametrize("sweep", [
    SectorPatrol(start_pan_deg=30.0, end_pan_deg=150.0, speed_deg_s=40.0),
    SectorPatrol(start_pan_deg=300.0, end_pan_deg=40.0, speed_deg_s=25.0, end_dwell_s=1.5),
    Rotation360(speed_deg_s=30.0),
])
def test_closed_form_matches_time_simulation(sweep):
    hfov = 12.0
    rng = np.random.default_rng(7)
    az = np.concatenate([rng.uniform(0, 360, 40),
                         [sweep.start_pan_deg if hasattr(sweep, "start_pan_deg") else 0.0]])
    if isinstance(sweep, SectorPatrol):                             # cells right at both ends
        az = np.concatenate([az, [sweep.start_pan_deg - 3, sweep.end_pan_deg + 3,
                                  sweep.end_pan_deg - 2]])
    swept, revisit, look, period = sweep_timing(az, hfov, sweep)
    for i, a in enumerate(az):
        gap, shortest = _simulate(sweep, a, hfov, period)
        if gap is None:
            assert not swept[i]
            continue
        assert swept[i]
        assert revisit[i] == pytest.approx(gap, abs=3 * DT), (a, revisit[i], gap)
        if math.isinf(shortest):
            assert math.isinf(look[i])
        else:
            assert look[i] == pytest.approx(shortest, abs=3 * DT), (a, look[i], shortest)


def test_sector_narrower_than_frame_is_continuous():
    swept, revisit, look, _p = sweep_timing(np.array([100.0, 105.0]), 30.0,
                                            SectorPatrol(start_pan_deg=95.0, end_pan_deg=110.0))
    assert swept.all() and (revisit == 0).all() and np.isinf(look).all()


def test_swept_area_is_the_union_of_its_fixed_views():
    clear_mount_cache()
    terr = generate_procedural_terrain("rolling_hills", grid_size=160, cell_size_m=4.0)
    x, y, mast = terr.width_m / 2, terr.height_m / 2, 12.0
    sweep = SectorPatrol(start_pan_deg=20.0, end_pan_deg=110.0, speed_deg_s=10.0,
                         tilt_deg=-6.0, focal_mm=12.0)
    res = evaluate_sweep(terr, x, y, mast, CAM, sweep, max_range_m=300.0, min_dwell_s=0.0)
    mv = mount_visibility(terr, x, y, mast, max_range_m=300.0)
    union = np.zeros_like(res.seen_mask)
    for pan in np.arange(20.0, 110.0 + 1e-9, 0.25):
        union |= view_mask(mv, CAM, pan_deg=float(pan), tilt_deg=-6.0, focal_mm=12.0).visibility_mask
    swept = res.seen_mask | res.glimpse_mask
    assert union.sum() > 300
    # same cells, up to the 0.25-degree pan sampling of the union
    assert np.count_nonzero(swept ^ union) <= 0.01 * np.count_nonzero(swept | union)
    assert res.occluded_area_m2 > 0                                 # hills cast shadows


def test_fast_tele_sweep_is_only_glimpses():
    clear_mount_cache()
    terr = TerrainData(z_grid=np.zeros((200, 200), np.float32), cell_size_m=5.0)
    x = y = 500.0
    fast = Rotation360(speed_deg_s=90.0, tilt_deg=-1.0, focal_mm=129.0)     # ~2.5 deg frame
    slow = Rotation360(speed_deg_s=2.0, tilt_deg=-1.0, focal_mm=129.0)
    r_fast = evaluate_sweep(terr, x, y, 12.0, CAM, fast, max_range_m=600.0, min_dwell_s=0.5)
    r_slow = evaluate_sweep(terr, x, y, 12.0, CAM, slow, max_range_m=600.0, min_dwell_s=0.5)
    assert r_fast.seen_area_m2 == 0 and r_fast.glimpse_area_m2 > 0           # ~28 ms per look
    assert r_slow.seen_area_m2 == pytest.approx(r_fast.glimpse_area_m2)      # same cells, slow enough
    assert r_slow.period_s == pytest.approx(180.0)
    assert r_slow.worst_revisit_s == pytest.approx(180.0 - r_slow.hfov_deg / 2.0)
    assert np.isinf(r_fast.revisit_grid[r_fast.glimpse_mask]).all()          # glimpses are not coverage
