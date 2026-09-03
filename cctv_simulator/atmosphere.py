"""Atmospheric attenuation for camera range calculations.

Johnson / NATO thermal ranges and optical DORI ranges assume clear air. Fog,
haze, rain and snow scatter and absorb along the path, so the target's apparent
contrast (visible) or apparent ΔT (thermal) falls as ``exp(-σ·R)``.

``σ`` (extinction coefficient, per metre) is derived from the meteorological
visual range with Koschmieder's relation ``σ_vis = 3.912 / V``. The 8–12 µm
(LWIR) and 3–5 µm (MWIR) bands see through haze far better than the visible
band, so a per-band factor scales it. Water fog and heavy rain hit every band.

No Tk, no numpy. Pure functions.
"""
from __future__ import annotations

import math
from typing import Dict

# name -> (visual range km, human label). Ordered clear -> worst.
WEATHER_PRESETS: Dict[str, float] = {
    "Berrak hava": 40.0,
    "Hafif pus": 12.0,
    "Pus": 5.0,
    "Hafif yağmur": 8.0,
    "Orta yağmur": 4.0,
    "Kuvvetli yağmur": 2.0,
    "Kar": 1.5,
    "Sis": 0.8,
    "Yoğun sis": 0.3,
}

# How transparent each band is relative to the visible-band extinction.
# Haze (fine aerosol) barely touches thermal; fog/rain droplets (>~10 µm) do.
_BAND_FACTOR = {
    "visible": 1.00,
    "nir": 0.85,       # 850 nm IR illumination
    "mwir": 0.55,      # 3-5 µm
    "lwir": 0.40,      # 8-12 µm
}

_FOG_BANDS = ("Kar", "Sis", "Yoğun sis", "Kuvvetli yağmur")  # droplet regimes: thermal hit harder too


def band_for_camera(sensor_name: str, model_name: str = "", *, night_ir: bool = False) -> str:
    s = (sensor_name or "").upper()
    m = (model_name or "").upper()
    if "MWIR" in s or "MWIR" in m:
        return "mwir"
    if "LWIR" in s or "TERMAL" in m or "THERMAL" in m:
        return "lwir"
    return "nir" if night_ir else "visible"


def extinction_per_m(visibility_km: float, band: str = "visible", weather: str = "") -> float:
    if visibility_km <= 0.01:
        return 10.0
    sigma_vis = 3.912 / (visibility_km * 1000.0)
    factor = _BAND_FACTOR.get(band, 1.0)
    if band in ("lwir", "mwir") and weather in _FOG_BANDS:
        factor = min(factor * 2.2, 1.0)   # droplets scatter thermal too
    return sigma_vis * factor


def transmittance(distance_m: float, visibility_km: float, band: str = "visible",
                  weather: str = "") -> float:
    """Path transmission in (0, 1]. 1.0 = no loss."""
    if distance_m <= 0:
        return 1.0
    return math.exp(-extinction_per_m(visibility_km, band, weather) * distance_m)


def usable_range_m(clear_range_m: float, visibility_km: float, band: str = "visible",
                   weather: str = "", tau_min: float = 0.10) -> float:
    """Clear-air range capped at the distance where transmission drops to
    ``tau_min`` (~2.3 optical depths by default) — beyond that the target
    contrast is gone regardless of pixel count."""
    sigma = extinction_per_m(visibility_km, band, weather)
    if sigma <= 1e-9:
        return clear_range_m
    return min(clear_range_m, -math.log(tau_min) / sigma)
