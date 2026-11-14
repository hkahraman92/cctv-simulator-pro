"""EN 62676-4 Monitoring (12.5 px/m) as an opt-in terrain level.

Zone codes were renumbered (OUT_OF_FOV 0, OCCLUDED 1, MONITOR 2, DETECT 3 ...
IDENT 6). The default engine floor stays Detection: no cell is ever coded
ZONE_MONITOR and every area / share is what it was. Only a caller asking for
the monitor floor gets Monitoring cells (and the reach that comes with it).
"""
from __future__ import annotations

import ast
import dataclasses
import io
import json
import math
import pathlib
import re
from types import SimpleNamespace

import numpy as np
import pytest

from cctv_simulator.calculations import camera_optics
from cctv_simulator.focal_optimizer import band_area, fence_roi, mounts_from_plan, optimize_layout
from cctv_simulator.models import CameraConfig
from cctv_simulator.mount_visibility import compute_mount_visibility, view_mask
from cctv_simulator.perimeter_planner import generate_perimeter_plan
from cctv_simulator.plan_statement import declared_level, levels_for, plan_conformity
from cctv_simulator.service import session
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import APP_ERROR, INVALID_PARAMS, Server
from cctv_simulator.terrain_loader import TerrainData
from cctv_simulator.viewshed_3d import (
    PPM_DETECT,
    PPM_IDENT,
    PPM_MONITOR,
    PPM_OBSERVE,
    PPM_RECOG,
    THERMAL_NO_MONITOR,
    ZONE_DETECT,
    ZONE_IDENT,
    ZONE_MONITOR,
    ZONE_OBSERVE,
    ZONE_OCCLUDED,
    ZONE_OUT_OF_FOV,
    ZONE_RECOG,
    calculate_3d_viewshed,
    calculate_multi_camera_viewshed,
    CameraPlacement,
    view_limits,
    zone_codes,
    zone_ppm,
)

# 4 MP 1/2.8": 30 mm tele -> 25 px/m at ~600 m, 12.5 px/m at ~1200 m, so a flat 2.2 km
# grid holds the whole monitor band on a narrow cone (180 rays in both runs).
CAM = CameraConfig(name="V", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                   focal_min_mm=6.0, focal_max_mm=30.0, pole_height_m=8.0)
MAST = 8.0
PTZ_CAM = CameraConfig(name="PTZ", sensor_name='1/2.8"', resolution_name="2 MP (1080p - 1920x1080)",
                       focal_min_mm=4.3, focal_max_mm=129.0)
THERMAL = CameraConfig(name="Termal", sensor_name="LWIR 640x512 (12µm)", resolution_name="LWIR (640x512)",
                       focal_min_mm=25.0, focal_max_mm=225.0)
CELL = 10.0


def _flat(n=220):
    return TerrainData(z_grid=np.zeros((n, n), np.float32), cell_size_m=CELL)


def _run(terr, monitor, **kw):
    args = dict(terrain=terr, cam_x_m=1100.0, cam_y_m=100.0, mast_height_m=MAST, camera=CAM, lens_mode="max",
                pan_deg=0.0, tilt_deg=-1.5, max_range_m=2000.0, with_profile=False)
    args.update(kw)
    return calculate_3d_viewshed(monitor_floor=monitor, **args)


def _ground_for_ppm(camera, focal, ppm, mast=MAST):
    slant = camera_optics(camera, focal).distance_for_ppm(ppm)
    return math.sqrt(slant * slant - mast * mast)


def _analytic_cells(terr, camera, floor_ppm, *, focal, tilt_deg, cam_xy, pan_deg=0.0):
    """Independent reference for a flat terrain: cells inside the horizontal cone, inside the
    vertical frame, between 1 m and the floor's ground distance (from camera_optics)."""
    optics = camera_optics(camera, focal)
    g_max = _ground_for_ppm(camera, focal, floor_ppm)
    xs = np.arange(terr.cols) * CELL
    ys = np.arange(terr.rows) * CELL
    mx, my = np.meshgrid(xs, ys)
    dx, dy = mx - cam_xy[0], my - cam_xy[1]
    g = np.hypot(dx, dy)
    pan = math.radians(pan_deg)
    az = np.arctan2(dx, dy)
    d_az = np.arctan2(np.sin(az - pan), np.cos(az - pan))
    elev = np.arctan2(-MAST, np.maximum(g, 0.1))
    lo = math.radians(tilt_deg - optics.vfov_deg / 2.0)
    hi = math.radians(tilt_deg + optics.vfov_deg / 2.0)
    return (np.abs(d_az) <= math.radians(optics.hfov_deg / 2.0)) & (g > 1.0) & (g <= g_max) & (elev >= lo) & (elev <= hi)


# ── zone codes ───────────────────────────────────────────────────────────────
def test_zone_codes_are_monotonic_and_named():
    seq = [ZONE_OUT_OF_FOV, ZONE_OCCLUDED, ZONE_MONITOR, ZONE_DETECT, ZONE_OBSERVE, ZONE_RECOG, ZONE_IDENT]
    assert seq == sorted(seq) and len(set(seq)) == 7


def test_zone_ppm_of_all_five_levels_and_thermal_has_no_monitoring():
    assert zone_ppm(ZONE_MONITOR) == PPM_MONITOR == 12.5
    assert [zone_ppm(z) for z in (ZONE_DETECT, ZONE_OBSERVE, ZONE_RECOG, ZONE_IDENT)] == [25.0, 62.5, 125.0, 250.0]
    assert zone_ppm(ZONE_DETECT, True) == pytest.approx(1.3)
    with pytest.raises(ValueError, match="İzleme"):
        zone_ppm(ZONE_MONITOR, True)
    assert "Termal kamerada İzleme" in THERMAL_NO_MONITOR


def test_zone_codes_only_code_monitor_when_asked():
    ppm = np.array([5.0, 12.5, 20.0, 25.0, 62.5, 300.0])
    vis = np.ones(6, bool)
    default = zone_codes(vis, vis, ppm)
    assert ZONE_MONITOR not in default                       # default: a visible cell is at least Detection
    mon = zone_codes(vis, vis, ppm, monitor=True)
    assert list(mon) == [ZONE_MONITOR, ZONE_MONITOR, ZONE_MONITOR, ZONE_DETECT, ZONE_OBSERVE, ZONE_IDENT]
    assert np.array_equal(mon >= ZONE_DETECT, ppm >= PPM_DETECT)
    # a thermal view has no monitoring level, flag or not
    assert ZONE_MONITOR not in zone_codes(vis, vis, ppm, thermal=True, monitor=True)


def test_view_limits_floor_is_opt_in_and_ignored_for_thermal():
    d = view_limits(CAM, 30.0, 5000.0)
    m = view_limits(CAM, 30.0, 5000.0, monitor_floor=True)
    assert d.min_detect_ppm == PPM_DETECT and not d.monitor
    assert m.min_detect_ppm == PPM_MONITOR and m.monitor
    assert m.optical_limit_m == pytest.approx(2.0 * d.optical_limit_m, rel=1e-6)
    t0 = view_limits(THERMAL, 25.0, 5000.0)
    t1 = view_limits(THERMAL, 25.0, 5000.0, monitor_floor=True)
    assert t1.min_detect_ppm == t0.min_detect_ppm and not t1.monitor and t1.optical_limit_m == t0.optical_limit_m


# ── the authoritative single-camera engine ───────────────────────────────────
def test_default_run_has_no_monitor_cells_and_matches_the_optics():
    terr = _flat()
    res = _run(terr, False)
    assert not np.any(res.dori_grid == ZONE_MONITOR)
    ref = _analytic_cells(terr, CAM, PPM_DETECT, focal=30.0, tilt_deg=-1.5, cam_xy=(1100.0, 100.0))
    assert ref.sum() > 300
    # the reference comes from camera_optics.distance_for_ppm, not from the engine
    assert res.visible_area_m2 / CELL ** 2 == pytest.approx(float(ref.sum()), rel=0.04)
    assert res.optical_limit_m == pytest.approx(math.hypot(_ground_for_ppm(CAM, 30.0, PPM_DETECT), MAST), rel=1e-6)
    assert float(res.ppm_grid[res.visibility_mask].min()) >= PPM_DETECT - 1e-3


def test_monitor_floor_adds_exactly_the_12_5_to_25_band():
    terr = _flat()
    d, m = _run(terr, False), _run(terr, True)
    assert np.array_equal(d.dori_grid >= ZONE_DETECT, m.dori_grid >= ZONE_DETECT)     # nothing else moves
    assert np.array_equal(d.dori_grid[d.dori_grid >= ZONE_DETECT], m.dori_grid[m.dori_grid >= ZONE_DETECT])
    mon = m.dori_grid == ZONE_MONITOR
    assert mon.sum() > 100
    # strictly more visible area at >= Monitoring than at >= Detection
    assert (m.dori_grid >= ZONE_MONITOR).sum() > (m.dori_grid >= ZONE_DETECT).sum()
    assert m.visible_area_m2 > d.visible_area_m2
    # the band lies between the 25 and 12.5 px/m ground distances (one cell of slack for sampling)
    rr, cc = np.nonzero(mon)
    g = np.hypot(cc * CELL - 1100.0, rr * CELL - 100.0)
    g25, g12 = _ground_for_ppm(CAM, 30.0, PPM_DETECT), _ground_for_ppm(CAM, 30.0, PPM_MONITOR)
    assert g.min() >= g25 - 2 * CELL and g.max() <= g12 + 2 * CELL
    ppm = m.ppm_grid[mon]
    assert ppm.min() >= PPM_MONITOR - 1e-3 and ppm.max() < PPM_DETECT + 1e-3
    # the monitor-band area equals the analytic ring between the two distances
    ring = (_analytic_cells(terr, CAM, PPM_MONITOR, focal=30.0, tilt_deg=-1.5, cam_xy=(1100.0, 100.0))
            & ~_analytic_cells(terr, CAM, PPM_DETECT, focal=30.0, tilt_deg=-1.5, cam_xy=(1100.0, 100.0)))
    assert mon.sum() == pytest.approx(float(ring.sum()), rel=0.04)
    assert m.max_range_m > d.max_range_m


def test_thermal_view_ignores_the_monitor_floor():
    terr = _flat()
    d = _run(terr, False, camera=THERMAL, mast_height_m=8.0)
    m = _run(terr, True, camera=THERMAL, mast_height_m=8.0)
    assert np.array_equal(d.dori_grid, m.dori_grid) and not np.any(m.dori_grid == ZONE_MONITOR)


# ── multi-camera merge ───────────────────────────────────────────────────────
def test_multi_camera_default_is_unchanged_and_monitor_is_opt_in():
    terr = _flat()
    pls = [CameraPlacement(x_m=1100.0, y_m=100.0, mast_height_m=MAST, camera=CAM, lens_mode="max",
                           pan_deg=0.0, tilt_deg=-1.5, max_range_m=2000.0)]
    d = calculate_multi_camera_viewshed(terr, pls)
    m = calculate_multi_camera_viewshed(terr, pls, monitor_floor=True)
    assert not np.any(d.dori_grid == ZONE_MONITOR) and "monitor" not in d.pct_by_zone
    assert np.array_equal(d.dori_grid >= ZONE_DETECT, m.dori_grid >= ZONE_DETECT)
    assert (m.dori_grid == ZONE_MONITOR).sum() > 100
    assert m.pct_by_zone["monitor"] > m.pct_by_zone["detect"] - 1e-9
    one = _run(terr, True)
    assert np.array_equal(m.dori_grid, one.dori_grid)       # one camera: merge == the single engine


# ── mount raster vs the authoritative engine ─────────────────────────────────
def test_mount_raster_agrees_with_the_engine_under_the_monitor_floor():
    terr = TerrainData(z_grid=np.zeros((400, 400), np.float32), cell_size_m=5.0)
    x, y, rng = terr.width_m / 2, terr.height_m / 2, 1000.0
    mv = compute_mount_visibility(terr, x, y, MAST, max_range_m=rng)
    xor = union = n_mon = 0
    for f, aim, pan in ((30.0, 500.0, 0.0), (30.0, 500.0, 133.0), (30.0, 500.0, 260.0), (22.0, 400.0, 70.0)):
        tilt = -math.degrees(math.atan(MAST / aim))
        ref = calculate_3d_viewshed(terr, x, y, MAST, PTZ_CAM, focal_mm_override=f, pan_deg=pan, tilt_deg=tilt,
                                    max_range_m=rng, with_profile=False, monitor_floor=True)
        vm = view_mask(mv, PTZ_CAM, pan_deg=pan, tilt_deg=tilt, focal_mm=f, max_range_m=rng, monitor_floor=True)
        xor += np.count_nonzero(ref.visibility_mask ^ vm.visibility_mask)
        union += np.count_nonzero(ref.visibility_mask | vm.visibility_mask)
        both = ref.visibility_mask & vm.visibility_mask
        assert np.mean(ref.dori_grid[both] == vm.dori_grid[both]) > 0.97          # same bar as the default test
        n_mon += int(np.count_nonzero(vm.dori_grid == ZONE_MONITOR))
        # default view of the same mount: no monitor cells, the >= Detection set is unchanged
        dv = view_mask(mv, PTZ_CAM, pan_deg=pan, tilt_deg=tilt, focal_mm=f, max_range_m=rng)
        assert not np.any(dv.dori_grid == ZONE_MONITOR)
        assert np.array_equal(dv.dori_grid >= ZONE_DETECT, vm.dori_grid >= ZONE_DETECT)
    assert union > 1000 and n_mon > 100
    assert 1 - xor / union >= 0.97


# ── errors ───────────────────────────────────────────────────────────────────
def test_thermal_monitor_level_is_a_turkish_application_error():
    from cctv_simulator.service.terrain_methods import _level_of
    from cctv_simulator.service.protocol import RpcError

    with pytest.raises(RpcError) as e:
        _level_of({"level": "monitor"}, THERMAL, monitor_ok=True)
    assert e.value.code == APP_ERROR and e.value.message == THERMAL_NO_MONITOR
    assert _level_of({"level": "monitor"}, CAM, monitor_ok=True) == ZONE_MONITOR
    with pytest.raises(RpcError) as e2:                                    # a method that does not carry the floor
        _level_of({"level": "monitor"}, CAM)
    assert e2.value.code == APP_ERROR and "desteklenmiyor" in e2.value.message
    with pytest.raises(RpcError) as e3:
        _level_of({"level": "bogus"}, CAM, monitor_ok=True)
    assert e3.value.code == INVALID_PARAMS


# ── plan statement ───────────────────────────────────────────────────────────
def test_plan_statement_declares_monitoring_only_from_a_monitor_run():
    assert declared_level(CAM, 12.5, None, monitor=False) == ZONE_DETECT      # the old fallback, no İzleme claim
    assert declared_level(CAM, 12.5, None, monitor=True) == ZONE_MONITOR
    assert declared_level(CAM, 40.0, None, monitor=True) == ZONE_DETECT
    assert [k for _z, k, _l in levels_for(CAM)] == ["detect", "observe", "recognize", "identify"]
    assert [k for _z, k, _l in levels_for(CAM, True)][0] == "monitor"
    assert levels_for(THERMAL, True) == levels_for(THERMAL)                   # a thermal never lists it
    grid = np.full((6, 6), ZONE_DETECT, np.int32)
    grid[:3] = ZONE_MONITOR
    mv = SimpleNamespace(dori_grid=grid)
    mask = np.ones((6, 6), bool)
    plan = SimpleNamespace(target_ppm=12.5)
    conf = plan_conformity(plan, CAM, mv, [{"name": "Alan", "mask": mask, "kind": "area", "size": 36.0}],
                           cell_size_m=10.0, goal_frac=0.95, monitor=True)
    assert conf["declared_level"] == "monitor" and conf["declared_label"] == "İzleme"
    row = conf["zones"][0]["rows"][0]
    assert row["level"] == "monitor" and row["required_ppm"] == 12.5 and row["continuous"] == pytest.approx(1.0)
    conf0 = plan_conformity(plan, CAM, mv, [{"name": "Alan", "mask": mask, "kind": "area", "size": 36.0}],
                            cell_size_m=10.0, goal_frac=0.95)
    assert all(r["level"] != "monitor" for r in conf0["zones"][0]["rows"])


# ── service ──────────────────────────────────────────────────────────────────
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


SVC_CAM = dataclasses.asdict(CameraConfig(focal_min_mm=2.8, focal_max_mm=12.0))
FENCE = [[400, 400], [800, 400], [800, 800], [400, 800]]


@pytest.fixture
def plan_session():
    session.reset()
    tid = ok("terrain.procedural", preset="ridge_and_valley", grid=100, width_m=1200.0)["terrain_id"]
    ok("perimeter.plan", terrain_id=tid, camera=SVC_CAM, fence=FENCE, target_ppm=40, mast_m=6)
    return tid


def test_service_coverage_monitor_share_is_at_least_the_detect_share(plan_session):
    tid = plan_session
    det = ok("perimeter.coverage", terrain_id=tid, level="detect", max_px=300)
    assert "monitor" not in det["band_share"] and det["monitor_floor"] is False
    assert "monitor" not in det["zone_areas_m2"]
    mon = ok("perimeter.coverage", terrain_id=tid, level="monitor", max_px=300)
    assert mon["monitor_floor"] is True and mon["target"]["label"] == "İzleme" and mon["target"]["ppm"] == 12.5
    assert mon["band_share"]["monitor"] >= det["band_share"]["detect"] - 1e-9
    assert mon["band_share"]["monitor"] >= mon["band_share"]["detect"]
    for k in ("detect", "observe", "recognize", "identify"):                    # the >= Detection shares do not move
        assert mon["band_share"][k] == pytest.approx(det["band_share"][k], abs=0.01)
    assert any("İzleme" in row[0] for row in mon["legend"])               # listed only where it can occur
    assert not any("İzleme" in row[0] for row in det["legend"])
    assert session.SESSION.get(tid).results["coverage_masks"]["monitor"] is True


def test_service_unknown_level_errors_and_thermal_monitor_is_rejected(plan_session):
    tid = plan_session
    r = call("perimeter.coverage", terrain_id=tid, level="bogus")
    assert r["error"]["code"] == INVALID_PARAMS
    r = call("focal.sweep", terrain_id=tid, camera=dataclasses.asdict(THERMAL), x_m=600, y_m=600, level="monitor")
    assert r["error"]["code"] == APP_ERROR and r["error"]["message"] == THERMAL_NO_MONITOR
    r = call("focal.sweep", terrain_id=tid, camera=SVC_CAM, x_m=600, y_m=600, level="bogus")
    assert r["error"]["code"] == INVALID_PARAMS
    # planners that do not carry the floor say so instead of answering at the Detection floor
    # (area.plan, ptz_patrol and city.analyse now carry it; the PTZ-assisted pole search does not)
    r = call("perimeter.plan_assisted", terrain_id=tid, camera=SVC_CAM, ptz_camera=dataclasses.asdict(PTZ_CAM),
             fence=FENCE, level="monitor")
    assert r["error"]["code"] == APP_ERROR and "desteklenmiyor" in r["error"]["message"]


def test_service_focal_sweep_and_single_viewshed_accept_monitor(plan_session):
    tid = plan_session
    s = ok("focal.sweep", terrain_id=tid, camera=SVC_CAM, x_m=600, y_m=600, mast_m=8, level="monitor", n_focals=3)
    assert s["level"] == "monitor" and s["verified"]["area_m2"] >= 0.0
    assert all("monitor" in c["areas_m2"] for c in s["curve"])
    one = ok("viewshed.single", terrain_id=tid, camera=SVC_CAM, x_m=600, y_m=300, mast_m=8, tilt_deg=-3,
             level="monitor", max_px=300)
    base = ok("viewshed.single", terrain_id=tid, camera=SVC_CAM, x_m=600, y_m=300, mast_m=8, tilt_deg=-3, max_px=300)
    assert one["monitor_floor"] is True and base["monitor_floor"] is False and "monitor" not in base["zone_areas_m2"]
    assert one["visible_area_m2"] >= base["visible_area_m2"]


# ── the optimiser's scores carry the floor ───────────────────────────────────
def test_layout_optimizer_scores_monitoring_when_targeted():
    terr = _flat(120)
    fence = [(300.0, 300.0), (900.0, 300.0), (900.0, 900.0), (300.0, 900.0)]
    plan = generate_perimeter_plan(terr, fence, CAM, target_ppm=40.0, mast_height_m=6.0)
    roi = fence_roi(terr, fence, True, 40.0)
    mounts = mounts_from_plan(plan, CAM)
    det = optimize_layout(terr, mounts, roi=roi, level=ZONE_DETECT, n_focals=3, passes=1)
    mon = optimize_layout(terr, mounts, roi=roi, level=ZONE_MONITOR, n_focals=3, passes=1)
    assert ZONE_MONITOR not in det.after_m2 and ZONE_MONITOR in mon.after_m2
    assert mon.after_m2[ZONE_MONITOR] >= mon.after_m2[ZONE_DETECT] >= 0.0
    _mv, a_det = band_area(terr, plan, CAM, roi, ZONE_DETECT)
    mv_m, a_mon = band_area(terr, plan, CAM, roi, ZONE_MONITOR)
    assert a_mon >= a_det and np.any(mv_m.dori_grid == ZONE_MONITOR)
    with pytest.raises(ValueError, match="İzleme"):
        band_area(terr, plan, THERMAL, roi, ZONE_MONITOR)


# ── guard: nobody compares a zone grid against a bare integer ────────────────
_ZONE_NAME = re.compile(r"(^|[._])(dori(_grid)?|[a-z]*_?zone)$")
# identifiers that contain "zone" without being a ZONE_* grid
_NOT_A_ZONE = re.compile(r"dead_?zone|zone_len|zone_areas|zone_ppm|zone_codes|zone_overlay|zone_end|read_zone|_zone_m")


def _is_zone_expr(node: ast.AST) -> bool:
    text = ast.unparse(node)
    if _NOT_A_ZONE.search(text):
        return False
    if isinstance(node, ast.Subscript):
        return _is_zone_expr(node.value)
    if isinstance(node, (ast.Name, ast.Attribute)):
        return bool(_ZONE_NAME.search(text.split("[")[0].split("(")[0]))
    return False


def test_no_module_compares_a_zone_grid_to_a_bare_integer():
    root = pathlib.Path(__file__).resolve().parents[1] / "cctv_simulator"
    offenders = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            sides = [node.left, *node.comparators]
            for a, b in zip(sides[:-1], sides[1:]):
                for zone_side, other in ((a, b), (b, a)):
                    if (_is_zone_expr(zone_side) and isinstance(other, ast.Constant)
                            and isinstance(other.value, (int, float)) and not isinstance(other.value, bool)
                            and other.value != 0):
                        offenders.append(f"{path.relative_to(root)}:{node.lineno}: {ast.unparse(node)}")
    assert not offenders, "zone grids must be compared with ZONE_* names (0 = out of frame is allowed):\n" + \
        "\n".join(offenders)


# ── Monitoring in the area planner, the city analysis and the PTZ patrol search ──────────────────
AREA_SQ = [(100.0, 100.0), (220.0, 100.0), (220.0, 200.0), (100.0, 200.0)]
AREA_BIG = [(60.0, 60.0), (340.0, 60.0), (340.0, 340.0), (60.0, 340.0)]
AREA_CAM = CameraConfig(focal_min_mm=2.8, focal_max_mm=12.0)


@pytest.mark.parametrize("poly", [AREA_SQ, AREA_BIG])
def test_area_plan_monitoring_is_engine_verified_and_never_needs_more_cameras_than_detection(poly):
    from cctv_simulator.area_planner import plan_area, polygon_mask

    terr = TerrainData(np.zeros((200, 200)), cell_size_m=2.0)
    det = plan_area(terr, poly, AREA_CAM, level=ZONE_DETECT, goal_frac=0.9, mast_height_m=6.0)
    mon = plan_area(terr, poly, AREA_CAM, level=ZONE_MONITOR, goal_frac=0.9, mast_height_m=6.0)
    assert det.met and mon.met
    assert mon.plan.camera_count <= det.plan.camera_count and mon.plan.pole_count <= det.plan.pole_count
    assert mon.sites_considered == det.sites_considered          # the longer reach never thins the candidates
    assert mon.plan.target_ppm == PPM_MONITOR
    # the reported share is the engine's, run with the monitor floor, over the plan's own polygon
    roi = polygon_mask(terr, poly)
    mv, _ = band_area(terr, mon.plan, AREA_CAM, roi, ZONE_MONITOR)
    assert mon.engine_frac == pytest.approx(float(((mv.dori_grid >= ZONE_MONITOR) & roi).sum()) / roi.sum())
    assert mon.engine_frac >= 0.9
    # a Detection-floor run of the same plan has no monitor cells and the same >= Detection cells
    mv_det, _ = band_area(terr, mon.plan, AREA_CAM, roi, ZONE_DETECT)
    assert not np.any(mv_det.dori_grid == ZONE_MONITOR)
    assert np.array_equal(mv_det.dori_grid >= ZONE_DETECT, mv.dori_grid >= ZONE_DETECT)
    assert not any("analiz menzilini" in n for n in mon.notes)   # no raster was clipped below the monitor reach


def test_area_plan_thermal_monitoring_is_still_rejected():
    from cctv_simulator.area_planner import plan_area

    terr = TerrainData(np.zeros((200, 200)), cell_size_m=2.0)
    with pytest.raises(ValueError, match="İzleme"):
        plan_area(terr, AREA_SQ, THERMAL, level=ZONE_MONITOR, goal_frac=0.9, mast_height_m=6.0)


def _city_scene():
    from cctv_simulator import city
    from tests.test_city import located, osm

    t = located()
    lay = city.build_layer(t, osm(t))
    cam = CameraConfig(name="S", focal_min_mm=2.8, focal_max_mm=12.0)
    pls = [CameraPlacement(20, 150, 4.0, cam, pan_deg=90, tilt_deg=-1.5, max_range_m=600, focal_mm_override=12,
                           label="sokak"),
           CameraPlacement(205, 140, 4.0, cam, pan_deg=40, tilt_deg=-2, max_range_m=200, focal_mm_override=4,
                           label="konut")]
    return t, lay, pls


def test_city_analysis_scores_monitoring_and_leaves_faces_and_privacy_alone():
    from cctv_simulator import city

    t, lay, pls = _city_scene()
    det_mv = calculate_multi_camera_viewshed(t, pls)
    mon_mv = calculate_multi_camera_viewshed(t, pls, monitor_floor=True)
    det = city.analyse_city(t, lay, det_mv, level=ZONE_DETECT)
    mon = city.analyse_city(t, lay, mon_mv, level=ZONE_MONITOR)
    assert ZONE_MONITOR not in det.share_by_level and ZONE_MONITOR in mon.share_by_level
    street = city.street_target(lay, t)
    assert mon.share_by_level[ZONE_MONITOR] == pytest.approx(
        float(((mon_mv.dori_grid >= ZONE_MONITOR) & street).sum()) / street.sum())
    assert mon.share_by_level[ZONE_MONITOR] > det.share_by_level[ZONE_DETECT]
    for lv in (ZONE_DETECT, ZONE_OBSERVE, ZONE_RECOG, ZONE_IDENT):               # the >= Detection shares do not move
        assert mon.share_by_level[lv] == det.share_by_level[lv]
    assert mon.face_share == det.face_share
    assert [(c.label, c.seen_m2, c.private_m2, c.flagged) for c in mon.privacy] == \
           [(c.label, c.seen_m2, c.private_m2, c.flagged) for c in det.privacy]
    assert all(np.array_equal(a, b) for a, b in zip(mon.private_masks, det.private_masks))
    # grids that were not run with the floor cannot be scored at it (they would report Detection as Monitoring)
    with pytest.raises(ValueError, match="monitor_floor"):
        city.analyse_city(t, lay, det_mv, level=ZONE_MONITOR)


def test_service_city_analyse_and_area_plan_accept_monitor_and_thermal_is_rejected(monkeypatch):
    from cctv_simulator import city
    from tests.test_city import osm

    session.reset()
    t = TerrainData(np.zeros((150, 150), np.float32), cell_size_m=2.0, is_measured=True, lat_center=39.92,
                    lon_center=32.85)
    entry = session.SESSION.add(dict(bare=t, label="mahalle"))
    monkeypatch.setattr(city, "fetch_city", lambda terrain: city.build_layer(terrain, osm(terrain)))
    ok("city.osm", terrain_id=entry.id)
    cam = dataclasses.asdict(AREA_CAM)
    poly = [[100, 100], [200, 100], [200, 200], [100, 200]]
    a = ok("area.plan", terrain_id=entry.id, camera=cam, fence=poly, level="monitor", goal_frac=0.9, mast_m=4,
           streets_only=True)
    assert a["level"] == "monitor" and a["monitor_floor"] is True and a["met"]
    cov = ok("perimeter.coverage", terrain_id=entry.id, max_px=300)          # the plan's own target: 12.5 px/m
    assert cov["monitor_floor"] is True and cov["band_share"]["monitor"] == pytest.approx(a["engine_frac"])
    r = ok("city.analyse", terrain_id=entry.id, level="monitor", max_px=300)
    assert r["level"] == "monitor" and r["monitor_floor"] is True
    d = ok("city.analyse", terrain_id=entry.id, level="detect", max_px=300)
    assert d["monitor_floor"] is False and "monitor" not in d["share_by_level"]
    assert r["share_by_level"]["monitor"] >= d["share_by_level"]["detect"] - 1e-9
    assert r["face_share"] == d["face_share"] and r["private_seen_m2"] == d["private_seen_m2"]
    # a thermal plan has no Monitoring: the existing Turkish error, from both methods
    th = dataclasses.asdict(THERMAL)
    e1 = call("area.plan", terrain_id=entry.id, camera=th, fence=poly, level="monitor")
    assert e1["error"]["code"] == APP_ERROR and e1["error"]["message"] == THERMAL_NO_MONITOR
    ok("area.plan", terrain_id=entry.id, camera=th, fence=poly, level="detect", mast_m=4)
    e2 = call("city.analyse", terrain_id=entry.id, level="monitor")
    assert e2["error"]["code"] == APP_ERROR and e2["error"]["message"] == THERMAL_NO_MONITOR


def test_ptz_patrol_search_scores_monitoring_with_the_sweep_engine_floor():
    from cctv_simulator.perimeter_ptz import _patrol_added, optimize_patrols, ptz_sweeps
    from tests.test_ptz_patrol import PTZ as PTZ_PATROL
    from tests.test_ptz_patrol import _engine_case

    terr, plan, roi = _engine_case()
    none = np.zeros(roi.shape, bool)
    res = optimize_patrols(terr, plan, PTZ_PATROL, roi=roi, level=ZONE_MONITOR, n_focals=5)
    assert res.level == ZONE_MONITOR
    # numbers are evaluate_sweep's, run with the monitor floor; nothing is ever lost
    assert res.before_m2 == _patrol_added(terr, plan, PTZ_PATROL, roi, none, ZONE_MONITOR, monitor_floor=True)[0]
    assert _patrol_added(terr, res.plan, PTZ_PATROL, roi, none, ZONE_MONITOR, monitor_floor=True)[0] == \
        max(res.before_m2, res.after_m2)
    assert res.after_m2 >= res.before_m2 > 0
    # Monitoring holds at least what the same patrols hold at Detection; the default sweep has no such cells
    assert res.before_m2 >= _patrol_added(terr, plan, PTZ_PATROL, roi, none, ZONE_DETECT)[0]
    assert not any((sw.dori_grid == ZONE_MONITOR).any() for sw in ptz_sweeps(terr, plan, PTZ_PATROL))
    assert any((sw.dori_grid == ZONE_MONITOR).any() for sw in ptz_sweeps(terr, plan, PTZ_PATROL, monitor_floor=True))
    # the raster is sized for the monitor reach: nothing is reported clipped
    assert not res.truncated and not any("analiz menzilini" in n for n in res.notes)


def test_ptz_patrol_thermal_monitoring_is_rejected_and_the_service_carries_the_floor(plan_session):
    from cctv_simulator.perimeter_ptz import optimize_patrols
    from tests.test_ptz_patrol import _engine_case

    terr, plan, roi = _engine_case()
    with pytest.raises(ValueError, match="İzleme"):
        optimize_patrols(terr, plan, THERMAL, roi=roi, level=ZONE_MONITOR)
    tid = plan_session
    ptz = dataclasses.asdict(PTZ_CAM)
    ok("perimeter.plan", terrain_id=tid, camera=SVC_CAM, fence=FENCE, target_ppm=40, mast_m=8,
       ptz_camera=ptz, ptz_every_n=2, ptz_patrol_span_deg=360)
    r = ok("perimeter.ptz_patrol", terrain_id=tid, level="monitor", band_m=100, n_focals=4)
    assert r["monitor_floor"] is True and r["after_m2"] >= r["before_m2"] and r["truncated"] is False
    assert r["applied"] is (r["after_m2"] > r["before_m2"])
    cov = ok("perimeter.coverage", terrain_id=tid, level="monitor", band_m=100, max_px=300)
    assert cov["monitor_floor"] is True and r["band_area_m2"] == pytest.approx(cov["band_area_m2"])
    # a thermal PTZ has no Monitoring level: the existing Turkish error
    ok("perimeter.plan", terrain_id=tid, camera=SVC_CAM, fence=FENCE, target_ppm=40, mast_m=8,
       ptz_camera=dataclasses.asdict(THERMAL), ptz_every_n=2, ptz_patrol_span_deg=360)
    e = call("perimeter.ptz_patrol", terrain_id=tid, level="monitor")
    assert e["error"]["code"] == APP_ERROR and e["error"]["message"] == THERMAL_NO_MONITOR
