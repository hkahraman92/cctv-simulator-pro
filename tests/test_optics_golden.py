"""Optic-engine regression guard.

CLAUDE.md, "Doğrulama alışkanlıkları": a calculations.py refactor must be
bit-identical on a few hundred random CameraConfigs except for the fix it
intends. This freezes that check.

If a diff here is intentional, run `py -3.13 -m tests.gen_optics_golden`,
review the JSON diff, and commit it in the SAME change.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cctv_simulator.calculations import calculate_for_camera, ppm_at_distance
from cctv_simulator.models import DEFAULT_LEVELS

from tests.gen_optics_golden import compute
from tests.optics_cases import build_cases

GOLDEN = Path(__file__).parent / "data" / "optics_golden.json"
TOL = 1e-6


@pytest.fixture(scope="module")
def golden():
    if not GOLDEN.exists():
        pytest.fail("tests/data/optics_golden.json missing — run `py -3.13 -m tests.gen_optics_golden`")
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _diff_scalar(path, a, b, out):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if abs(a - b) > TOL:
            out.append(f"{path}: {a} != {b}")
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")


def test_engine_matches_golden(golden):
    current = compute()
    assert current["seed"] == golden["seed"]
    assert current["n"] == golden["n"]

    diffs: list[str] = []
    for cur_case, gold_case in zip(current["cases"], golden["cases"]):
        name = gold_case["name"]
        for mode in ("min", "max"):
            cm, gm = cur_case["modes"][mode], gold_case["modes"][mode]
            for k in gm:
                if k == "rows":
                    continue
                _diff_scalar(f"{name}/{mode}/{k}", cm[k], gm[k], diffs)
            for i, (cr, gr) in enumerate(zip(cm["rows"], gm["rows"])):
                for k in gr:
                    _diff_scalar(f"{name}/{mode}/rows[{i}]/{k}", cr[k], gr[k], diffs)

    assert not diffs, "optic engine drifted from golden:\n" + "\n".join(diffs[:40])


def test_ppm_monotonic_decreasing_with_distance():
    """Sanity: PPM must fall as the subject moves away."""
    for cam in build_cases()[:40]:
        res = calculate_for_camera(cam, "min", DEFAULT_LEVELS)
        prev = float("inf")
        for d in (5, 10, 25, 50, 100, 200):
            ppm = ppm_at_distance(res, d)
            assert ppm <= prev + TOL, f"{cam.name}: PPM rose from {prev} to {ppm} at {d} m"
            prev = ppm


def test_dead_zone_non_negative_and_finite():
    for cam in build_cases():
        for mode in ("min", "max"):
            res = calculate_for_camera(cam, mode, DEFAULT_LEVELS)
            assert res.dead_zone_m >= 0.0
            assert res.dead_zone_area_m2 >= 0.0
