"""Memory layout of ``MultiViewshedResult.per_camera``: each kept view stores only its
window (``WindowedViewshedResult``), yet every full-array attribute still equals a
direct ``calculate_3d_viewshed`` call, bit for bit."""
from __future__ import annotations

import dataclasses
import pickle

import numpy as np

from cctv_simulator.models import CameraConfig
from cctv_simulator.terrain_loader import generate_procedural_terrain
from cctv_simulator.viewshed_3d import (
    CameraPlacement,
    ViewshedResult,
    WindowedViewshedResult,
    calculate_3d_viewshed,
    calculate_multi_camera_viewshed,
    target_height,
)

WIDE = CameraConfig(name="W", sensor_name='1/2.8"', resolution_name="8 MP (4K - 3840x2160)",
                    focal_min_mm=3.6, focal_max_mm=9.0)
GRIDS = ("visibility_mask", "dori_grid", "ppm_grid", "slant_dist_grid")


def _placements(n: int, side: float, seed: int = 3):
    rng = np.random.default_rng(seed)
    pls = [CameraPlacement(*rng.uniform(0.05 * side, 0.95 * side, 2), 6.0, WIDE,
                           pan_deg=float(rng.uniform(0, 360)), tilt_deg=-4.0,
                           max_range_m=60.0, lens_mode="max") for _ in range(n)]
    # an edge camera and one looking off the map (empty window)
    pls.append(CameraPlacement(1.0, 1.0, 6.0, WIDE, pan_deg=45, tilt_deg=-4, max_range_m=200.0, lens_mode="max"))
    pls.append(CameraPlacement(5.0, 5.0, 6.0, WIDE, pan_deg=225, tilt_deg=-3, max_range_m=100.0, lens_mode="max"))
    return pls


def _direct(t, p, **kw):
    return calculate_3d_viewshed(
        terrain=t, cam_x_m=p.x_m, cam_y_m=p.y_m, mast_height_m=p.mast_height_m, camera=p.camera,
        lens_mode=p.lens_mode, pan_deg=p.pan_deg, tilt_deg=p.tilt_deg, max_range_m=p.max_range_m,
        focal_mm_override=p.focal_mm_override, with_profile=False, **kw)


def test_per_camera_properties_equal_a_direct_call():
    t = generate_procedural_terrain("ridge_and_valley", 120, 10.0)
    pls = _placements(12, 1200.0)
    for th in (0.0, 1.8):
        with target_height(th):
            mv = calculate_multi_camera_viewshed(t, pls)
            for p, r in zip(pls, mv.per_camera):
                ref = _direct(t, p)
                assert isinstance(r, WindowedViewshedResult) and isinstance(r, ViewshedResult)
                for g in GRIDS:
                    a, b = getattr(r, g), getattr(ref, g)
                    assert a.dtype == b.dtype and a.shape == b.shape, g
                    assert np.array_equal(a, b), g
                for f in dataclasses.fields(ViewshedResult):
                    if f.name not in GRIDS:
                        va, vb = getattr(r, f.name), getattr(ref, f.name)
                        assert (np.array_equal(va, vb) if isinstance(va, np.ndarray) else va == vb), f.name


def test_window_holds_everything_and_empty_window_works():
    t = generate_procedural_terrain("flat", 100, 10.0)
    pls = _placements(4, 1000.0)
    mv = calculate_multi_camera_viewshed(t, pls)
    for r in mv.per_camera:
        r0, r1, c0, c1 = r.window
        assert r.full_shape == (t.rows, t.cols)
        for g in GRIDS:
            full = getattr(r, g)
            outside = full.copy()
            outside[r0:r1, c0:c1] = 0
            assert not outside.any(), g                      # nothing written outside the window
    off = mv.per_camera[-1]                                  # looks off the map: a sliver at most
    assert (off.window[1] - off.window[0]) * (off.window[3] - off.window[2]) <= 4
    assert not off.dori_grid.any()
    # a result with nothing written has an empty window and still round-trips
    blank = dataclasses.replace(mv.per_camera[0], dori_grid=np.zeros((t.rows, t.cols), np.int32),
                                visibility_mask=np.zeros((t.rows, t.cols), bool),
                                ppm_grid=np.zeros((t.rows, t.cols), np.float32),
                                slant_dist_grid=np.zeros((t.rows, t.cols), np.float32))
    empty = WindowedViewshedResult.from_full(blank)
    assert empty.window == (0, 0, 0, 0) and empty.dori_window.size == 0
    assert empty.dori_grid.shape == (t.rows, t.cols) and not empty.dori_grid.any()


def test_retained_bytes_are_far_below_the_full_layout():
    n = 400
    t = generate_procedural_terrain("ridge_and_valley", n, 2000.0 / n)
    pls = _placements(110, 2000.0)
    mv = calculate_multi_camera_viewshed(t, pls)
    assert len(mv.per_camera) >= 100
    kept = sum(r.window_nbytes for r in mv.per_camera)
    full_layout = len(pls) * n * n * 13                      # old layout: 13 B per cell per camera
    assert full_layout / kept > 10, (full_layout, kept)
    # and nothing terrain-sized hides in the instance dict
    for r in mv.per_camera:
        big = [k for k, v in r.__dict__.items() if isinstance(v, np.ndarray) and v.size >= n * n // 4]
        assert not big, big


def test_replace_asdict_pickle_and_assignment_still_work():
    t = generate_procedural_terrain("ridge_and_valley", 80, 10.0)
    r = calculate_multi_camera_viewshed(t, _placements(3, 800.0)[:3]).per_camera[0]
    for clone in (pickle.loads(pickle.dumps(r)), dataclasses.replace(r, coverage_pct=1.5)):
        for g in GRIDS:
            assert np.array_equal(getattr(clone, g), getattr(r, g))
    assert dataclasses.replace(r, coverage_pct=1.5).coverage_pct == 1.5
    d = dataclasses.asdict(r)
    assert np.array_equal(d["dori_grid"], r.dori_grid)
    new = np.full((t.rows, t.cols), 7, np.int32)
    r.dori_grid = new                                        # a caller may still assign a full grid
    assert r.dori_grid is new
    r.window                                                 # accessors keep working


def test_monitor_floor_views_are_windowed_too_and_equal_direct():
    t = generate_procedural_terrain("ridge_and_valley", 100, 10.0)
    pls = _placements(5, 1000.0)
    mv = calculate_multi_camera_viewshed(t, pls, monitor_floor=True)
    for p, r in zip(pls, mv.per_camera):
        ref = _direct(t, p, monitor_floor=True)
        for g in GRIDS:
            assert np.array_equal(getattr(r, g), getattr(ref, g)), g
