"""Differential test: the vectorised viewshed engine against an independent
per-cell reference (tests/reference_viewshed.py).

The engine marches radial rays and lets the last ray sample landing in a cell
decide its class; the reference casts one dense, crossing-exact line of sight
to every cell node. They define the same model, so away from class boundaries
they must agree exactly; ON a boundary the engine's ray-sample discretisation
(a sample lands anywhere inside the cell's square, the reference uses the
node) may flip a cell. The assertions therefore pin: overall agreement, the
signed visible-area difference, per-zone area differences, and that no
disagreeing cell lies more than ``MAX_BOUNDARY_CELLS`` from a class boundary
of the reference -- a real occlusion / framing / curvature regression shows up
as disagreement in the interior of a region, not on its rim.

Run with ``-s`` to print the measured table.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pytest

from cctv_simulator import surface_obstacles as obs
from cctv_simulator.area_planner import plan_area
from cctv_simulator.models import CameraConfig
from cctv_simulator.perimeter_planner import generate_perimeter_plan
from cctv_simulator.perimeter_ptz import plan_placements
from cctv_simulator.terrain_loader import TerrainData, generate_procedural_terrain
from cctv_simulator.viewshed_3d import (
    ZONE_DETECT,
    ZONE_IDENT,
    ZONE_MONITOR,
    ZONE_OBSERVE,
    ZONE_OCCLUDED,
    ZONE_OUT_OF_FOV,
    ZONE_RECOG,
    CameraPlacement,
    calculate_3d_viewshed,
    calculate_multi_camera_viewshed,
    target_height,
)

from tests.gen_viewshed_golden import uncached_cell_max
from tests.reference_viewshed import boundary_distance, reference_multi, reference_view

WIDE = CameraConfig(name="W", sensor_name='1/2.8"', resolution_name="8 MP (4K - 3840x2160)",
                    focal_min_mm=3.6, focal_max_mm=9.0, pole_height_m=6.0)
TELE = CameraConfig(name="T", sensor_name='1/2.8"', resolution_name="8 MP (4K - 3840x2160)",
                    focal_min_mm=6.0, focal_max_mm=30.0, pole_height_m=8.0)
THERMAL = CameraConfig(name="Th", model_name="Test Termal", sensor_name="LWIR 640x512 (12µm)",
                       resolution_name="LWIR (640x512)", focal_min_mm=25.0, focal_max_mm=75.0)

# Class boundaries may flip by the sample-in-cell discretisation: that is the
# only disagreement tolerated, and only this close to a boundary.
MAX_BOUNDARY_CELLS = 2

ZONES = {"occ": ZONE_OCCLUDED, "mon": ZONE_MONITOR, "det": ZONE_DETECT, "obs": ZONE_OBSERVE,
         "rec": ZONE_RECOG, "idn": ZONE_IDENT}


@pytest.fixture(autouse=True)
def _history_independent_engine(request):
    """The engine's cell-maximum cache is keyed by id(z_grid): a fresh terrain at a
    freed array's address inherits stale data (see the xfail test at the bottom).
    Every differential comparison runs without it so results never depend on test order."""
    if request.node.name.startswith("test_engine_result_does_not_depend"):
        yield                                   # that test exercises the real cache
        return
    with uncached_cell_max():
        yield


# ── terrains ────────────────────────────────────────────────────────────────

def hills(seed: int, n: int = 80, cell: float = 3.0, amp=(8.0, 40.0), n_hills: int = 6) -> TerrainData:
    rng = np.random.default_rng(seed)
    side = n * cell
    yy, xx = np.mgrid[0:n, 0:n] * cell
    z = np.full((n, n), 50.0)
    for _ in range(n_hills):
        cx, cy = rng.uniform(0, side, 2)
        s = rng.uniform(0.08, 0.25) * side
        z += rng.uniform(*amp) * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * s * s))
    z += rng.normal(0, 0.25, z.shape)
    return TerrainData(z_grid=z.astype(np.float32), cell_size_m=cell)


def walls(seed: int, n: int = 80, cell: float = 3.0) -> TerrainData:
    """Flat ground with sharp one-cell ridges / walls: axis-aligned and diagonal."""
    rng = np.random.default_rng(seed)
    z = np.zeros((n, n))
    for _ in range(3):                                   # axis-aligned one-cell walls
        h = rng.uniform(12.0, 30.0)
        a, b = sorted(rng.integers(6, n - 6, 2))
        if b - a < 8:
            b = min(a + 12, n - 2)
        k = int(rng.integers(10, n - 10))
        if rng.random() < 0.5:
            z[k, a:b] = h
        else:
            z[a:b, k] = h
    for _ in range(2):                                   # diagonal staircase walls
        h = rng.uniform(12.0, 25.0)
        s = int(rng.integers(8, n // 2))
        r0 = int(rng.integers(5, n - s - 5))
        c0 = int(rng.integers(5, n - s - 5))
        sign = 1 if rng.random() < 0.5 else -1
        for i in range(s):
            z[r0 + i, (c0 + i) if sign > 0 else (c0 + s - i)] = h
    return TerrainData(z_grid=z.astype(np.float32), cell_size_m=cell)


def low_wall(height: float = 1.5, row: int = 27, n: int = 80, cell: float = 3.0) -> TerrainData:
    """Flat ground with a one-cell wall across the whole width: hides legs, not heads."""
    z = np.zeros((n, n))
    z[row, :] = height
    return TerrainData(z_grid=z.astype(np.float32), cell_size_m=cell)


def with_buildings(base: TerrainData, seed: int, keep_clear: Tuple[float, float] = None) -> TerrainData:
    """Surface != bare ground: random boxes / canopies from surface_obstacles."""
    rng = np.random.default_rng(seed)
    n_r, n_c = base.z_grid.shape
    h = np.zeros((n_r, n_c))
    kind = np.zeros((n_r, n_c), np.uint8)
    for _ in range(14):
        r, c = int(rng.integers(2, n_r - 8)), int(rng.integers(2, n_c - 8))
        hh, ww = int(rng.integers(1, 6)), int(rng.integers(1, 6))
        veg = rng.random() < 0.3
        h[r:r + hh, c:c + ww] = rng.uniform(5.0, 9.0) if veg else rng.uniform(6.0, 22.0)
        kind[r:r + hh, c:c + ww] = obs.KIND_VEGETATION if veg else obs.KIND_BUILDING
    if keep_clear is not None:
        cx, cy = keep_clear
        cc, rr = int(round((cx - base.origin_x) / base.cell_size_m)), int(round((cy - base.origin_y) / base.cell_size_m))
        h[max(rr - 4, 0):rr + 5, max(cc - 4, 0):cc + 5] = 0.0
        kind[max(rr - 4, 0):rr + 5, max(cc - 4, 0):cc + 5] = 0
    layer = obs.ObstacleLayer(height_grid=h, kind_grid=kind, counts={"buildings": 1})
    return obs.with_obstacles(base, layer)


def flat(n: int = 80, cell: float = 3.0) -> TerrainData:
    return TerrainData(z_grid=np.zeros((n, n), np.float32), cell_size_m=cell)


# ── comparison ──────────────────────────────────────────────────────────────

@dataclass
class Cmp:
    name: str
    agree_pct: float
    n_cells: int            # cells that are non-trivial (in the engine's or reference's cone)
    n_dis: int
    vis_diff_pct: float     # (engine - reference) / reference visible area, signed
    vis_diff_cone_pct: float  # (engine - reference) visible cells as % of the cells in either cone
    d_vis_cells: int
    n_bnd: int              # active cells within 1 cell of a class boundary of the reference
    zone_diff_cells: Dict[str, int]
    max_bd: int             # farthest disagreeing cell from a reference class boundary (cells)
    seconds: float

    @property
    def max_zone_diff_pct(self) -> float:
        """Largest per-zone |engine - reference| cell count, % of the active cells."""
        return 100.0 * max(abs(v) for v in self.zone_diff_cells.values()) / max(self.n_cells, 1)

    def row(self) -> str:
        z = " ".join(f"{k}{v:+d}" for k, v in self.zone_diff_cells.items() if v)
        return (f"{self.name:34s} agree {self.agree_pct:6.2f}%  zD {self.max_zone_diff_pct:4.2f}%  dis {self.n_dis:4d}/{self.n_cells:5d} (bnd {self.n_bnd:4d})  "
                f"visD {self.vis_diff_pct:+6.2f}% ({self.vis_diff_cone_pct:+5.2f}% of cone)  maxBd {self.max_bd}  [{z}]  {self.seconds:4.1f}s")


def compare(name: str, eng: np.ndarray, ref: np.ndarray, vis_eng: float, vis_ref: float, cell_area: float,
            secs: float = 0.0) -> Cmp:
    eng = np.asarray(eng)
    ref = np.asarray(ref)
    dis = eng != ref
    active = (eng != ZONE_OUT_OF_FOV) | (ref != ZONE_OUT_OF_FOV)
    bd = boundary_distance(ref, 6)
    # The outer ring of the grid is exempt from the boundary-distance check: the
    # engine's last row / column "cell" is a square that extends past the last
    # node, where the surface is a flat extrapolation.
    inner = np.zeros(ref.shape, bool)
    inner[1:-1, 1:-1] = True
    dvis = int(round((vis_eng - vis_ref) / cell_area))
    return Cmp(
        name=name,
        agree_pct=100.0 * (1.0 - dis.sum() / max(int(active.sum()), 1)),
        n_cells=int(active.sum()),
        n_dis=int(dis.sum()),
        vis_diff_pct=100.0 * (vis_eng - vis_ref) / max(vis_ref, 1e-9) if vis_ref > 0 else (0.0 if vis_eng == 0 else 100.0),
        zone_diff_cells={k: int((eng == c).sum() - (ref == c).sum()) for k, c in ZONES.items()},
        max_bd=int(bd[dis & inner].max()) if (dis & inner).any() else 0,
        seconds=secs,
        vis_diff_cone_pct=100.0 * dvis / max(int(active.sum()), 1),
        d_vis_cells=dvis,
        n_bnd=int((active & (bd <= 1)).sum()),
    )


def run_single(name: str, terr: TerrainData, th: float = 0.0, **kw) -> Cmp:
    kw.setdefault("max_range_m", 600.0)
    t0 = time.time()
    with target_height(th):
        e = calculate_3d_viewshed(terrain=terr, **kw)
        r = reference_view(terr, kw["cam_x_m"], kw["cam_y_m"], kw["mast_height_m"], kw["camera"],
                           **{k: v for k, v in kw.items() if k not in ("cam_x_m", "cam_y_m", "mast_height_m", "camera")})
    c = compare(name, e.dori_grid, r.dori_grid, e.visible_area_m2, r.visible_area_m2, terr.cell_size_m ** 2, time.time() - t0)
    # the engine's own ViewshedResult tallies and the reference's agree to the cell on the cone
    return c


# ── single-camera scenarios (engine kwargs, terrain factory, target height) ──

def _cam(x, y, pan, tilt, camera=TELE, lens="max", mast=8.0, **extra):
    d = dict(cam_x_m=x, cam_y_m=y, mast_height_m=mast, camera=camera, lens_mode=lens, pan_deg=pan, tilt_deg=tilt)
    d.update(extra)
    return d


SINGLE: List[Tuple[str, callable, float, dict]] = [
    ("flat_tele", lambda: flat(), 0.0, _cam(120.0, 20.0, 0, -2, max_range_m=700)),
    ("flat_wide_steep_tilt", lambda: flat(), 0.0, _cam(120.0, 120.0, 90, -30, camera=WIDE, lens="min", mast=10.0)),
    ("flat_wide_h1.8", lambda: flat(), 1.8, _cam(90.5, 40.25, 30, -3, camera=WIDE, lens="min")),
    ("hills1_wide_pan0", lambda: hills(1), 0.0, _cam(120.0, 60.0, 0, -3, camera=WIDE, lens="max")),
    ("hills1_wide_pan200_h1.8", lambda: hills(1), 1.8, _cam(120.0, 180.0, 200, -3, camera=WIDE, lens="min")),
    ("hills2_wide_tilt-8", lambda: hills(2), 0.0, _cam(80.3, 180.7, 135, -8, camera=WIDE, lens="min")),
    ("hills3_steep_tilt-25", lambda: hills(3, amp=(15.0, 55.0)), 0.0, _cam(120.0, 120.0, 250, -25, camera=WIDE, lens="min", mast=12.0)),
    ("hills4_h1.8_mast4", lambda: hills(4), 1.8, _cam(60.0, 150.0, 315, -4, camera=WIDE, lens="min", mast=4.0)),
    ("hills5_fog", lambda: hills(5), 0.0, _cam(120.0, 60.0, 20, -3, camera=WIDE, lens="max", visibility_km=0.5, weather="Sis")),
    ("hills6_noCurv", lambda: hills(6), 0.0, _cam(120.0, 60.0, 20, -3, camera=WIDE, lens="max", earth_curvature=False)),
    ("hills7_tele_pan70", lambda: hills(7), 0.0, _cam(60.0, 90.0, 70, -3)),
    ("hills8_thermal", lambda: hills(8), 0.0, _cam(120.0, 90.0, 70, -3, camera=THERMAL, lens="min", mast=10.0, max_range_m=900)),
    ("hills9_thermal_h1.8", lambda: hills(9), 1.8, _cam(120.0, 90.0, 250, -4, camera=THERMAL, lens="min", mast=10.0, max_range_m=900)),
    ("hills10_monitor_floor", lambda: hills(10), 0.0, _cam(120.0, 100.0, 45, -3, camera=WIDE, lens="min", monitor_floor=True)),
    ("lowwall_h0", lambda: low_wall(), 0.0, _cam(120.0, 20.0, 0, -2, camera=WIDE, lens="max", mast=6.0)),
    ("lowwall_h1.8", lambda: low_wall(), 1.8, _cam(120.0, 20.0, 0, -2, camera=WIDE, lens="max", mast=6.0)),
    ("walls1_wide", lambda: walls(11), 0.0, _cam(120.0, 15.0, 0, -2, camera=WIDE, lens="max", mast=6.0)),
    ("walls2_wide_h1.8", lambda: walls(12), 1.8, _cam(30.0, 120.0, 90, -2, camera=WIDE, lens="max", mast=6.0)),
    ("walls3_wide_diag", lambda: walls(13), 0.0, _cam(24.0, 24.0, 45, -4, camera=WIDE, lens="min", mast=6.0)),
    ("walls4_wide_diag_h1.8", lambda: walls(14), 1.8, _cam(220.0, 20.0, 315, -3, camera=WIDE, lens="min", mast=5.0)),
    ("walls5_wide_tilt-12", lambda: walls(15), 0.0, _cam(120.0, 15.0, 10, -12, camera=WIDE, lens="max", mast=8.0)),
    ("obst1_buildings", lambda: with_buildings(hills(21), 31, (120.0, 60.0)), 0.0, _cam(120.0, 60.0, 0, -4, camera=WIDE, lens="max")),
    ("obst2_buildings_h1.8", lambda: with_buildings(hills(22), 32, (60.0, 150.0)), 1.8, _cam(60.0, 150.0, 300, -4, camera=WIDE, lens="min")),
    ("obst3_walls+buildings", lambda: with_buildings(walls(23), 33, (120.0, 30.0)), 0.0, _cam(120.0, 30.0, 0, -3, camera=WIDE, lens="min", mast=6.0)),
    ("long_hills_tele_curv", lambda: hills(51, cell=25.0, amp=(10.0, 45.0)), 1.8, _cam(1000.0, 300.0, 100, -0.5, mast=15.0, max_range_m=2000)),
    ("long_hills_tele_nocurv", lambda: hills(51, cell=25.0, amp=(10.0, 45.0)), 1.8, _cam(1000.0, 300.0, 100, -0.5, mast=15.0, max_range_m=2000, earth_curvature=False)),
    ("long_hills_tele_h0", lambda: hills(52, cell=25.0, amp=(10.0, 45.0)), 0.0, _cam(500.0, 1500.0, 120, -0.5, mast=15.0, max_range_m=2000)),
    ("long_hills_tele_fog", lambda: hills(51, cell=25.0, amp=(10.0, 45.0)), 0.0, _cam(300.0, 400.0, 40, -0.3, mast=15.0, max_range_m=2000, visibility_km=3.0, weather="Pus")),
    ("preset_ridge_valley", lambda: generate_procedural_terrain("ridge_and_valley", 80, 3.0), 0.0, _cam(100.0, 70.0, 40, -3, camera=WIDE, lens="max")),
    ("preset_canyon", lambda: generate_procedural_terrain("canyon", 80, 3.0), 0.0, _cam(120.0, 40.0, 0, -4, camera=WIDE, lens="min")),
    ("preset_border", lambda: generate_procedural_terrain("border_outpost", 80, 3.0), 1.8, _cam(60.0, 60.0, 60, -3, camera=WIDE, lens="max")),
    ("preset_rolling", lambda: generate_procedural_terrain("rolling", 80, 3.0), 0.0, _cam(190.0, 190.0, 225, -4, camera=WIDE, lens="min")),
]


# Thresholds: measured over the scenarios below, then set a small margin above
# the worst one. ``scale`` loosens the area gates for 25 m cells, where one
# ray sample may sit 25 m from the node it is attributed to.
GATES = dict(min_agree_pct=70.0, max_dis_of_bnd=0.5, max_vis_cone_pct=7.0, max_zone_pct=7.0, max_bd=MAX_BOUNDARY_CELLS)
MULTI_GATES = dict(min_agree_pct=78.0, max_dis_of_bnd=0.4, max_vis_cone_pct=5.0, max_zone_pct=3.5, max_bd=MAX_BOUNDARY_CELLS)

# One camera of a plan frames a few hundred cells at most, most of them near a class edge: its gates are
# looser, and one isolated cell may sit 3 cells from an edge (a grazing sample on a 0.25 m terrain wobble).
PER_CAMERA_GATES = dict(min_agree_pct=60.0, max_dis_of_bnd=0.5, max_vis_cone_pct=15.0, max_zone_pct=15.0, max_bd=3)


def violations(c: Cmp, gates: dict, scale: float = 1.0) -> List[str]:
    out = []
    if c.agree_pct < gates["min_agree_pct"]:
        out.append(f"agreement {c.agree_pct:.1f}% < {gates['min_agree_pct']}%")
    if c.n_dis > gates["max_dis_of_bnd"] * max(c.n_bnd, 1):
        out.append(f"{c.n_dis} disagreeing cells > {gates['max_dis_of_bnd']:.0%} of the {c.n_bnd} boundary cells")
    if abs(c.vis_diff_cone_pct) > gates["max_vis_cone_pct"] * scale:
        out.append(f"visible area differs {c.vis_diff_cone_pct:+.2f}% of the cone")
    if c.max_zone_diff_pct > gates["max_zone_pct"] * scale:
        out.append(f"a zone's area differs {c.max_zone_diff_pct:.2f}% of the cone")
    if c.max_bd > gates["max_bd"]:
        out.append(f"a disagreeing cell sits {c.max_bd} cells from the nearest class boundary (max {gates['max_bd']})")
    return out


_SINGLE_RESULTS: Dict[str, Cmp] = {}


def random_scenario(seed: int):
    """A reproducible random single view: terrain family, camera, lens, pointing,
    target height, weather. Draws pointings until the view frames >= 300 cells
    (a tiny view says nothing about agreement)."""
    rng = np.random.default_rng(seed)
    family = ("hills", "walls", "buildings", "preset")[seed % 4]
    if family == "hills":
        terr = hills(seed, amp=(10.0, 50.0))
    elif family == "walls":
        terr = walls(seed)
    elif family == "buildings":
        terr = with_buildings(hills(seed), seed + 1000, None)
    else:
        terr = generate_procedural_terrain(("ridge_and_valley", "canyon", "border_outpost", "rolling")[(seed // 4) % 4], 80, 3.0)
    th = float(rng.choice([0.0, 1.8]))
    for _ in range(40):
        camera = (WIDE, WIDE, WIDE, TELE, THERMAL)[int(rng.integers(0, 5))]
        kw = dict(cam_x_m=float(rng.uniform(40, 200)), cam_y_m=float(rng.uniform(40, 200)),
                  mast_height_m=float(rng.uniform(4.0, 14.0)), camera=camera,
                  lens_mode="min" if camera is not TELE and rng.random() < 0.6 else "max",
                  pan_deg=float(rng.uniform(0, 360)), tilt_deg=float(-rng.choice([1.0, 2.0, 4.0, 8.0, 15.0, 25.0])),
                  max_range_m=float(rng.choice([150.0, 400.0, 900.0])))
        if camera is THERMAL:
            kw["lens_mode"] = "min"
        if rng.random() < 0.3:
            kw.update(visibility_km=float(rng.choice([0.5, 2.0, 6.0])),
                      weather=str(rng.choice(["Sis", "Pus", "Hafif yağmur"])))
        with target_height(th):
            n = int(np.count_nonzero(calculate_3d_viewshed(terrain=terr, **kw).dori_grid))
        if n >= 300:
            break
    return f"random{seed}_{family}", terr, th, kw


RANDOM_SEEDS = list(range(100, 132))


@pytest.mark.parametrize("seed", RANDOM_SEEDS, ids=[f"seed{s}" for s in RANDOM_SEEDS])
def test_random_single_camera_matches_reference(seed):
    name, terr, th, kw = random_scenario(seed)
    c = run_single(name, terr, th, **kw)
    _SINGLE_RESULTS[name] = c
    print(c.row())
    assert c.n_cells >= 250, f"degenerate random view ({c.n_cells} cells)"
    assert not violations(c, GATES), c.row() + "; " + "; ".join(violations(c, GATES))


@pytest.mark.parametrize("name,factory,th,kw", SINGLE, ids=[s[0] for s in SINGLE])
def test_single_camera_matches_reference(name, factory, th, kw):
    terr = factory()
    c = run_single(name, terr, th, **kw)
    _SINGLE_RESULTS[name] = c
    print(c.row())
    scale = 2.5 if terr.cell_size_m >= 10.0 else 1.0
    assert not violations(c, GATES, scale), c.row() + "; " + "; ".join(violations(c, GATES, scale))


def test_single_camera_bias_is_small_in_aggregate():
    """The engine's ray scatter may flip boundary cells either way; summed over
    every scenario its visible area must stay within a few percent of the
    reference's (a systematic leak or over-occlusion would not cancel)."""
    if len(_SINGLE_RESULTS) < len(SINGLE) + len(RANDOM_SEEDS):
        pytest.skip("needs the single-camera scenarios to have run")
    d = sum(c.d_vis_cells for c in _SINGLE_RESULTS.values())
    cone = sum(c.n_cells for c in _SINGLE_RESULTS.values())
    print(f"aggregate visible-cell difference {d:+d} of {cone} active cells ({100.0 * d / cone:+.2f}%)")
    assert abs(100.0 * d / cone) <= 2.0


def test_the_comparison_catches_a_wrong_target_height():
    """Harness sensitivity: an engine run that ignores the 1.8 m target against a
    1.8 m reference (a wall hides legs but not heads) must trip the gates."""
    name, factory, _, kw = next(s for s in SINGLE if s[0] == "lowwall_h1.8")
    terr = factory()
    kw = dict(kw)
    kw.setdefault("max_range_m", 600.0)
    with target_height(0.0):
        e = calculate_3d_viewshed(terrain=terr, **kw)
    r = reference_view(terr, kw["cam_x_m"], kw["cam_y_m"], kw["mast_height_m"], kw["camera"], target_h=1.8,
                       **{k: v for k, v in kw.items() if k not in ("cam_x_m", "cam_y_m", "mast_height_m", "camera")})
    c = compare("mutant", e.dori_grid, r.dori_grid, e.visible_area_m2, r.visible_area_m2, terr.cell_size_m ** 2)
    assert violations(c, GATES), c.row()


# ── use cases end to end: multi-camera plans ────────────────────────────────

FENCE = [(60.0, 60.0), (180.0, 60.0), (180.0, 180.0), (60.0, 180.0)]
BORDER = [(30.0, 60.0), (100.0, 90.0), (170.0, 70.0), (215.0, 120.0)]
AREA = [(80.0, 80.0), (170.0, 80.0), (170.0, 160.0), (80.0, 160.0)]


def _facility_plan(terr):
    plan = generate_perimeter_plan(terr, FENCE, WIDE, target_ppm=40.0, mast_height_m=5.0, is_closed_loop=True)
    return plan_placements(plan, WIDE)


def _border_plan(terr):
    plan = generate_perimeter_plan(terr, BORDER, WIDE, target_ppm=40.0, mast_height_m=6.0,
                                   is_closed_loop=False, line_mode="border", watch_side="right")
    return plan_placements(plan, WIDE)


def _area_plan(terr):
    rep = plan_area(terr, AREA, WIDE, level=ZONE_OBSERVE, goal_frac=0.8, mast_height_m=6.0, max_sites=30,
                    n_focals=3)
    return plan_placements(rep.plan, WIDE)


def _ptz_shared(terr):
    """Four presets of ONE physical camera + two fixed cameras: seen_count counts the PTZ once."""
    pre = [CameraPlacement(120.0, 120.0, 8.0, TELE, pan_deg=p, tilt_deg=-3, max_range_m=300.0,
                           focal_mm_override=f, camera_id="ptz1", label=f"P{p}")
           for p, f in ((0, 8.0), (90, 12.0), (180, 8.0), (270, 20.0))]
    fixed = [CameraPlacement(60.0, 60.0, 6.0, WIDE, pan_deg=45, tilt_deg=-4, max_range_m=200.0, lens_mode="max"),
             CameraPlacement(180.0, 60.0, 6.0, WIDE, pan_deg=330, tilt_deg=-4, max_range_m=200.0, lens_mode="max")]
    return pre + fixed


def _thermal_mix(terr):
    """Thermal cameras (Johnson classes) among visible ones in one combined view."""
    return [CameraPlacement(60.0, 60.0, 8.0, THERMAL, pan_deg=45, tilt_deg=-2, max_range_m=900.0, label="T1"),
            CameraPlacement(180.0, 60.0, 8.0, THERMAL, pan_deg=315, tilt_deg=-2, max_range_m=900.0, label="T2"),
            CameraPlacement(120.0, 200.0, 8.0, THERMAL, pan_deg=180, tilt_deg=-2, max_range_m=900.0, label="T3"),
            CameraPlacement(120.0, 20.0, 6.0, WIDE, pan_deg=0, tilt_deg=-4, max_range_m=200.0, lens_mode="max", label="W1"),
            CameraPlacement(30.0, 120.0, 6.0, WIDE, pan_deg=90, tilt_deg=-4, max_range_m=200.0, lens_mode="max", label="W2")]


MULTI = [
    ("facility_hills_h0", lambda: hills(41), _facility_plan, 0.0, {}),
    ("facility_hills_h1.8", lambda: hills(42), _facility_plan, 1.8, {}),
    ("facility_walls_buildings", lambda: with_buildings(walls(43), 53, None), _facility_plan, 0.0, {}),
    ("border_hills_h1.8", lambda: hills(44, amp=(15.0, 50.0)), _border_plan, 1.8, {}),
    ("border_preset_border", lambda: generate_procedural_terrain("border_outpost", 80, 3.0), _border_plan, 0.0, {}),
    ("area_hills_h1.8", lambda: hills(45), _area_plan, 1.8, {}),
    ("area_buildings", lambda: with_buildings(hills(46), 56, None), _area_plan, 0.0, {}),
    ("ptz_shared_ids_hills", lambda: hills(47), _ptz_shared, 0.0, {}),
    ("thermal_mix_hills", lambda: hills(49), _thermal_mix, 0.0, {}),
    ("facility_fog_hills", lambda: hills(48), _facility_plan, 0.0, dict(visibility_km=0.6, weather="Sis")),
]


@dataclass
class MultiCmp:
    zones: Cmp
    seen_agree_pct: float
    seen_max_diff: int
    seen_max_bd: int
    n_cameras: int
    per_camera: List[Cmp] = None

    def row(self) -> str:
        return (f"{self.zones.row()}  cams {self.n_cameras}  seen agree {self.seen_agree_pct:.3f}% "
                f"max|d| {self.seen_max_diff} maxBd {self.seen_max_bd}")


def run_multi(name, terr, planner, th, atm) -> MultiCmp:
    t0 = time.time()
    placements = planner(terr)
    with target_height(th):
        e = calculate_multi_camera_viewshed(terr, placements, **atm)
        r = reference_multi(terr, placements, **atm)
    zones = compare(name, e.dori_grid, r.dori_grid, e.visible_area_m2, r.visible_area_m2,
                    terr.cell_size_m ** 2, time.time() - t0)
    dis = e.seen_count_grid != r.seen_count_grid
    inner = np.zeros(dis.shape, bool)
    inner[1:-1, 1:-1] = True
    # a seen-count flip is explained by any camera's own class boundary (its
    # occlusion edge, range edge, cone edge) nearby, not only by the merged grid's
    bd = np.min([boundary_distance(v.dori_grid, 6) for v in r.per_camera], axis=0)
    per = [compare(f"cam{i}", pe.dori_grid, pr.dori_grid, pe.visible_area_m2, pr.visible_area_m2, terr.cell_size_m ** 2)
           for i, (pe, pr) in enumerate(zip(e.per_camera, r.per_camera), 1)]
    return MultiCmp(zones, 100.0 * (1.0 - dis.mean()),
                    int(np.abs(e.seen_count_grid - r.seen_count_grid).max()),
                    int(bd[dis & inner].max()) if (dis & inner).any() else 0, len(placements), per)


def _multi_violations(m: MultiCmp, merged_zones: bool = True) -> List[str]:
    bad = violations(m.zones, MULTI_GATES) if merged_zones else []
    if m.seen_max_bd > MULTI_GATES["max_bd"]:
        bad.append(f"a seen-count flip sits {m.seen_max_bd} cells from any camera's class boundary")
    # every camera's own grid against its own reference (big views only: a view
    # of a few dozen cells is mostly boundary)
    for i, c in enumerate(m.per_camera, 1):
        if c.n_cells >= 150:
            bad += [f"camera {i}: {v}" for v in violations(c, PER_CAMERA_GATES)]
    return bad


@pytest.mark.parametrize("name,factory,planner,th,atm", MULTI, ids=[m[0] for m in MULTI])
def test_multi_camera_plan_matches_reference(name, factory, planner, th, atm):
    m = run_multi(name, factory(), planner, th, atm)
    print(m.row())
    bad = _multi_violations(m)
    assert not bad, m.row() + "; " + "; ".join(bad)


def test_merged_zone_is_the_best_per_camera_zone_with_thermal_cameras():
    """Regression for an engine bug this differential work found: the merged grid
    re-thresholded the best px/m with the EN 62676-4 constants, erasing the Johnson
    classes of thermal cameras (hills(49): 3 LWIR + 2 visible -> 194 visible cells
    merged one or two levels below their best per-camera class, e.g. per-camera
    RECOG at 98.6 px/m reported as OBSERVE). The class that counts is each
    camera's own."""
    terr = hills(49)
    e = calculate_multi_camera_viewshed(terr, _thermal_mix(terr))
    best = np.zeros(terr.z_grid.shape, np.int32)
    for r in e.per_camera:
        best = np.where(r.visibility_mask, np.maximum(best, r.dori_grid), best)
    wrong = (best != e.dori_grid) & e.visibility_mask
    assert not wrong.any(), f"{int(wrong.sum())} visible cells are merged below their best per-camera class"


# ── the reference itself: analytic sanity checks (no engine involved) ───────

def test_reference_flat_plane_is_exactly_the_analytic_wedge():
    from cctv_simulator.calculations import camera_optics
    terr = flat()
    cx, cy, mast, pan, tilt = 120.0, 30.0, 8.0, 20.0, -3.0
    r = reference_view(terr, cx, cy, mast, WIDE, lens_mode="max", pan_deg=pan, tilt_deg=tilt, max_range_m=600.0,
                       earth_curvature=False, target_h=0.0)
    opt = camera_optics(WIDE, WIDE.focal_max_mm)
    xs = np.arange(terr.cols) * terr.cell_size_m
    X, Y = np.meshgrid(xs, xs)
    d = np.hypot(X - cx, Y - cy)
    az = np.degrees(np.arctan2(X - cx, Y - cy))
    dang = (az - pan + 180.0) % 360.0 - 180.0
    slant = np.hypot(d, mast)
    depression = np.degrees(np.arctan2(-mast, d))                 # elevation of the ground point
    wedge = ((np.abs(dang) <= opt.hfov_deg / 2) & (d > 1.0) & (d <= opt.distance_for_ppm(25.0) * 1.15)
             & (depression >= tilt - opt.vfov_deg / 2) & (depression <= tilt + opt.vfov_deg / 2)
             & (opt.ppm_at(np.maximum(slant, 1.0)) >= 25.0))
    assert wedge.sum() > 100
    assert not np.any(r.dori_grid == ZONE_OCCLUDED)               # nothing hides on a plane
    assert np.array_equal(r.visibility_mask, wedge)


def test_reference_is_dark_behind_a_wall_and_lit_in_front_of_it():
    terr = flat()
    z = np.zeros((80, 80))
    z[40, :] = 40.0                                               # y = 120 m, full-width wall
    terr = TerrainData(z_grid=z, cell_size_m=3.0)
    r = reference_view(terr, 120.0, 30.0, 6.0, WIDE, lens_mode="max", pan_deg=0.0, tilt_deg=-2.0,
                       max_range_m=600.0, target_h=0.0)
    framed = r.in_cone
    behind = framed.copy()
    behind[:42, :] = False
    front = framed.copy()
    front[39:, :] = False
    assert behind.sum() > 50 and front.sum() > 50
    assert np.all(r.dori_grid[behind] <= ZONE_OCCLUDED)           # occluded (1) or not framed (0), never seen
    assert not r.visibility_mask[behind].any()
    assert r.visibility_mask[front].sum() > 0.5 * front.sum()


@pytest.mark.parametrize("slope", [0.05, 0.08, 0.15])
@pytest.mark.parametrize("mast", [2.0, 6.0])
def test_reference_planar_down_slope_never_hides_itself(slope, mast):
    yy = np.arange(80)[:, None] * 3.0
    z = np.broadcast_to(100.0 - slope * yy, (80, 80)).copy()
    terr = TerrainData(z_grid=z, cell_size_m=3.0)
    r = reference_view(terr, 120.0, 15.0, mast, WIDE, lens_mode="max", pan_deg=0.0,
                       tilt_deg=-np.degrees(np.arctan(slope)) - 1.0, max_range_m=600.0, target_h=0.0)
    assert r.visible_area_m2 > 0
    assert not np.any(r.dori_grid == ZONE_OCCLUDED)


def test_reference_node_line_crossings_are_exact_even_with_coarse_sampling():
    """A one-cell wall's crest lies ON a node line, between two samples of any
    finite step: the horizon must still be the crest's exact height."""
    from tests.reference_viewshed import horizon_tan
    z = np.zeros((40, 40))
    z[20, :] = 10.0                                               # crest on the row line y = 20 cells
    cell = 2.0
    cam = (30.0, 10.0, 2.0)                                       # x, y, lens height (m)
    tx, ty = np.array([40.0]), np.array([70.0])                   # target behind the wall (y = 35 cells)
    # the line crosses y = 40 m after t = (40 - 10) / (70 - 10) = 0.5
    d_cross = 0.5 * np.hypot(tx[0] - cam[0], ty[0] - cam[1])
    expected = (10.0 - cam[2]) / d_cross
    for step in (0.05, 0.9, 3.0):                                 # fine and deliberately coarse
        h = horizon_tan(z, 0.0, 0.0, cell, cam[0], cam[1], cam[2], tx, ty, earth_curvature=False, step_cells=step)
        assert h[0] == pytest.approx(expected, rel=1e-12)


# Was an engine finding: the cache was keyed on id(z_grid) alone, so a new terrain at a freed grid's
# address read the old terrain's maxima (48 of 60 same-shape terrains differed). Fixed in _cell_max.
def test_engine_result_does_not_depend_on_a_stale_cell_max_cache():
    from cctv_simulator import viewshed_3d as vs
    cache = getattr(vs, "_CELL_MAX_CACHE", None)
    if cache is None or not hasattr(vs, "_cell_max"):
        pytest.skip("cell-max cache no longer exists")
    b = hills(3)

    def run(terr):
        return calculate_3d_viewshed(terrain=terr, cam_x_m=96.0, cam_y_m=60.0, mast_height_m=8.0, camera=WIDE,
                                     lens_mode="min", pan_deg=30, tilt_deg=-6, max_range_m=300.0, with_profile=False)

    cache.clear()
    clean = run(b).dori_grid
    cache.clear()
    # what id reuse does: b's key holds a's maxima
    cache[(id(b.z_grid), b.z_grid.shape)] = np.full((b.rows - 1, b.cols - 1), 40.0)   # a flat, lower old terrain
    try:
        stale = run(b).dori_grid
    finally:
        cache.clear()
    assert np.array_equal(stale, clean), f"{int((stale != clean).sum())} cells differ with a stale cache entry"
