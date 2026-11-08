"""Hidden approach: covert region, breach, warning distance / time, sectors, buildings, sides."""
from __future__ import annotations

import heapq
import math

import numpy as np
import pytest

from cctv_simulator import surface_obstacles as obs
from cctv_simulator.approach import analyse_approach, geodesic
from cctv_simulator.terrain_loader import TerrainData

CELL = 5.0
FENCE = [(400.0, 400.0), (600.0, 400.0), (600.0, 600.0), (400.0, 600.0)]


def flat(n=200):
    return TerrainData(np.zeros((n, n)), cell_size_m=CELL)


def ring_detection(t, inner_m, outer_m):
    """Detected = everything between inner_m and outer_m outside the square fence."""
    cols = np.arange(t.cols) * CELL
    rows = np.arange(t.rows) * CELL
    mx, my = np.meshgrid(cols, rows)
    dx = np.maximum(np.maximum(400 - mx, mx - 600), 0)
    dy = np.maximum(np.maximum(400 - my, my - 600), 0)
    d = np.hypot(dx, dy)
    return (d >= inner_m) & (d <= outer_m), mx, my


def test_geodesic_equals_dijkstra_around_walls():
    rng = np.random.default_rng(7)
    passable = rng.random((50, 50)) > 0.3
    src = np.zeros((50, 50), bool)
    src[25, 25] = passable[25, 25] = True
    d, _ = geodesic(src, passable, 2.0)
    ref = np.full((50, 50), np.inf)
    ref[25, 25] = 0.0
    h = [(0.0, 25, 25)]
    while h:
        dd, r, c = heapq.heappop(h)
        if dd > ref[r, c]:
            continue
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                rr, cc = r + dr, c + dc
                if (dr or dc) and 0 <= rr < 50 and 0 <= cc < 50 and passable[rr, cc]:
                    nd = dd + 2.0 * (math.sqrt(2) if dr and dc else 1.0)
                    if nd < ref[rr, cc] - 1e-12:
                        ref[rr, cc] = nd
                        heapq.heappush(h, (nd, rr, cc))
    assert np.array_equal(np.isinf(d), np.isinf(ref))
    assert np.allclose(d[np.isfinite(d)], ref[np.isfinite(ref)])


def test_a_closed_detection_ring_gives_its_inner_edge_as_warning_distance():
    t = flat()
    det, _, _ = ring_detection(t, 0.0, 150.0)
    r = analyse_approach(t, det, FENCE, mode="facility", closed=True, depth_m=400, response_s=60)
    assert not r.breach
    # first seen at the ring's outer edge: ~150 m walk to the fence
    assert 145.0 <= r.warning_m <= 160.0
    assert r.warning_s["Yürüyerek"] == pytest.approx(r.warning_m / 1.4)
    assert r.sufficient["Yürüyerek"] and not r.sufficient["Araçla (40 km/s)"]
    assert r.dash_path[0] == r.breakout_xy and len(r.dash_path) > 10
    assert all(math.isfinite(s.warning_m) for s in r.sectors)


def test_an_unseen_corridor_through_the_ring_is_a_breach_on_that_side():
    t = flat()
    det, mx, my = ring_detection(t, 0.0, 150.0)
    det &= ~((np.abs(mx - 500) <= 10) & (my < 400))          # a dry stream bed to the south fence
    r = analyse_approach(t, det, FENCE, mode="facility", closed=True, depth_m=400)
    assert r.breach and r.warning_m == 0.0 and r.breach_points
    assert all(abs(x - 500) <= 15 and y <= 400 for x, y in r.breach_points)
    zero = [s for s in r.sectors if s.warning_m == 0.0]
    assert zero and all(min(s.y0, s.y1) <= 400.0 + 1e-6 for s in zero)
    assert any(s.warning_m > 100 for s in r.sectors)            # the other sides still warn


def test_a_blind_strip_behind_the_camera_band_is_not_a_breach():
    """Nothing is seen in the last 30 m before the fence, but the strip can only
    be reached through the band: the intruder is seen at the band's outer edge."""
    t = flat()
    det, _, _ = ring_detection(t, 30.0, 150.0)
    r = analyse_approach(t, det, FENCE, mode="facility", closed=True, depth_m=400)
    assert not r.breach and 145.0 <= r.warning_m <= 160.0


def test_buildings_force_a_detour_and_lengthen_the_warning():
    bare = flat()
    det, mx, my = ring_detection(bare, 0.0, 60.0)
    base = analyse_approach(bare, det, FENCE, mode="facility", closed=True, depth_m=300)
    h = np.zeros_like(bare.z_grid)
    band = np.hypot(np.maximum(np.maximum(400 - mx, mx - 600), 0), np.maximum(np.maximum(400 - my, my - 600), 0))
    h[(band > 0) & (band <= 40)] = 10.0                       # a solid row of buildings round the fence...
    h[(band > 0) & (band <= 40) & (np.abs(mx - 500) <= 5) & (my > 600)] = 0.0   # ...with one gate to the north
    surf = obs.with_obstacles(bare, obs.ObstacleLayer(height_grid=h, kind_grid=(h > 0).astype(np.uint8), counts={}))
    r = analyse_approach(surf, det, FENCE, mode="facility", closed=True, depth_m=300)
    assert not r.breach and r.warning_m >= base.warning_m - 1e-6
    north = [s for s in r.sectors if min(s.y0, s.y1) >= 600 - 1e-6 and math.isfinite(s.warning_m)]
    assert north and all(not math.isfinite(s.warning_m) for s in r.sectors if max(s.y0, s.y1) <= 400 + 1e-6)


def test_border_mode_only_counts_intruders_from_the_watched_side():
    t = flat()
    line = [(100.0, 500.0), (900.0, 500.0)]                  # drawn west -> east: right = south
    cols = np.arange(t.cols) * CELL
    mx, my = np.meshgrid(cols, cols)
    det = (my > 500) & (my < 700)                             # only the north (left) side is watched
    from_south = analyse_approach(t, det, line, mode="border", watch_side="right", depth_m=300)
    from_north = analyse_approach(t, det, line, mode="border", watch_side="left", depth_m=300)
    assert from_south.breach
    assert not from_north.breach and from_north.warning_m >= 190


def test_layer_marks_covert_and_detected_cells():
    t = flat()
    det, _, _ = ring_detection(t, 0.0, 100.0)
    r = analyse_approach(t, det, FENCE, mode="facility", closed=True, depth_m=300)
    assert set(np.unique(r.layer)) <= {0, 2, 3}
    assert (r.layer == 3).sum() * CELL * CELL == pytest.approx(r.covert_area_m2)
    assert not ((r.layer == 3) & det).any()


def test_bad_targets_are_refused():
    t = flat()
    with pytest.raises(ValueError):
        analyse_approach(t, np.zeros((200, 200), bool), [(5000.0, 5000.0), (6000.0, 5000.0)], mode="highway", closed=False)
