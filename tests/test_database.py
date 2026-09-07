"""Camera library helpers — PTZ / fixed classification."""
from __future__ import annotations

from cctv_simulator.config import DEFAULT_CAMERA_LIBRARY
from cctv_simulator.database import is_ptz_camera


def test_builtin_aselsan_uma_types():
    lib = DEFAULT_CAMERA_LIBRARY
    t5 = next(v for k, v in lib.items() if "T5" in k)
    fix = next(v for k, v in lib.items() if "Fix" in k)
    assert is_ptz_camera(t5) is True          # "Termal / PTZ Zoom"
    assert is_ptz_camera(fix) is False        # "Termal / Sabit Bullet"


def test_camera_type_keywords():
    assert is_ptz_camera({"camera_type": "PTZ / Speed Dome"}) is True
    assert is_ptz_camera({"camera_type": "Sabit (Bullet)"}) is False
    assert is_ptz_camera({"camera_type": "Varifokal (Dome)"}) is False
    assert is_ptz_camera({"model_name": "Bosch AUTODOME IP 5000i sürekli optik zoom"}) is True


def test_explicit_is_ptz_field_wins():
    assert is_ptz_camera({"is_ptz": True, "camera_type": "Sabit (Bullet)"}) is True
    assert is_ptz_camera({"is_ptz": False, "camera_type": "PTZ / Speed Dome"}) is False
    assert is_ptz_camera({"is_ptz": "evet"}) is True


def test_unknown_and_bad_input():
    assert is_ptz_camera({}) is False
    assert is_ptz_camera(None) is False
    assert is_ptz_camera({"camera_type": ""}) is False
