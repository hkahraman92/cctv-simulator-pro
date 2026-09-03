"""cctv_iq slanted-edge MTF core + effective_px_ratio flow into the optic engine."""
from __future__ import annotations

import numpy as np
import pytest

from cctv_simulator.calculations import calculate_for_camera, ppm_at_distance
from cctv_simulator.cctv_iq import MTFResult, slanted_edge_mtf, synthetic_edge
from cctv_simulator.models import DEFAULT_LEVELS, CameraConfig


def test_recovers_edge_angle():
    for ang in (2.0, 5.0, 9.0):
        r = slanted_edge_mtf(synthetic_edge(160, angle_deg=ang, blur_px=1.0))
        assert r.edge_angle_deg == pytest.approx(ang, abs=0.3)


def test_k_falls_monotonically_with_blur():
    ks = [slanted_edge_mtf(synthetic_edge(180, 5.0, b)).k for b in (0.4, 0.8, 1.5, 2.5, 4.0)]
    assert ks == sorted(ks, reverse=True)
    assert ks[0] > ks[-1]
    assert all(0.0 < k <= 1.0 for k in ks)


def test_horizontal_edge_detected():
    r = slanted_edge_mtf(synthetic_edge(160, 5.0, 1.0).T)
    assert r.orientation == "horizontal"


def test_rejects_untilted_edge():
    with pytest.raises(ValueError):
        slanted_edge_mtf(synthetic_edge(120, angle_deg=0.0, blur_px=1.0))


def test_rejects_tiny_roi():
    with pytest.raises(ValueError):
        slanted_edge_mtf(np.zeros((5, 5)))


def test_effective_mp_is_k_squared():
    r = MTFResult(edge_angle_deg=5, mtf50_cy_px=0.25, mtf_at_nyquist=0.1, k=0.5,
                  samples=100, orientation="vertical")
    assert r.effective_mp(8.0) == pytest.approx(2.0)
    assert r.effective_lines(3840) == pytest.approx(1920)


def test_effective_px_ratio_scales_ppm_linearly():
    base = CameraConfig(name="B", effective_px_ratio=1.0)
    soft = CameraConfig(name="S", effective_px_ratio=0.5)
    rb = calculate_for_camera(base, "min", DEFAULT_LEVELS)
    rs = calculate_for_camera(soft, "min", DEFAULT_LEVELS)

    assert rs.effective_px_ratio == 0.5
    assert rs.nominal_res_width_px == rb.nominal_res_width_px
    # half the effective pixels -> half the PPM at any distance
    assert ppm_at_distance(rs, 30.0) == pytest.approx(0.5 * ppm_at_distance(rb, 30.0), rel=1e-9)
    # FOV is optical, must NOT change
    assert rs.hfov_deg == pytest.approx(rb.hfov_deg)


def test_default_ratio_is_identity():
    cam = CameraConfig(name="D")
    res = calculate_for_camera(cam, "min", DEFAULT_LEVELS)
    assert res.effective_px_ratio == 1.0
    assert res.res_width_px == res.nominal_res_width_px
