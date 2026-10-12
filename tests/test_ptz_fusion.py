"""Fixed + PTZ coverage fusion and slew-to-cue (detect -> identify) response."""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from cctv_simulator.calculations import camera_optics
from cctv_simulator.models import CameraConfig
from cctv_simulator.mount_visibility import clear_mount_cache
from cctv_simulator.ptz_fusion import (
    CONTINUOUS,
    CUE_ATMOSPHERE,
    CUE_LENS,
    CUE_NO_LOS,
    CUE_OK,
    CUE_TILT,
    INTERMITTENT,
    NONE,
    PTZCoverage,
    cue_response,
    detect_then_identify,
    fuse_coverage,
)
from cctv_simulator.ptz_schedule import SectorPatrol, evaluate_sweep
from cctv_simulator.ptz_tour import move_time_s
from cctv_simulator.terrain_loader import TerrainData
from cctv_simulator.viewshed_3d import (
    ZONE_DETECT,
    ZONE_IDENT,
    ZONE_RECOG,
    CameraPlacement,
    calculate_multi_camera_viewshed,
)

PTZ = CameraConfig(name="PTZ", sensor_name='1/2.8"', resolution_name="2 MP (1080p - 1920x1080)",
                   focal_min_mm=4.3, focal_max_mm=129.0)
FIXED = CameraConfig(name="Sabit", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                     focal_min_mm=6.0, focal_max_mm=6.0)


def _flat(n=200, cell=5.0):
    return TerrainData(z_grid=np.zeros((n, n), np.float32), cell_size_m=cell)


# ── fusion ───────────────────────────────────────────────────────────────────
def test_fusion_classifies_each_level():
    #            A           B            C
    static = SimpleNamespace(dori_grid=np.array([[ZONE_IDENT, ZONE_DETECT, 0]]))
    p1 = PTZCoverage(np.array([[0, ZONE_IDENT, ZONE_RECOG]]), np.array([[np.inf, 10.0, 20.0]]), "P1")
    p2 = PTZCoverage(np.array([[0, 0, ZONE_RECOG]]), np.array([[np.inf, np.inf, 8.0]]), "P2")
    f = fuse_coverage(static, [p1, p2], cell_size_m=1.0)
    assert f.status["ident"].tolist() == [[CONTINUOUS, INTERMITTENT, NONE]]
    assert f.revisit["ident"].tolist() == [[0.0, 10.0, math.inf]]
    assert f.status["recog"].tolist() == [[CONTINUOUS, INTERMITTENT, INTERMITTENT]]
    assert f.revisit["recog"].tolist() == [[0.0, 10.0, 8.0]]          # best PTZ gap wins
    # a fixed camera's detection makes the cell continuous at that level:
    # the PTZ gap there is irrelevant
    assert f.status["detect"].tolist() == [[CONTINUOUS, CONTINUOUS, INTERMITTENT]]
    assert f.worst_intermittent_revisit_s["recog"] == 10.0
    assert f.continuous_area_m2["ident"] == 1.0 and f.intermittent_area_m2["recog"] == 2.0


def test_fusion_rejects_mismatched_grids():
    with pytest.raises(ValueError):
        fuse_coverage(SimpleNamespace(dori_grid=np.zeros((3, 3))),
                      [PTZCoverage(np.zeros((4, 4)), np.zeros((4, 4)))], 1.0)


# ── slew-to-cue ──────────────────────────────────────────────────────────────
def test_response_is_aim_plus_zoom_and_reaches_target_ppm():
    clear_mount_cache()
    terr = _flat()
    x = y = 500.0
    cue = cue_response(terr, x, y, 10.0, PTZ, home_pan_deg=0.0, home_tilt_deg=0.0,
                       max_range_m=400.0)
    r, c = int(y / 5), int((x + 100.0) / 5)                       # 100 m due east
    assert cue.reason[r, c] == CUE_OK
    f = cue.focal_mm[r, c]
    slant = math.hypot(100.0, 10.0)
    assert camera_optics(PTZ, f).ppm_at(slant) == pytest.approx(250.0, rel=1e-3)
    tilt = -math.degrees(math.atan2(10.0, 100.0))
    slew, zoom, settle = move_time_s(90.0, tilt, f - PTZ.focal_min_mm, PTZ, slew_speed_deg_s=90.0,
                                     zoom_full_sweep_s=3.0, settle_s=1.5)
    assert cue.response_s[r, c] == pytest.approx(float(slew + zoom + settle), rel=0.02)


def test_identification_limit_matches_the_optic_engine():
    clear_mount_cache()
    terr = _flat(n=400, cell=5.0)
    cue = cue_response(terr, 1000.0, 1000.0, 10.0, PTZ, home_pan_deg=0, home_tilt_deg=0,
                       max_range_m=900.0)
    ok = cue.reason == CUE_OK
    rows, cols = np.nonzero(ok)
    reach = np.hypot(cols * 5.0 - 1000.0, rows * 5.0 - 1000.0).max()
    expected = math.sqrt(camera_optics(PTZ, PTZ.focal_max_mm).distance_for_ppm(250.0) ** 2 - 100)
    assert reach == pytest.approx(expected, abs=10.0)              # within two cells
    assert np.any(cue.reason == CUE_LENS)                          # farther cells: lens too short


def test_tilt_limit_wall_and_fog_give_reasons():
    clear_mount_cache()
    z = np.zeros((200, 200), np.float32)
    z[120:124, 60:140] = 30.0                                       # 30 m wall north of the mast
    terr = TerrainData(z_grid=z, cell_size_m=5.0)
    base = dict(home_pan_deg=0, home_tilt_deg=0, max_range_m=450.0)
    clear = cue_response(terr, 500.0, 500.0, 10.0, PTZ, **base)
    assert np.any(clear.reason[130:180, 90:110] == CUE_NO_LOS)      # behind the wall
    steep = cue_response(terr, 500.0, 500.0, 10.0, PTZ, tilt_max_deg=-10.0, **base)
    assert np.count_nonzero(steep.reason == CUE_TILT) > 0           # far, flat-angle cells
    assert steep.reachable_area_m2 < clear.reachable_area_m2
    fog = cue_response(terr, 500.0, 500.0, 10.0, PTZ, visibility_km=0.2, weather="Sis", **base)
    assert np.count_nonzero(fog.reason == CUE_ATMOSPHERE) > 0
    assert fog.reachable_area_m2 < clear.reachable_area_m2


# ── end to end: fixed cameras detect, a sweeping PTZ patrols, PTZ identifies ──
def test_fixed_plus_sweep_plus_cue():
    clear_mount_cache()
    terr = _flat(n=160, cell=5.0)
    fixed = [CameraPlacement(x_m=400, y_m=200, mast_height_m=6, camera=FIXED, pan_deg=0,
                             tilt_deg=-8, max_range_m=300, label="S1")]
    static = calculate_multi_camera_viewshed(terr, fixed)
    sweep = evaluate_sweep(terr, 400.0, 400.0, 10.0, PTZ,
                           SectorPatrol(90.0, 270.0, speed_deg_s=15.0, tilt_deg=-4.0, focal_mm=12.0),
                           max_range_m=350.0)
    fused = fuse_coverage(static, [PTZCoverage.from_sweep(sweep)], terr.cell_size_m)
    det = fused.status["detect"]
    assert np.any(det == CONTINUOUS) and np.any(det == INTERMITTENT)
    assert np.all(fused.revisit["detect"][det == CONTINUOUS] == 0.0)
    assert np.all(np.isfinite(fused.revisit["detect"][det == INTERMITTENT]))

    cue = cue_response(terr, 400.0, 400.0, 10.0, PTZ, home_pan_deg=180.0, home_tilt_deg=-4.0,
                       home_focal_mm=12.0, max_range_m=350.0)
    d2i = detect_then_identify(static, cue)
    triggered = static.dori_grid >= ZONE_DETECT
    assert np.all(np.isinf(d2i[~triggered]))
    assert np.any(np.isfinite(d2i[triggered]))
