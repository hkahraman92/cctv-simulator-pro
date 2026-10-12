"""Per-mount 360-degree LOS raster + per-view masks vs the authoritative engine."""
from __future__ import annotations

import math

import numpy as np
import pytest

from cctv_simulator.models import CameraConfig
from cctv_simulator.mount_visibility import (
    clear_mount_cache,
    compute_mount_visibility,
    mount_visibility,
    view_mask,
)
from cctv_simulator.perimeter_planner import _sample_terrain
from cctv_simulator.terrain_loader import TerrainData, generate_procedural_terrain
from cctv_simulator.viewshed_3d import _K_REFRACT, _R_EARTH, calculate_3d_viewshed

CAM = CameraConfig(name="PTZ", sensor_name='1/2.8"', resolution_name="2 MP (1080p - 1920x1080)",
                   focal_min_mm=4.3, focal_max_mm=129.0)
MAST = 12.0


def _views():
    for f, aim in ((12.0, 60.0), (30.0, 150.0), (129.0, 300.0)):
        tilt = -math.degrees(math.atan(MAST / aim))
        for pan in (0.0, 133.0, 260.0):
            yield f, tilt, pan


def _bilinear_sightline(terr, x, y, cam_z, rr, cc):
    """Independent reference: straight eye -> cell-centre line over the
    bilinear terrain surface, sampled every 1/4 cell."""
    cs, k = terr.cell_size_m, (1 - _K_REFRACT) / (2 * _R_EARTH)
    out = np.zeros(len(rr), bool)
    for i, (r, c) in enumerate(zip(rr, cc)):
        tx, ty = terr.origin_x + c * cs, terr.origin_y + r * cs
        dist = math.hypot(tx - x, ty - y)
        n = max(int(dist / (cs * 0.25)), 2)
        t = np.arange(1, n) / n
        z = _sample_terrain(terr, x + t * (tx - x), y + t * (ty - y)) - (t * dist) ** 2 * k
        tz = float(_sample_terrain(terr, np.array([tx]), np.array([ty]))[0]) - dist * dist * k
        out[i] = not np.any((z - cam_z) / (t * dist) > (tz - cam_z) / dist + 1e-9)
    return out


def test_no_less_accurate_than_the_engine_on_hilly_terrain():
    terr = generate_procedural_terrain("rolling_hills", grid_size=200, cell_size_m=3.0)
    x, y, rng = terr.width_m / 2, terr.height_m / 2, 300.0
    cam_z = terr.get_elevation_at(x, y) + MAST
    mv = compute_mount_visibility(terr, x, y, MAST, max_range_m=rng)
    score = {"engine": [0, 0], "raster": [0, 0]}
    for f, tilt, pan in _views():
        ref = calculate_3d_viewshed(terr, x, y, MAST, CAM, focal_mm_override=f, pan_deg=pan,
                                    tilt_deg=tilt, max_range_m=rng, with_profile=False)
        vm = view_mask(mv, CAM, pan_deg=pan, tilt_deg=tilt, focal_mm=f, max_range_m=rng)
        rr, cc = np.nonzero((ref.dori_grid != 0) | (vm.dori_grid != 0))
        truth = _bilinear_sightline(terr, x, y, cam_z, rr, cc)
        for name, g in (("engine", ref), ("raster", vm)):
            framed = g.dori_grid[rr, cc] != 0
            score[name][0] += np.count_nonzero(g.visibility_mask[rr, cc][framed] == truth[framed])
            score[name][1] += np.count_nonzero(framed)
    acc = {k: a / max(n, 1) for k, (a, n) in score.items()}
    assert score["raster"][1] > 500                    # the views actually frame terrain
    assert acc["raster"] >= acc["engine"] - 0.02, acc


def test_matches_engine_on_flat_terrain():
    terr = TerrainData(z_grid=np.zeros((240, 240), np.float32), cell_size_m=5.0)
    x, y = terr.width_m / 2, terr.height_m / 2
    mv = compute_mount_visibility(terr, x, y, MAST, max_range_m=500.0)
    xor = union = 0
    for f, tilt, pan in _views():
        ref = calculate_3d_viewshed(terr, x, y, MAST, CAM, focal_mm_override=f, pan_deg=pan,
                                    tilt_deg=tilt, max_range_m=500.0, with_profile=False)
        vm = view_mask(mv, CAM, pan_deg=pan, tilt_deg=tilt, focal_mm=f, max_range_m=500.0)
        xor += np.count_nonzero(ref.visibility_mask ^ vm.visibility_mask)
        union += np.count_nonzero(ref.visibility_mask | vm.visibility_mask)
        # same DORI classification wherever both see the cell
        both = ref.visibility_mask & vm.visibility_mask
        assert np.mean(ref.dori_grid[both] == vm.dori_grid[both]) > 0.97
    assert union > 1000
    assert 1 - xor / union >= 0.97


def test_view_returns_full_grid_and_flags_truncation():
    terr = TerrainData(z_grid=np.zeros((300, 300), np.float32), cell_size_m=5.0)
    mv = compute_mount_visibility(terr, 750.0, 750.0, MAST, max_range_m=100.0)
    assert mv.written.shape[0] < 300                    # raster is cropped to its range
    assert not view_mask(mv, CAM, pan_deg=0.0, tilt_deg=-1.0, focal_mm=129.0).truncated
    vm = view_mask(mv, CAM, pan_deg=0.0, tilt_deg=-1.0, focal_mm=129.0, max_range_m=2000.0)
    assert vm.visibility_mask.shape == (300, 300)
    assert vm.truncated                                # asked for 2 km, raster has 100 m
    assert vm.effective_max_range_m == pytest.approx(100.0)


def test_cache_reuses_and_invalidates():
    clear_mount_cache()
    terr = TerrainData(z_grid=np.zeros((100, 100), np.float32), cell_size_m=5.0)
    a = mount_visibility(terr, 250.0, 250.0, MAST, max_range_m=200.0)
    assert mount_visibility(terr, 250.0, 250.0, MAST, max_range_m=200.0) is a
    assert mount_visibility(terr, 250.0, 250.0, MAST + 1, max_range_m=200.0) is not a
    terr.set_custom_width_m(1000.0)                     # recalibrated -> new geometry
    assert mount_visibility(terr, 250.0, 250.0, MAST, max_range_m=200.0) is not a
    other = TerrainData(z_grid=np.zeros((100, 100), np.float32), cell_size_m=5.0)
    assert mount_visibility(other, 250.0, 250.0, MAST, max_range_m=200.0) is not a
    clear_mount_cache()
