"""Regenerate tests/data/viewshed_golden.npz from the current viewshed engine.

Run ONLY when a viewshed-engine change is intended and reviewed:

    py -3.13 -m tests.gen_viewshed_golden

The golden file freezes the engine's results on ~14 deterministic scenarios
(single views, an obstacle surface, the three planning use cases through
``calculate_multi_camera_viewshed``, a PTZ sweep and a mixed fixed + PTZ
evaluation): zone grids / masks as integers (compared exactly), float grids and
scalars as float64 (compared at rtol / atol 1e-9, np.hypot ULP noise).

Regenerating is legitimate when you changed the engine on purpose (occlusion,
framing, range limits, zone thresholds, a planner's placement rule...). Then
inspect which scenarios and keys moved -- the test names them -- and commit the
new .npz IN THE SAME COMMIT as the engine change, saying why it moved. If it
moves and you did not mean to change results, that is the regression this file
exists to catch: do NOT regenerate, fix the code.

The scenarios are built here, self-contained (not imported from the reference
tests), so editing a test elsewhere cannot silently move the golden values.
"""
from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Callable, Dict

import numpy as np

from cctv_simulator import surface_obstacles as obs
from cctv_simulator import viewshed_3d as _vs
from cctv_simulator.area_planner import plan_area
from cctv_simulator.models import CameraConfig
from cctv_simulator.mount_visibility import clear_mount_cache
from cctv_simulator.perimeter_planner import generate_perimeter_plan
from cctv_simulator.perimeter_ptz import evaluate_mixed_perimeter, plan_placements
from cctv_simulator.ptz_schedule import SectorPatrol, evaluate_sweep
from cctv_simulator.terrain_loader import TerrainData, generate_procedural_terrain
from cctv_simulator.viewshed_3d import (
    CameraPlacement,
    ZONE_OBSERVE,
    calculate_3d_viewshed,
    calculate_multi_camera_viewshed,
    target_height,
)

GOLDEN = Path(__file__).parent / "data" / "viewshed_golden.npz"
SEP = "__"

WIDE = CameraConfig(name="W", sensor_name='1/2.8"', resolution_name="8 MP (4K - 3840x2160)",
                    focal_min_mm=3.6, focal_max_mm=9.0, pole_height_m=6.0)
TELE = CameraConfig(name="T", sensor_name='1/2.8"', resolution_name="8 MP (4K - 3840x2160)",
                    focal_min_mm=6.0, focal_max_mm=30.0, pole_height_m=8.0)
PTZ = CameraConfig(name="P", sensor_name='1/2.8"', resolution_name="2 MP (1080p - 1920x1080)",
                   focal_min_mm=4.3, focal_max_mm=129.0)
THERMAL = CameraConfig(name="Th", model_name="Golden Termal", sensor_name="LWIR 640x512 (12µm)",
                       resolution_name="LWIR (640x512)", focal_min_mm=25.0, focal_max_mm=75.0)

N, CELL = 64, 3.0


# ── terrains ────────────────────────────────────────────────────────────────

def hills(seed: int) -> TerrainData:
    rng = np.random.default_rng(seed)
    side = N * CELL
    yy, xx = np.mgrid[0:N, 0:N] * CELL
    z = np.full((N, N), 50.0)
    for _ in range(5):
        cx, cy = rng.uniform(0, side, 2)
        s = rng.uniform(0.08, 0.25) * side
        z += rng.uniform(8.0, 40.0) * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * s * s))
    z += rng.normal(0, 0.25, z.shape)
    return TerrainData(z_grid=z.astype(np.float32), cell_size_m=CELL)


def walls() -> TerrainData:
    z = np.zeros((N, N))
    z[30, 8:50] = 25.0                                    # one-cell wall along x
    z[10:40, 44] = 18.0                                   # one-cell wall along y
    for i in range(20):                                   # diagonal staircase
        z[8 + i, 8 + i] = 15.0
    return TerrainData(z_grid=z.astype(np.float32), cell_size_m=CELL)


def buildings(base: TerrainData) -> TerrainData:
    rng = np.random.default_rng(7)
    h = np.zeros((N, N))
    kind = np.zeros((N, N), np.uint8)
    for _ in range(10):
        r, c = int(rng.integers(2, N - 8)), int(rng.integers(2, N - 8))
        hh, ww = int(rng.integers(1, 5)), int(rng.integers(1, 5))
        veg = rng.random() < 0.3
        h[r:r + hh, c:c + ww] = rng.uniform(5.0, 9.0) if veg else rng.uniform(6.0, 20.0)
        kind[r:r + hh, c:c + ww] = obs.KIND_VEGETATION if veg else obs.KIND_BUILDING
    h[18:25, 28:35] = 0.0                                 # keep the mast clear
    kind[18:25, 28:35] = 0
    return obs.with_obstacles(base, obs.ObstacleLayer(height_grid=h, kind_grid=kind, counts={"buildings": 1}))


# ── result -> arrays ────────────────────────────────────────────────────────

def _single(res) -> Dict[str, np.ndarray]:
    out = {"dori": res.dori_grid, "vis": res.visibility_mask, "ppm": res.ppm_grid, "slant": res.slant_dist_grid}
    for k in ("cam_total_z_m", "cam_ground_z_m", "hfov_deg", "vfov_deg", "max_range_m", "optical_limit_m",
              "atmospheric_limit_m", "fov_area_m2", "visible_area_m2", "occluded_area_m2", "coverage_pct",
              "max_los_reach_m"):
        out[k] = np.float64(getattr(res, k))
    return out


def _multi(res) -> Dict[str, np.ndarray]:
    out = {"dori": res.dori_grid, "ppm": res.ppm_grid, "vis": res.visibility_mask,
           "best_cam": res.best_cam_grid, "seen": res.seen_count_grid,
           "camera_count": np.int64(res.camera_count), "n_views": np.int64(len(res.per_camera))}
    for k in ("fov_area_m2", "visible_area_m2", "occluded_area_m2", "coverage_pct", "overlap_area_m2",
              "single_cover_area_m2"):
        out[k] = np.float64(getattr(res, k))
    for k, v in sorted(res.pct_by_zone.items()):
        out[f"pct_{k}"] = np.float64(v)
    return out


# ── scenarios ───────────────────────────────────────────────────────────────

def _view(terr, x, y, cam, th=0.0, **kw):
    kw.setdefault("max_range_m", 600.0)
    with target_height(th):
        return _single(calculate_3d_viewshed(terrain=terr, cam_x_m=x, cam_y_m=y, camera=cam,
                                             mast_height_m=kw.pop("mast", 8.0), **kw))


def s_flat_tele():
    t = TerrainData(z_grid=np.zeros((N, N), np.float32), cell_size_m=CELL)
    return _view(t, 96.0, 20.0, TELE, lens_mode="max", pan_deg=0, tilt_deg=-2)


def s_hills_wide_tilt():
    return _view(hills(1), 96.0, 60.0, WIDE, lens_mode="min", pan_deg=30, tilt_deg=-12)


def s_hills_target_height():
    return _view(hills(2), 96.0, 60.0, WIDE, th=1.8, lens_mode="min", pan_deg=200, tilt_deg=-3)


def s_hills_fog_tele():
    return _view(hills(3), 96.0, 60.0, TELE, lens_mode="max", pan_deg=45, tilt_deg=-3,
                 visibility_km=0.5, weather="Sis")


def s_hills_thermal():
    return _view(hills(4), 96.0, 96.0, THERMAL, th=1.8, lens_mode="min", pan_deg=250, tilt_deg=-4,
                 mast=10.0, max_range_m=900.0)


def s_hills_monitor_floor():
    return _view(hills(5), 96.0, 80.0, WIDE, lens_mode="min", pan_deg=45, tilt_deg=-3, monitor_floor=True)


def s_walls_wide():
    return _view(walls(), 96.0, 12.0, WIDE, th=1.8, lens_mode="max", pan_deg=10, tilt_deg=-3, mast=6.0)


def s_obstacles():
    return _view(buildings(hills(6)), 96.0, 63.0, WIDE, lens_mode="max", pan_deg=0, tilt_deg=-4)


FENCE = [(45.0, 45.0), (150.0, 45.0), (150.0, 150.0), (45.0, 150.0)]
BORDER = [(20.0, 50.0), (80.0, 80.0), (140.0, 60.0), (180.0, 100.0)]
AREA = [(60.0, 60.0), (140.0, 60.0), (140.0, 130.0), (60.0, 130.0)]


def s_facility_plan():
    t = hills(11)
    plan = generate_perimeter_plan(t, FENCE, WIDE, target_ppm=40.0, mast_height_m=5.0, is_closed_loop=True)
    out = _multi(calculate_multi_camera_viewshed(t, plan_placements(plan, WIDE)))
    out["gap_count"] = np.int64(len(plan.gaps))
    out["coverage_percentage"] = np.float64(plan.coverage_percentage)
    out["pole_count"] = np.int64(plan.pole_count)
    return out


def s_border_plan():
    t = hills(12)
    plan = generate_perimeter_plan(t, BORDER, WIDE, target_ppm=40.0, mast_height_m=6.0, is_closed_loop=False,
                                   line_mode="border", watch_side="right")
    with target_height(1.8):
        out = _multi(calculate_multi_camera_viewshed(t, plan_placements(plan, WIDE)))
    out["pole_count"] = np.int64(plan.pole_count)
    return out


def s_area_plan():
    t = buildings(hills(13))
    rep = plan_area(t, AREA, WIDE, level=ZONE_OBSERVE, goal_frac=0.8, mast_height_m=6.0, max_sites=30, n_focals=3)
    out = _multi(calculate_multi_camera_viewshed(t, plan_placements(rep.plan, WIDE)))
    out["engine_frac"] = np.float64(rep.engine_frac)
    out["camera_count_plan"] = np.int64(rep.plan.camera_count)
    return out


def s_thermal_mix():
    """Thermal (Johnson classes) + visible cameras merged: each camera's own class counts."""
    t = hills(16)
    pl = [CameraPlacement(50.0, 50.0, 8.0, THERMAL, pan_deg=45, tilt_deg=-2, max_range_m=900.0),
          CameraPlacement(150.0, 50.0, 8.0, THERMAL, pan_deg=315, tilt_deg=-2, max_range_m=900.0),
          CameraPlacement(100.0, 15.0, 6.0, WIDE, pan_deg=0, tilt_deg=-4, max_range_m=200.0, lens_mode="max"),
          CameraPlacement(20.0, 100.0, 6.0, WIDE, pan_deg=90, tilt_deg=-4, max_range_m=200.0, lens_mode="max")]
    return _multi(calculate_multi_camera_viewshed(t, pl))


def s_ptz_sweep():
    clear_mount_cache()
    r = evaluate_sweep(hills(14), 96.0, 96.0, 8.0, PTZ,
                       SectorPatrol(start_pan_deg=300.0, end_pan_deg=60.0, speed_deg_s=20.0, end_dwell_s=2.0,
                                    tilt_deg=-4.0, focal_mm=12.0), max_range_m=400.0)
    out = {"revisit": r.revisit_grid, "look": r.look_grid, "dori": r.dori_grid,
           "seen": r.seen_mask, "glimpse": r.glimpse_mask, "truncated": np.bool_(r.truncated)}
    for k in ("period_s", "focal_mm", "hfov_deg", "vfov_deg", "seen_area_m2", "glimpse_area_m2",
              "continuous_area_m2", "occluded_area_m2", "mean_revisit_s", "worst_revisit_s"):
        out[k] = np.float64(getattr(r, k))
    return out


def s_mixed_perimeter():
    clear_mount_cache()
    t = hills(15)
    plan = generate_perimeter_plan(t, FENCE, WIDE, target_ppm=40.0, mast_height_m=6.0, is_closed_loop=True,
                                   ptz_camera=PTZ, ptz_every_n=3)
    m = evaluate_mixed_perimeter(t, plan, WIDE, PTZ)
    out = {"static_dori": m.static.dori_grid, "static_seen": m.static.seen_count_grid,
           "cue_s": m.cue_s, "d2i_s": m.detect_to_identify_s,
           "n_ptz": np.int64(len(m.sweeps)), "n_poles": np.int64(plan.pole_count)}
    for k in ("cue_target_ppm", "detected_area_m2", "identifiable_area_m2", "median_d2i_s", "p90_d2i_s",
              "worst_d2i_s", "ptz_detect_floor_ppm"):
        out[k] = np.float64(getattr(m, k))
    for lvl in sorted(m.fused.status):
        out[f"fused_status_{lvl}"] = np.asarray(m.fused.status[lvl])
        out[f"fused_revisit_{lvl}"] = np.asarray(m.fused.revisit[lvl], np.float64)
        out[f"fused_cont_{lvl}"] = np.float64(m.fused.continuous_area_m2[lvl])
        out[f"fused_inter_{lvl}"] = np.float64(m.fused.intermittent_area_m2[lvl])
    return out


SCENARIOS: Dict[str, Callable[[], Dict[str, np.ndarray]]] = {
    "single_flat_tele": s_flat_tele,
    "single_hills_wide_tilt": s_hills_wide_tilt,
    "single_hills_target_height": s_hills_target_height,
    "single_hills_fog_tele": s_hills_fog_tele,
    "single_hills_thermal": s_hills_thermal,
    "single_hills_monitor_floor": s_hills_monitor_floor,
    "single_walls_wide": s_walls_wide,
    "single_obstacles": s_obstacles,
    "plan_facility": s_facility_plan,
    "plan_border": s_border_plan,
    "plan_area": s_area_plan,
    "plan_thermal_mix": s_thermal_mix,
    "ptz_sweep": s_ptz_sweep,
    "mixed_perimeter": s_mixed_perimeter,
}


def _freeze(a) -> np.ndarray:
    """Integers / bools stay exact; every float becomes float64."""
    a = np.asarray(a)
    if a.dtype.kind == "f":
        return a.astype(np.float64)
    if a.dtype.kind in "iu":
        return a.astype(np.int64) if a.dtype.itemsize > 4 else a.astype(np.int32)
    return a


@contextlib.contextmanager
def uncached_cell_max():
    """Run the engine without the process-wide cell-maximum cache.

    ``viewshed_3d._cell_max`` caches by ``id(terrain.z_grid)``; a NEW terrain whose
    array lands at the address of a freed one with the same shape reads the old
    terrain's maxima, and the occlusion pre-filter then skips (or keeps) crossings
    wrongly -- a result that depends on what ran before (see
    tests/test_viewshed_reference.py::test_engine_result_does_not_depend_on_a_stale_cell_max_cache).
    Golden and reference runs must be history-independent, so they bypass it.
    """
    orig = getattr(_vs, "_cell_max", None)
    if orig is None:
        yield
        return

    def fresh(terrain):
        getattr(_vs, "_CELL_MAX_CACHE", {}).clear()
        return orig(terrain)

    _vs._cell_max = fresh
    try:
        yield
    finally:
        _vs._cell_max = orig
        getattr(_vs, "_CELL_MAX_CACHE", {}).clear()


def compute_scenario(name: str) -> Dict[str, np.ndarray]:
    with uncached_cell_max():
        return {k: _freeze(v) for k, v in SCENARIOS[name]().items()}


def compute() -> Dict[str, np.ndarray]:
    out: Dict[str, np.ndarray] = {}
    for name in SCENARIOS:
        for k, v in compute_scenario(name).items():
            out[f"{name}{SEP}{k}"] = v
    return out


def main() -> None:
    data = compute()
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(GOLDEN, **data)
    print(f"wrote {GOLDEN} ({GOLDEN.stat().st_size / 1024:.0f} KiB, {len(data)} arrays, {len(SCENARIOS)} scenarios)")


if __name__ == "__main__":
    main()
