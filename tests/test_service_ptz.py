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


def test_coverage_counts_ptz_as_intermittent_and_never_changes_the_fixed_share(hills):
    """PTZ patrols enter coverage as their own share; the continuous (fixed) numbers stay the engine's."""
    fence = [[250, 250], [750, 250], [750, 750], [250, 750]]
    ok("perimeter.plan", terrain_id=hills, camera=dataclasses.asdict(FIX), fence=fence, target_ppm=40, mast_m=6)
    plain = ok("perimeter.coverage", terrain_id=hills, max_px=300, layer="target")
    assert plain["ptz_count"] == 0 and all(v == 0 for v in plain["band_share_ptz"].values())
    assert len(plain["legend"]) == 3                    # no PTZ row without PTZs
    p = ok("perimeter.plan", terrain_id=hills, camera=dataclasses.asdict(FIX), fence=fence, target_ppm=40, mast_m=6,
           ptz_camera=dataclasses.asdict(PTZ), ptz_every_n=6, ptz_patrol_span_deg=360)
    mixed = ok("perimeter.coverage", terrain_id=hills, max_px=300, layer="target")
    assert mixed["ptz_count"] == len(p["ptz"]) > 0
    assert mixed["band_share"] == plain["band_share"]   # fixed = continuous, unchanged by the PTZs
    assert mixed["band_share_ptz"]["detect"] > 0 and mixed["target"]["ptz_only"] > 0
    assert len(mixed["legend"]) == 4 and mixed["ptz_worst_revisit_s"] > 0
    shares = mixed["target"]
    assert sum(shares[k] for k in ("held", "ptz_only", "below", "unseen")) == pytest.approx(1.0)
    assert shares["held"] == pytest.approx(plain["target"]["held"])
    assert mixed["fence_line"]["with_ptz"] >= mixed["fence_line"]["held"]


def test_area_plan_takes_ptz_and_closes_holes(hills):
    poly = [[350, 350], [650, 350], [650, 600], [350, 600]]
    base = ok("area.plan", terrain_id=hills, camera=dataclasses.asdict(FIX), fence=poly, level="observe",
              goal_frac=0.85, mast_m=8)
    closed = ok("area.plan", terrain_id=hills, camera=dataclasses.asdict(FIX), fence=poly, level="observe",
                goal_frac=0.85, mast_m=8, max_hole_m2=400, ptz_camera=dataclasses.asdict(PTZ), ptz_every_n=3)
    assert base["holes_count"] >= 1 and base["hole_cameras"] == 0
    assert closed["largest_hole_m2"] <= max(400.0, base["largest_hole_m2"])
    assert closed["engine_frac"] >= base["engine_frac"] - 1e-9
    assert closed["plan"]["ptz_count"] >= 1 and len(closed["plan"]["ptz"]) == closed["plan"]["ptz_count"]
    cov = ok("perimeter.coverage", terrain_id=hills, max_px=300, layer="target")
    assert cov["ptz_count"] == closed["plan"]["ptz_count"] and cov["fence_line"] is None
    assert cov["target"]["held"] == pytest.approx(closed["engine_frac"])


def test_a_ptz_as_the_main_camera_is_flagged(hills):
    fence = [[250, 250], [750, 250], [750, 750]]
    cam = dataclasses.asdict(dataclasses.replace(PTZ, camera_type="PTZ"))
    p = ok("perimeter.plan", terrain_id=hills, camera=cam, fence=fence, closed=False, target_ppm=40, mast_m=6)
    assert any("PTZ" in w for w in p["warnings"])
    q = ok("perimeter.plan", terrain_id=hills, camera=dataclasses.asdict(FIX), fence=fence, closed=False)
    assert q["warnings"] == []


def test_playback_gives_every_plan_ptz_its_patrol_and_the_engine_layers(hills):
    """Live sweep: one entry per planned PTZ, at the plan's focal / tilt, the
    same visibility as the Tk playback (SweepPlayback), pictures over the mount window."""
    from cctv_simulator.perimeter_ptz import patrol_for
    from cctv_simulator.ptz_animation import SweepPlayback, sweep_period_s

    fence = [[250, 250], [750, 250], [750, 750], [250, 750]]
    err = call("ptz.playback", terrain_id=hills)
    assert "PTZ" in err["error"]["message"]
    p = ok("perimeter.plan", terrain_id=hills, camera=dataclasses.asdict(FIX), fence=fence, target_ppm=40, mast_m=6,
           ptz_camera=dataclasses.asdict(PTZ), ptz_every_n=10, ptz_patrol_span_deg=360)
    r = ok("ptz.playback", terrain_id=hills, speed_deg_s=30, max_px=300)
    assert len(r["ptz"]) == len(p["ptz"]) > 0
    first, row = r["ptz"][0], p["ptz"][0]
    assert (first["x_m"], first["y_m"], first["focal_mm"], first["tilt_deg"]) == (row["x_m"], row["y_m"], row["focal_mm"], row["tilt_deg"])
    assert first["mode"] == "rotation" and first["period_s"] == pytest.approx(12.0)
    t = session.SESSION.get(hills).bare
    plan = session.SESSION.get(hills).results["plan"][0]
    sw = patrol_for(plan.ptz_cameras[0], 30.0, 2.0)
    want = SweepPlayback(t, row["x_m"], row["y_m"], row["mast_height_m"], PTZ, sw, colors={}, shadow_rgba=(0, 0, 0, 0),
                         max_range_m=1000.0)
    assert sweep_period_s(sw) == first["period_s"]
    assert first["hfov_deg"] == pytest.approx(want.hfov_deg)
    x0, y0, x1, y1 = first["box_m"]
    assert x0 <= row["x_m"] <= x1 and y0 <= row["y_m"] <= y1
    assert first["lit"]["data"].startswith("data:image/png") and first["shadow"]["data"].startswith("data:image/png")
    one = ok("ptz.playback", terrain_id=hills, source="single", camera=dataclasses.asdict(PTZ), x_m=500, y_m=500,
             mast_m=10, mode="sector", start_pan_deg=0, end_pan_deg=90, speed_deg_s=30, end_dwell_s=2, tilt_deg=-4,
             focal_mm=10, range_m=500, max_px=300)
    assert len(one["ptz"]) == 1 and one["ptz"][0]["span_deg"] == pytest.approx(90) and one["ptz"][0]["period_s"] == pytest.approx(10.0)
