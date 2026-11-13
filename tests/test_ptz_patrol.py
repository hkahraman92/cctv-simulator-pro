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
from cctv_simulator.service.protocol import APP_ERROR, Server
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
