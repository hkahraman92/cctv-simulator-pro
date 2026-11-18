"""Data-uncertainty (sensitivity) analysis: the error model, run 0 = the data as is, classes, refinement.

Assertions are relations, bounds and identities -- never exact engine numbers (the engine's cone tie
rule is being changed independently)."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from cctv_simulator import sensitivity as S
from cctv_simulator import surface_obstacles as obs
from cctv_simulator.models import CameraConfig
from cctv_simulator.terrain_loader import TerrainData, refine_terrain
from cctv_simulator.viewshed_3d import ZONE_DETECT, CameraPlacement, calculate_multi_camera_viewshed

CAM = CameraConfig(resolution_name="8 MP (4K - 3840x2160)", focal_min_mm=12.0, focal_max_mm=12.0)
CELL = 5.0
N = 200
WALL_COL = 44                         # x = 220 m; the camera stands at x = 100 m, y = 500 m, lens 4 m
ROI_ROWS, ROI_COLS = slice(85, 116), slice(24, 64)       # x 120..320 m, y 425..575 m


def _held(mv):
    return mv.visibility_mask


def _scene(wall_m: float, source: int = S.SRC_DEFAULT, *, measured: bool = True):
    """Flat ground and one thin wall across the camera's view."""
    z = np.zeros((N, N), np.float32)
    h = np.zeros((N, N), np.float32)
    kind = np.zeros((N, N), np.uint8)
    h[99:102, WALL_COL] = wall_m
    kind[99:102, WALL_COL] = obs.KIND_BUILDING
    t = TerrainData(z_grid=z + h, cell_size_m=CELL, ground_z_grid=z.copy(), obstacle_grid=h, obstacle_kind=kind,
                    is_measured=measured, source_note="Terrarium (test)")
    return t, np.where(h > 0, source, 0).astype(np.uint8)


def _placements():
    return [CameraPlacement(x_m=100.0, y_m=500.0, mast_height_m=4.0, camera=CAM, lens_mode="min", pan_deg=90.0,
                            tilt_deg=-2.0, max_range_m=500.0)]


def _roi():
    roi = np.zeros((N, N), bool)
    roi[ROI_ROWS, ROI_COLS] = True
    return roi


def _run(t, src=None, **kw):
    kw.setdefault("n_runs", 12)
    return S.coverage_sensitivity(t, _placements(), roi=_roi(), held=_held, source_grid=src, **kw)


# ── building blocks ─────────────────────────────────────────────────────────
def test_label_objects_groups_equal_keys_8_connected():
    k = np.zeros((6, 8), np.int64)
    k[1:3, 1:4] = 1
    k[1:3, 5:7] = 1                    # a separate object of the same key
    k[3, 1] = 1                        # touches the first one (4-neighbour)
    k[4, 0] = 2                        # diagonal to (3,1) but another key: its own object
    lab = S.label_objects(k)
    assert len({int(v) for v in lab[k == 1]}) == 2 and len({int(v) for v in lab[k == 2]}) == 1
    assert lab[1, 1] == lab[3, 1] != lab[1, 5] and lab[4, 0] != lab[3, 1] and not lab[k == 0].any()


def test_correlated_field_is_deterministic_zero_mean_and_correlated():
    a = S.correlated_field((300, 300), 10.0, 4.0, 90.0, np.random.default_rng(3))
    b = S.correlated_field((300, 300), 10.0, 4.0, 90.0, np.random.default_rng(3))
    assert np.array_equal(a, b)
    assert 1.5 < a.std() < 6.0                                   # ~sigma (a finite field: few independent patches)
    assert abs(float(np.corrcoef(a[:, :-1].ravel(), a[:, 1:].ravel())[0, 1])) > 0.95     # neighbours 10 m apart
    c = S.correlated_field((300, 300), 10.0, 4.0, 10.0, np.random.default_rng(3))
    assert abs(float(np.corrcoef(c[:, :-1].ravel(), c[:, 1:].ravel())[0, 1])) < 0.9      # shorter correlation
    assert not S.correlated_field((20, 20), 10.0, 0.0, 90.0, np.random.default_rng(0)).any()


def test_default_assumptions_by_source_and_unmeasured_flag():
    base = dict(z_grid=np.zeros((10, 10), np.float32), cell_size_m=10.0)
    terr = S.default_dem_assumption(TerrainData(is_measured=True, source_note="Yükselti: AWS Terrarium", **base))
    assert (terr.sigma_m, terr.corr_m, terr.measured) == (4.0, 90.0, True)
    oe = S.default_dem_assumption(TerrainData(is_measured=True, source_note="Yükselti: Open-Elevation (SRTM)", **base))
    assert oe.sigma_m > terr.sigma_m and oe.corr_m > terr.corr_m
    loc = S.default_dem_assumption(TerrainData(is_measured=True, source_note="Yerel GeoTIFF DEM (a.tif)", **base),
                                   base_cell_m=5.0)
    assert (loc.sigma_m, loc.corr_m) == (S.LOCAL_DEM_SIGMA_M, 3 * 5.0)
    proc = S.default_dem_assumption(TerrainData(is_measured=False, **base))
    assert proc.measured is False and proc.sigma_m > 0


def test_summary_line_wording():
    line = S.sensitivity_line(0.912, 0.061, 12, {"dem_sigma_m": 4.0, "dem_corr_m": 90.0, "obstacle_scale": 1.0,
                                                  "objects": {"default": 31, "tagged": 7}})
    assert line == ("Veri hassasiyeti: %91,2 kesin, %6,1 veriye bağlı (12 koşu, DEM σ 4 m, korelasyon 90 m; "
                    "bina yükseklikleri: 31 varsayılan, 7 etiketli)")
    assert "ölçülmüş değil" in S.sensitivity_line(0.5, 0.1, 4, {"dem_sigma_m": 3.0, "dem_corr_m": 90.0}, measured=False)


# ── identities ──────────────────────────────────────────────────────────────
def test_no_uncertainty_every_run_equals_run_zero_and_the_plain_engine():
    t, src = _scene(2.0)
    r = _run(t, src, dem_sigma_m=0.0, obstacle_scale=0.0)
    assert len(set(r.run_shares)) == 1 and r.uncertain_share == 0.0
    mv = calculate_multi_camera_viewshed(t, _placements())
    roi = _roi()
    want = float(((mv.visibility_mask) & roi).sum() * CELL ** 2) / float(roi.sum() * CELL ** 2)
    assert r.nominal_share == want                                     # exactly: the same expression
    assert r.certain_share + r.never_share == pytest.approx(1.0)
    assert r.share_min == r.share_max == r.nominal_share


def test_run_zero_is_the_unperturbed_data_bit_for_bit():
    t, src = _scene(2.0)
    r = _run(t, src)
    mv = calculate_multi_camera_viewshed(t, _placements())
    roi = _roi()
    assert r.run_shares[0] == float(((mv.visibility_mask) & roi).sum() * CELL ** 2) / float(roi.sum() * CELL ** 2)
    assert S.CLASS_UNCERTAIN in r.class_grid and r.nominal_share == r.run_shares[0]
    # every cell the data does not shake keeps the nominal verdict: CERTAIN cells are held at nominal, NEVER ones not
    nominal = mv.visibility_mask & _roi()
    assert nominal[r.class_grid == S.CLASS_CERTAIN].all() and not nominal[r.class_grid == S.CLASS_NEVER].any()


def test_shares_add_up_and_range_brackets_the_nominal():
    t, src = _scene(2.0)
    r = _run(t, src)
    assert r.certain_share + r.uncertain_share + r.never_share == pytest.approx(1.0)
    assert r.share_min <= r.share_p10 <= r.share_p90 <= r.share_max
    assert r.share_min <= r.nominal_share <= r.share_max
    assert r.certain_share <= r.nominal_share <= r.certain_share + r.uncertain_share + 1e-12
    assert 0.0 <= r.held_fraction.min() and r.held_fraction.max() <= 1.0
    assert (r.class_grid[~_roi()] == 0).all()


def test_deterministic_per_seed_and_seeds_differ():
    t, src = _scene(2.0)
    a, b, c = _run(t, src, seed=7), _run(t, src, seed=7), _run(t, src, seed=8)
    assert a.run_shares == b.run_shares and np.array_equal(a.class_grid, b.class_grid)
    assert a.run_shares[0] == c.run_shares[0]                          # run 0 is the data, whatever the seed
    assert a.run_shares[1:] != c.run_shares[1:]


# ── behaviour ───────────────────────────────────────────────────────────────
def test_flat_open_ground_stays_nearly_certain_when_the_whole_site_moves():
    """No obstacles: the error field moves camera and ground together; only its SLOPE changes the picture.
    Measured here (12 mm, tilt -2, 4 m mast): uncertain share 0.002 at sigma 1 m, 0.17 at 3 m, 0.26 at 4 m --
    a long lens looking along the ground grazes the slope error. A wide lens (2.8 mm, tilt -10): 0.000 at 1-3 m,
    0.002 at 4 m. Bounds: 2 % for sigma 1 m (tele) and sigma 4 m (wide); sigma 4 m tele is clearly worse."""
    t, _ = _scene(2.0)
    bare = replace(t, z_grid=t.ground_z_grid.copy(), ground_z_grid=None, obstacle_grid=None, obstacle_kind=None)
    small = _run(bare, dem_sigma_m=1.0)
    assert small.uncertain_share <= 0.02 and small.assumptions["objects"] == {}
    assert small.assumptions["dem_sigma_m"] == 1.0
    big = _run(bare, dem_sigma_m=4.0)
    assert big.uncertain_share > small.uncertain_share
    assert big.share_max <= big.nominal_share + 1e-12            # flat ground is the best case: error only hides cells
    wide = CameraConfig(resolution_name="4 MP (2K - 2688x1520)", focal_min_mm=2.8, focal_max_mm=2.8)
    pl = [CameraPlacement(x_m=100.0, y_m=500.0, mast_height_m=6.0, camera=wide, lens_mode="min", pan_deg=90.0,
                          tilt_deg=-10.0, max_range_m=120.0)]
    r = S.coverage_sensitivity(bare, pl, roi=_roi(), held=_held, n_runs=12, dem_sigma_m=4.0)
    assert r.uncertain_share <= 0.02


def _classes_at(r):
    c = r.class_grid
    return {"before": c[100, 30], "shadow_near": c[100, 47], "shadow_far": c[100, 58], "beside": c[108, 60]}


def test_low_wall_makes_the_shadow_uncertain_tall_wall_keeps_it_never_open_cells_certain():
    low = _classes_at(_run(*_scene(2.0), dem_sigma_m=0.0))
    tall = _classes_at(_run(*_scene(40.0), dem_sigma_m=0.0))
    assert low["before"] == S.CLASS_CERTAIN and tall["before"] == S.CLASS_CERTAIN        # in front of the wall
    assert low["beside"] == S.CLASS_CERTAIN and tall["beside"] == S.CLASS_CERTAIN        # off the shadow, in the cone
    assert low["shadow_near"] == S.CLASS_NEVER                         # right behind even the lowest wall draw
    assert low["shadow_far"] == S.CLASS_UNCERTAIN                      # the shadow ends somewhere along 160-480 m
    assert tall["shadow_near"] == tall["shadow_far"] == S.CLASS_NEVER  # a wall above the lens shadows everything
    r_low, r_tall = _run(*_scene(2.0), dem_sigma_m=0.0), _run(*_scene(40.0), dem_sigma_m=0.0)
    assert r_low.uncertain_share > 10 * r_tall.uncertain_share


def test_default_height_buildings_are_more_uncertain_than_tagged_ones():
    default = _run(*_scene(2.0, S.SRC_DEFAULT), dem_sigma_m=0.0)
    tagged = _run(*_scene(2.0, S.SRC_TAGGED), dem_sigma_m=0.0)
    levels = _run(*_scene(2.0, S.SRC_LEVELS), dem_sigma_m=0.0)
    assert default.uncertain_share > tagged.uncertain_share
    assert default.nominal_share == tagged.nominal_share == levels.nominal_share         # the same data
    assert default.assumptions["objects"] == {"tagged": 0, "levels": 0, "default": 1}
    assert tagged.assumptions["objects"]["tagged"] == 1


def test_unknown_source_is_read_as_default_and_obstacle_scale_scales_the_spread():
    t, _ = _scene(2.0)
    unknown = _run(t, None, dem_sigma_m=0.0)
    assert unknown.assumptions["objects"]["default"] == 1 and unknown.assumptions["source_known"] is False
    half = _run(t, None, dem_sigma_m=0.0, obstacle_scale=0.5)
    assert half.uncertain_share < unknown.uncertain_share


def test_measured_surface_cells_get_a_correlated_height_error_not_objects():
    t, src = _scene(3.0, S.SRC_MEASURED)
    r = _run(t, src, dem_sigma_m=0.0)
    assert r.assumptions["measured_cells"] == 3 and r.assumptions["objects"] == {"tagged": 0, "levels": 0, "default": 0}
    assert r.uncertain_share > 0.0 and "ölçülmüş yüzey" in r.summary_line()


def test_level_shares_bracket_their_nominal():
    t, src = _scene(2.0)
    r = S.coverage_sensitivity(t, _placements(), roi=_roi(), level=ZONE_DETECT, source_grid=src, n_runs=6)
    d = r.level_shares[ZONE_DETECT]
    assert d["min"] <= d["nominal"] <= d["max"] and set(r.level_shares) >= {ZONE_DETECT}
    assert r.nominal_share == d["nominal"]                             # default criterion: merged zone >= level


def test_refined_terrain_builds_the_error_on_the_original_grid():
    """The error belongs to the data: the same field (same seed) is refined with ``refine_terrain``'s
    interpolation, so the answer on a 4x finer analysis grid stays close. Measured: |difference of the
    uncertain share| = 0.007 on this scene (0.118 vs 0.111; bound 0.05)."""
    n = 40
    yy, xx = np.mgrid[0:n, 0:n] * 10.0
    z = (12 * np.sin(xx / 60.0) * np.cos(yy / 70.0) + 0.02 * xx).astype(np.float32)
    base = TerrainData(z_grid=z, cell_size_m=10.0, is_measured=True, source_note="Terrarium (test)")
    fine = refine_terrain(base, 4)
    pl = [CameraPlacement(x_m=60.0, y_m=200.0, mast_height_m=6.0, camera=CAM, lens_mode="min", pan_deg=90.0,
                          tilt_deg=-3.0, max_range_m=300.0)]
    roi = np.zeros((n, n), bool)
    roi[8:32, 10:34] = True
    kw = dict(held=_held, n_runs=8, seed=5, dem_sigma_m=4.0, dem_corr_m=90.0)
    a = S.coverage_sensitivity(base, pl, roi=roi, **kw)
    b = S.coverage_sensitivity(fine, pl, roi=np.kron(roi, np.ones((4, 4), bool)), refine_factor=4, **kw)
    assert a.assumptions["refine_factor"] == 1 and b.assumptions["refine_factor"] == 4
    assert b.assumptions["base_cell_m"] == pytest.approx(10.0)
    assert abs(a.uncertain_share - b.uncertain_share) <= 0.05
    assert a.uncertain_share > 0                                       # the scene is not trivially certain
    with pytest.raises(ValueError):                                    # a grid not divisible by the factor is refused
        S.coverage_sensitivity(fine, pl, roi=np.ones(fine.z_grid.shape, bool), refine_factor=7, **kw)


# ── control ────────────────────────────────────────────────────────────────
def test_progress_is_reported_per_run_and_cancel_stops_between_runs():
    t, src = _scene(2.0)
    seen = []
    r = _run(t, src, n_runs=5, progress=lambda d, n, m: seen.append((d, n)))
    assert seen[0] == (0, 5) and seen[-1] == (5, 5) and [d for d, _ in seen] == list(range(6)) and r.n_runs == 5
    calls = []
    with pytest.raises(S.SensitivityCancelled):
        _run(t, src, cancel=lambda: calls.append(1) or len(calls) > 2)
    assert len(calls) == 3


def test_invalid_arguments_are_refused_in_turkish():
    t, src = _scene(2.0)
    for bad in (dict(n_runs=1), dict(n_runs=99), dict(dem_sigma_m=-1.0), dict(dem_corr_m=0.0), dict(obstacle_scale=-1.0)):
        with pytest.raises(ValueError, match="olmalı"):
            _run(t, src, **bad)
    with pytest.raises(ValueError, match="Yerleşim yok"):
        S.coverage_sensitivity(t, [], roi=_roi())
