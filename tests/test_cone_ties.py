"""Boundary ties of the node classification are half-open (viewshed_3d "Boundary ties").

A cell is classified at its own node, so a boundary through grid nodes (a 90 deg cone panned to 0 has
its edges on the grid diagonals; a range of 100 m on a 10 m grid has axis nodes at exactly 100 m)
puts every such node ON the boundary. A closed test counted them all: the wedge was over-counted by
14 / 8 / 4.6 % at 10 / 5 / 2.5 m cells. The rule now counts half of the ties and leaves every other
node's answer untouched.
"""
from __future__ import annotations

import contextlib
import dataclasses
import math

import numpy as np
import pytest

import cctv_simulator.mount_visibility as mvm
import cctv_simulator.viewshed_3d as vs
from cctv_simulator.calculations import camera_optics
from cctv_simulator.models import CameraConfig
from cctv_simulator.terrain_loader import TerrainData, generate_procedural_terrain

BASE = CameraConfig(name="W", sensor_name='1/2.8"', resolution_name="8 MP (4K - 3840x2160)",
                    focal_min_mm=3.6, focal_max_mm=9.0, pole_height_m=6.0)
SENSOR_W = camera_optics(BASE, 5.0).sensor_w_mm
H_3_4 = math.degrees(2 * math.atan(4 / 3))      # cone edges through the (4, 3) lattice direction
H_1_2 = math.degrees(2 * math.atan(2))          # cone edges through the (1, 2) lattice direction
MAST = 8.0
TILT = -2.0


def _cam(hfov_deg: float) -> CameraConfig:
    f = SENSOR_W / (2 * math.tan(math.radians(hfov_deg) / 2))
    return dataclasses.replace(BASE, focal_min_mm=f, focal_max_mm=f)


def _flat(cell: float, side: float = 300.0) -> TerrainData:
    n = int(round(side / cell)) + 1
    return TerrainData(z_grid=np.zeros((n, n), np.float32), cell_size_m=cell)


def _centre(t: TerrainData) -> float:
    return (t.cols // 2) * t.cell_size_m


def _offsets(t: TerrainData):
    c = _centre(t)
    g = np.arange(t.cols) * t.cell_size_m
    return np.meshgrid(g - c, g - c)               # dx, dy of every node from the lens


@contextlib.contextmanager
def _closed_rule():
    """The old behaviour (every boundary test inclusive), for before / after comparisons."""
    leg = dict(in_horizontal_cone=lambda ad, d, h, c: np.abs(ad) <= h,
               range_ok=lambda d, r, dx, dy: (d <= r) & (d > 1.0),
               vertical_ok=lambda e, lo, hi, dx, dy: (e >= lo) & (e <= hi))
    saved = {}
    for m in (vs, mvm):
        for k, f in leg.items():
            if hasattr(m, k):
                saved[(m, k)] = getattr(m, k)
                setattr(m, k, f)
    try:
        yield
    finally:
        for (m, k), f in saved.items():
            setattr(m, k, f)


@contextlib.contextmanager
def _limits(hfov=None, eff=None):
    saved = vs.view_limits, mvm.view_limits

    def wrap(orig):
        def f(*a, **k):
            lim = orig(*a, **k)
            optics = lim.optics if hfov is None else dataclasses.replace(lim.optics, hfov_deg=hfov)
            return dataclasses.replace(lim, optics=optics,
                                       effective_max_range_m=lim.effective_max_range_m if eff is None else eff)
        return f
    vs.view_limits, mvm.view_limits = wrap(saved[0]), wrap(saved[1])
    try:
        yield
    finally:
        vs.view_limits, mvm.view_limits = saved


def _view(t, cam, pan, th=0.0, mast=MAST, tilt=TILT, max_range=600.0):
    c = _centre(t)
    with vs.target_height(th):
        return vs.calculate_3d_viewshed(t, c, c, mast, cam, lens_mode="min", pan_deg=pan, tilt_deg=tilt,
                                        max_range_m=max_range, with_profile=False)


def _wedge_area(cam):
    """Analytic frameable wedge on flat ground: the hfov slice of the annulus between the near edge of
    the vertical frame and min(range, far edge of the frame)."""
    o = camera_optics(cam, cam.focal_min_mm)
    lim = vs.view_limits(cam, cam.focal_min_mm, 600.0)
    v_lo = math.radians(TILT - o.vfov_deg / 2)
    v_hi = math.radians(TILT + o.vfov_deg / 2)
    near = MAST / math.tan(-v_lo) if v_lo < 0 else math.inf
    far = MAST / math.tan(-v_hi) if v_hi < 0 else math.inf
    return o.hfov_deg / 360 * math.pi * (min(lim.effective_max_range_m, far) ** 2 - near ** 2)


# ── the wedge area ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("cell", [10.0, 5.0, 2.5])
@pytest.mark.parametrize("hfov", [90.0, H_3_4, H_1_2])
def test_aligned_wedge_area_is_as_unbiased_as_an_off_axis_one(cell, hfov):
    """Pans whose cone edges run through grid nodes stay within the band of the analytic wedge that a
    pan hitting no node has (the closed rule: +14 % at 10 m for a 90 deg cone)."""
    t = _flat(cell)
    cam = _cam(hfov)
    analytic = _wedge_area(cam)
    err = {pan: _view(t, cam, pan).fov_area_m2 / analytic - 1.0 for pan in (0, 90, 180, 270, 45, 37)}
    for pan in (0, 90, 180, 270, 45):
        assert abs(err[pan]) <= max(0.015, abs(err[37]) + 0.01), (pan, err)
    if hfov == 90.0:
        for pan in (0, 45, 90):                         # the over-counted cases: +2.4 .. +13 % before
            assert abs(err[pan]) < 0.015, (pan, err)


def test_half_of_every_cone_edge_is_kept():
    """Pan 0, 90 deg: the +-45 deg diagonals are all ties. The left edge keeps every other node
    (ceil(n/2)), the right edge the others (floor(n/2))."""
    t = _flat(10.0)
    res = _view(t, _cam(90.0), 0)
    dx, dy = _offsets(t)
    d = np.hypot(dx, dy)
    elev = np.arctan2(-MAST, np.maximum(d, 0.1))
    lo = math.radians(res.tilt_deg - res.vfov_deg / 2)
    hi = math.radians(res.tilt_deg + res.vfov_deg / 2)
    # independent of the engine's tie rule: strictly inside range and vertical frame
    live = (d > 1.0) & (d < res.max_range_m - 1e-6) & (elev > lo + 1e-9) & (elev < hi - 1e-9)
    framed = res.dori_grid != 0
    left = live & (dy > 0) & (np.abs(dx + dy) < 1e-9)       # azimuth -45 deg
    right = live & (dy > 0) & (np.abs(dx - dy) < 1e-9)      # azimuth +45 deg
    n = int(left.sum())
    assert n == int(right.sum()) and n >= 4
    assert int(framed[left].sum()) == (n + 1) // 2
    assert int(framed[right].sum()) == n // 2


# ── a full disc ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cell,tol", [(10.0, 0.02), (5.0, 0.01), (2.5, 0.005)])
def test_full_disc_keeps_half_of_its_range_ring_and_is_unbiased(cell, tol):
    """360 deg view whose range (100 m) falls on lattice nodes: the axis nodes and the 3-4-5 ones sit
    at exactly R. Half of them count, so the area is unbiased (a closed ring over-counts)."""
    t = _flat(cell)
    c = _centre(t)
    with _limits(hfov=360.0, eff=100.0):
        # tilt -20 at a 0.5 m mast frames every ground node from 1 m outwards
        res = vs.calculate_3d_viewshed(t, c, c, 0.5, _cam(90.0), lens_mode="min", pan_deg=0.0,
                                       tilt_deg=-20.0, max_range_m=600.0, with_profile=False)
    dx, dy = _offsets(t)
    d = np.hypot(dx, dy)
    ring = np.abs(d - 100.0) <= 1e-6
    assert ring.sum() >= 12
    in_fov = res.ppm_grid > 0                    # the node's own px/m inside the horizontal cone + range
    assert np.all(in_fov[(d > 1.0 + 1e-6) & (d < 100.0 - 1e-6)])
    assert not np.any(in_fov[d > 100.0 + 1e-6])
    assert 2 * int(in_fov[ring].sum()) == int(ring.sum())               # exactly half of the ring
    assert abs(res.fov_area_m2 / (math.pi * (100.0 ** 2 - 1.0)) - 1.0) < tol


# ── nothing but ties moves ──────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["flat", "hills"])
def test_non_tie_views_are_bit_identical_to_the_closed_rule(kind):
    t = _flat(5.0) if kind == "flat" else generate_procedural_terrain("ridge_and_valley", 61, 5.0)
    cam = _cam(60.0)
    new = _view(t, cam, 37, th=1.8)
    with _closed_rule():
        old = _view(t, cam, 37, th=1.8)
    for k in ("dori_grid", "visibility_mask", "ppm_grid", "slant_dist_grid"):
        assert np.array_equal(getattr(new, k), getattr(old, k)), k
    assert new.fov_area_m2 == old.fov_area_m2


@pytest.mark.parametrize("pan", [0, 45, 90, 180])
def test_aligned_views_change_only_on_the_edge_nodes(pan):
    t = _flat(5.0)
    cam = _cam(90.0)
    new = _view(t, cam, pan)
    with _closed_rule():
        old = _view(t, cam, pan)
    changed = ((new.dori_grid != old.dori_grid) | (new.visibility_mask != old.visibility_mask)
               | (new.ppm_grid != old.ppm_grid))
    dx, dy = _offsets(t)
    az = np.arctan2(dx, dy)
    diff = np.arctan2(np.sin(az - math.radians(pan)), np.cos(az - math.radians(pan)))
    on_edge = np.abs(np.abs(diff) - math.pi / 4) <= 1e-7
    assert changed.any()
    assert np.all(on_edge[changed])
    # never more than the closed rule; the pans whose two edges the old test split by float luck
    # (180: one edge in, one out) keep their area while the nodes alternate instead
    assert new.fov_area_m2 <= old.fov_area_m2


# ── the mount raster follows the same rule ──────────────────────────────────

@pytest.mark.parametrize("pan", [0, 45, 90, 270])
def test_mount_raster_agrees_with_the_engine_on_aligned_views(pan):
    t = _flat(2.5, 400.0)
    cam = _cam(90.0)
    c = _centre(t)
    eng = vs.calculate_3d_viewshed(t, c, c, MAST, cam, lens_mode="min", pan_deg=pan, tilt_deg=TILT,
                                   max_range_m=300.0, with_profile=False)
    mv = mvm.compute_mount_visibility(t, c, c, MAST, max_range_m=300.0)
    vm = mvm.view_mask(mv, cam, pan_deg=pan, tilt_deg=TILT, max_range_m=300.0)
    # the same framed set, node for node, edges included
    assert np.array_equal(eng.dori_grid != 0, vm.dori_grid != 0)
    xor = np.count_nonzero(eng.visibility_mask ^ vm.visibility_mask)
    union = np.count_nonzero(eng.visibility_mask | vm.visibility_mask)
    assert union > 300
    assert 1 - xor / union >= 0.97


# ── the focal sweep follows the same rule ───────────────────────────────────

@pytest.mark.parametrize("pan", [0.0, 90.0, 45.0])
def test_focal_sweep_areas_equal_view_mask_on_tie_views(pan):
    """pan 0 / hfov 90 puts both cone edges on the grid diagonals; the sweep's per-level areas must
    still equal view_mask's node for node (the non-tie test is test_focal_optimizer)."""
    from cctv_simulator import focal_optimizer as fo
    from cctv_simulator.viewshed_3d import ZONE_DETECT
    t = _flat(2.5, 400.0)
    c = _centre(t)
    cam = _cam(90.0)
    rep = fo.sweep_focals(t, c, c, MAST, cam, pan_deg=pan, level=ZONE_DETECT, n_focals=1, tilts=[TILT])
    mv = mvm.mount_visibility(t, c, c, MAST, max_range_m=rep.reach_m)
    b = rep.best
    vm = mvm.view_mask(mv, cam, pan_deg=b.pan_deg, tilt_deg=b.tilt_deg, focal_mm=b.focal_mm,
                       max_range_m=rep.reach_m)
    z = np.where(vm.visibility_mask, vm.dori_grid, 0)
    for lv in fo.LEVELS:
        assert b.areas_m2[lv] == float((z >= lv).sum()) * t.cell_size_m ** 2
    assert b.areas_m2[ZONE_DETECT] > 0
    with _closed_rule():                                  # the closed rule would count the edges whole
        vm_old = mvm.view_mask(mv, cam, pan_deg=b.pan_deg, tilt_deg=b.tilt_deg, focal_mm=b.focal_mm,
                               max_range_m=rep.reach_m)
    assert vm_old.visibility_mask.sum() > vm.visibility_mask.sum()
