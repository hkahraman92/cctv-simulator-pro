"""PTZ patrol search (focal / tilt per PTZ) and the on-demand reach column."""
from __future__ import annotations

import dataclasses
import io
import json

import numpy as np
import pytest

from cctv_simulator.focal_optimizer import fence_roi
from cctv_simulator.models import CameraConfig
from cctv_simulator.perimeter_planner import generate_perimeter_plan
from cctv_simulator.perimeter_ptz import _patrol_added, optimize_patrols
from cctv_simulator.service import session
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import APP_ERROR, INVALID_PARAMS, Server
from cctv_simulator.terrain_loader import generate_procedural_terrain
from cctv_simulator.viewshed_3d import ZONE_DETECT, ZONE_RECOG

FIX = CameraConfig(name="Sabit", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                   focal_min_mm=2.8, focal_max_mm=12.0)
PTZ = CameraConfig(name="PTZ", focal_min_mm=4.8, focal_max_mm=153.0, resolution_name="2 MP (1080p - 1920x1080)")
SQUARE = [[300, 300], [700, 300], [700, 700], [300, 700]]
KW = dict(target_ppm=40.0, mast_height_m=8.0, ptz_camera=PTZ, ptz_every_n=3, ptz_patrol_span_deg=360.0)


def call(method, **params):
    out = io.StringIO()
    server = Server(METHODS, out=out)
    server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
    server.wait_idle()
    return next(json.loads(x) for x in out.getvalue().splitlines() if json.loads(x).get("id") == 1)


def ok(method, **params):
    r = call(method, **params)
    assert "result" in r, r.get("error")
    return r["result"]


@pytest.fixture
def hills():
    session.reset()
    return ok("terrain.procedural", preset="ridge_and_valley", grid=100, width_m=1000.0)["terrain_id"]


def _engine_case():
    terr = generate_procedural_terrain(preset="ridge_and_valley", grid_size=100, cell_size_m=10.0)
    fence = [tuple(p) for p in SQUARE]
    plan = generate_perimeter_plan(terr, fence, FIX, **KW)
    return terr, plan, fence_roi(terr, fence, True, 100.0)


@pytest.mark.parametrize("level", [ZONE_DETECT, ZONE_RECOG])
def test_patrol_search_is_scored_by_the_sweep_engine_and_never_loses(level):
    terr, plan, roi = _engine_case()
    assert plan.ptz_cameras
    res = optimize_patrols(terr, plan, PTZ, roi=roi, level=level, n_focals=5)
    none = np.zeros(roi.shape, bool)
    assert res.before_m2 == _patrol_added(terr, plan, PTZ, roi, none, level)[0]
    assert _patrol_added(terr, res.plan, PTZ, roi, none, level)[0] == max(res.before_m2, res.after_m2)
    if res.applied:
        assert res.after_m2 > res.before_m2 and any(c.changed for c in res.choices)
    else:
        assert res.plan is plan and not any(c.changed for c in res.choices)
    # only the patrol changes: same poles, same span, the fixed cameras untouched
    assert [(p.pole_id, p.x_m, p.y_m, p.patrol_span_deg) for p in res.plan.ptz_cameras] == \
           [(p.pole_id, p.x_m, p.y_m, p.patrol_span_deg) for p in plan.ptz_cameras]
    assert res.plan.placed_cameras == plan.placed_cameras
    for p, c in zip(res.plan.ptz_cameras, res.choices):
        assert PTZ.focal_min_mm - 1e-6 <= p.focal_mm <= PTZ.focal_max_mm + 1e-6 and c.focal_mm == p.focal_mm


def test_recognition_patrol_zooms_in():
    # the planner patrols at the wide end; holding Recognition further out takes a longer focal
    terr, plan, roi = _engine_case()
    res = optimize_patrols(terr, plan, PTZ, roi=roi, level=ZONE_RECOG, n_focals=5)
    assert res.applied and res.after_m2 > res.before_m2
    assert max(c.focal_mm for c in res.choices) > PTZ.focal_min_mm + 1.0


def test_cells_fixed_cameras_hold_are_not_what_a_patrol_is_chosen_for():
    terr, plan, roi = _engine_case()
    everything = np.ones(roi.shape, bool)
    res = optimize_patrols(terr, plan, PTZ, roi=roi, exclude=everything, level=ZONE_DETECT, n_focals=4)
    assert res.before_m2 == 0 and res.after_m2 == 0 and not res.applied


def test_service_patrol_matches_the_engine_and_updates_the_plan(hills):
    cam, ptz = dataclasses.asdict(FIX), dataclasses.asdict(PTZ)
    p = ok("perimeter.plan", terrain_id=hills, camera=cam, fence=SQUARE, target_ppm=40, mast_m=8,
           ptz_camera=ptz, ptz_every_n=3, ptz_patrol_span_deg=360)
    assert p["ptz"]
    before = ok("perimeter.coverage", terrain_id=hills, level="recognize", band_m=100, max_px=300)
    r = ok("perimeter.ptz_patrol", terrain_id=hills, level="recognize", band_m=100, n_focals=5)
    assert r["ptz_count"] == len(p["ptz"]) and r["after_m2"] >= r["before_m2"]
    assert r["applied"] is (r["after_m2"] > r["before_m2"])
    assert [c["focal_mm"] for c in r["choices"]] == [q["focal_mm"] for q in r["plan"]["ptz"]]
    after = ok("perimeter.coverage", terrain_id=hills, level="recognize", band_m=100, max_px=300)
    # the coverage report runs the same patrols: its PTZ-only share is the search's own number
    assert after["band_share_ptz"]["recognize"] * after["band_area_m2"] == pytest.approx(r["after_m2"], abs=1.0)
    assert before["band_share_ptz"]["recognize"] * before["band_area_m2"] == pytest.approx(r["before_m2"], abs=1.0)
    assert after["band_share"] == before["band_share"]             # fixed cameras: untouched


def test_patrol_search_needs_a_plan_with_a_ptz(hills):
    ok("perimeter.plan", terrain_id=hills, camera=dataclasses.asdict(FIX), fence=SQUARE, target_ppm=40, mast_m=8)
    r = call("perimeter.ptz_patrol", terrain_id=hills)
    assert r["error"]["code"] == APP_ERROR and "PTZ" in r["error"]["message"]


def test_on_demand_reach_is_a_third_column_on_request(hills):
    cam, ptz = dataclasses.asdict(FIX), dataclasses.asdict(PTZ)
    ok("perimeter.plan", terrain_id=hills, camera=cam, fence=SQUARE, target_ppm=40, mast_m=8,
       ptz_camera=ptz, ptz_every_n=3, ptz_patrol_span_deg=90)
    plain = ok("perimeter.coverage", terrain_id=hills, band_m=100, max_px=300)
    assert plain["band_share_reach"] is None
    c = ok("perimeter.coverage", terrain_id=hills, band_m=100, max_px=300, ptz_reach=True)
    reach = c["band_share_reach"]
    for k in ("detect", "observe", "recognize", "identify"):
        assert reach[k] >= 0 and c["band_share"][k] + c["band_share_ptz"][k] + reach[k] <= 1 + 1e-9
    # a 90 degree patrol leaves most of the circle to be reached on demand
    assert reach["detect"] > 0 and reach["recognize"] > 0
    assert c["band_share"] == plain["band_share"] and c["band_share_ptz"] == plain["band_share_ptz"]


# ── patrol speed as a searched dimension ──────────────────────────────────────
SPEEDS = [5.0, 10.0, 20.0, 40.0, 80.0]


def _plan_signature(plan):
    return [(p.pole_id, p.focal_mm, p.tilt_deg, p.hfov_deg, p.vfov_deg, p.dead_zone_m, p.speed_deg_s)
            for p in plan.ptz_cameras]


def test_no_speed_candidates_is_bit_identical_and_keeps_every_ptz_speed_unset():
    terr, plan, roi = _engine_case()
    base = optimize_patrols(terr, plan, PTZ, roi=roi, level=ZONE_RECOG, n_focals=5)
    only_current = optimize_patrols(terr, plan, PTZ, roi=roi, level=ZONE_RECOG, n_focals=5, speeds_deg_s=[20.0])
    for res in (base, only_current):
        assert all(p.speed_deg_s is None for p in res.plan.ptz_cameras)      # nothing sets a per-PTZ speed
        assert all(c.speed_deg_s == 20.0 for c in res.choices)
    for a, b in ((base.before_m2, only_current.before_m2), (base.after_m2, only_current.after_m2),
                 (base.worst_revisit_before_s, only_current.worst_revisit_before_s),
                 (base.worst_revisit_after_s, only_current.worst_revisit_after_s)):
        assert a == b
    assert _plan_signature(base.plan) == _plan_signature(only_current.plan)
    assert [(c.focal_mm, c.tilt_deg, c.hfov_deg, c.changed) for c in base.choices] == \
           [(c.focal_mm, c.tilt_deg, c.hfov_deg, c.changed) for c in only_current.choices]


def test_a_speed_search_is_never_worse_and_slows_down_where_it_pays():
    terr, plan, roi = _engine_case()
    base = optimize_patrols(terr, plan, PTZ, roi=roi, level=ZONE_RECOG, n_focals=5)
    fast = optimize_patrols(terr, plan, PTZ, roi=roi, level=ZONE_RECOG, n_focals=5, speeds_deg_s=SPEEDS)
    assert fast.before_m2 == base.before_m2
    assert fast.after_m2 >= base.after_m2 and fast.applied
    slow = [c for c in fast.choices if c.speed_deg_s < 20.0]
    assert slow and fast.after_m2 > base.after_m2        # a narrow tele frame only holds a look at a low speed
    # the speed is on the plan; the engine, run on that plan at the global speed, is the search's own number
    chosen = {p.pole_id: p.speed_deg_s for p in fast.plan.ptz_cameras}
    assert all(chosen[c.pole_id] == (c.speed_deg_s if c.speed_deg_s != 20.0 else None) for c in fast.choices)
    none = np.zeros(roi.shape, bool)
    assert _patrol_added(terr, fast.plan, PTZ, roi, none, ZONE_RECOG)[0] == fast.after_m2
    # revisit is the price: reported per PTZ, before and after
    assert max(c.revisit_after_s for c in slow) > max(c.revisit_before_s for c in slow)
    assert fast.worst_revisit_after_s == pytest.approx(max(c.revisit_after_s for c in fast.choices))


def _worst_revisit_per_ptz(terr, plan, roi, level):
    from cctv_simulator.perimeter_ptz import ptz_sweeps

    out = []
    for sw in ptz_sweeps(terr, plan, PTZ):
        seen = np.isfinite(sw.revisit_grid) & (sw.dori_grid >= level) & roi
        out.append(float(sw.revisit_grid[seen].max()) if seen.any() else 0.0)
    return out


def test_max_revisit_is_respected_by_the_sweep_engine():
    terr, plan, roi = _engine_case()
    free = optimize_patrols(terr, plan, PTZ, roi=roi, level=ZONE_RECOG, n_focals=5, speeds_deg_s=SPEEDS)
    assert max(_worst_revisit_per_ptz(terr, free.plan, roi, ZONE_RECOG)) > 30.0       # the limit below binds
    for limit in (30.0, 100.0):
        res = optimize_patrols(terr, plan, PTZ, roi=roi, level=ZONE_RECOG, n_focals=5, speeds_deg_s=SPEEDS,
                               max_revisit_s=limit)
        worst = _worst_revisit_per_ptz(terr, res.plan, roi, ZONE_RECOG)
        assert max(worst) <= limit + 1e-9                      # evaluate_sweep's own revisit, per PTZ
        assert res.worst_revisit_after_s <= limit + 1e-9
        assert res.after_m2 >= res.before_m2
    tight = optimize_patrols(terr, plan, PTZ, roi=roi, level=ZONE_RECOG, n_focals=5, speeds_deg_s=SPEEDS,
                             max_revisit_s=30.0)
    loose = optimize_patrols(terr, plan, PTZ, roi=roi, level=ZONE_RECOG, n_focals=5, speeds_deg_s=SPEEDS,
                             max_revisit_s=100.0)
    assert loose.after_m2 > tight.after_m2 and any(c.speed_deg_s < 20.0 for c in loose.choices)
    assert all(c.speed_deg_s >= 20.0 for c in tight.choices)


def test_a_revisit_limit_nothing_can_meet_leaves_the_plan_and_says_so():
    terr, plan, roi = _engine_case()
    res = optimize_patrols(terr, plan, PTZ, roi=roi, level=ZONE_RECOG, n_focals=4, speeds_deg_s=SPEEDS,
                           max_revisit_s=1.0)
    assert not res.applied and res.plan is plan and not any(c.changed for c in res.choices)
    assert any("sınırını tutmadı" in n for n in res.notes)
    with pytest.raises(ValueError, match="hız"):
        optimize_patrols(terr, plan, PTZ, roi=roi, level=ZONE_RECOG, speeds_deg_s=[0.0])
    with pytest.raises(ValueError, match="sınır"):
        optimize_patrols(terr, plan, PTZ, roi=roi, level=ZONE_RECOG, max_revisit_s=-3.0)


@pytest.mark.parametrize("span", [360.0, 120.0])
def test_a_per_ptz_speed_drives_patrol_for_and_the_coverage_share(span):
    from cctv_simulator.perimeter_ptz import patrol_for, ptz_sweeps

    terr = generate_procedural_terrain(preset="ridge_and_valley", grid_size=100, cell_size_m=10.0)
    fence = [tuple(p) for p in SQUARE]
    plan = generate_perimeter_plan(terr, fence, FIX, **dict(KW, ptz_patrol_span_deg=span))
    p0 = plan.ptz_cameras[0]
    assert patrol_for(p0, 20.0, 2.0).speed_deg_s == 20.0          # no per-PTZ speed: the caller's value
    slow_p = dataclasses.replace(p0, speed_deg_s=5.0)
    assert patrol_for(slow_p, 20.0, 2.0).speed_deg_s == 5.0       # the PTZ's own speed wins
    assert type(patrol_for(slow_p, 20.0, 2.0)) is type(patrol_for(p0, 20.0, 2.0))
    slow_plan = dataclasses.replace(plan, ptz_cameras=[dataclasses.replace(c, speed_deg_s=5.0)
                                                       for c in plan.ptz_cameras])
    own = ptz_sweeps(terr, slow_plan, PTZ)                      # global 20 deg/s, every PTZ carries 5
    glob = ptz_sweeps(terr, plan, PTZ, speed_deg_s=5.0)         # the old way: 5 deg/s for everybody
    default = ptz_sweeps(terr, plan, PTZ)
    for a, b in zip(own, glob):
        assert a.period_s == b.period_s and a.seen_area_m2 == b.seen_area_m2
        assert np.array_equal(a.revisit_grid, b.revisit_grid) and np.array_equal(a.dori_grid, b.dori_grid)
    assert [s.period_s for s in own] != [s.period_s for s in default]
    roi = fence_roi(terr, fence, True, 100.0)
    none = np.zeros(roi.shape, bool)
    assert _patrol_added(terr, slow_plan, PTZ, roi, none, ZONE_DETECT)[0] == \
        _patrol_added(terr, plan, PTZ, roi, none, ZONE_DETECT, speed_deg_s=5.0)[0]


def test_service_patrol_speed_search_round_trips_through_the_plan_json(hills):
    from cctv_simulator.perimeter_planner import PlacedCamera

    cam, ptz = dataclasses.asdict(FIX), dataclasses.asdict(PTZ)
    ok("perimeter.plan", terrain_id=hills, camera=cam, fence=SQUARE, target_ppm=40, mast_m=8,
       ptz_camera=ptz, ptz_every_n=3, ptz_patrol_span_deg=360)
    plain = ok("perimeter.ptz_patrol", terrain_id=hills, level="recognize", band_m=100, n_focals=5, apply=False)
    assert plain["speed_search"] is False and all(c["speed_deg_s"] == 20.0 for c in plain["choices"])
    r = ok("perimeter.ptz_patrol", terrain_id=hills, level="recognize", band_m=100, n_focals=5,
           speeds_deg_s=SPEEDS, max_revisit_s=100)
    assert r["speed_search"] is True and r["max_revisit_s"] == 100 and r["applied"]
    assert r["after_m2"] >= plain["after_m2"] and r["worst_revisit_after_s"] <= 100.0 + 1e-9
    assert all(c["revisit_after_s"] <= 100.0 + 1e-9 for c in r["choices"])
    assert {c["speed_deg_s"] for c in r["choices"]} - {20.0}
    # the plan JSON carries the speed (null where the PTZ follows the global value) and rebuilds the placed camera
    assert [q["speed_deg_s"] for q in r["plan"]["ptz"]] == \
        [None if c["speed_deg_s"] == 20.0 else c["speed_deg_s"] for c in r["choices"]]
    entry = session.SESSION.get(hills)
    stored = entry.results["plan"][0].ptz_cameras
    assert [PlacedCamera(**q) for q in r["plan"]["ptz"]] == stored
    # coverage and live playback run the chosen speeds
    cov = ok("perimeter.coverage", terrain_id=hills, level="recognize", band_m=100, max_px=300)
    assert cov["band_share_ptz"]["recognize"] * cov["band_area_m2"] == pytest.approx(r["after_m2"], abs=1.0)
    play = ok("ptz.playback", terrain_id=hills, max_px=200)
    assert [x["speed_deg_s"] for x in play["ptz"]] == [c["speed_deg_s"] for c in r["choices"]]
    for bad in ([], [0.1], "fast", [5, "x"], [True], list(range(1, 20))):
        e = call("perimeter.ptz_patrol", terrain_id=hills, speeds_deg_s=bad)
        assert e["error"]["code"] == INVALID_PARAMS
    e = call("perimeter.ptz_patrol", terrain_id=hills, max_revisit_s=0)
    assert e["error"]["code"] == INVALID_PARAMS
