"""Frozen viewshed results: recompute the golden scenarios and compare.

tests/data/viewshed_golden.npz is written by ``py -3.13 -m tests.gen_viewshed_golden``
(see that module's docstring for when regenerating is legitimate). Zone grids,
masks and counts must match exactly; float grids and scalars at rtol / atol
1e-9 (np.hypot ULP noise).
"""
from __future__ import annotations

import numpy as np
import pytest

from tests import gen_viewshed_golden as gen

REGEN = "regenerate ONLY if the engine change is intended: py -3.13 -m tests.gen_viewshed_golden (review the diff, commit it with the change)"


@pytest.fixture(scope="module")
def golden():
    assert gen.GOLDEN.exists(), f"{gen.GOLDEN} missing -- {REGEN}"
    with np.load(gen.GOLDEN) as z:
        return {k: z[k] for k in z.files}


def test_golden_covers_exactly_the_scenario_list(golden):
    names = {k.split(gen.SEP)[0] for k in golden}
    assert names == set(gen.SCENARIOS), (
        f"golden scenarios differ from gen_viewshed_golden.SCENARIOS: only in file {sorted(names - set(gen.SCENARIOS))}, "
        f"only in code {sorted(set(gen.SCENARIOS) - names)} -- {REGEN}")


@pytest.mark.parametrize("name", list(gen.SCENARIOS))
def test_scenario_matches_golden(name, golden):
    got = gen.compute_scenario(name)
    want = {k.split(gen.SEP, 1)[1]: v for k, v in golden.items() if k.split(gen.SEP)[0] == name}
    assert set(got) == set(want), (
        f"[{name}] result keys changed: new {sorted(set(got) - set(want))}, gone {sorted(set(want) - set(got))} -- {REGEN}")
    bad = []
    for key in sorted(want):
        g, w = np.asarray(got[key]), np.asarray(want[key])
        if g.shape != w.shape:
            bad.append(f"{key}: shape {g.shape} != golden {w.shape}")
        elif w.dtype.kind in "biu":
            if not np.array_equal(g, w):
                n = int(np.count_nonzero(g != w)) if g.shape else 1
                bad.append(f"{key}: {n} integer/bool values differ (exact compare)")
        else:
            try:
                np.testing.assert_allclose(g, w, rtol=1e-9, atol=1e-9)
            except AssertionError:
                fin = np.isfinite(g) & np.isfinite(w)
                dmax = float(np.max(np.abs(g[fin] - w[fin]))) if fin.any() else float("nan")
                bad.append(f"{key}: floats differ (max |d| {dmax:.3g}, {int(np.count_nonzero(g != w)) if g.shape else 1} values)")
    assert not bad, f"[{name}] differs from the frozen golden -- " + "; ".join(bad) + f". {REGEN}"
