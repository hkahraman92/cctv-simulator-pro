"""Solar position and camera glare / backlight assessment.

Low-precision NOAA solar-position algorithm (≈0.01° for dates near 2000-2050),
enough to tell whether the low morning / evening sun sits in a camera's frame.

No Tk, no numpy. ``datetime`` in — pass an aware datetime or a naive one that is
already UTC.
"""
from __future__ import annotations

import datetime as _dt
import math
from typing import Optional, Tuple

_GLARE_MARGIN_DEG = 8.0


def sun_position(lat_deg: float, lon_deg: float, when: _dt.datetime) -> Tuple[float, float]:
    """Returns ``(azimuth_deg, elevation_deg)``. Azimuth is clockwise from north."""
    if when.tzinfo is not None:
        when = when.astimezone(_dt.timezone.utc)
    frac_hour = when.hour + when.minute / 60.0 + when.second / 3600.0
    doy = when.timetuple().tm_yday

    gamma = 2.0 * math.pi / 365.0 * (doy - 1 + (frac_hour - 12.0) / 24.0)
    eqtime = 229.18 * (
        0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    )

    time_offset = eqtime + 4.0 * lon_deg          # minutes (when is UTC)
    tst = frac_hour * 60.0 + time_offset          # true solar time, minutes
    ha = math.radians(tst / 4.0 - 180.0)          # hour angle

    lat = math.radians(lat_deg)
    cos_zen = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(ha)
    cos_zen = max(-1.0, min(1.0, cos_zen))
    zenith = math.acos(cos_zen)
    elevation = 90.0 - math.degrees(zenith)

    sin_zen = math.sin(zenith)
    if sin_zen < 1e-6:
        return 180.0, elevation
    cos_az = (math.sin(lat) * cos_zen - math.sin(decl)) / (math.cos(lat) * sin_zen)
    cos_az = max(-1.0, min(1.0, cos_az))
    a = math.degrees(math.acos(cos_az))
    # NOAA convention: afternoon (ha>0) -> a+180, morning -> 540-a, both mod 360.
    az = (a + 180.0) % 360.0 if ha > 0 else (540.0 - a) % 360.0
    return az, elevation


def utc_offset_for_lon(lon_deg: float) -> float:
    """Rough local-time offset from longitude (no DST, no political borders)."""
    return round(lon_deg / 15.0)


def _ang_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def assess_glare(view_azimuth_deg: float, hfov_deg: float,
                 sun_az_deg: float, sun_el_deg: float) -> Tuple[str, str]:
    """Returns ``(level, note)`` with level in {yok, düşük, orta, yüksek}."""
    if sun_el_deg <= -0.5:
        return "yok", "güneş ufkun altında"
    off = _ang_diff(view_azimuth_deg, sun_az_deg)
    half = hfov_deg / 2.0
    if off > half + _GLARE_MARGIN_DEG:
        return "düşük", f"güneş kadraj dışında (Δ{off:.0f}°)"
    if sun_el_deg <= 12.0:
        return "yüksek", f"alçak güneş kadrajda (yükseklik {sun_el_deg:.0f}°) — arkadan ışık / parlama"
    if sun_el_deg <= 28.0 and off <= half:
        return "orta", f"güneş kadraja yakın (yükseklik {sun_el_deg:.0f}°)"
    return "düşük", f"güneş kadrajda ama yüksek (yükseklik {sun_el_deg:.0f}°)"


def worst_glare_over_day(lat_deg: float, lon_deg: float, date: _dt.date,
                         view_azimuth_deg: float, hfov_deg: float,
                         step_min: int = 20,
                         tz_offset_hours: Optional[float] = None
                         ) -> Tuple[str, Optional[_dt.datetime], float, float]:
    """Sweeps a whole local day. Returns ``(worst_level, worst_time, sun_az, sun_el)``.

    ``worst_time`` is a naive datetime in local time (offset from longitude, or
    the given ``tz_offset_hours``). The sweep covers the local calendar day.
    """
    if tz_offset_hours is None:
        tz_offset_hours = utc_offset_for_lon(lon_deg)
    order = {"yok": 0, "düşük": 1, "orta": 2, "yüksek": 3}
    worst = ("yok", None, 0.0, -90.0)
    local0 = _dt.datetime(date.year, date.month, date.day)
    utc0 = local0 - _dt.timedelta(hours=tz_offset_hours)
    steps = int(24 * 60 / step_min)
    for i in range(steps):
        t_utc = utc0.replace(tzinfo=_dt.timezone.utc) + _dt.timedelta(minutes=i * step_min)
        az, el = sun_position(lat_deg, lon_deg, t_utc)
        level, _note = assess_glare(view_azimuth_deg, hfov_deg, az, el)
        if order[level] > order[worst[0]]:
            worst = (level, local0 + _dt.timedelta(minutes=i * step_min), az, el)
    return worst
