"""Core service terrain API: engine parity, pictures, long jobs, regions, surface model."""
from __future__ import annotations

import base64
import dataclasses
import io
import json
import threading

import numpy as np
import pytest
from PIL import Image

from cctv_simulator import surface_obstacles as obs
from cctv_simulator.models import CameraConfig
from cctv_simulator.perimeter_planner import generate_perimeter_plan
from cctv_simulator.service import session
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import INVALID_PARAMS, REQUEST_CANCELLED, Server
from cctv_simulator.viewshed_3d import calculate_3d_viewshed

CAM = dataclasses.asdict(CameraConfig(focal_min_mm=2.8, focal_max_mm=12.0))


def call(method, **params):
    out = io.StringIO()
    server = Server(METHODS, out=out)
    server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
    server.wait_idle()
    msgs = [json.loads(x) for x in out.getvalue().splitlines()]
    reply = next(m for m in msgs if m.get("id") == 1)
    return reply, [m for m in msgs if m.get("method") == "$/progress"]


def ok(method, **params):
    reply, _ = call(method, **params)
    assert "result" in reply, reply.get("error")
    return reply["result"]


@pytest.fixture(autouse=True)
def fresh_session():
    session.reset()
    yield


@pytest.fixture
def hills():
    return ok("terrain.procedural", preset="ridge_and_valley", grid=120, width_m=1200.0)


def _decode(img):
    head, data = img["data"].split(",", 1)
    return Image.open(io.BytesIO(base64.b64decode(data))), head


def test_procedural_terrain_is_never_called_measured(hills):
    assert hills["is_measured"] is False
    assert hills["width_m"] == pytest.approx(1200.0) and hills["rows"] == 120
    base = ok("terrain.image", terrain_id=hills["terrain_id"], mode="hybrid", max_px=600)
    im, head = _decode(base)
    assert head.startswith("data:image/jpeg") and max(im.size) == 600
    assert base["procedural_texture"] is True


def test_single_viewshed_matches_the_engine(hills):
    tid = hills["terrain_id"]
    r = ok("viewshed.single", terrain_id=tid, camera=CAM, x_m=600, y_m=600, mast_m=8, pan_deg=45,
           tilt_deg=-4, range_m=500, lens_mode="max", max_px=500)
    t = session.SESSION.get(tid).bare
    want = calculate_3d_viewshed(t, 600, 600, 8, CameraConfig(**CAM), lens_mode="max", pan_deg=45,
                                 tilt_deg=-4, max_range_m=500)
    assert r["visible_area_m2"] == want.visible_area_m2
    assert r["coverage_pct"] == want.coverage_pct
    assert sum(r["zone_areas_m2"][k] for k in ("identify", "recognize", "observe", "detect")) == pytest.approx(
        want.visible_area_m2)
    im, head = _decode(r["overlay"])
    assert head.startswith("data:image/png") and im.mode == "RGBA"
    assert np.asarray(im)[..., 3].any()            # the fan is drawn
    assert any("ölçüm değil" in w for w in r["warnings"])
    assert len(r["profile"]["dist_m"]) == len(r["profile"]["visible"]) > 10


def test_bad_parameters_are_named(hills):
    reply, _ = call("viewshed.single", terrain_id=hills["terrain_id"], camera=CAM, x_m="a", y_m=1)
    assert reply["error"]["code"] == INVALID_PARAMS and "x_m" in reply["error"]["message"]
    reply, _ = call("viewshed.single", terrain_id="t999", camera=CAM, x_m=1, y_m=1)
    assert "oturumda yok" in reply["error"]["message"]


def test_perimeter_plan_matches_the_planner_and_coverage_runs(hills):
    tid = hills["terrain_id"]
    fence = [[300, 300], [900, 300], [900, 900], [300, 900]]
    p = ok("perimeter.plan", terrain_id=tid, camera=CAM, fence=fence, target_ppm=40, mast_m=6)
    t = session.SESSION.get(tid).bare
    want = generate_perimeter_plan(t, [tuple(x) for x in fence], CameraConfig(**CAM), target_ppm=40,
                                   mast_height_m=6.0)
    assert p["pole_count"] == want.pole_count and len(p["poles"]) == len(want.placed_cameras)
    assert p["coverage_percentage"] == want.coverage_percentage
    cov, prog = call("perimeter.coverage", terrain_id=tid, max_px=500)
    c = cov["result"]
    assert c["camera_count"] == len(want.placed_cameras)
    shares = c["band_share"]
    assert 0 < shares["detect"] <= 1 and shares["identify"] <= shares["recognize"] <= shares["detect"]
    assert prog, "coverage reports progress"


def test_spacing_override_changes_pole_count(hills):
    tid = hills["terrain_id"]
    fence = [[200, 600], [1000, 600]]
    dense = ok("perimeter.plan", terrain_id=tid, camera=CAM, fence=fence, closed=False, spacing_m=20)
    sparse = ok("perimeter.plan", terrain_id=tid, camera=CAM, fence=fence, closed=False, spacing_m=80)
    assert dense["pole_count"] > sparse["pole_count"]


def test_pole_search_plan_is_the_one_that_met_the_goal(hills):
    """The returned plan carries the optimised per-pole lenses: the engine's
    band share for it holds the goal, and its fence-line gaps were re-analysed
    (they used to describe the planner's own lenses)."""
    tid = hills["terrain_id"]
    fence = [[300, 300], [900, 300], [900, 900], [300, 900]]
    r = ok("perimeter.minimize_poles", terrain_id=tid, camera=CAM, fence=fence, target_ppm=40, mast_m=6,
           goal_frac=0.6, level="detect")
    assert r["met"] and r["plan"] is not None
    assert r["plan"]["pole_count"] == r["best"]["fixed_poles"] <= r["base"]["fixed_poles"]
    raw = ok("perimeter.plan", terrain_id=tid, camera=CAM, fence=fence, target_ppm=40, mast_m=6,
             spacing_m=r["best"]["spacing_m"])
    assert [p["focal_mm"] for p in r["plan"]["poles"]] != [p["focal_mm"] for p in raw["poles"]]
    # re-run the search so its plan is the session's current plan, then score it
    ok("perimeter.minimize_poles", terrain_id=tid, camera=CAM, fence=fence, target_ppm=40, mast_m=6,
       goal_frac=0.6, level="detect")
    cov = ok("perimeter.coverage", terrain_id=tid, max_px=400)
    assert cov["band_share"]["detect"] >= 0.6 - 0.02


def test_optimize_in_place_never_loses_band_area_and_can_move_poles(hills):
    tid = hills["terrain_id"]
    fence = [[300, 300], [900, 300], [900, 900], [300, 900]]
    p = ok("perimeter.plan", terrain_id=tid, camera=CAM, fence=fence, target_ppm=40, mast_m=6, spacing_m=60)
    r = ok("perimeter.optimize", terrain_id=tid, level="detect", position_steps=1, n_focals=4)
    assert r["after_m2"] >= r["before_m2"] - 1e-6
    assert r["plan"]["pole_count"] == p["pole_count"]
    moved = [(a["x_m"], a["y_m"]) != (b["x_m"], b["y_m"]) for a, b in zip(p["poles"], r["plan"]["poles"])]
    assert sum(moved) == r["moved"]


def test_focal_sweep_curve_is_wide_to_tele(hills):
    r = ok("focal.sweep", terrain_id=hills["terrain_id"], camera=CAM, x_m=600, y_m=600, mast_m=8,
           level="detect", n_focals=5)
    f = [c["focal_mm"] for c in r["curve"]]
    assert f == sorted(f) and f[0] == pytest.approx(2.8) and f[-1] == pytest.approx(12.0)
    assert r["best"]["score"] >= max(r["wide"]["score"], r["tele"]["score"])


def test_download_reports_progress_and_can_be_cancelled(monkeypatch):
    from cctv_simulator import online_map_loader as oml
    from cctv_simulator.terrain_loader import TerrainData

    gate = threading.Event()

    def fake_mosaic(**kw):
        for i in range(50):
            kw["progress_callback"](i, 50)
            gate.wait(0.02)
        return Image.new("RGB", (64, 64))

    def fake_dem(**kw):
        kw["progress_callback"](1, 1)
        return TerrainData(np.zeros((40, 40)), cell_size_m=10, is_measured=True, lat_center=kw["center_lat"],
                           lon_center=kw["center_lon"])

    monkeypatch.setattr(oml, "download_satellite_mosaic", fake_mosaic)
    monkeypatch.setattr(oml, "fetch_online_elevation_grid", fake_dem)
    reply, prog = call("terrain.download", lat=39.9, lon=32.8, width_m=400)
    assert reply["result"]["is_measured"] is True and reply["result"]["has_photo"] is True
    assert len(prog) >= 50

    out = io.StringIO()
    server = Server(METHODS, out=out)
    server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 5, "method": "terrain.download",
                                   "params": {"lat": 39.9, "lon": 32.8, "width_m": 400}}))
    server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "$/cancelRequest", "params": {"id": 5}}))
    server.wait_idle()
    reply = next(json.loads(x) for x in out.getvalue().splitlines() if json.loads(x).get("id") == 5)
    assert reply["error"]["code"] == REQUEST_CANCELLED


def test_download_failure_is_reported_in_turkish(monkeypatch):
    from cctv_simulator import online_map_loader as oml

    def boom(**kw):
        raise RuntimeError("karoların %40'ı indirilemedi")

    monkeypatch.setattr(oml, "download_satellite_mosaic", boom)
    reply, _ = call("terrain.download", lat=39.9, lon=32.8, width_m=400)
    assert "Harita indirilemedi" in reply["error"]["message"] and "%40" in reply["error"]["message"]


def test_region_roundtrip_through_the_api(monkeypatch, tmp_path, hills):
    from cctv_simulator import region_library as rl

    monkeypatch.setattr(rl, "regions_dir", lambda base=None: tmp_path / "regions")
    saved = ok("regions.save", terrain_id=hills["terrain_id"], name="Deneme Bölgesi")
    regions = ok("regions.list")["regions"]
    assert [r["region_id"] for r in regions] == [saved["region_id"]] and regions[0]["name"] == "Deneme Bölgesi"
    opened = ok("regions.open", region_id=saved["region_id"])
    a = session.SESSION.get(hills["terrain_id"]).bare.z_grid
    b = session.SESSION.get(opened["terrain_id"]).bare.z_grid
    assert np.array_equal(a, b)
    ok("regions.delete", region_id=saved["region_id"])
    assert ok("regions.list")["regions"] == []


def test_osm_surface_blocks_the_view(monkeypatch):
    from cctv_simulator.terrain_loader import TerrainData

    t = TerrainData(np.zeros((100, 100)), cell_size_m=5, is_measured=True, lat_center=39.92, lon_center=32.85)
    entry = session.SESSION.add(dict(bare=t, label="düz"))
    wall = np.zeros((100, 100)); kind = np.zeros((100, 100), np.uint8)
    wall[46:48, 20:80] = 30.0; kind[46:48, 20:80] = obs.KIND_BUILDING
    layer = obs.ObstacleLayer(height_grid=wall, kind_grid=kind, counts={"building": 1, "height_tag": 1})
    monkeypatch.setattr(obs, "fetch_osm_obstacles", lambda bbox: ["bir bina"])
    monkeypatch.setattr(obs, "rasterize", lambda terrain, feats: layer)
    r = ok("surface.osm", terrain_id=entry.id)
    assert r["has_obstacles"] is True and r["features"] == 1
    args = dict(terrain_id=entry.id, camera=CAM, x_m=250, y_m=200, mast_m=6, pan_deg=0, tilt_deg=-2,
                range_m=300, lens_mode="min")
    bare = ok("viewshed.single", use_obstacles=False, **args)
    walled = ok("viewshed.single", use_obstacles=True, **args)
    assert walled["visible_area_m2"] < bare["visible_area_m2"]
