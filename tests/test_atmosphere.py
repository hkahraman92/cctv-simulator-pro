"""Atmospheric attenuation model."""
from __future__ import annotations

import math

import pytest

from cctv_simulator import atmosphere as atm


def test_clear_air_barely_attenuates():
    assert atm.transmittance(100.0, 40.0, "visible") > 0.98


def test_fog_kills_range():
    # 300 m visibility -> at 300 m transmission is ~ exp(-3.912) ~ 0.02
    assert atm.transmittance(300.0, 0.3, "visible") < 0.05


def test_thermal_sees_through_haze_better_than_visible():
    vis = atm.transmittance(500.0, 5.0, "visible")
    lwir = atm.transmittance(500.0, 5.0, "lwir", weather="Pus")
    assert lwir > vis


def test_fog_hits_thermal_too():
    haze = atm.transmittance(400.0, 1.5, "lwir", weather="Hafif pus")
    snow = atm.transmittance(400.0, 1.5, "lwir", weather="Kar")
    assert snow < haze          # droplet regime scatters thermal


def test_usable_range_caps_clear_range():
    capped = atm.usable_range_m(5000.0, 1.0, "lwir", weather="Sis")
    assert capped < 5000.0
    # in genuinely clear air the cap does not bite for a modest range
    assert atm.usable_range_m(400.0, 40.0, "visible") == pytest.approx(400.0)


def test_transmittance_monotonic_in_distance():
    prev = 1.0
    for d in (50, 100, 200, 400, 800):
        t = atm.transmittance(d, 4.0, "visible")
        assert t <= prev
        prev = t


def test_band_selection():
    assert atm.band_for_camera('LWIR 640x512 (12um)') == "lwir"
    assert atm.band_for_camera('1/2.8"', night_ir=True) == "nir"
    assert atm.band_for_camera('1/2.8"') == "visible"


def test_koschmieder_relation():
    # sigma at 1 km visibility -> 3.912e-3 /m, so tau at 1 km ~ 0.02
    assert atm.transmittance(1000.0, 1.0, "visible") == pytest.approx(math.exp(-3.912), rel=1e-6)
