"""terrain.refine: the analysis grid of a loaded terrain, refined in place (same id, same extent)."""
from __future__ import annotations

import dataclasses
import io
import json
import math

import numpy as np
import pytest

from cctv_simulator import surface_obstacles as obs
from cctv_simulator.models import CameraConfig
from cctv_simulator.service import session
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import Server
from cctv_simulator.terrain_loader import TerrainData, refine_terrain
from cctv_simulator.viewshed_3d import ZONE_DETECT, ZONE_OBSERVE

CAMC = CameraConfig(sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                    focal_min_mm=2.8, focal_max_mm=12.0)
CAM = dataclasses.asdict(CAMC)


def call(method, **params):
    out = io.StringIO()
    server = Server(METHODS, out=out)
    server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
    server.wait_idle()
    msgs = [json.loads(x) for x in out.getvalue().splitlines()]
    return next(m for m in msgs if m.get("id") == 1)


def ok(method, **params):
    reply = call(method, **params)
    assert "result" in reply, reply.get("error")
    return reply["result"]


@pytest.fixture(autouse=True)
def fresh_session():
    session.reset()
    yield


def _hills(grid=60, width=600.0):
    return ok("terrain.procedural", preset="ridge_and_valley", grid=grid, width_m=width)


def test_refine_is_the_engines_refine_terrain_under_the_same_id():
    info = _hills()
    tid = info["terrain_id"]
    entry = session.SESSION.get(tid)
    orig = entry.bare
    r = ok("terrain.refine", terrain_id=tid, factor=4)
    assert r["terrain_id"] == tid and r["changed"] is True
    assert (r["refine_factor"], r["base_cell_m"]) == (4, 10.0)
    assert r["cell_size_m"] == pytest.approx(2.5) and r["rows"] == 240 and r["cols"] == 240
    assert r["width_m"] == info["width_m"] and r["height_m"] == info["height_m"]
    want = refine_terrain(orig, 4)
    got = session.SESSION.get(tid).bare
    assert got is not orig and np.array_equal(got.z_grid, want.z_grid)
    assert np.array_equal(got.z_grid[::4, ::4], orig.z_grid)
    # the same factor again is a no-op that keeps the (computed) results
    entry.results["multi"] = "x"
    assert ok("terrain.refine", terrain_id=tid, factor=4)["changed"] is False
    assert entry.results["multi"] == "x"
    # reported by the terrain summary of the other loaders too
    assert ok("terrain.procedural", preset="canyon", grid=50)["refine_factor"] == 1


def test_target_cell_picks_the_smallest_factor_that_reaches_it():
    tid = _hills()["terrain_id"]
    assert ok("terrain.refine", terrain_id=tid, target_cell_m=3.4)["refine_factor"] == 3
    assert ok("terrain.refine", terrain_id=tid, target_cell_m=10.0)["refine_factor"] == 1
    bad = call("terrain.refine", terrain_id=tid, target_cell_m=0.1)
    assert "error" in bad and "16" in bad["error"]["message"]
    assert "error" in call("terrain.refine", terrain_id=tid)
    assert "error" in call("terrain.refine", terrain_id=tid, factor=17)
    assert "error" in call("terrain.refine", terrain_id=tid, factor=2.5)


def test_plan_is_kept_derived_results_and_pictures_are_dropped_and_coverage_reruns():
    tid = _hills()["terrain_id"]
    fence = [[200, 200], [400, 200], [400, 400], [200, 400]]
    ok("perimeter.plan", terrain_id=tid, camera=CAM, fence=fence, target_ppm=40, mast_m=6)
    entry = session.SESSION.get(tid)
    plan = entry.results["plan"]
    before = ok("perimeter.coverage", terrain_id=tid, max_px=300)
    img0 = ok("terrain.image", terrain_id=tid, max_px=300)
    assert {"multi", "band_share", "_image"} <= set(entry.results)
    ok("terrain.refine", terrain_id=tid, factor=3)
    assert set(entry.results) == {"plan"} and entry.results["plan"] is plan
    img1 = ok("terrain.image", terrain_id=tid, max_px=300)
    assert img1["data"] != img0["data"] or entry.results["_image"][0][1] == id(entry.bare)   # recomputed, not served
    after = ok("perimeter.coverage", terrain_id=tid, max_px=300)
    assert after["camera_count"] == before["camera_count"]
    assert entry.results["multi"].dori_grid.shape == entry.bare.z_grid.shape
    # the plan did not move: same poles in metres
    assert entry.results["plan"][0].placed_cameras[0].x_m == plan[0].placed_cameras[0].x_m


def test_factor_one_restores_the_original_and_a_new_factor_starts_from_it():
    tid = _hills()["terrain_id"]
    entry = session.SESSION.get(tid)
    orig, keep = entry.bare, entry.bare.z_grid.copy()
    ok("terrain.refine", terrain_id=tid, factor=4)
    r = ok("terrain.refine", terrain_id=tid, factor=2)
    assert r["cell_size_m"] == pytest.approx(5.0) and r["base_cell_m"] == 10.0
    assert np.array_equal(entry.bare.z_grid, refine_terrain(orig, 2).z_grid)      # not 2 x 4
    r = ok("terrain.refine", terrain_id=tid, factor=1)
    assert r["refine_factor"] == 1 and r["cell_size_m"] == 10.0
    assert entry.bare is orig and np.array_equal(entry.bare.z_grid, keep)


def test_node_limit_is_refused_in_turkish_with_the_finest_allowed_cell():
    tid = _hills(grid=400, width=4000.0)["terrain_id"]          # 160 000 nodes -> at most 3x
    reply = call("terrain.refine", terrain_id=tid, factor=4)
    msg = reply["error"]["message"]
    assert "1.600.000" in msg and "2.560.000" in msg
    assert "en ince hücre 3,33 m" in msg and "3×" in msg
    assert reply["error"]["data"]["max_factor"] == 3
    assert session.SESSION.get(tid).refine_factor == 1            # nothing changed
    assert ok("terrain.refine", terrain_id=tid, factor=3)["rows"] == 1200


def test_a_saved_region_always_holds_the_original_dem(monkeypatch, tmp_path):
    from cctv_simulator import region_library as rl

    monkeypatch.setattr(rl, "regions_dir", lambda base=None: tmp_path / "regions")
    tid = _hills()["terrain_id"]
    orig = session.SESSION.get(tid).bare.z_grid.copy()
    ok("terrain.refine", terrain_id=tid, factor=4)
    saved = ok("regions.save", terrain_id=tid, name="Inceltilmis")
    t, _img, info = rl.load_region(saved["region_id"])
    assert np.array_equal(t.z_grid, orig) and t.cell_size_m == 10.0 and info.cell_size_m == 10.0


def _wall_feature(terrain, x0, y0, x1, y1, h):
    s, w, _, _ = obs.terrain_bbox(terrain)
    m_lat = 111320.0
    m_lon = 111320.0 * math.cos(math.radians(terrain.lat_center))
    ring = [(s + y / m_lat, w + x / m_lon) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    return obs.Feature(obs.KIND_BUILDING, h, "tagged", [ring])


def test_obstacles_are_re_rasterised_on_the_fine_grid_and_a_wall_still_shadows(monkeypatch):
    t = TerrainData(np.full((60, 60), 100.0, np.float32), cell_size_m=10.0, lat_center=39.92, lon_center=32.85)
    entry = session.SESSION.add(dict(bare=t, label="duz"))
    # an off-grid wall 12 m thick, 40 m high
    wall = _wall_feature(t, 150.0, 303.7, 450.0, 315.7, 40.0)
    monkeypatch.setattr(obs, "fetch_osm_obstacles", lambda bbox: [wall])
    ok("surface.osm", terrain_id=entry.id)
    coarse = entry.surface
    ok("terrain.refine", terrain_id=entry.id, factor=4)
    fine = entry.surface
    assert fine.z_grid.shape == (240, 240) and entry.base_surface is coarse and entry.base_bare is t
    truth = 300.0 * 12.0
    a_coarse = float((coarse.obstacle_grid > 0).sum()) * 100.0
    a_fine = float((fine.obstacle_grid > 0).sum()) * 6.25
    assert abs(a_fine - truth) < abs(a_coarse - truth)
    # sharper than nearest-node upsampling of the coarse footprint
    assert not np.array_equal(fine.obstacle_grid, refine_terrain(coarse, 4).obstacle_grid)
    assert np.array_equal(fine.z_grid, fine.ground_z_grid + fine.obstacle_grid)
    args = dict(terrain_id=entry.id, camera=CAM, x_m=300, y_m=275, mast_m=6, pan_deg=0, tilt_deg=-4,
                range_m=300, lens_mode="min")
    bare = ok("viewshed.single", use_obstacles=False, **args)
    walled = ok("viewshed.single", use_obstacles=True, **args)
    assert walled["visible_area_m2"] < 0.9 * bare["visible_area_m2"], (walled["visible_area_m2"], bare["visible_area_m2"])
    # back to 1: the original surface object; a region would write the unrefined layer
    ok("terrain.refine", terrain_id=entry.id, factor=1)
    assert entry.surface is coarse and entry.bare is t


def test_surface_loaded_after_refining_lands_on_both_grids(monkeypatch):
    t = TerrainData(np.full((60, 60), 100.0, np.float32), cell_size_m=10.0, lat_center=39.92, lon_center=32.85)
    entry = session.SESSION.add(dict(bare=t, label="duz"))
    ok("terrain.refine", terrain_id=entry.id, factor=2)
    monkeypatch.setattr(obs, "fetch_osm_obstacles", lambda bbox: [_wall_feature(t, 150.0, 303.7, 450.0, 315.7, 40.0)])
    ok("surface.osm", terrain_id=entry.id)
    assert entry.surface.z_grid.shape == (120, 120) and entry.base_surface.z_grid.shape == (60, 60)


def test_a_surface_without_sources_is_upsampled_nearest(monkeypatch):
    t = TerrainData(np.full((40, 40), 50.0, np.float32), cell_size_m=10.0)
    hg = np.zeros((40, 40), np.float32)
    hg[10:14, 10:30] = 20.0
    surf = obs.with_obstacles(t, obs.ObstacleLayer(height_grid=hg, kind_grid=(hg > 0).astype(np.uint8), counts={}))
    entry = session.SESSION.add(dict(bare=obs.bare_earth(surf), surface=surf, label="bolge"))
    ok("terrain.refine", terrain_id=entry.id, factor=2)
    assert np.array_equal(entry.surface.obstacle_grid, refine_terrain(surf, 2).obstacle_grid)


def test_coverage_shares_move_toward_the_fine_reference_on_flat_terrain():
    from cctv_simulator import map_render
    from cctv_simulator.focal_optimizer import fence_roi, full_reach_placements

    t = TerrainData(np.zeros((100, 100), np.float32), cell_size_m=10.0)
    entry = session.SESSION.add(dict(bare=t, label="duz"))
    tid = entry.id
    fence = [[303.7, 301.3], [703.7, 301.3], [703.7, 701.3], [303.7, 701.3]]
    ok("perimeter.plan", terrain_id=tid, camera=CAM, fence=fence, target_ppm=40, mast_m=8)
    cov1 = ok("perimeter.coverage", terrain_id=tid, max_px=300)
    plan, cam, _kw, fp = entry.results["plan"]
    mv1 = entry.results["multi"]
    # reference: the analytic 0.5 m picture of the very same cameras, inside the same band
    size = int(t.width_m / 0.5)
    pls = full_reach_placements(plan, cam, ZONE_DETECT)
    cl = map_render.multi_view_classes(t, mv1, pls, (size, size), pixel_budget=10 ** 9)
    fine = TerrainData(np.zeros((size, size), np.float32), cell_size_m=0.5)
    roi = np.flipud(fence_roi(fine, fp, True, 40.0))
    ref = {n: float(((cl.zone >= z) & roi).sum()) / float(roi.sum())
           for n, z in (("observe", ZONE_OBSERVE), ("detect", ZONE_DETECT))}
    ok("terrain.refine", terrain_id=tid, factor=4)
    cov4 = ok("perimeter.coverage", terrain_id=tid, max_px=300)
    e1 = abs(cov1["band_share"]["observe"] - ref["observe"])
    e4 = abs(cov4["band_share"]["observe"] - ref["observe"])
    print(f"observe share: ref {ref['observe']:.4f}  x1 {cov1['band_share']['observe']:.4f} (err {e1:.4f})  "
          f"x4 {cov4['band_share']['observe']:.4f} (err {e4:.4f})")
    assert e4 < e1


def test_grid_suggest_is_machine_readable_and_the_text_carries_real_numbers():
    tid = _hills(grid=100, width=1000.0)["terrain_id"]
    ok("perimeter.plan", terrain_id=tid, camera=CAM, fence=[[300, 300], [600, 300], [600, 600], [300, 600]],
       target_ppm=40, mast_m=8)
    cov = ok("perimeter.coverage", terrain_id=tid, max_px=300)
    sug = cov["grid_suggest"]
    assert cov["grid_note"] and "Analiz hücresi 10 m" in cov["grid_note"] and "Gözlem" in cov["grid_note"]
    assert sug["factor"] in (2, 3, 4, 5, 6, 8, 10, 12, 16) and sug["cell_m"] == pytest.approx(10.0 / sug["factor"])
    assert f"{sug['factor']}×" in cov["grid_note"]
    # applying the suggestion silences the note
    ok("terrain.refine", terrain_id=tid, factor=sug["factor"])
    again = ok("perimeter.coverage", terrain_id=tid, max_px=300)
    assert again["grid_note"] is None and again["grid_suggest"] is None


def test_grid_suggest_names_the_finest_possible_when_the_node_limit_bites():
    tid = _hills(grid=400, width=4000.0)["terrain_id"]          # at most 3x of 10 m
    ok("perimeter.plan", terrain_id=tid, camera=CAM, fence=[[1500, 1500], [2000, 1500], [2000, 2000]],
       target_ppm=40, mast_m=8, closed=False)
    cov = ok("perimeter.coverage", terrain_id=tid, max_px=300)
    assert cov["grid_suggest"] == {"factor": 3, "cell_m": pytest.approx(10.0 / 3)}
    assert "en ince" in cov["grid_note"]
