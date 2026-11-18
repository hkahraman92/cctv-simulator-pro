"""Data-uncertainty (sensitivity) analysis of a coverage share.

A coverage share is only as good as the data under it: a downloaded DEM (~30 m, SRTM-derived) is
metres off, and most OSM building heights are a class default. This module re-runs the AUTHORITATIVE
multi-camera engine (``calculate_multi_camera_viewshed``, the very run ``perimeter.coverage`` scores)
on ``n_runs`` perturbed copies of the terrain and says per cell how often the requirement held:

* CERTAIN   -- held in every run (survives the data error),
* UNCERTAIN -- held in some runs (depends on data accuracy),
* NEVER     -- held in none.

Run 0 is the UNPERTURBED data (the same terrain object, so it equals the plain coverage result
bit for bit); runs 1.. draw an error field each. Nothing here derives optics: every number is a
count over engine grids (Rule 1). No Tk.

Error model (every number below is an ASSUMPTION, not a datasheet value)
------------------------------------------------------------------------
* Ground: bare-earth DEM + a spatially correlated Gaussian field (std ``dem_sigma_m``,
  correlation length ``dem_corr_m``: white noise smoothed with a separable Gaussian of that
  standard deviation, renormalised analytically to unit variance). The field is generated on the
  ORIGINAL DEM grid and refined with the same bilinear interpolation as ``refine_terrain``: the
  error belongs to the data, not to the analysis grid. The camera mast stands on the perturbed
  ground (``ground_elevation_at``), i.e. the whole site moves with the error -- intentional:
  what matters is the RELATIVE error between camera, occluder and target.
* Obstacles: each building / tree OBJECT (an 8-connected component of cells with the same kind,
  height source and height) draws ONE uniform variate per run, scaled by its height source:

  ========  ==========================================  =================================
  source    meaning                                     perturbation
  ========  ==========================================  =================================
  tagged    ``height`` tag in OSM                       height x (1 +/- 10 %)
  levels    ``building:levels`` x 3 m                   height +/- 1.5 m (one storey / 2)
  default   class default (building 6, tree 8, wood 12) height x (1 +/- 50 %)
  measured  local nDSM / DSM raster                     + correlated field, std 1 m
  ========  ==========================================  =================================

  Uniform (not lognormal) so the bounds are explainable: a default-height building is anywhere
  from half to one and a half times the guess. Without a per-cell source map every object is
  treated as ``default`` (the conservative reading). ``obstacle_scale`` multiplies all spreads
  (0 = obstacles exact, 1 = the table).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .terrain_loader import TerrainData, _upsample_bilinear
from .viewshed_3d import (ZONE_DETECT, ZONE_IDENT, ZONE_MONITOR, ZONE_OBSERVE, ZONE_RECOG,
                          CameraPlacement, MultiViewshedResult, calculate_multi_camera_viewshed)

# height-source codes of ``source_grid``
SRC_NONE, SRC_TAGGED, SRC_LEVELS, SRC_DEFAULT, SRC_MEASURED = 0, 1, 2, 3, 4
SOURCE_NAMES = {SRC_TAGGED: "tagged", SRC_LEVELS: "levels", SRC_DEFAULT: "default"}

# (multiplicative spread, additive spread in m) of the uniform draw, per height source
OBSTACLE_SPREAD = {SRC_TAGGED: (0.10, 0.0), SRC_LEVELS: (0.0, 1.5), SRC_DEFAULT: (0.50, 0.0)}
MEASURED_OBSTACLE_SIGMA_M = 1.0

# ASSUMPTIONS, not datasheet values: relative (site-scale) vertical error of the DEM sources this
# application loads, and the horizontal length over which that error is correlated. Chosen
# conservatively from published SRTM-class accuracy (absolute ~5-10 m, relative a few m).
#   marker in ``source_note`` (lower case) -> (sigma_m, corr_m, label)
DEM_ASSUMPTIONS = (
    ("terrarium", 4.0, 90.0, "Terrarium (SRTM / karma kaynak, ~30 m)"),
    ("open-elevation", 6.0, 250.0, "Open-Elevation (SRTM, kaba ızgara)"),
)
LOCAL_DEM_SIGMA_M = 1.0                 # a local GeoTIFF: ask the caller; this is the default
LOCAL_DEM_CORR_CELLS = 3.0
UNKNOWN_MEASURED = (4.0, 90.0, "Ölçülmüş DEM (kaynak bilinmiyor)")
PROCEDURAL = (3.0, 90.0, "Prosedürel arazi (ölçüm değil)")
MAX_RUNS = 32
MIN_RUNS = 2


class SensitivityCancelled(RuntimeError):
    """The caller's ``cancel`` callback asked to stop."""


@dataclass
class DemAssumption:
    sigma_m: float
    corr_m: float
    basis: str
    measured: bool

    def as_dict(self) -> Dict[str, Any]:
        return {"dem_sigma_m": self.sigma_m, "dem_corr_m": self.corr_m, "basis": self.basis,
                "measured": self.measured}


def default_dem_assumption(terrain: TerrainData, *, base_cell_m: Optional[float] = None) -> DemAssumption:
    """Documented default error of this terrain's DEM (see ``DEM_ASSUMPTIONS``): by source note
    for downloaded data, ``LOCAL_DEM_SIGMA_M`` for a local GeoTIFF, a nominal figure for
    procedural relief (flagged not measured)."""
    cell = float(base_cell_m if base_cell_m else terrain.cell_size_m)
    if not terrain.is_measured:
        s, c, b = PROCEDURAL
        return DemAssumption(s, c, b, False)
    note = (terrain.source_note or "").lower()
    for marker, s, c, b in DEM_ASSUMPTIONS:
        if marker in note:
            return DemAssumption(s, c, b, True)
    if terrain.georef is not None or "geotiff" in note:
        return DemAssumption(LOCAL_DEM_SIGMA_M, LOCAL_DEM_CORR_CELLS * cell,
                             "Yerel GeoTIFF DEM (varsayılan; kendi doğruluğunuzu girin)", True)
    s, c, b = UNKNOWN_MEASURED
    return DemAssumption(s, c, b, True)


# ── error fields ───────────────────────────────────────────────────────────
def correlated_field(shape: Tuple[int, int], cell_m: float, sigma_m: float, corr_m: float,
                     rng: np.random.Generator) -> np.ndarray:
    """Zero-mean Gaussian field of std ``sigma_m`` and correlation length ``corr_m`` (std of the
    Gaussian kernel): white noise, separable Gaussian smoothing with reflected edges, divided by
    the kernel's analytic gain so the interior variance is ``sigma_m ** 2``."""
    white = rng.standard_normal(shape)
    if sigma_m <= 0:
        return np.zeros(shape, np.float64)
    s = float(corr_m) / float(cell_m)
    if s < 0.3:
        return white * sigma_m
    radius = max(1, int(np.ceil(3.0 * s)))
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / s) ** 2)
    k /= k.sum()
    gain = float((k ** 2).sum())          # two separable passes: variance gain is (sum k^2)^2
    out = white
    for axis in (0, 1):
        n = out.shape[axis]
        pad = [(0, 0), (0, 0)]
        pad[axis] = (radius, radius)
        p = np.pad(out, pad, mode="reflect" if n > radius else "symmetric")
        acc = np.zeros_like(out)
        for i, w in enumerate(k):
            sl = [slice(None), slice(None)]
            sl[axis] = slice(i, i + n)
            acc += w * p[tuple(sl)]
        out = acc
    return out * (sigma_m / gain)


def label_objects(key: np.ndarray) -> np.ndarray:
    """8-connected components of equal non-zero ``key`` (int array): 1-based ids, 0 = none. Min-label
    propagation with pointer jumping (scipy-free); converges in a handful of passes."""
    key = np.asarray(key)
    rows, cols = key.shape
    fg = key != 0
    lab = np.where(fg, np.arange(1, key.size + 1, dtype=np.int64).reshape(key.shape), 0)
    if not fg.any():
        return lab
    big = np.int64(key.size + 2)
    shifts = [(dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if (dr, dc) != (0, 0)]
    while True:
        cur = np.where(fg, lab, big)
        new = cur.copy()
        for dr, dc in shifts:
            sh = np.full_like(cur, big)
            sk = np.zeros_like(key)
            rs, rd = slice(max(dr, 0), rows + min(dr, 0)), slice(max(-dr, 0), rows + min(-dr, 0))
            cs, cd = slice(max(dc, 0), cols + min(dc, 0)), slice(max(-dc, 0), cols + min(-dc, 0))
            sh[rd, cd] = cur[rs, cs]
            sk[rd, cd] = key[rs, cs]
            ok = fg & (sk == key)
            new = np.where(ok & (sh < new), sh, new)
        flat = new.ravel()
        safe = np.where(flat < big, flat - 1, 0)
        jumped = np.minimum(flat, np.where(flat < big, flat[safe], big)).reshape(new.shape)
        jumped = np.where(fg, jumped, 0)
        if np.array_equal(jumped, lab):
            return lab
        lab = jumped


@dataclass
class _Objects:
    """The obstacle objects of a terrain: per-cell object id and, per object, its spreads."""
    ids: np.ndarray                  # int, 0 = no object (or a measured cell)
    mult: np.ndarray                 # per object: multiplicative spread
    add: np.ndarray                  # per object: additive spread (m)
    source: np.ndarray               # per object: SRC_* code
    measured: np.ndarray             # bool grid: cells of a measured surface

    @property
    def count(self) -> int:
        return int(self.mult.size)


def _objects_of(terrain: TerrainData, source_grid: Optional[np.ndarray]) -> Optional[_Objects]:
    if terrain.obstacle_grid is None:
        return None
    h = np.asarray(terrain.obstacle_grid)
    solid = h > 0
    src = np.zeros(h.shape, np.uint8) if source_grid is None else np.asarray(source_grid, np.uint8)
    if src.shape != h.shape:
        raise ValueError("Kaynak ızgarası engel ızgarasıyla aynı boyutta değil.")
    src = np.where(solid & (src == SRC_NONE), SRC_DEFAULT, src)        # unknown source: the conservative reading
    kind = np.zeros(h.shape, np.uint8) if terrain.obstacle_kind is None else np.asarray(terrain.obstacle_kind, np.uint8)
    measured = solid & (src == SRC_MEASURED)
    obj_cells = solid & ~measured
    key = np.zeros(h.shape, np.int64)
    if obj_cells.any():
        stack = np.stack([kind[obj_cells].astype(np.float64), src[obj_cells].astype(np.float64),
                          h[obj_cells].astype(np.float64)], axis=1)
        _u, inv = np.unique(stack, axis=0, return_inverse=True)
        key[obj_cells] = np.asarray(inv).reshape(-1) + 1
    ids = label_objects(key)
    n = int(ids.max())
    mult, add, source = np.zeros(n), np.zeros(n), np.zeros(n, np.uint8)
    if n:
        flat_ids = ids[obj_cells]
        flat_src = src[obj_cells]
        source[flat_ids - 1] = flat_src
        for code, (m, a) in OBSTACLE_SPREAD.items():
            sel = source == code
            mult[sel], add[sel] = m, a
    return _Objects(ids=ids, mult=mult, add=add, source=source, measured=measured)


# ── result ─────────────────────────────────────────────────────────────────
@dataclass
class SensitivityResult:
    n_runs: int
    seed: int
    roi_cells: int
    cell_area_m2: float
    certain_share: float
    uncertain_share: float
    never_share: float
    nominal_share: float
    share_min: float
    share_max: float
    share_p10: float
    share_p90: float
    run_shares: List[float]
    level_shares: Dict[int, Dict[str, float]]       # zone code -> {min, nominal, max}
    class_grid: np.ndarray                          # int8: 0 outside the ROI, 1 certain, 2 uncertain, 3 never
    held_fraction: np.ndarray                       # float32: share of runs that held (inside the ROI)
    assumptions: Dict[str, Any] = field(default_factory=dict)
    measured: bool = False

    def summary_line(self) -> str:
        return sensitivity_line(self.certain_share, self.uncertain_share, self.n_runs, self.assumptions,
                                measured=self.measured)


CLASS_CERTAIN, CLASS_UNCERTAIN, CLASS_NEVER = 1, 2, 3


def _pct(f: float) -> str:
    return "%" + f"{f * 100:.1f}".replace(".", ",")


def _m(x: float) -> str:
    return f"{x:.3g}".replace(".", ",")


def object_phrase(objects: Dict[str, int]) -> str:
    """'31 varsayılan, 4 kat sayısından, 7 etiketli' -- only the sources that occur."""
    parts = [(objects.get("default", 0), "varsayılan"), (objects.get("levels", 0), "kat sayısından"),
             (objects.get("tagged", 0), "etiketli")]
    return ", ".join(f"{n} {txt}" for n, txt in parts if n)


def sensitivity_line(certain: float, uncertain: float, n_runs: int, assumptions: Dict[str, Any], *,
                     measured: bool = True) -> str:
    """The sentence the reports carry (Turkish): 'Veri hassasiyeti: %X kesin, %Y veriye bağlı (N koşu, DEM σ …
    m, korelasyon … m; bina yükseklikleri: … varsayılan)'."""
    objs = object_phrase(assumptions.get("objects") or {})
    scale = float(assumptions.get("obstacle_scale", 1.0))
    if objs and scale > 0:
        obst = f"; bina yükseklikleri: {objs}"
    elif objs:
        obst = "; bina yükseklikleri kesin kabul edildi"
    else:
        obst = ""
    if assumptions.get("measured_cells"):
        obst += ("; ölçülmüş yüzey ±" + _m(MEASURED_OBSTACLE_SIGMA_M * scale) + " m")
    text = (f"Veri hassasiyeti: {_pct(certain)} kesin, {_pct(uncertain)} veriye bağlı ({n_runs} koşu, "
            f"DEM σ {_m(assumptions.get('dem_sigma_m', 0.0))} m, korelasyon {_m(assumptions.get('dem_corr_m', 0.0))} m{obst})")
    if not measured:
        text += " — arazi ölçülmüş değil (temsili)"
    return text


# ── the analysis ───────────────────────────────────────────────────────────
def coverage_sensitivity(terrain: TerrainData, placements: Sequence[CameraPlacement], *,
                         roi: Optional[np.ndarray] = None, level: int = ZONE_DETECT,
                         held: Optional[Callable[[MultiViewshedResult], np.ndarray]] = None,
                         levels: Optional[Sequence[int]] = None, n_runs: int = 12, seed: int = 0,
                         dem_sigma_m: Optional[float] = None, dem_corr_m: Optional[float] = None,
                         obstacle_scale: float = 1.0, source_grid: Optional[np.ndarray] = None,
                         refine_factor: int = 1, visibility_km: float = 40.0, weather: str = "",
                         monitor_floor: bool = False,
                         progress: Optional[Callable[[int, int, str], None]] = None,
                         cancel: Optional[Callable[[], bool]] = None) -> SensitivityResult:
    """Per-cell reliability of a coverage requirement under DEM and obstacle-height error.

    ``terrain`` is the analysis terrain (surface model included, refined or not); ``placements``
    the run ``perimeter.coverage`` scores. A cell counts as held in a run when ``held(mv)`` is True
    (default: merged zone >= ``level``). ``levels`` (zone codes; default Detection..Identification,
    plus Monitoring for a monitor-floor run) add per-level share ranges.
    ``refine_factor`` > 1: ``terrain`` is a refinement of an original grid ``factor`` times
    coarser; the ground error field is generated on that original grid and refined with
    ``refine_terrain``'s bilinear interpolation.
    ``source_grid``: uint8 ``SRC_*`` per cell for the obstacle height sources (None: all default).
    Raises ``SensitivityCancelled`` when ``cancel()`` returns True between runs."""
    if not placements:
        raise ValueError("Yerleşim yok.")
    n_runs = int(n_runs)
    if not MIN_RUNS <= n_runs <= MAX_RUNS:
        raise ValueError(f"Koşu sayısı {MIN_RUNS}–{MAX_RUNS} aralığında olmalı.")
    if dem_sigma_m is not None and dem_sigma_m < 0 or dem_corr_m is not None and dem_corr_m <= 0:
        raise ValueError("DEM σ ≥ 0 ve korelasyon uzunluğu > 0 olmalı.")
    if obstacle_scale < 0:
        raise ValueError("Engel belirsizliği ≥ 0 olmalı.")
    refine_factor = max(1, int(refine_factor))
    base_cell = terrain.cell_size_m * refine_factor
    dflt = default_dem_assumption(terrain, base_cell_m=base_cell)
    sigma = dflt.sigma_m if dem_sigma_m is None else float(dem_sigma_m)
    corr = dflt.corr_m if dem_corr_m is None else float(dem_corr_m)
    base_shape = (terrain.rows // refine_factor, terrain.cols // refine_factor)
    if base_shape[0] * refine_factor != terrain.rows or base_shape[1] * refine_factor != terrain.cols:
        raise ValueError("Arazi ızgarası inceltme katsayısına bölünmüyor.")

    shape = (terrain.rows, terrain.cols)
    inside = np.ones(shape, bool) if roi is None else np.asarray(roi, bool)
    n_roi = int(inside.sum())
    cell = float(terrain.cell_size_m) ** 2
    objects = _objects_of(terrain, source_grid)
    if levels is None:
        levels = [ZONE_DETECT, ZONE_OBSERVE, ZONE_RECOG, ZONE_IDENT] + ([ZONE_MONITOR] if monitor_floor else [])
    levels = list(levels)
    is_held = held if held is not None else (lambda mv: mv.dori_grid >= level)

    def share(mask: np.ndarray) -> float:
        # the very expression ``perimeter.coverage`` uses for its shares (bit-equal by construction)
        return float(((mask) & inside).sum() * cell) / float(n_roi * cell) if n_roi else 0.0

    counts = np.zeros(shape, np.int32)
    run_shares: List[float] = []
    lvl_runs: Dict[int, List[float]] = {z: [] for z in levels}
    dtype = terrain.z_grid.dtype
    for k in range(n_runs):
        if cancel is not None and cancel():
            raise SensitivityCancelled("Hassasiyet analizi iptal edildi.")
        if progress is not None:
            progress(k, n_runs, f"Koşu {k + 1}/{n_runs}" + (" (veri olduğu gibi)" if k == 0 else ""))
        t_run = terrain if k == 0 else _perturbed(terrain, objects, np.random.default_rng((int(seed), k)),
                                                   sigma, corr, obstacle_scale, base_shape, base_cell,
                                                   refine_factor, dtype)
        mv = calculate_multi_camera_viewshed(t_run, list(placements), visibility_km=visibility_km, weather=weather,
                                             monitor_floor=monitor_floor)
        if mv is None:
            raise ValueError("Yerleşimlerden hesaplanacak görüş yok.")
        ok = np.asarray(is_held(mv), bool)
        counts += ok & inside
        run_shares.append(share(ok))
        for z in levels:
            lvl_runs[z].append(share(mv.dori_grid >= z))
        del mv
    if progress is not None:
        progress(n_runs, n_runs, "Tamam")

    cls = np.zeros(shape, np.int8)
    cls[inside & (counts == n_runs)] = CLASS_CERTAIN
    cls[inside & (counts > 0) & (counts < n_runs)] = CLASS_UNCERTAIN
    cls[inside & (counts == 0)] = CLASS_NEVER
    nn = float(n_roi) if n_roi else 1.0
    arr = np.asarray(run_shares)
    obj_counts: Dict[str, int] = {}
    if objects is not None:
        for code, name in SOURCE_NAMES.items():
            obj_counts[name] = int((objects.source == code).sum())
    assumptions = {
        "dem_sigma_m": sigma, "dem_corr_m": corr, "dem_basis": dflt.basis if dem_sigma_m is None and dem_corr_m is None
        else "Kullanıcı değeri", "obstacle_scale": float(obstacle_scale), "refine_factor": refine_factor,
        "base_cell_m": base_cell, "objects": obj_counts,
        "measured_cells": int(objects.measured.sum()) if objects is not None else 0,
        "source_known": source_grid is not None,
    }
    return SensitivityResult(
        n_runs=n_runs, seed=int(seed), roi_cells=n_roi, cell_area_m2=cell,
        certain_share=float((cls == CLASS_CERTAIN).sum()) / nn if n_roi else 0.0,
        uncertain_share=float((cls == CLASS_UNCERTAIN).sum()) / nn if n_roi else 0.0,
        never_share=float((cls == CLASS_NEVER).sum()) / nn if n_roi else 0.0,
        nominal_share=run_shares[0], share_min=float(arr.min()), share_max=float(arr.max()),
        share_p10=float(np.percentile(arr, 10)), share_p90=float(np.percentile(arr, 90)),
        run_shares=[float(x) for x in run_shares],
        level_shares={z: {"min": float(min(v)), "nominal": float(v[0]), "max": float(max(v))}
                      for z, v in lvl_runs.items()},
        class_grid=cls, held_fraction=np.where(inside, counts / float(n_runs), 0.0).astype(np.float32),
        assumptions=assumptions, measured=bool(terrain.is_measured))


def _perturbed(terrain: TerrainData, objects: Optional[_Objects], rng: np.random.Generator, sigma: float,
               corr_m: float, scale: float, base_shape: Tuple[int, int], base_cell: float, factor: int,
               dtype) -> TerrainData:
    """One draw: ground error (original grid, refined) + per-object height factors on the surface."""
    field_base = correlated_field(base_shape, base_cell, sigma, corr_m, rng)
    ground_err = _upsample_bilinear(field_base, factor) if factor > 1 else field_base
    dz = ground_err
    obs_new = None
    if objects is not None:
        h = np.asarray(terrain.obstacle_grid, np.float64)
        u = rng.uniform(-1.0, 1.0, objects.count)
        new_h = h.copy()
        if objects.count:
            idx = np.clip(objects.ids - 1, 0, None)
            hit = objects.ids > 0
            f = np.where(hit, 1.0 + scale * objects.mult[idx] * u[idx], 1.0)
            a = np.where(hit, scale * objects.add[idx] * u[idx], 0.0)
            new_h = np.where(hit, np.maximum(h * f + a, np.minimum(h, 0.5)), h)   # an object never vanishes
        if objects.measured.any() and scale > 0:
            err = correlated_field(h.shape, terrain.cell_size_m, MEASURED_OBSTACLE_SIGMA_M * scale,
                                   max(3.0 * terrain.cell_size_m, 1.0), rng)
            new_h = np.where(objects.measured, np.maximum(h + err, 0.0), new_h)
        obs_new = new_h
        dz = dz + (new_h - h)
    z = (terrain.z_grid.astype(np.float64) + dz).astype(dtype)
    if objects is None:
        return replace(terrain, z_grid=z)
    ground = (terrain.ground_z_grid.astype(np.float64) + ground_err).astype(terrain.ground_z_grid.dtype)
    return replace(terrain, z_grid=z, ground_z_grid=ground,
                   obstacle_grid=obs_new.astype(terrain.obstacle_grid.dtype))
