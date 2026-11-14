"""PTZ-assisted target: fewest poles + PTZs with fixed cameras detecting and a PTZ holding a level in time."""
from __future__ import annotations

import dataclasses
import io
import json

import numpy as np
import pytest

from cctv_simulator.focal_optimizer import band_area, fence_roi
from cctv_simulator.models import CameraConfig
from cctv_simulator.perimeter_planner import generate_perimeter_plan
from cctv_simulator.perimeter_ptz import evaluate_mixed_perimeter
from cctv_simulator.pole_optimizer import minimize_poles, minimize_poles_assisted
from cctv_simulator.service import session
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import APP_ERROR, INVALID_PARAMS, Server
from cctv_simulator.terrain_loader import TerrainData
from cctv_simulator.viewshed_3d import ZONE_DETECT, ZONE_IDENT, ZONE_MONITOR, ZONE_OBSERVE, ZONE_RECOG, zone_ppm

# a long-lens fixed camera: Observation / Recognition are within reach of fixed poles on a short line
FIX = CameraConfig(name="Sabit", sensor_name='1/2.8"', resolution_name="8 MP (4K - 3840x2160)",
                   focal_min_mm=4.0, focal_max_mm=25.0, pole_height_m=6.0)
WIDE = CameraConfig(name="Sabit", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                    focal_min_mm=2.8, focal_max_mm=12.0)
PTZ = CameraConfig(name="PTZ", focal_min_mm=4.8, focal_max_mm=153.0, resolution_name="2 MP (1080p - 1920x1080)")
SHORT_PTZ = dataclasses.replace(PTZ, focal_max_mm=8.0)         # cannot hold Recognition at any useful distance
LINE = [(100.0, 300.0), (500.0, 300.0)]
KW = dict(target_ppm=25.0, overlap_pct=0.15, mast_height_m=6.0, lens_mode="min", is_closed_loop=False)
SQUARE = [[300, 300], [700, 300], [700, 700], [300, 700]]


def _flat(n=160, cell=5.0):
    return TerrainData(z_grid=np.zeros((n, n), np.float32), cell_size_m=cell)


def _wide_flat():
    return TerrainData(z_grid=np.zeros((100, 100), np.float32), cell_size_m=10.0)


def _shares(terr, res, fence, camera, ptz, closed):
    """The plan statement's definition, computed independently of the planner."""
    roi = fence_roi(terr, [tuple(p) for p in fence], closed, 40.0, None, None)
    mixed = evaluate_mixed_perimeter(terr, res.plan, camera, ptz, cue_target_ppm=res.target_ppm,
                                     fixed_reach_level=ZONE_DETECT)
    mv, _ = band_area(terr, res.plan, camera, roi, ZONE_DETECT)
    det = (mv.dori_grid >= ZONE_DETECT) & roi
    n = int(roi.sum())
    return float(det.sum()) / n, float((det & (mixed.detect_to_identify_s <= res.max_s)).sum()) / n


def test_reachable_target_is_met_and_the_shares_are_the_authoritative_ones():
    terr = _flat()
    res = minimize_poles_assisted(terr, LINE, FIX, PTZ, level=ZONE_RECOG, goal_frac=0.2, max_s=10.0,
                                  plan_kwargs=KW, n_focals=4)
    assert res.met and res.plan is not None
    det, ok_share = _shares(terr, res, LINE, FIX, PTZ, False)
    assert res.detect_share == pytest.approx(det, abs=1e-12) and res.assisted_share == pytest.approx(ok_share, abs=1e-12)
    assert det >= 0.2 and ok_share >= 0.2
    assert res.target_ppm == zone_ppm(ZONE_RECOG)
    assert res.ptz_count == len(res.plan.ptz_cameras) >= 1 and res.fixed_cameras == len(res.plan.placed_cameras)
    assert res.plan.camera_count == res.fixed_cameras + res.ptz_count and res.pole_count == res.plan.pole_count
    assert res.roi_area_m2 == float(fence_roi(terr, LINE, False, 40.0, None, None).sum()) * terr.cell_size_m ** 2


def test_fewer_poles_than_the_fixed_only_search_at_the_same_level():
    terr = _flat()
    res = minimize_poles_assisted(terr, LINE, FIX, PTZ, level=ZONE_RECOG, goal_frac=0.2, max_s=10.0,
                                  plan_kwargs=KW, n_focals=4)
    detect = minimize_poles(terr, LINE, FIX, plan_kwargs=KW, goal_frac=0.2, level=ZONE_DETECT, n_focals=4)
    fixed_l = minimize_poles(terr, LINE, FIX, plan_kwargs=KW, goal_frac=0.2, level=ZONE_RECOG, n_focals=4)
    assert res.met and detect.met and fixed_l.met                 # Recognition is reachable by fixed cameras here
    assert res.pole_count <= detect.best.fixed_poles              # the fixed line IS the Detection search's
    assert res.pole_count < fixed_l.best.fixed_poles


def test_a_larger_time_budget_never_needs_more_ptz_and_n_is_maximal():
    terr = _wide_flat()
    fence = [(300, 300), (700, 300), (700, 700), (300, 700)]
    kw = dict(target_ppm=25.0, mast_height_m=8.0, is_closed_loop=True)
    out = {}
    for s in (6.0, 10.0, 30.0):
        out[s] = minimize_poles_assisted(terr, fence, WIDE, PTZ, level=ZONE_RECOG, goal_frac=0.9, max_s=s,
                                         plan_kwargs=kw, n_focals=4)
    assert all(r.met for r in out.values()), [r.notes for r in out.values()]
    assert out[30.0].ptz_count <= out[10.0].ptz_count <= out[6.0].ptz_count
    assert len({r.pole_count for r in out.values()}) == 1         # same fixed line: only the PTZ density moves
    r = out[10.0]
    n = r.ptz_every_n
    assert r.assisted_share >= 0.9
    mine = {t.ptz_every_n: t for t in r.trials if t.fixed_poles == r.pole_count}
    assert mine[n].assisted_share == pytest.approx(r.assisted_share)
    # n is the largest that holds: n + 1 was evaluated and fails, or n is already the single-PTZ case
    assert (n + 1 in mine and mine[n + 1].assisted_share < 0.9) or r.ptz_count == 1
    assert not r.capped


def test_fixed_poles_do_not_move_with_the_ptz_density():
    terr = _wide_flat()
    fence = [(300, 300), (700, 300), (700, 700), (300, 700)]
    kw = dict(target_ppm=25.0, mast_height_m=8.0, is_closed_loop=True)
    res = minimize_poles_assisted(terr, fence, WIDE, PTZ, level=ZONE_RECOG, goal_frac=0.9, plan_kwargs=kw, n_focals=4)
    sp = res.pole_search.best_spacing_m
    xy = None
    for n in (1, 3, 7, 40):
        p = generate_perimeter_plan(terr, fence, WIDE, spacing_m=sp, ptz_camera=PTZ, ptz_every_n=n,
                                    ptz_patrol_span_deg=360.0, **kw)
        got = [(c.pole_id, c.x_m, c.y_m) for c in p.placed_cameras]
        xy = xy or got
        assert got == xy
    assert [(c.pole_id, c.x_m, c.y_m) for c in res.plan.placed_cameras] == xy
    # and every PTZ stands on a fixed pole
    poles = {(c.x_m, c.y_m) for c in res.plan.placed_cameras}
    assert all((p.x_m, p.y_m) in poles for p in res.plan.ptz_cameras)


def test_too_short_a_time_budget_is_not_met_and_names_part_b():
    res = minimize_poles_assisted(_flat(), LINE, FIX, PTZ, level=ZONE_RECOG, goal_frac=0.2, max_s=1.0,
                                  plan_kwargs=KW, n_focals=4)           # settle alone is 1.5 s
    assert not res.met and res.assisted_share < 0.2
    assert any(n.startswith("(b)") for n in res.notes) and "plan değiştirilmedi" in res.notes[-1]
    assert res.detect_share >= 0.2                                       # (a) was fine


def test_a_ptz_lens_that_cannot_hold_the_level_is_not_met():
    res = minimize_poles_assisted(_flat(), LINE, FIX, SHORT_PTZ, level=ZONE_IDENT, goal_frac=0.2, plan_kwargs=KW,
                                  n_focals=4)
    assert not res.met and any(n.startswith("(b)") for n in res.notes)
    assert res.assisted_share < 0.2


def test_unreachable_continuous_detection_is_part_a():
    res = minimize_poles_assisted(_flat(), LINE, FIX, PTZ, level=ZONE_RECOG, goal_frac=1.01, plan_kwargs=KW,
                                  n_focals=4, max_trials=5)
    assert not res.met and res.notes[0].startswith("(a)") and res.detect_share < 1.01


@pytest.mark.parametrize("level", [ZONE_DETECT, ZONE_MONITOR])
def test_a_level_at_or_below_detection_is_rejected(level):
    with pytest.raises(ValueError, match="Algılama'nın üstünde"):
        minimize_poles_assisted(_flat(), LINE, FIX, PTZ, level=level, plan_kwargs=KW)


def test_thermal_identification_is_rejected_and_area_mode_too():
    thermal = dataclasses.replace(PTZ, sensor_name="LWIR 640x512 (12µm)")
    with pytest.raises(ValueError, match="Termal"):
        minimize_poles_assisted(_flat(), LINE, FIX, thermal, level=ZONE_IDENT, plan_kwargs=KW)
    with pytest.raises(ValueError, match="alan modunda"):
        minimize_poles_assisted(_flat(), LINE, FIX, PTZ, level=ZONE_OBSERVE, plan_kwargs=dict(KW, line_mode="area"))
    with pytest.raises(ValueError, match="süresi"):
        minimize_poles_assisted(_flat(), LINE, FIX, PTZ, level=ZONE_OBSERVE, max_s=0.0, plan_kwargs=KW)


# ── service ────────────────────────────────────────────────────────────────
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


def test_service_applies_a_met_plan_and_the_statement_agrees(hills):
    cam, ptz = dataclasses.asdict(WIDE), dataclasses.asdict(PTZ)
    r = ok("perimeter.plan_assisted", terrain_id=hills, camera=cam, ptz_camera=ptz, fence=SQUARE, target_ppm=25,
           mast_m=8, goal_frac=0.8, level="recognize", max_s=10)
    assert r["met"] and r["plan"] is not None and r["level"] == "recognize"
    assert r["plan"]["pole_count"] == r["pole_count"] and r["plan"]["ptz_count"] == r["ptz_count"] >= 1
    assert r["detect_share"] >= 0.8 and r["assisted_share"] >= 0.8 and r["target_ppm"] == 125.0
    assert r["fixed"]["best"]["fixed_poles"] <= r["pole_count"] and r["trials"]
    entry = session.SESSION.get(hills)
    plan, _cam, kw, _fence = entry.results["plan"]
    assert len(plan.ptz_cameras) == r["ptz_count"] and kw["ptz_every_n"] == r["ptz_every_n"]
    # the plan statement, fed by the same coverage + mixed runs, passes the assisted section at the same max_s
    ok("perimeter.coverage", terrain_id=hills, max_px=300)
    ok("perimeter.mixed", terrain_id=hills, cue_target_ppm=r["target_ppm"], max_px=300)
    s = ok("export.plan_statement", terrain_id=hills, goal_frac=0.8, assist_max_s=10)
    a = s["assist"]
    assert a["verdict"] == "Uygun" and a["max_s"] == 10.0 and a["target_ppm"] == r["target_ppm"]
    row = a["zones"][0]
    assert row["detect"] == pytest.approx(r["detect_share"], abs=1e-9)
    assert row["assisted"] == pytest.approx(r["assisted_share"], abs=1e-9)


def test_service_leaves_the_plan_alone_when_not_met_or_invalid(hills):
    cam, ptz = dataclasses.asdict(WIDE), dataclasses.asdict(PTZ)
    ok("perimeter.plan", terrain_id=hills, camera=cam, fence=SQUARE, target_ppm=25, mast_m=8)
    before = session.SESSION.get(hills).results["plan"]
    r = ok("perimeter.plan_assisted", terrain_id=hills, camera=cam, ptz_camera=ptz, fence=SQUARE, target_ppm=25,
           mast_m=8, goal_frac=0.8, level="recognize", max_s=1)
    assert r["met"] is False and r["plan"] is None and any(n.startswith("(b)") for n in r["notes"])
    assert session.SESSION.get(hills).results["plan"] is before
    for bad, code in ((dict(level="detect"), APP_ERROR), (dict(level="identify", ptz_camera=dict(ptz, sensor_name="LWIR 640x512 (12µm)", resolution_name="LWIR (640x512)", focal_min_mm=25.0, focal_max_mm=225.0)),
                                                          APP_ERROR),
                      (dict(level="nonsense"), INVALID_PARAMS), (dict(level="recognize", ptz_camera=None), INVALID_PARAMS)):
        args = dict(terrain_id=hills, camera=cam, ptz_camera=ptz, fence=SQUARE, target_ppm=25, mast_m=8)
        args.update(bad)
        if args["ptz_camera"] is None:
            del args["ptz_camera"]
        e = call("perimeter.plan_assisted", **args)["error"]
        assert e["code"] == code, (bad, e)
        assert session.SESSION.get(hills).results["plan"] is before


# ── the default 40 px/m fence target ────────────────────────────────────────
def test_mixed_detection_with_the_reach_level_is_the_coverage_detection_at_a_40ppm_target():
    terr = _wide_flat()
    fence = [(300, 300), (700, 300), (700, 700), (300, 700)]
    plan = generate_perimeter_plan(terr, fence, WIDE, target_ppm=40.0, mast_height_m=8.0, is_closed_loop=True,
                                   ptz_camera=PTZ, ptz_every_n=3)
    roi = fence_roi(terr, fence, True, 40.0, None, None)
    mv, _ = band_area(terr, plan, WIDE, roi, ZONE_DETECT)
    want = (mv.dori_grid >= ZONE_DETECT) & roi
    capped = evaluate_mixed_perimeter(terr, plan, WIDE, PTZ)                       # default: the plan's own 40 px/m range
    full = evaluate_mixed_perimeter(terr, plan, WIDE, PTZ, fixed_reach_level=ZONE_DETECT)
    assert np.array_equal((full.static.dori_grid >= ZONE_DETECT) & roi, want)
    assert ((capped.static.dori_grid >= ZONE_DETECT) & roi).sum() < want.sum()     # the old definition lost cells
    assert full.detected_area_m2 > capped.detected_area_m2


def test_a_reachable_target_is_met_on_the_default_40ppm_plan():
    terr = _flat()
    res = minimize_poles_assisted(terr, LINE, FIX, PTZ, level=ZONE_RECOG, goal_frac=0.2, max_s=10.0,
                                  plan_kwargs=dict(KW, target_ppm=40.0), n_focals=4)
    assert res.met, res.notes
    det, ok_share = _shares(terr, res, LINE, FIX, PTZ, False)
    assert res.assisted_share == pytest.approx(ok_share, abs=1e-12) and ok_share >= 0.2 and det >= 0.2
