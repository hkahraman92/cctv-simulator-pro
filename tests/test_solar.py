"""Solar position + camera glare assessment."""
from __future__ import annotations

import datetime as dt

import pytest

from cctv_simulator.solar import assess_glare, sun_position, worst_glare_over_day


def test_noon_sun_is_due_south_northern_hemisphere():
    # London, Dec solstice, ~solar noon UTC -> due south, low
    az, el = sun_position(51.5, -0.13, dt.datetime(2026, 12, 21, 12, 0, tzinfo=dt.timezone.utc))
    assert abs(az - 180.0) < 4.0
    assert 12.0 < el < 18.0


def test_sun_rises_in_the_east_sets_in_the_west():
    d = dt.date(2026, 6, 21)
    rise_az, rise_el = sun_position(39.93, 32.86, dt.datetime(d.year, d.month, d.day, 3, 30, tzinfo=dt.timezone.utc))
    set_az, set_el = sun_position(39.93, 32.86, dt.datetime(d.year, d.month, d.day, 15, 30, tzinfo=dt.timezone.utc))
    assert 45 < rise_az < 110       # eastern half
    assert 250 < set_az < 315       # western half
    assert rise_el < 25 and set_el < 25


def test_below_horizon_is_no_glare():
    level, _ = assess_glare(90.0, 40.0, 90.0, -5.0)
    assert level == "yok"


def test_low_sun_in_frame_is_high_glare():
    level, note = assess_glare(view_azimuth_deg=90.0, hfov_deg=40.0, sun_az_deg=95.0, sun_el_deg=8.0)
    assert level == "yüksek"
    assert "parlama" in note or "ışık" in note


def test_sun_behind_camera_is_low_glare():
    level, _ = assess_glare(0.0, 40.0, 180.0, 10.0)   # camera north, sun south
    assert level == "düşük"


def test_worst_over_day_flags_east_facing_but_not_north_facing():
    d = dt.date(2026, 6, 21)
    east = worst_glare_over_day(39.93, 32.86, d, view_azimuth_deg=90.0, hfov_deg=45.0)
    north = worst_glare_over_day(39.93, 32.86, d, view_azimuth_deg=0.0, hfov_deg=45.0)
    assert east[0] == "yüksek"
    assert east[1] is not None                       # a time was found
    assert north[0] in ("yok", "düşük")


def test_naive_datetime_treated_as_utc():
    a = sun_position(39.93, 32.86, dt.datetime(2026, 6, 21, 9, 0))
    b = sun_position(39.93, 32.86, dt.datetime(2026, 6, 21, 9, 0, tzinfo=dt.timezone.utc))
    assert a == pytest.approx(b)
