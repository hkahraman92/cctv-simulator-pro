"""The single optics resolver every engine reads (Kural 1)."""
from __future__ import annotations

import numpy as np
import pytest

from cctv_simulator.calculations import calculate_for_camera, camera_optics, focal_for_ppm
from cctv_simulator.models import DEFAULT_LEVELS, CameraConfig

CAM = CameraConfig(sensor_name='1/2.8"', resolution_name="2 MP (1080p - 1920x1080)",
                   focal_min_mm=3.2, focal_max_mm=10.5)


def test_matches_the_optic_table_engine():
    r = calculate_for_camera(CAM, "max", DEFAULT_LEVELS)
    o = camera_optics(CAM, CAM.focal_max_mm)
    assert (o.hfov_deg, o.vfov_deg, o.res_w, o.sensor_w_mm) == (
        r.hfov_deg, r.vfov_deg, r.res_width_px, r.sensor_width_mm)
    for row in r.rows:
        assert o.distance_for_ppm(row.ppm) == row.optical_distance_m


def test_vfov_follows_resolution_aspect():
    wide = camera_optics(CAM, 3.2)
    four_three = camera_optics(CameraConfig(sensor_name='1/2.8"', resolution_name="5 MP (2592x1944)"), 3.2)
    assert wide.hfov_deg == four_three.hfov_deg
    assert wide.vfov_deg < four_three.vfov_deg


def test_focal_for_ppm_inverts_ppm_at():
    f = focal_for_ppm(CAM, 125.0, 40.0)
    assert camera_optics(CAM, f).ppm_at(40.0) == pytest.approx(125.0, rel=1e-12)


def test_ppm_at_takes_arrays():
    o = camera_optics(CAM, 6.0)
    d = np.array([10.0, 20.0, 40.0])
    np.testing.assert_allclose(o.ppm_at(d), [o.ppm_at(x) for x in d])


def test_measured_k_scales_resolution_not_fov():
    soft = CameraConfig(sensor_name=CAM.sensor_name, resolution_name=CAM.resolution_name,
                        effective_px_ratio=0.5)
    sharp_o, soft_o = camera_optics(CAM, 6.0), camera_optics(soft, 6.0)
    assert soft_o.res_w == sharp_o.res_w * 0.5
    assert soft_o.hfov_deg == sharp_o.hfov_deg
    # half the pixels -> needs twice the focal for the same density
    assert focal_for_ppm(soft, 125, 30) == pytest.approx(2 * focal_for_ppm(CAM, 125, 30))


def test_unknown_names_fall_back_to_defaults_or_raise_when_strict():
    odd = CameraConfig(sensor_name="bilinmeyen", resolution_name="yok")
    default = CameraConfig()
    assert camera_optics(odd, 4.0) == camera_optics(default, 4.0)
    with pytest.raises(KeyError):
        camera_optics(odd, 4.0, strict=True)
