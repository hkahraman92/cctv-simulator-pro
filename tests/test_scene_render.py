"""Camera-eye frame renderer: geometry, degradation and palettes (headless)."""
from __future__ import annotations

import numpy as np
import pytest

from cctv_simulator.models import CameraConfig, DEFAULT_LEVELS
from cctv_simulator.perspective_3d import Perspective3DEngine, generate_dori_ground_polygons
from cctv_simulator.scene_render import _degrade, render_camera_frame
from PIL import Image

pytest.importorskip("PIL")

CAM = CameraConfig(name="T", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                   focal_min_mm=4.0, focal_max_mm=20.0, pole_height_m=4.0, tilt_deg=8.0, ir_range_m=40.0)


def _frame(dist, **kw):
    eng = Perspective3DEngine(CAM, focal_mm=4.0, viewport_size=(480, 300))
    ppm = eng.calculate_ppm_at_distance(dist, target_h_m=1.8)
    dori = generate_dori_ground_polygons(eng, list(DEFAULT_LEVELS))
    img, zoom = render_camera_frame(eng, w=480, h=300, target_dist=dist, ppm=ppm,
                                    dori_polys=dori, **kw)
    return img, zoom, eng


def _hf_energy(img):
    a = np.asarray(img.convert("L"), float)
    return float(np.mean(np.abs(np.diff(a, axis=0))) + np.mean(np.abs(np.diff(a, axis=1))))


def test_frame_size_and_viewport_restored():
    img, _z, eng = _frame(20.0)
    assert img.size == (480, 300)
    assert (eng.viewport_w, eng.viewport_h) == (480, 300)   # restored for the caller's overlays


def test_zoom_grows_with_distance():
    _i1, z_near, _ = _frame(6.0)
    _i2, z_far, _ = _frame(80.0)
    assert z_far > z_near
    assert z_near >= 1.0


def _detail_beyond(img, n=14):
    """How much image content lives at a scale finer than an n-pixel grid.
    A frame that only carries ~n real pixels of detail loses almost nothing."""
    w, h = img.size
    lo = img.resize((n, n), Image.Resampling.BOX).resize((w, h), Image.Resampling.BILINEAR)
    return float(np.mean(np.abs(np.asarray(img, float) - np.asarray(lo, float))))


def _selfmatch(img, n):
    """0 when the image already carries only ~n px of detail per axis."""
    w, h = img.size
    r = img.resize((n, max(int(n * h / w), 2)), Image.Resampling.BOX).resize((w, h), Image.Resampling.NEAREST)
    return float(np.mean(np.abs(np.asarray(img, float) - np.asarray(r, float))))


def test_degrade_reduces_resolution():
    rng = np.random.default_rng(1)
    src = Image.fromarray((rng.random((160, 220, 3)) * 255).astype(np.uint8), "RGB")
    same = _degrade(src, 100, 100)                 # true_px >= screen_px -> untouched
    heavy = _degrade(src, 100, 14)                 # 14 px of real detail over 100
    assert np.array_equal(np.asarray(same), np.asarray(src))
    # heavy already lives at ~14 px, so re-quantising it to 14 barely changes it
    assert _selfmatch(heavy, 14) < 0.30 * _selfmatch(src, 14)


def test_inset_zoom_scales_with_range():
    _n, z_near, _ = _frame(8.0)
    _m, z_mid, _ = _frame(35.0)
    _f, z_far, _ = _frame(90.0)
    assert z_near <= z_mid <= z_far


def test_mtf_k_blurs_the_frame():
    sharp, _z, _ = _frame(25.0, k=1.0, zoom_inset=False)
    soft, _z2, _ = _frame(25.0, k=0.3, zoom_inset=False)
    assert _hf_energy(soft) <= _hf_energy(sharp) + 0.05


def test_palettes_differ():
    day, _z, _ = _frame(20.0, palette="day", zoom_inset=False)
    ir, _z2, _ = _frame(20.0, palette="ir", ir_range_m=40.0, zoom_inset=False)
    iron, _z3, _ = _frame(20.0, palette="thermal_ironbow", zoom_inset=False)
    da, ia, na = (np.asarray(x, float) for x in (day, ir, iron))
    # IR is green-dominant
    assert ia[:, :, 1].mean() > ia[:, :, 0].mean() + 10
    assert not np.allclose(da, na, atol=20)


def test_ir_gain_falls_off_past_illuminator_range():
    inside, _z, _ = _frame(15.0, palette="ir", ir_range_m=40.0, zoom_inset=False)
    outside, _z2, _ = _frame(120.0, palette="ir", ir_range_m=40.0, zoom_inset=False)
    assert np.asarray(outside, float).mean() < np.asarray(inside, float).mean()


def test_fast_mode_still_valid_frame():
    img, _z, _ = _frame(30.0, fast=True)
    assert img.size == (480, 300)
    assert np.asarray(img).std() > 1.0     # not a flat rectangle
