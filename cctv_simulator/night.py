"""Night scenario: how much light is there, and how far can each camera still see.

DORI ranges are optical: they assume the scene is lit. At night a visible /
near-IR camera either has enough ambient light (moon, sky glow) for its
datasheet minimum illumination, or it sees only as far as its own
illuminator (IR LEDs, or an added white-light lamp) reaches. A thermal
camera does not care. This module answers that with stated, simple models:

- Sun: NOAA position (``solar.sun_position``); horizontal sky illuminance from
  the sun's elevation, a log-interpolated clear-sky table (Bond & Henderson
  style: ~700 lux at sunset, ~3 lux at the end of civil twilight, ~0.008 at
  the end of nautical twilight, starlight + airglow ~0.0007 lux).
- Moon: low-precision lunar theory (main periodic terms; ~0.3 deg), topocentric
  parallax, phase angle -> illuminated fraction; illuminance = 0.32 lux (full
  moon, outside the atmosphere) x phase law (Krisciunas & Schaefer 1991) x
  extinction (0.2 mag / airmass) x sin(altitude).
- Clouds scale the sun + moon parts by (1 - 0.9 x cover); sky glow (light
  pollution) is added as given (rural 0, suburban ~0.01, city ~0.05 lux).

The range cap is applied through the engine's own per-camera range
(``CameraPlacement.max_range_m``), so the night map is the authoritative
viewshed with a shorter reach -- nothing else changes. Not modelled: IR beam
angle narrower than the wide-end FOV, retro-reflection, target albedo.
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .solar import sun_position, utc_offset_for_lon

# log10(lux) of horizontal clear-sky illuminance vs solar elevation (deg)
_SUN_TABLE: Tuple[Tuple[float, float], ...] = (
    (-18.0, -3.15), (-15.0, -2.70), (-12.0, -2.10), (-10.0, -1.15), (-8.0, -0.30), (-6.0, 0.53),
    (-4.0, 1.60), (-2.0, 2.30), (0.0, 2.85), (5.0, 3.60), (10.0, 4.10), (20.0, 4.50), (40.0, 4.85),
    (90.0, 5.05))
STARLIGHT_LUX = 10 ** -3.15
FULL_MOON_LUX = 0.32
SKY_GLOW_PRESETS = {"Kırsal (karanlık gökyüzü)": 0.0, "Banliyö": 0.01, "Şehir": 0.05}


def sun_sky_lux(sun_el_deg: float) -> float:
    """Horizontal illuminance from sun + twilight sky, clear sky (starlight floor excluded)."""
    t = _SUN_TABLE
    if sun_el_deg <= t[0][0]:
        return 0.0
    for (e0, l0), (e1, l1) in zip(t, t[1:]):
        if sun_el_deg <= e1:
            lux = 10 ** (l0 + (l1 - l0) * (sun_el_deg - e0) / (e1 - e0))
            return max(lux - STARLIGHT_LUX, 0.0)
    return 10 ** t[-1][1]


def _jd(utc: _dt.datetime) -> float:
    return utc.timestamp() / 86400.0 + 2440587.5 if utc.tzinfo else \
        (utc - _dt.datetime(1970, 1, 1)).total_seconds() / 86400.0 + 2440587.5


def moon_position(lat_deg: float, lon_deg: float, utc: _dt.datetime) -> Tuple[float, float, float, float]:
    """(azimuth deg from north clockwise, topocentric altitude deg, illuminated
    fraction 0..1, phase angle deg). Naive datetime = UTC."""
    d = _jd(utc) - 2451545.0
    r = math.radians
    Lp = r((218.316 + 13.176396 * d) % 360)
    Mp = r((134.963 + 13.064993 * d) % 360)
    F = r((93.272 + 13.229350 * d) % 360)
    D = r((297.850 + 12.190749 * d) % 360)
    Ms = r((357.529 + 0.98560028 * d) % 360)
    lam = Lp + r(6.289 * math.sin(Mp) + 1.274 * math.sin(2 * D - Mp) + 0.658 * math.sin(2 * D)
                 + 0.214 * math.sin(2 * Mp) - 0.186 * math.sin(Ms) - 0.114 * math.sin(2 * F))
    beta = r(5.128 * math.sin(F) + 0.281 * math.sin(Mp + F) + 0.278 * math.sin(Mp - F)
             + 0.173 * math.sin(2 * D - F))
    eps = r(23.439 - 0.0000004 * d)
    ra = math.atan2(math.sin(lam) * math.cos(eps) - math.tan(beta) * math.sin(eps), math.cos(lam))
    dec = math.asin(math.sin(beta) * math.cos(eps) + math.cos(beta) * math.sin(eps) * math.sin(lam))
    gmst = r((280.46061837 + 360.98564736629 * d) % 360)
    H = gmst + r(lon_deg) - ra
    phi = r(lat_deg)
    alt = math.asin(math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(H))
    az = math.atan2(-math.sin(H), math.tan(dec) * math.cos(phi) - math.sin(phi) * math.cos(H))
    alt_topo = alt - r(0.9507) * math.cos(alt)               # horizontal parallax ~57'
    # elongation from the sun -> phase angle -> illuminated fraction
    lam_s = r(280.460 + 0.9856474 * d) + r(1.915 * math.sin(Ms) + 0.020 * math.sin(2 * Ms))
    elong = math.acos(max(-1.0, min(1.0, math.cos(beta) * math.cos(lam - lam_s))))
    phase = math.pi - elong
    frac = (1 + math.cos(phase)) / 2
    return (math.degrees(az) % 360.0, math.degrees(alt_topo), frac, math.degrees(phase))


def moon_lux(alt_deg: float, phase_deg: float) -> float:
    """Horizontal illuminance of the moon, clear sky."""
    if alt_deg <= 0:
        return 0.0
    a = abs(phase_deg)
    phase_law = 10 ** (-0.4 * (0.026 * a + 4e-9 * a ** 4))
    s = math.sin(math.radians(alt_deg))
    airmass = 1.0 / (s + 0.025 * math.exp(-11.0 * s))
    return FULL_MOON_LUX * phase_law * 10 ** (-0.4 * 0.2 * airmass) * s


@dataclass
class LightCondition:
    when_local: _dt.datetime
    sun_el_deg: float
    sun_az_deg: float
    moon_el_deg: float
    moon_az_deg: float
    moon_fraction: float
    lux: float                  # total horizontal ambient illuminance
    period: str                 # "gündüz" | "alacakaranlık" | "gece"


def light_condition(lat: float, lon: float, when_local: _dt.datetime, *, utc_offset_h: Optional[float] = None,
                    cloud_cover: float = 0.0, sky_glow_lux: float = 0.0) -> LightCondition:
    """Ambient light at a local (naive) time. ``utc_offset_h`` defaults to round(lon / 15)."""
    off = utc_offset_for_lon(lon) if utc_offset_h is None else utc_offset_h
    utc = when_local - _dt.timedelta(hours=off)
    s_az, s_el = sun_position(lat, lon, utc)
    m_az, m_el, frac, phase = moon_position(lat, lon, utc)
    sky = (1.0 - 0.9 * min(max(cloud_cover, 0.0), 1.0))
    lux = (sun_sky_lux(s_el) + moon_lux(m_el, phase)) * sky + STARLIGHT_LUX * sky + max(sky_glow_lux, 0.0)
    period = "gündüz" if s_el > 0 else ("alacakaranlık" if s_el > -6 else "gece")
    return LightCondition(when_local, s_el, s_az, m_el, m_az, frac, lux, period)


@dataclass
class NightMode:
    mode: str                   # "termal" | "pasif" | "ir" | "beyaz" | "yok"
    range_cap_m: Optional[float]   # None = no light cap (optical range stands)
    text: str


def camera_night_mode(sensor_name: str, model_name: str, min_lux: float, ir_range_m: float, lux: float, *,
                      ir_on: bool = True, white_light_range_m: float = 0.0) -> NightMode:
    """What lights the scene for this camera at ``lux`` ambient, and how far."""
    from .atmosphere import band_for_camera
    if band_for_camera(sensor_name, model_name) in ("lwir", "mwir"):
        return NightMode("termal", None, "Termal: ortam ışığından bağımsız")
    if min_lux > 0 and lux >= min_lux:
        return NightMode("pasif", None, f"Ortam ışığı yeterli ({lux:.3g} lux ≥ {min_lux:g} lux)")
    ir = ir_range_m if (ir_on and ir_range_m > 0) else 0.0
    white = max(white_light_range_m, 0.0)
    if white <= 0 and ir <= 0:
        return NightMode("yok", 0.0, f"Ortam ışığı yetersiz ({lux:.3g} lux < {min_lux:g} lux), aydınlatma yok")
    if white >= ir:
        return NightMode("beyaz", white, f"Beyaz ışık: {white:.0f} m (renkli)")
    return NightMode("ir", ir, f"IR aydınlatma: {ir:.0f} m (siyah-beyaz)")


def night_timeline(lat: float, lon: float, date: _dt.date, *, step_min: int = 30, utc_offset_h: Optional[float] = None,
                   cloud_cover: float = 0.0, sky_glow_lux: float = 0.0) -> List[LightCondition]:
    """Light conditions from local noon of ``date`` to noon of the next day."""
    start = _dt.datetime.combine(date, _dt.time(12, 0))
    n = int(24 * 60 / step_min) + 1
    return [light_condition(lat, lon, start + _dt.timedelta(minutes=i * step_min), utc_offset_h=utc_offset_h,
                            cloud_cover=cloud_cover, sky_glow_lux=sky_glow_lux) for i in range(n)]


def capped_placements(placements: Sequence, mode: NightMode) -> list:
    """Placements with the night light cap on their reach (engine applies it)."""
    from dataclasses import replace
    if mode.range_cap_m is None:
        return list(placements)
    if mode.range_cap_m <= 0:
        return []                                       # no light at all: the camera sees nothing
    return [replace(p, max_range_m=min(p.max_range_m, mode.range_cap_m)) for p in placements]
