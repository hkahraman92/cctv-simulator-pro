"""Core service PTZ API: sweep, cue response and mixed perimeter match the engines."""
from __future__ import annotations

import dataclasses
import io
import json

import pytest

from cctv_simulator.models import CameraConfig
from cctv_simulator.ptz_fusion import cue_response
from cctv_simulator.ptz_schedule import Rotation360, evaluate_sweep
from cctv_simulator.service import session
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import Server

PTZ = CameraConfig(name="PTZ", focal_min_mm=4.8, focal_max_mm=153.0, resolution_name="2 MP (1080p - 1920x1080)")
FIX = CameraConfig(name="Sabit", focal_min_mm=2.8, focal_max_mm=12.0)


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


def test_sweep_matches_the_engine(hills):
    r = ok("ptz.sweep", terrain_id=hills, camera=dataclasses.asdict(PTZ), x_m=500, y_m=500, mast_m=10,
           mode="rotation", speed_deg_s=30, tilt_deg=-4, focal_mm=10.0, range_m=500, max_px=400)
    t = session.SESSION.get(hills).bare
    want = evaluate_sweep(t, 500, 500, 10, PTZ, Rotation360(speed_deg_s=30, tilt_deg=-4, focal_mm=10.0), max_range_m=500)
    assert r["period_s"] == want.period_s and r["seen_area_m2"] == want.seen_area_m2
    assert r["worst_revisit_s"] == want.worst_revisit_s
    assert r["overlay"]["data"].startswith("data:image/png")


def test_cue_matches_the_engine_and_explains_unreachable_cells(hills):
    r = ok("ptz.cue", terrain_id=hills, camera=dataclasses.asdict(PTZ), x_m=500, y_m=500, mast_m=10,
           home_pan_deg=0, home_tilt_deg=-4, target_ppm=250, range_m=500, max_px=400)
    t = session.SESSION.get(hills).bare
    want = cue_response(t, 500, 500, 10, PTZ, home_pan_deg=0, home_tilt_deg=-4, target_ppm=250, max_range_m=500)
    assert r["reachable_area_m2"] == want.reachable_area_m2 and r["mean_response_s"] == want.mean_response_s
    assert r["unreachable"] and all(u["area_m2"] > 0 for u in r["unreachable"])


def test_tour_matches_the_engine(hills):
    from cctv_simulator.ptz_tour import PTZPreset, PTZTour, evaluate_ptz_tour

    presets = [{"name": "Kapı", "pan_deg": 0, "tilt_deg": -4, "focal_mm": 20.0, "dwell_s": 4},
               {"name": "Otopark", "pan_deg": 120, "tilt_deg": -6, "dwell_s": 6},
               {"name": "Arka", "pan_deg": 240, "tilt_deg": -5, "focal_mm": 60.0, "dwell_s": 3}]
    r = ok("ptz.tour", terrain_id=hills, camera=dataclasses.asdict(PTZ), x_m=500, y_m=500, mast_m=10,
           presets=presets, range_m=500, max_px=400)
    t = session.SESSION.get(hills).bare
    tour = PTZTour(x_m=500, y_m=500, mast_height_m=10, camera=PTZ, max_range_m=500, presets=[
        PTZPreset("Kapı", 0, -4, focal_mm=20.0, dwell_s=4), PTZPreset("Otopark", 120, -6, dwell_s=6),
        PTZPreset("Arka", 240, -5, focal_mm=60.0, dwell_s=3)])
    want = evaluate_ptz_tour(t, tour)
    assert r["tour_period_s"] == want.tour_period_s and r["worst_revisit_s"] == want.worst_revisit_s
    assert r["preset_labels"] == want.preset_labels
    assert call("ptz.tour", terrain_id=hills, camera=dataclasses.asdict(PTZ), x_m=1, y_m=1,
                presets=presets[:1])["error"]["code"] == -32602


def test_mixed_perimeter_needs_ptz_then_reports_continuous_and_intermittent(hills):
    fence = [[250, 250], [750, 250], [750, 750], [250, 750]]
    ok("perimeter.plan", terrain_id=hills, camera=dataclasses.asdict(FIX), fence=fence, target_ppm=40, mast_m=6)
    err = call("perimeter.mixed", terrain_id=hills)
    assert "PTZ yok" in err["error"]["message"]
    p = ok("perimeter.plan", terrain_id=hills, camera=dataclasses.asdict(FIX), fence=fence, target_ppm=40, mast_m=6,
           ptz_camera=dataclasses.asdict(PTZ), ptz_every_n=10, ptz_patrol_span_deg=360)
    assert p["ptz"]
    r = ok("perimeter.mixed", terrain_id=hills, max_px=400)
    assert r["ptz_count"] == len(p["ptz"]) and r["fixed_poles"] == p["pole_count"]
    assert r["continuous_detect_m2"] > 0 and r["intermittent_detect_m2"] >= 0
    assert 0 <= r["identifiable_pct"] <= 100
