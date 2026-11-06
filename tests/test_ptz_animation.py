"""Animated PTZ sweep: pan motion model, per-frame visibility, terrain shadow."""
from __future__ import annotations

import time

import numpy as np
import pytest

from cctv_simulator.models import CameraConfig
from cctv_simulator.ptz_animation import SweepPlayback, pan_at, sight_profile, sweep_period_s
from cctv_simulator.ptz_schedule import Rotation360, SectorPatrol
from cctv_simulator.terrain_loader import TerrainData
from cctv_simulator.viewshed_3d import ZONE_DETECT, ZONE_IDENT, ZONE_OBSERVE, ZONE_RECOG

CAM = CameraConfig(name="P", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                   focal_min_mm=6.0, focal_max_mm=30.0, pole_height_m=8.0)
COLORS = {ZONE_IDENT: (1, 1, 1, 200), ZONE_RECOG: (2, 2, 2, 200),
          ZONE_OBSERVE: (3, 3, 3, 200), ZONE_DETECT: (4, 4, 4, 200)}
SHADOW = (9, 9, 9, 180)


def test_rotation_turns_clockwise_at_constant_speed():
    rot = Rotation360(speed_deg_s=30.0)
    assert sweep_period_s(rot) == pytest.approx(12.0)
    assert pan_at(rot, 0.0, 350.0) == pytest.approx(350.0)
    assert pan_at(rot, 1.0, 350.0) == pytest.approx(20.0)        # wraps through north
    assert pan_at(rot, 12.0) == pytest.approx(0.0)


def test_sector_patrol_goes_out_dwells_and_returns():
    sec = SectorPatrol(300.0, 60.0, speed_deg_s=20.0, end_dwell_s=2.0)   # 120 deg through north
    assert sec.span_deg == pytest.approx(120.0)
    assert sweep_period_s(sec) == pytest.approx(2 * 6.0 + 2 * 2.0)
    assert pan_at(sec, 3.0) == pytest.approx(0.0)                 # halfway out
    assert pan_at(sec, 6.5) == pytest.approx(60.0)                # dwelling at the end
    assert pan_at(sec, 11.0) == pytest.approx(0.0)                # halfway back
    assert pan_at(sec, 15.0) == pytest.approx(300.0)              # dwelling at the start


def _terrain_with_hill():
    n, cell = 120, 10.0
    yy, xx = np.mgrid[0:n, 0:n] * cell
    z = 40.0 * np.exp(-(((xx - 600) ** 2 + (yy - 850) ** 2) / (2 * 60.0 ** 2)))   # hill north
    return TerrainData(z_grid=z.astype(np.float32), cell_size_m=cell)


def _playback(**kw):
    terr = _terrain_with_hill()
    sweep = Rotation360(speed_deg_s=45.0, tilt_deg=-2.0, lens_mode="max")
    return terr, SweepPlayback(terr, 600.0, 600.0, 6.0, CAM, sweep, colors=COLORS,
                               shadow_rgba=SHADOW, max_range_m=500.0, **kw)


def test_frame_lights_only_the_current_cone_and_shades_the_hill_behind():
    _terr, pb = _playback()
    north = pb.frame(0.0)                      # pan 0 = looking at the hill
    lit = np.all(north.rgba == np.array(COLORS[ZONE_DETECT], np.uint8), axis=-1) | (north.rgba[..., 3] == 200)
    shade = np.all(north.rgba == np.array(SHADOW, np.uint8), axis=-1)
    assert north.shadow_area_m2 > 0 and shade.any()
    # every lit cell is inside the current horizontal cone
    assert np.all(pb.in_cone(north.pan_deg)[lit])
    pb.reset()
    south = pb.frame(4.0)                      # pan 180: flat ground, nothing hidden
    assert south.pan_deg == pytest.approx(180.0)
    assert south.shadow_area_m2 == 0.0 and south.lit_area_m2 > 0


def test_one_period_sees_everything_the_mount_can_see_and_trail_fades():
    _terr, pb = _playback()
    steps = np.linspace(0.0, pb.period_s, 240)
    for t in steps:
        fr = pb.frame(float(t))
    assert fr.seen_area_m2 == pytest.approx(float(pb.visible.sum()) * pb.cell_area, rel=0.02)
    # long after the cone passed, a cell is back to the faint memory alpha
    pb.reset()
    pb.frame(0.0)
    later = pb.frame(pb.period_s * 0.5)
    behind = pb.in_cone(0.0) & pb.visible & ~pb.in_cone(later.pan_deg)
    assert np.all(later.rgba[behind][:, 3] == pb.memory_alpha)


def test_frames_are_cheap():
    _terr, pb = _playback()
    pb.frame(0.0)
    t = time.perf_counter()
    for i in range(20):
        pb.frame(i * 0.1)
    assert (time.perf_counter() - t) / 20 < 0.03


def test_sight_profile_sees_the_hill_face_not_its_back():
    terr = _terrain_with_hill()
    d, ground, surf, vis, cam_z = sight_profile(terr, 600.0, 600.0, 6.0, 0.0, 500.0)
    assert cam_z == pytest.approx(terr.ground_elevation_at(600, 600) + 6.0)
    top = int(np.argmax(surf))
    # the steep lower face is in view (near the summit the slope flattens
    # below the sightline and the shoulder hides it -- that is correct)
    assert vis[: top - 10].all()
    assert not vis[top + 5:].all()             # the far side drops into shadow
    assert np.array_equal(ground, surf)        # bare terrain: ground == surface
