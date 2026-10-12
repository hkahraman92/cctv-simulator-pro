"""Mixed fixed + PTZ perimeter: placement rules and whole-grid evaluation."""
from __future__ import annotations

import math

import numpy as np
import pytest

from cctv_simulator.calculations import camera_optics
from cctv_simulator.models import CameraConfig
from cctv_simulator.mount_visibility import clear_mount_cache
from cctv_simulator.perimeter_planner import generate_perimeter_plan
from cctv_simulator.perimeter_ptz import evaluate_mixed_perimeter, patrol_for, plan_placements
from cctv_simulator.ptz_fusion import CONTINUOUS, INTERMITTENT, cue_response, detect_then_identify
from cctv_simulator.ptz_schedule import Rotation360, SectorPatrol
from cctv_simulator.terrain_loader import TerrainData
from cctv_simulator.viewshed_3d import PPM_IDENT, ZONE_DETECT

FIXED = CameraConfig(name="Sabit", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                     focal_min_mm=6.0, focal_max_mm=12.0)
PTZ = CameraConfig(name="PTZ", sensor_name='1/2.8"', resolution_name="2 MP (1080p - 1920x1080)",
                   focal_min_mm=4.3, focal_max_mm=129.0)
SQUARE_CCW = [(250.0, 250.0), (550.0, 250.0), (550.0, 550.0), (250.0, 550.0)]


def _flat(n=160, cell=5.0):
    return TerrainData(z_grid=np.zeros((n, n), np.float32), cell_size_m=cell)


def _plan(terr, fence=SQUARE_CCW, **kw):
    base = dict(target_ppm=62.5, mast_height_m=6.0, lens_mode="min", is_closed_loop=True)
    base.update(kw)
    return generate_perimeter_plan(terr, fence, FIXED, **base)


def test_no_ptz_leaves_the_fixed_plan_unchanged():
    terr = _flat()
    a = _plan(terr)
    b = _plan(terr, ptz_camera=PTZ, ptz_every_n=0)
    assert a.placed_cameras == b.placed_cameras and a.gaps == b.gaps
    assert b.ptz_cameras == [] and b.camera_count == a.camera_count
    assert b.estimated_bandwidth_mbps == a.estimated_bandwidth_mbps


def test_ptz_co_mounted_on_every_nth_regular_pole():
    terr = _flat()
    a = _plan(terr)
    m = _plan(terr, ptz_camera=PTZ, ptz_every_n=3)
    regular = [c for c in a.placed_cameras if "[" not in c.camera_model]
    hosts = regular[::3]
    assert [(p.pole_id, p.x_m, p.y_m, p.mast_height_m) for p in m.ptz_cameras] == \
        [(h.pole_id, h.x_m, h.y_m, h.mast_height_m) for h in hosts]
    # fixed layout, gap analysis and pole count untouched; cameras and bandwidth add up
    assert m.placed_cameras == a.placed_cameras and m.coverage_percentage == a.coverage_percentage
    assert m.pole_count == a.pole_count
    assert m.camera_count == a.camera_count + len(hosts) == len(m.placed_cameras) + m.ptz_count
    assert m.estimated_bandwidth_mbps == pytest.approx(a.estimated_bandwidth_mbps + 4.0 * len(hosts))


@pytest.mark.parametrize("fence", [SQUARE_CCW, SQUARE_CCW[::-1]])
def test_facility_patrol_faces_outward_for_either_winding(fence):
    m = _plan(_flat(), fence=fence, ptz_camera=PTZ, ptz_every_n=1, ptz_patrol_span_deg=180.0)
    assert m.ptz_cameras
    for p in m.ptz_cameras:
        dx, dy = p.x_m - 400.0, p.y_m - 400.0                    # away from the yard centre
        ux, uy = math.sin(math.radians(p.pan_deg)), math.cos(math.radians(p.pan_deg))
        if math.hypot(dx, dy) > 1.0:
            assert ux * dx + uy * dy > 0.0, (p.pole_id, p.pan_deg)
        assert p.patrol_span_deg == 180.0


def test_border_patrol_centres_on_the_watched_side():
    line = [(100.0, 400.0), (700.0, 400.0)]                     # drawn eastward
    m = generate_perimeter_plan(_flat(), line, FIXED, target_ppm=62.5, mast_height_m=6.0,
                                is_closed_loop=False, line_mode="border", watch_side="left",
                                ptz_camera=PTZ, ptz_every_n=2)
    assert m.ptz_cameras and all(p.pan_deg == 0.0 for p in m.ptz_cameras)   # north


def test_ptz_reach_is_the_tele_identification_range():
    m = _plan(_flat(), ptz_camera=PTZ, ptz_every_n=4, ptz_target_ppm=PPM_IDENT)
    slant = camera_optics(PTZ, PTZ.focal_max_mm).distance_for_ppm(PPM_IDENT)
    for p in m.ptz_cameras:
        assert p.effective_range_m == pytest.approx(math.sqrt(slant ** 2 - 36.0), abs=0.06)
        assert p.focal_mm == PTZ.focal_min_mm and p.tilt_deg < 0.0


def test_patrol_kind_follows_span():
    m = _plan(_flat(), ptz_camera=PTZ, ptz_every_n=4, ptz_patrol_span_deg=360.0)
    assert isinstance(patrol_for(m.ptz_cameras[0], 20.0, 2.0), Rotation360)
    m = _plan(_flat(), ptz_camera=PTZ, ptz_every_n=4, ptz_patrol_span_deg=120.0)
    p = m.ptz_cameras[0]
    s = patrol_for(p, 20.0, 2.0)
    assert isinstance(s, SectorPatrol) and s.span_deg == pytest.approx(120.0)
    assert (s.start_pan_deg + 60.0) % 360.0 == pytest.approx(p.pan_deg)


def test_placements_use_the_planned_focal():
    # The UI used to hard-code lens_mode="max" for every pole, so a plan made
    # with the wide lens was evaluated at full zoom.
    m = _plan(_flat(), lens_mode="min")
    assert all(pl.focal_mm_override == c.focal_mm == FIXED.focal_min_mm
               for pl, c in zip(plan_placements(m, FIXED), m.placed_cameras))


def test_mixed_evaluation_end_to_end():
    clear_mount_cache()
    terr = _flat()
    m = _plan(terr, ptz_camera=PTZ, ptz_every_n=4, ptz_patrol_span_deg=180.0)
    r = evaluate_mixed_perimeter(terr, m, FIXED, PTZ, speed_deg_s=30.0)
    assert len(r.sweeps) == m.ptz_count
    det = r.fused.status["detect"]
    assert np.any(det == CONTINUOUS) and np.any(det == INTERMITTENT)
    # detect->identify only where a fixed pole detects, and it is the fastest PTZ's cue time
    detected = r.static.dori_grid >= ZONE_DETECT
    assert np.all(np.isinf(r.detect_to_identify_s[~detected]))
    p0 = m.ptz_cameras[0]
    one = cue_response(terr, p0.x_m, p0.y_m, p0.mast_height_m, PTZ, home_pan_deg=p0.pan_deg,
                       home_tilt_deg=p0.tilt_deg, home_focal_mm=p0.focal_mm,
                       max_range_m=min(max(p0.effective_range_m, 50.0), 3000.0))
    assert np.all(r.detect_to_identify_s <= detect_then_identify(r.static, one))
    assert 0.0 < r.identifiable_pct <= 100.0
    assert r.median_d2i_s <= r.p90_d2i_s <= r.worst_d2i_s < math.inf


def test_mixed_evaluation_needs_ptz():
    terr = _flat()
    with pytest.raises(ValueError):
        evaluate_mixed_perimeter(terr, _plan(terr), FIXED, PTZ)
