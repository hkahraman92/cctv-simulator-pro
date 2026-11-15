"""Multi-camera coverage picture: analytic optics per pixel, occlusion from the engine grid.

Display only -- the numbers of ``perimeter.coverage`` stay the grid engine's (last test)."""
from __future__ import annotations

import dataclasses
import io
import json
import math

import numpy as np
import pytest

from cctv_simulator import map_render
from cctv_simulator.models import CameraConfig
from cctv_simulator.service import session
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import Server
from cctv_simulator.terrain_loader import TerrainData
from cctv_simulator.viewshed_3d import (
    ZONE_DETECT, ZONE_OCCLUDED, CameraPlacement, calculate_multi_camera_viewshed, target_height,
)

CAM = CameraConfig(sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)", focal_min_mm=2.8, focal_max_mm=12.0)


def _flat(n, cell):
    return TerrainData(z_grid=np.zeros((n, n), np.float32), cell_size_m=cell)


def _pl(x, y, pan, *, mast=8.0, tilt=-24.0, rng=32.6, cid=None, cam=CAM):
    return CameraPlacement(x_m=x, y_m=y, mast_height_m=mast, camera=cam, pan_deg=pan, tilt_deg=tilt,
                           max_range_m=rng, focal_mm_override=2.8, camera_id=cid)


def test_fine_grid_pixels_agree_with_the_engine_grid():
    t = _flat(120, 1.0)
    pls = [_pl(30.0, 60.0, 90.0), _pl(60.0, 30.0, 0.0)]
    with target_height(1.8):
        mv = calculate_multi_camera_viewshed(t, pls)
        cl = map_render.multi_view_classes(t, mv, pls, (480, 480))
    rows, cols = np.mgrid[0:120, 0:120]
    near = np.zeros((120, 120), bool)
    for p in pls:
        near |= np.hypot(cols - p.x_m, rows - p.y_m) <= 33.0 + 1.0
    # the pixel under each cell node (north-up picture: row 0 = north)
    pi = np.clip(((120 - rows) * 4 - 1), 0, 479)
    pj = np.clip(cols * 4, 0, 479)
    got = cl.zone[pi, pj]
    agree = float((got == mv.dori_grid)[near].mean())
    print(f"pixel vs grid agreement on a 1 m grid: {agree:.4f} over {int(near.sum())} cells")
    assert agree >= 0.99


def test_coarse_grid_wedge_is_filled_to_its_optical_limits():
    """The user's report: 10 m cells, 2.8 mm camera, 33 m plan range -- the grid painted
    about half of the wedge."""
    t = _flat(100, 10.0)
    p = _pl(500.0, 500.0, 0.0)
    mv = calculate_multi_camera_viewshed(t, [p])
    r = mv.per_camera[0]
    size = (800, 800)
    cl = map_render.multi_view_classes(t, mv, [p], size)
    px_area = (t.width_m / size[0]) * (t.height_m / size[1])
    painted = float((cl.zone >= ZONE_DETECT).sum()) * px_area
    dead = 8.0 / math.tan(math.radians(-(p.tilt_deg - r.vfov_deg / 2.0)))           # frame's lower ray meets the ground
    wedge = 0.5 * math.radians(r.hfov_deg) * (r.max_range_m ** 2 - dead ** 2)
    grid = float((mv.dori_grid >= ZONE_DETECT).sum()) * t.cell_size_m ** 2
    print(f"wedge {wedge:.0f} m2  painted {painted:.0f}  grid {grid:.0f}")
    assert painted == pytest.approx(wedge, rel=0.05)
    assert abs(grid - wedge) / wedge > 0.10            # what the cell picture got wrong
    # the dead zone beside the pole is left empty
    ci, cj = 400, 400                                  # 1.25 m pixels: within 5 m of the pole
    assert (cl.zone[ci - 3:ci + 3, cj - 3:cj + 3] == 0).all()


def test_a_wall_still_casts_a_shadow_in_the_picture():
    t = _flat(100, 2.0)
    z = t.z_grid.copy()
    z[30:170 // 2, 45:47] = 15.0                       # wall x = 90..94 m, y = 60..168 m
    t = TerrainData(z_grid=z, cell_size_m=2.0)
    p = _pl(60.0, 100.0, 90.0, mast=4.0, tilt=-3.0, rng=60.0)
    mv = calculate_multi_camera_viewshed(t, [p])
    cl = map_render.multi_view_classes(t, mv, [p], (800, 800))
    px = lambda x, y: (int((200.0 - y) / 200.0 * 800), int(x / 200.0 * 800))      # noqa: E731
    i, j = px(105.0, 100.0)                              # 11 m behind the wall, on the axis
    assert cl.zone[i, j] == ZONE_OCCLUDED and not cl.visible[i, j]
    i, j = px(75.0, 100.0)                               # in front of it
    assert cl.zone[i, j] >= ZONE_DETECT
    behind = cl.zone[px(100.0, 105.0)[0]:px(100.0, 95.0)[0], px(100.0, 100.0)[1]:px(112.0, 100.0)[1]]
    assert (behind < ZONE_DETECT).all()
    # the shadow edge is not a staircase of 2 m cells: columns of the edge differ by < the cell
    edge = [int(np.argmax(cl.zone[r, :] == ZONE_OCCLUDED)) for r in range(px(120, 110)[0], px(120, 90)[0])]
    assert len(set(edge)) < 15


def test_seen_count_counts_a_physical_camera_once():
    t = _flat(100, 5.0)
    a, b = _pl(250.0, 250.0, 0.0, cid="ptz-1"), _pl(250.0, 250.0, 0.0, cid="ptz-1")
    mv = calculate_multi_camera_viewshed(t, [a, b])
    cl = map_render.multi_view_classes(t, mv, [a, b], (500, 500))
    assert mv.seen_count_grid.max() == 1 and cl.seen.max() == 1
    c, d = _pl(250.0, 250.0, 0.0), _pl(250.0, 250.0, 0.0)
    mv2 = calculate_multi_camera_viewshed(t, [c, d])
    cl2 = map_render.multi_view_classes(t, mv2, [c, d], (500, 500))
    assert mv2.seen_count_grid.max() == 2 and cl2.seen.max() == 2
    assert cl2.visible.sum() == cl.visible.sum()


def test_over_budget_works_at_a_reduced_resolution_and_returns_full_size():
    t = _flat(100, 10.0)
    p = _pl(500.0, 500.0, 0.0)
    mv = calculate_multi_camera_viewshed(t, [p])
    cl = map_render.multi_view_classes(t, mv, [p], (800, 800), pixel_budget=2_000)
    assert cl.work_size[0] < 800 and cl.zone.shape == (800, 800)
    full = map_render.multi_view_classes(t, mv, [p], (800, 800))
    assert abs(int((cl.zone >= ZONE_DETECT).sum()) - int((full.zone >= ZONE_DETECT).sum())) < 0.1 * (full.zone >= ZONE_DETECT).sum()


# ── service ──────────────────────────────────────────────────────────────────
def _ok(method, **params):
    out = io.StringIO()
    s = Server(METHODS, out=out)
    s.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
    s.wait_idle()
    r = next(m for m in (json.loads(x) for x in out.getvalue().splitlines()) if m.get("id") == 1)
    assert "result" in r, r.get("error")
    return r["result"]


@pytest.fixture(autouse=True)
def _fresh():
    session.reset()
    yield


def test_coverage_numbers_are_the_engines_and_the_note_names_a_coarse_grid():
    from cctv_simulator.focal_optimizer import band_area, fence_roi

    cam = dataclasses.asdict(CAM)
    tid = _ok("terrain.procedural", preset="ridge_and_valley", grid=120, width_m=1200.0)["terrain_id"]
    fence = [[300, 300], [900, 300], [900, 900], [300, 900]]
    _ok("perimeter.plan", terrain_id=tid, camera=cam, fence=fence, target_ppm=40, mast_m=6)
    for layer in ("dori", "overlap", "target"):
        cov = _ok("perimeter.coverage", terrain_id=tid, max_px=400, layer=layer)
        assert cov["overlay"]["data"].startswith("data:image/png")
    entry = session.SESSION.get(tid)
    plan, c, kw, fp = entry.results["plan"]
    t = entry.bare
    roi = fence_roi(t, fp, True, 40.0)
    mv, _ = band_area(t, plan, c, roi, ZONE_DETECT)
    assert cov["camera_count"] == mv.camera_count
    assert cov["coverage_pct"] == mv.coverage_pct and cov["visible_area_m2"] == mv.visible_area_m2
    assert cov["overlap_area_m2"] == mv.overlap_area_m2
    held = (mv.ppm_grid >= 40 - 1e-6) & mv.visibility_mask
    assert cov["target"]["held"] == pytest.approx(float((held & roi).sum()) / float(roi.sum()))
    # 10 m cells against a ~33 m plan range: noted, with the real numbers
    shortest = min(p.effective_range_m for p in plan.placed_cameras)
    assert cov["grid_note"] and "Analiz hücresi 10 m" in cov["grid_note"] and f"{shortest:.0f} m" in cov["grid_note"]
    # a fine grid carries no note
    session.reset()
    tid = _ok("terrain.procedural", preset="ridge_and_valley", grid=120, width_m=240.0)["terrain_id"]
    _ok("perimeter.plan", terrain_id=tid, camera=cam, fence=[[60, 60], [180, 60], [180, 180], [60, 180]],
        target_ppm=40, mast_m=6)
    assert _ok("perimeter.coverage", terrain_id=tid, max_px=300)["grid_note"] is None


def test_thermal_pixels_keep_their_johnson_classes():
    from cctv_simulator.config import DEFAULT_CAMERA_LIBRARY
    m = DEFAULT_CAMERA_LIBRARY["ASELSAN UMA Fix 55mm Termal Kamera"]
    cam = CameraConfig(name="T", model_name="ASELSAN UMA Fix 55mm Termal Kamera", sensor_name=m["sensor_name"],
                       resolution_name=m["resolution_name"], focal_min_mm=55.0, focal_max_mm=55.0)
    t = _flat(100, 10.0)
    pls = [CameraPlacement(x_m=300, y_m=500, mast_height_m=8, camera=cam, pan_deg=90, tilt_deg=-1, max_range_m=700),
           CameraPlacement(x_m=700, y_m=500, mast_height_m=8, camera=cam, pan_deg=270, tilt_deg=-1, max_range_m=700)]
    mv = calculate_multi_camera_viewshed(t, pls)
    cl = map_render.multi_view_classes(t, mv, pls, (500, 500))
    assert set(np.unique(mv.dori_grid)) - {0, 1} >= {int(z) for z in np.unique(cl.zone)} - {0, 1}
    assert int((cl.zone > ZONE_DETECT).sum()) > 0


def _old_slice(r, rw0, rw1, cw0, cw1):
    return r.dori_grid[rw0:rw1, cw0:cw1]


def test_windowed_slice_gives_pixel_identical_pictures(monkeypatch):
    """``multi_view_classes`` reads each camera's window without materialising the full grid."""
    t = _flat(120, 1.0)
    pls = [_pl(30.0, 60.0, 90.0), _pl(60.0, 30.0, 0.0)]
    t2 = _flat(100, 2.0)
    z = t2.z_grid.copy()
    z[30:85, 45:47] = 15.0
    t2 = TerrainData(z_grid=z, cell_size_m=2.0)
    cases = [(t, pls, (480, 480)), (t2, [_pl(60.0, 100.0, 90.0, mast=4.0, tilt=-3.0, rng=60.0)], (800, 800))]
    for terr, pl, size in cases:
        with target_height(1.8):
            mv = calculate_multi_camera_viewshed(terr, pl)
            assert hasattr(mv.per_camera[0], "window")
            new = map_render.multi_view_classes(terr, mv, pl, size)
            monkeypatch.setattr(map_render, "_dori_slice", _old_slice)
            old = map_render.multi_view_classes(terr, mv, pl, size)
            monkeypatch.undo()
        for name in ("zone", "seen", "ppm", "visible"):
            assert np.array_equal(getattr(new, name), getattr(old, name)), name
