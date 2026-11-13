"""System-audit fixes: what the service and the optimisers must not get wrong again.

A1 "apply optimum" could blind the camera, A2 layout optimisation opened fence
gaps, A4/A5/A12 thermal levels and the median, A7/A8 framed reach and the
uncovered fence length, A10 truncation at a steep tilt, A11 the pole search
chasing a goal no number of poles reaches.
"""
from __future__ import annotations

import dataclasses
import io
import json

import numpy as np
import pytest

from cctv_simulator import atmosphere
from cctv_simulator.focal_optimizer import (apply_layout, apply_layout_keeping_fence, fence_roi, mounts_from_plan,
                                            optimize_layout)
from cctv_simulator.models import CameraConfig
from cctv_simulator.mount_visibility import clear_mount_cache
from cctv_simulator.perimeter_planner import _fence_points_covered, dead_zone_for, generate_perimeter_plan
from cctv_simulator.pole_optimizer import minimize_poles
from cctv_simulator.ptz_fusion import cue_response
from cctv_simulator.ptz_schedule import Rotation360, evaluate_sweep
from cctv_simulator.service import session
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import APP_ERROR, Server
from cctv_simulator.terrain_loader import TerrainData, generate_procedural_terrain
from cctv_simulator.viewshed_3d import ZONE_DETECT, ZONE_RECOG, calculate_3d_viewshed, identify_ppm

FIX = CameraConfig(name="Sabit", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                   focal_min_mm=2.8, focal_max_mm=12.0)
PTZ = CameraConfig(name="PTZ", focal_min_mm=4.8, focal_max_mm=153.0, resolution_name="2 MP (1080p - 1920x1080)")
THERMAL = CameraConfig(name="Termal PTZ", sensor_name="LWIR 640x512 (12µm)", resolution_name="LWIR (640x512)",
                       focal_min_mm=25.0, focal_max_mm=225.0)
SQUARE = [[300, 300], [700, 300], [700, 700], [300, 700]]


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


# ── A4 / A5: cue target and the median ──────────────────────────────────────
def test_cue_reports_the_real_median_next_to_the_mean(hills):
    r = ok("ptz.cue", terrain_id=hills, camera=dataclasses.asdict(PTZ), x_m=500, y_m=500, mast_m=10,
           home_pan_deg=0, home_tilt_deg=-4, target_ppm=250, range_m=500, max_px=300)
    t = session.SESSION.get(hills).bare
    want = cue_response(t, 500, 500, 10, PTZ, home_pan_deg=0, home_tilt_deg=-4, target_ppm=250, max_range_m=500)
    assert r["median_response_s"] == want.median_response_s
    assert r["median_response_s"] != r["mean_response_s"]          # the tile used to show the mean as "Medyan"
    assert r["thermal"] is False


def test_thermal_cue_defaults_to_the_johnson_identification_target(hills):
    r = ok("ptz.cue", terrain_id=hills, camera=dataclasses.asdict(THERMAL), x_m=500, y_m=500, mast_m=10,
           home_pan_deg=0, home_tilt_deg=-4, range_m=500, max_px=300)
    t = session.SESSION.get(hills).bare
    want = cue_response(t, 500, 500, 10, THERMAL, home_pan_deg=0, home_tilt_deg=-4,
                        target_ppm=identify_ppm(THERMAL), max_range_m=500)
    assert identify_ppm(THERMAL) == pytest.approx(13.33)
    assert r["thermal"] is True and r["reachable_area_m2"] == want.reachable_area_m2
    en = cue_response(t, 500, 500, 10, THERMAL, home_pan_deg=0, home_tilt_deg=-4, target_ppm=250, max_range_m=500)
    assert want.reachable_area_m2 > en.reachable_area_m2            # EN 250 px/m cut a thermal off at ~75 m


# ── A12: a thermal has no fourth level ──────────────────────────────────────
def test_thermal_plan_rejects_the_face_level_in_words(hills):
    cam = dataclasses.asdict(THERMAL)
    ok("perimeter.plan", terrain_id=hills, camera=cam, fence=SQUARE, target_ppm=1.3, mast_m=8)
    for method, extra in (("perimeter.optimize", {}), ("perimeter.coverage", {"max_px": 300})):
        r = call(method, terrain_id=hills, level="identify", **extra)
        assert r["error"]["code"] == APP_ERROR and "Termal" in r["error"]["message"], method
    r = call("area.plan", terrain_id=hills, camera=cam, fence=SQUARE, level="identify", goal_frac=0.5, mast_m=8)
    assert r["error"]["code"] == APP_ERROR and "Johnson" in r["error"]["message"]


# ── A7 / A8: framed reach, uncovered fence length ───────────────────────────
def test_framed_reach_follows_the_tilt(hills):
    cam = dataclasses.asdict(FIX)
    far = ok("viewshed.single", terrain_id=hills, camera=cam, x_m=500, y_m=500, mast_m=10, pan_deg=0,
             tilt_deg=-3, range_m=400, max_px=200)
    near = ok("viewshed.single", terrain_id=hills, camera=cam, x_m=500, y_m=500, mast_m=10, pan_deg=0,
              tilt_deg=-60, range_m=400, max_px=200)
    assert 0 < near["framed_reach_m"] < 0.5 * far["framed_reach_m"] <= far["max_range_m"]


def test_coverage_states_the_uncovered_fence_length_for_the_chosen_target(hills):
    p = ok("perimeter.plan", terrain_id=hills, camera=dataclasses.asdict(FIX), fence=SQUARE, target_ppm=40, mast_m=6)
    fl = {}
    for target in ("detect", "identify"):
        fl[target] = ok("perimeter.coverage", terrain_id=hills, level=target, max_px=300)["fence_line"]
        assert fl[target]["uncovered_m"] == pytest.approx((1 - fl[target]["held"]) * p["total_fence_length_m"])
    assert fl["identify"]["uncovered_m"] > fl["detect"]["uncovered_m"]   # the line follows the target


# ── A10: truncation at a steep tilt ─────────────────────────────────────────
def test_steep_tele_sweep_is_not_called_truncated():
    clear_mount_cache()
    terr = TerrainData(z_grid=np.zeros((120, 120), np.float32), cell_size_m=5.0)
    steep = Rotation360(speed_deg_s=10.0, tilt_deg=-45.0, focal_mm=129.0)   # frames the mast's foot only
    level = Rotation360(speed_deg_s=10.0, tilt_deg=-1.0, focal_mm=129.0)
    assert not evaluate_sweep(terr, 300.0, 300.0, 6.0, PTZ, steep, max_range_m=100.0).truncated
    assert evaluate_sweep(terr, 300.0, 300.0, 6.0, PTZ, level, max_range_m=100.0).truncated


# ── A2: layout optimisation and the fence line ──────────────────────────────
def _hills_engine():
    return generate_procedural_terrain(preset="ridge_and_valley", grid_size=100, cell_size_m=10.0)


def _flags(plan, cams, terr):
    band = atmosphere.band_for_camera(FIX.sensor_name, FIX.model_name)
    return _fence_points_covered(list(plan.fence_points), list(cams), 40.0, "", band, terr)[2]


def test_applied_layout_carries_the_dead_zone_of_its_new_frame():
    terr = _hills_engine()
    fence = [tuple(p) for p in SQUARE]
    plan = generate_perimeter_plan(terr, fence, FIX, target_ppm=40.0, mast_height_m=6.0)
    res = optimize_layout(terr, mounts_from_plan(plan, FIX), roi=fence_roi(terr, fence, True), level=ZONE_DETECT,
                          n_focals=4)
    new = apply_layout(plan, res, FIX, 40.0, terrain=terr)
    changed = [(c, ch) for c, ch in zip(new.placed_cameras, res.choices) if ch.changed]
    assert changed, "the optimiser changed nothing; the test needs another terrain"
    for cam, _ in changed:
        assert cam.dead_zone_m == pytest.approx(dead_zone_for(cam.mast_height_m, -cam.tilt_deg, cam.vfov_deg), abs=0.06)


def test_fence_guard_never_gives_up_covered_fence():
    terr = _hills_engine()
    fence = [tuple(p) for p in SQUARE]
    plan = generate_perimeter_plan(terr, fence, FIX, target_ppm=40.0, mast_height_m=6.0)
    res = optimize_layout(terr, mounts_from_plan(plan, FIX), roi=fence_roi(terr, fence, True), level=ZONE_DETECT,
                          n_focals=4)
    guarded, kept, reverted = apply_layout_keeping_fence(plan, res, FIX, 40.0, terrain=terr)
    before, after = _flags(plan, plan.placed_cameras, terr), _flags(guarded, guarded.placed_cameras, terr)
    per_sample = plan.total_fence_length_m / before.size
    net_lost_m = (int((before & ~after).sum()) - int((after & ~before).sum())) * per_sample
    assert net_lost_m <= 4.0
    assert sum(1 for c in kept.choices if c.changed) == sum(1 for c in res.choices if c.changed) - reverted
    if reverted:                                   # everything the reverted poles held is held again
        assert not (before & ~after).any()


def test_optimize_reports_the_fence_line_and_what_it_applied(hills):
    ok("perimeter.plan", terrain_id=hills, camera=dataclasses.asdict(FIX), fence=SQUARE, target_ppm=40, mast_m=6)
    r = ok("perimeter.optimize", terrain_id=hills, level="detect", n_focals=4)
    fl = r["fence_line"]
    assert fl["after_uncovered_m"] <= fl["before_uncovered_m"] + 4.0
    assert r["applied"] is (r["after_m2"] >= r["before_m2"] - 50.0)
    if r["reverted"]:
        assert any("eski ayarında" in n for n in r["notes"])
    # a border pole looks across the line by design: no fence-line verdict there
    ok("perimeter.plan", terrain_id=hills, camera=dataclasses.asdict(FIX), fence=[[200, 500], [800, 500]],
       closed=False, line_mode="border", watch_side="left", target_ppm=40, mast_m=6)
    assert ok("perimeter.optimize", terrain_id=hills, level="detect", n_focals=4)["fence_line"] is None


# ── A1: "apply optimum" goes through the engine first ───────────────────────
def test_focal_sweep_verifies_the_optimum_with_the_engine(hills):
    cam = dataclasses.asdict(FIX)
    r = ok("focal.sweep", terrain_id=hills, camera=cam, x_m=500, y_m=500, mast_m=12, pan_deg=0, level="recognize",
           n_focals=8, range_m=600, current_tilt_deg=-5, current_focal_mm=12.0)
    t = session.SESSION.get(hills).bare
    v = r["verified"]
    want = calculate_3d_viewshed(t, 500, 500, 12, FIX, pan_deg=v["pan_deg"], tilt_deg=v["tilt_deg"],
                                 max_range_m=r["reach_m"], focal_mm_override=v["focal_mm"], with_profile=False)
    assert v["area_m2"] == float((want.dori_grid >= ZONE_RECOG).sum()) * t.cell_size_m ** 2
    assert v["visible_area_m2"] == want.visible_area_m2
    assert r["gain_m2"] == pytest.approx(v["area_m2"] - r["current"]["area_m2"])
    if r["gain_m2"] <= 0:
        assert r["worth_applying"] is False and "korunur" in r["note"]


def test_focal_sweep_refuses_an_optimum_that_sees_nothing(hills):
    # 250 px/m ends ~23 m from this lens: from a 60 m mast no focal puts it on the ground
    r = ok("focal.sweep", terrain_id=hills, camera=dataclasses.asdict(FIX), x_m=500, y_m=500, mast_m=60, pan_deg=0,
           level="identify", n_focals=6, range_m=600, current_tilt_deg=-5, current_focal_mm=2.8)
    assert r["verified"]["area_m2"] == 0 and r["worth_applying"] is False
    assert "ulaşmıyor" in r["note"]


def test_focal_sweep_without_a_current_setting_still_verifies(hills):
    r = ok("focal.sweep", terrain_id=hills, camera=dataclasses.asdict(FIX), x_m=500, y_m=500, mast_m=8, pan_deg=0,
           level="detect", n_focals=6)
    assert r["current"] is None and r["gain_m2"] is None and r["verified"]["area_m2"] > 0 and r["worth_applying"]


# ── A11: a goal no number of poles reaches ──────────────────────────────────
def test_pole_search_stops_when_denser_poles_stop_helping():
    terr = _hills_engine()
    fence = [(200.0, 200.0), (800.0, 200.0), (800.0, 800.0), (200.0, 800.0)]
    kw = dict(target_ppm=40.0, overlap_pct=0.15, mast_height_m=6.0, lens_mode="min", is_closed_loop=True)
    res = minimize_poles(terr, fence, FIX, plan_kwargs=kw, goal_frac=0.8, level=ZONE_RECOG, n_focals=4)
    assert not res.met and len(res.trials) <= 3
    assert any("Arama durduruldu" in n for n in res.notes)
    assert res.best.optimised_frac == max(t.optimised_frac for t in res.trials)
