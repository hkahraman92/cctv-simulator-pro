"""Regenerate tests/data/optics_golden.json from the current optic engine.

Run ONLY when an optic-engine change is intended and reviewed:

    py -3.13 -m tests.gen_optics_golden

Then diff the JSON: only the fields your change touches should move.
"""
from __future__ import annotations

import json
from pathlib import Path

from cctv_simulator.calculations import calculate_for_camera, ppm_at_distance
from cctv_simulator.models import DEFAULT_LEVELS

from tests.optics_cases import N_CASES, SEED, build_cases

GOLDEN = Path(__file__).parent / "data" / "optics_golden.json"


def _row(r):
    return {
        "level": r.level,
        "ppm": r.ppm,
        "optical_distance_m": round(r.optical_distance_m, 6),
        "ground_distance_m": round(r.ground_distance_m, 6),
        "status": r.status,
    }


def compute() -> dict:
    out = {"seed": SEED, "n": N_CASES, "cases": []}
    for cam in build_cases():
        entry = {"name": cam.name, "modes": {}}
        for mode in ("min", "max"):
            res = calculate_for_camera(cam, mode, DEFAULT_LEVELS)
            entry["modes"][mode] = {
                "focal_mm": round(res.focal_mm, 6),
                "hfov_deg": round(res.hfov_deg, 6),
                "vfov_deg": round(res.vfov_deg, 6),
                "dead_zone_m": round(res.dead_zone_m, 6),
                "dead_zone_area_m2": round(res.dead_zone_area_m2, 6),
                "max_geom_dist_m": (
                    None if res.max_geom_dist_m == float("inf") else round(res.max_geom_dist_m, 6)
                ),
                "vertical_drop_m": round(res.vertical_drop_m, 6),
                "ppm_at_25m": round(ppm_at_distance(res, 25.0), 6),
                "ppm_at_75m": round(ppm_at_distance(res, 75.0), 6),
                "rows": [_row(r) for r in res.rows],
            }
        out["cases"].append(entry)
    return out


def main() -> None:
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps(compute(), indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {GOLDEN}  ({GOLDEN.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
