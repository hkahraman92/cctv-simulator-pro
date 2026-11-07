"""Varifocal / PTZ optimisation: full focal sweep, envelope, multi-mount layout."""
from __future__ import annotations

import numpy as np
import pytest

from cctv_simulator import focal_optimizer as fo
from cctv_simulator.models import CameraConfig
from cctv_simulator.mount_visibility import mount_visibility, view_mask
from cctv_simulator.terrain_loader import TerrainData
from cctv_simulator.viewshed_3d import ZONE_DETECT, ZONE_IDENT, ZONE_RECOG

CAM = CameraConfig(name="V", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                   focal_min_mm=2.8, focal_max_mm=12.0, pole_height_m=6.0)


def _flat(n=160, cell=5.0):
    return TerrainData(z_grid=np.zeros((n, n), np.float32), cell_size_m=cell)


def _hills(n=160, cell=5.0):
    yy, xx = np.mgrid[0:n, 0:n] * cell
    z = 25.0 * np.exp(-(((xx - 520) ** 2 + (yy - 560) ** 2) / (2 * 45.0 ** 2)))
    return TerrainData(z_grid=z.astype(np.float32), cell_size_m=cell)


def test_focal_steps_span_the_lens_log_spaced():
    f = fo.focal_steps(CAM, 9)
    assert f[0] == CAM.focal_min_mm and f[-1] == CAM.focal_max_mm
    ratios = np.diff(np.log(f))
    assert np.allclose(ratios, ratios[0], rtol=0.05)
    fixed = CameraConfig(focal_min_mm=4.0, focal_max_mm=4.0)
    assert fo.focal_steps(fixed, 9) == [4.0]


@pytest.mark.parametrize("level", [ZONE_DETECT, ZONE_RECOG, ZONE_IDENT])
def test_optimum_is_never_worse_than_either_lens_end(level):
    rep = fo.sweep_focals(_hills(), 400.0, 400.0, 6.0, CAM, pan_deg=45.0, level=level, n_focals=10)
    assert rep.best.score == max(c.score for c in rep.curve)
    assert rep.best.score >= rep.wide.score and rep.best.score >= rep.tele.score
    assert len(rep.curve) == len(fo.focal_steps(CAM, 10))


def test_sweep_areas_match_the_view_mask_exactly():
    terr = _hills()
    rep = fo.sweep_focals(terr, 400.0, 400.0, 6.0, CAM, pan_deg=45.0, level=ZONE_RECOG, n_focals=6)
    mv = mount_visibility(terr, 400.0, 400.0, 6.0, max_range_m=rep.reach_m)
    b = rep.best
    vm = view_mask(mv, CAM, pan_deg=b.pan_deg, tilt_deg=b.tilt_deg, focal_mm=b.focal_mm,
                   max_range_m=rep.reach_m)
    z = np.where(vm.visibility_mask, vm.dori_grid, 0)
    for lv in fo.LEVELS:
        assert b.areas_m2[lv] == pytest.approx(float((z >= lv).sum()) * terr.cell_size_m ** 2)


def test_tele_wins_identification_area_on_open_ground():
    # On flat open ground the identification area grows with focal length
    # (range ~ f, angle ~ 1/f, area ~ f): the optimum must not sit at the wide end.
    rep = fo.sweep_focals(_flat(), 400.0, 100.0, 6.0, CAM, pan_deg=0.0, level=ZONE_IDENT, n_focals=8)
    assert rep.best.focal_mm > CAM.focal_min_mm
    assert rep.best.areas_m2[ZONE_IDENT] > rep.wide.areas_m2[ZONE_IDENT]


def test_pan_search_finds_the_open_side_of_a_hill():
    terr = _hills()
    fixed = fo.sweep_focals(terr, 520.0, 470.0, 4.0, CAM, pan_deg=0.0, level=ZONE_DETECT, n_focals=5)
    free = fo.sweep_focals(terr, 520.0, 470.0, 4.0, CAM, pan_deg=0.0, pan_search_deg=90.0,
                           level=ZONE_DETECT, n_focals=5)
    assert free.best.score >= fixed.best.score
    assert free.best.pan_deg != 0.0                    # looking into the hill is not best


def test_envelope_covers_every_single_view():
    terr = _hills()
    env = fo.envelope(terr, 400.0, 400.0, 6.0, CAM, pan_deg=45.0, n_focals=6)
    rep = fo.sweep_focals(terr, 400.0, 400.0, 6.0, CAM, pan_deg=45.0, level=ZONE_RECOG, n_focals=6)
    mv = mount_visibility(terr, 400.0, 400.0, 6.0, max_range_m=rep.reach_m)
    for c in rep.curve:
        vm = view_mask(mv, CAM, pan_deg=c.pan_deg, tilt_deg=c.tilt_deg, focal_mm=c.focal_mm,
                       max_range_m=rep.reach_m)
        z = np.where(vm.visibility_mask, vm.dori_grid, 0)
        assert np.all(env >= z)
    ptz = fo.envelope(terr, 400.0, 400.0, 6.0, CAM, pan_deg=None, n_focals=6)
    assert np.all(ptz >= env) and (ptz > 0).sum() > (env > 0).sum()


def test_layout_never_scores_below_the_start_and_fixes_a_bad_start():
    terr = _flat(n=160, cell=5.0)
    # four poles on a line, all started at full tele looking along the fence
    mounts = [fo.Mount(x, 400.0, 6.0, CAM, pan_deg=90.0, tilt_deg=-1.0, focal_mm=12.0, label=f"D{i}")
              for i, x in enumerate((100.0, 220.0, 340.0, 460.0))]
    roi = fo.fence_roi(terr, [(80.0, 400.0), (600.0, 400.0)], closed=False, band_m=30.0)
    res = fo.optimize_layout(terr, mounts, roi=roi, level=ZONE_DETECT, objective="area", n_focals=6)
    assert res.after_m2[ZONE_DETECT] >= res.before_m2[ZONE_DETECT]
    assert res.after_m2[ZONE_DETECT] > res.before_m2[ZONE_DETECT]       # the bad start gets better
    assert any(c.changed for c in res.choices)
    assert res.after_m2[ZONE_DETECT] <= res.roi_area_m2 + 1e-6


def test_ptz_counts_as_on_demand_reach_on_top_of_fixed():
    terr = _flat(n=120, cell=5.0)
    mounts = [fo.Mount(150.0, 300.0, 6.0, CAM, pan_deg=90.0, focal_mm=4.0, label="S"),
              fo.Mount(450.0, 300.0, 8.0, CAM, ptz=True, label="P")]
    res = fo.optimize_layout(terr, mounts, level=ZONE_IDENT, objective="balanced", n_focals=5)
    for lv in fo.LEVELS:
        assert res.combined_m2[lv] >= res.after_m2[lv] - 1e-6
        assert res.combined_m2[lv] >= res.ptz_on_demand_m2[lv] - 1e-6
    assert res.ptz_on_demand_m2[ZONE_IDENT] > 0 and res.notes


def test_fence_roi_is_a_band_around_the_line():
    terr = _flat(n=100, cell=5.0)
    roi = fo.fence_roi(terr, [(100.0, 250.0), (400.0, 250.0)], closed=False, band_m=20.0)
    assert roi[50, 40] and roi[53, 40]                     # on and next to the fence (y = 250)
    assert not roi[60, 40] and not roi[50, 5]              # 50 m away / past the end
