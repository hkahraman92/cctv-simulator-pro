"""Night scenario: sun / moon light model and the camera's light-limited reach."""
import datetime as dt

import numpy as np
import pytest

from cctv_simulator.night import (
    STARLIGHT_LUX,
    camera_night_mode,
    capped_placements,
    light_condition,
    moon_lux,
    moon_position,
    night_timeline,
    sun_sky_lux,
)
from cctv_simulator.viewshed_3d import CameraPlacement
from cctv_simulator.models import CameraConfig

ANK = (39.92, 32.85)


def test_phase_follows_the_published_lunations():
    # full moon 2024-01-25 17:54 UTC, new moon 2024-01-11 11:57 UTC, first quarter 2024-01-18 03:53 UTC
    assert moon_position(*ANK, dt.datetime(2024, 1, 25, 17, 54))[2] > 0.99
    assert moon_position(*ANK, dt.datetime(2024, 1, 11, 11, 57))[2] < 0.01
    assert moon_position(*ANK, dt.datetime(2024, 1, 18, 3, 53))[2] == pytest.approx(0.5, abs=0.03)


def test_full_moon_rises_opposite_the_setting_sun():
    # the evening of a full moon: sun just set in the west-south-west, moon low in the east-north-east
    c = light_condition(*ANK, dt.datetime(2024, 1, 25, 18, 5), utc_offset_h=3)
    assert -6 < c.sun_el_deg < 2 and 200 < c.sun_az_deg < 260
    assert -3 < c.moon_el_deg < 12 and 50 < c.moon_az_deg < 90


def test_phase_and_fraction_stay_in_range_over_two_months():
    for h in range(0, 24 * 60, 7):
        _az, _el, frac, phase = moon_position(0.0, 0.0, dt.datetime(2025, 3, 1) + dt.timedelta(hours=h))
        assert 0.0 <= frac <= 1.0 and 0.0 <= phase <= 180.0


def test_light_levels_are_the_familiar_ones():
    assert sun_sky_lux(30) > 20_000
    assert 300 < sun_sky_lux(0) < 1500                     # sunset
    assert 1 < sun_sky_lux(-6) < 10                        # end of civil twilight
    assert sun_sky_lux(-12) < 0.02 and sun_sky_lux(-30) == 0.0
    assert 0.1 < moon_lux(60, 0) < 0.32                    # high full moon
    assert moon_lux(60, 90) < moon_lux(60, 0) / 5          # quarter moon is much fainter than half the light
    assert moon_lux(-1, 0) == 0.0


def test_moonless_night_is_starlight_and_clouds_and_glow_count():
    c = light_condition(*ANK, dt.datetime(2024, 1, 11, 23, 0), utc_offset_h=3)
    assert c.period == "gece" and c.lux == pytest.approx(STARLIGHT_LUX, rel=0.05)
    cloudy = light_condition(*ANK, dt.datetime(2024, 1, 11, 23, 0), utc_offset_h=3, cloud_cover=1.0)
    assert cloudy.lux < c.lux
    city = light_condition(*ANK, dt.datetime(2024, 1, 11, 23, 0), utc_offset_h=3, sky_glow_lux=0.05)
    assert city.lux == pytest.approx(c.lux + 0.05)


def test_camera_mode_picks_the_light_source():
    vis = ("1/2.8\"", "Bullet")
    assert camera_night_mode(*vis, 0.01, 40, 0.2).mode == "pasif"
    m = camera_night_mode(*vis, 0.01, 40, 0.001)
    assert m.mode == "ir" and m.range_cap_m == 40
    assert camera_night_mode(*vis, 0.01, 40, 0.001, white_light_range_m=60).range_cap_m == 60
    assert camera_night_mode(*vis, 0.01, 40, 0.001, ir_on=False).mode == "yok"
    assert camera_night_mode("LWIR 640", "Termal", 0.01, 0, 0.0).range_cap_m is None


def test_cap_shortens_reach_and_no_light_removes_the_camera():
    pl = [CameraPlacement(x_m=0, y_m=0, mast_height_m=6, camera=CameraConfig(), max_range_m=120.0)]
    assert capped_placements(pl, camera_night_mode("1/2.8\"", "", 0.01, 40, 0.001))[0].max_range_m == 40
    assert capped_placements(pl, camera_night_mode("1/2.8\"", "", 0.01, 400, 0.001))[0].max_range_m == 120
    assert capped_placements(pl, camera_night_mode("1/2.8\"", "", 0.01, 0, 0.001)) == []
    assert capped_placements(pl, camera_night_mode("1/2.8\"", "", 0.01, 40, 5.0)) == pl


def test_timeline_spans_noon_to_noon_with_a_night_in_it():
    tl = night_timeline(*ANK, dt.date(2024, 6, 21), step_min=60, utc_offset_h=3)
    assert len(tl) == 25 and tl[0].period == "gündüz" and tl[-1].period == "gündüz"
    assert any(c.period == "gece" for c in tl)
    assert np.argmin([c.sun_el_deg for c in tl]) in range(11, 15)          # deepest near local midnight
