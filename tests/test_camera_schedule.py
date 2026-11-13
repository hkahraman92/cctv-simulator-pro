"""Camera schedule (pole list): position, achieved px/m, switch port, power."""
from __future__ import annotations

import math

import numpy as np
import pytest

from cctv_simulator.calculations import camera_optics
from cctv_simulator.models import CameraConfig
from cctv_simulator.network_plan import plan_network, pse_w
from cctv_simulator.offer import pole_rows
from cctv_simulator.online_map_loader import calculate_bbox
from cctv_simulator.perimeter_planner import generate_perimeter_plan
from cctv_simulator.terrain_loader import TerrainData

FIX = CameraConfig(name="Sabit", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                   focal_min_mm=2.8, focal_max_mm=12.0)
PTZ = CameraConfig(name="PTZ", focal_min_mm=4.8, focal_max_mm=153.0, resolution_name="2 MP (1080p - 1920x1080)")
FENCE = [(100.0, 100.0), (500.0, 100.0), (500.0, 400.0), (100.0, 400.0)]


def _terrain(positioned=True):
    t = TerrainData(z_grid=np.zeros((60, 60), np.float32), cell_size_m=10.0)
    if positioned:
        t.lat_center, t.lon_center = 39.92, 32.85
    return t


def _plan(terr):
    return generate_perimeter_plan(terr, FENCE, FIX, target_ppm=40.0, mast_height_m=6.0, ptz_camera=PTZ,
                                   ptz_every_n=4)


def _table(rows):
    head = rows[0]
    return [dict(zip(head, r)) for r in rows[1:]]


def test_old_columns_are_unchanged_and_unknowns_stay_empty():
    terr = _terrain(positioned=False)
    plan = _plan(terr)
    rows = pole_rows(plan)
    assert rows[0][:14] == ["Direk", "Tür", "X (m)", "Y (m)", "Zemin (m)", "Direk boyu (m)", "Model", "Odak (mm)",
                            "HFOV (°)", "Yön (°)", "Eğim (°)", "Menzil (m)", "Kör bölge (m)", "Devriye (°)"]
    assert len(rows) == 1 + len(plan.placed_cameras) + len(plan.ptz_cameras)
    for r in _table(rows):                     # nothing computed, nothing guessed
        assert [r[k] for k in ("Enlem", "Boylam", "Menzil ucunda px/m", "Switch", "Port", "Kablo (m)", "Güç (W)")] == [""] * 7
    assert all(r["Enlem"] == "" for r in _table(pole_rows(plan, terrain=terr)))     # a terrain without a position


def test_position_is_the_same_frame_the_kml_uses():
    terr = _terrain()
    plan = _plan(terr)
    s, w, _n, _e = calculate_bbox(terr.lat_center, terr.lon_center, terr.width_m, terr.height_m)
    for r, c in zip(_table(pole_rows(plan, terrain=terr)), plan.placed_cameras):
        assert r["Enlem"] == pytest.approx(s + c.y_m / 111320.0, abs=1e-6)
        assert r["Boylam"] == pytest.approx(w + c.x_m / (111320.0 * math.cos(math.radians(terr.lat_center))), abs=1e-6)


def test_px_per_metre_at_the_end_of_the_range_comes_from_the_optics():
    terr = _terrain()
    plan = _plan(terr)
    rows = _table(pole_rows(plan, camera=FIX, ptz_camera=PTZ))
    fixed = [r for r in rows if r["Tür"] == "Sabit"]
    for r, c in zip(fixed, plan.placed_cameras):
        want = camera_optics(FIX, c.focal_mm).ppm_at(math.hypot(c.effective_range_m, c.mast_height_m))
        assert r["Menzil ucunda px/m"] == pytest.approx(float(want), abs=0.06)
    # a pole placed by the planner holds the fence target at the end of its range
    normal = [r for r, c in zip(fixed, plan.placed_cameras) if abs(c.focal_mm - FIX.focal_min_mm) < 1e-6]
    assert normal and all(r["Menzil ucunda px/m"] >= 40.0 - 0.5 for r in normal)
    # a PTZ's range is its tele identification reach
    assert all(r["Menzil ucunda px/m"] == pytest.approx(250.0, rel=0.02) for r in rows if r["Tür"] == "PTZ")


def test_every_camera_lands_on_one_port_and_the_ports_add_up_to_the_switches():
    terr = _terrain()
    plan = _plan(terr)
    net = plan_network(plan, switch_ports=8, poe_budget_w=240.0)
    assert len(net.feeds) == len(plan.placed_cameras) + len(plan.ptz_cameras)
    assert len({(f.switch_index, f.port) for f in net.feeds}) == len(net.feeds)          # no port used twice
    assert len({(f.ptz, f.cam_index) for f in net.feeds}) == len(net.feeds)              # no camera fed twice
    for sw in net.switches:
        mine = [f for f in net.feeds if f.switch_index == sw.index]
        assert len(mine) == sw.ports_used and sorted(f.port for f in mine) == list(range(1, sw.ports_used + 1))
        assert sum(f.port_w for f in mine) == pytest.approx(sw.poe_w)
        assert sum(f.cable_m for f in mine) == pytest.approx(sw.copper_m)
        assert sorted(f.pole_id for f in mine) == sorted(sw.poles)
    rows = _table(pole_rows(plan, network=net))
    ptz = [r for r in rows if r["Tür"] == "PTZ"]
    assert all(r["Switch"].startswith("SW") and r["Port"] >= 1 and r["Kablo (m)"] > 0 for r in rows)
    assert ptz and all(r["Güç (W)"] == 60.0 and r["Port gücü (W)"] == pse_w(60.0) for r in ptz)
