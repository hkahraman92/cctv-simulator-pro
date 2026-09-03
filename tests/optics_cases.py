"""Deterministic CameraConfig fixtures for optics regression tests.

The same seeded set is used to (a) generate the committed golden file and
(b) re-check the engine on every run. If you intentionally change the optic
engine, regenerate with:

    py -3.13 -m tests.gen_optics_golden
"""
from __future__ import annotations

import random
from typing import List

from cctv_simulator.config import RESOLUTIONS, SENSOR_DIMS_MM
from cctv_simulator.models import DEFAULT_LEVELS, CameraConfig

# Visible-light sensor/resolution pairs only; thermal PPM bands behave
# differently and are covered separately if needed.
_VIS_SENSORS = ['1/3"', '1/2.8"', '1/2.7"', '1/2"', '1/1.8"', '1/1.2"', '1"']
_VIS_RES = [
    "2 MP (1080p - 1920x1080)",
    "4 MP (2K - 2688x1520)",
    "5 MP (2592x1944)",
    "8 MP (4K - 3840x2160)",
    "12 MP (4000x3000)",
]

SEED = 20260903
N_CASES = 150


def build_cases(n: int = N_CASES, seed: int = SEED) -> List[CameraConfig]:
    rng = random.Random(seed)
    cases: List[CameraConfig] = []
    for i in range(n):
        focal_min = round(rng.uniform(2.0, 12.0), 2)
        focal_max = round(focal_min * rng.uniform(1.0, 6.0), 2)
        cases.append(
            CameraConfig(
                name=f"C{i:03d}",
                sensor_name=rng.choice(_VIS_SENSORS),
                resolution_name=rng.choice(_VIS_RES),
                focal_min_mm=focal_min,
                focal_max_mm=focal_max,
                pole_height_m=round(rng.uniform(2.5, 12.0), 2),
                tilt_deg=round(rng.uniform(0.0, 45.0), 2),
                target_height_m=round(rng.uniform(0.0, 2.0), 2),
                heading_deg=round(rng.uniform(0.0, 359.0), 1),
                ir_range_m=round(rng.uniform(0.0, 120.0), 1),
                min_lux=round(rng.uniform(0.0, 1.0), 3),
            )
        )
    return cases


assert SENSOR_DIMS_MM and RESOLUTIONS and DEFAULT_LEVELS  # import sanity
