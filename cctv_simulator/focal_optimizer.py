"""Varifocal / PTZ coverage optimisation over the whole focal range.

The viewshed used to score a varifocal lens at its two ends only (``min`` =
wide, ``max`` = tele). The best installation is usually in between: wide
covers a big area at low pixel density, tele a long thin strip at high
density, and the area at a given DORI level peaks somewhere inside the
range. A PTZ can be at any focal on demand.

Everything here is built on the cached 360-degree mount raster
(``mount_visibility``): ``view_layers`` is the pan-independent part of a view
for one (focal, tilt), so a focal x tilt x pan search costs one layer build
per (focal, tilt) and a cone comparison per pan -- the same rules as the
fixed-view engine (Kural 1). Final numbers shown to the user are re-run
through the authoritative engine by the UI.

* :func:`sweep_focals` -- one mount: area per DORI level at every focal
  (best tilt, optionally best pan), the optimum for an objective, and the
  two lens ends for comparison.
* :func:`envelope` -- best DORI each cell can get over all focals (and all
  pans for a PTZ). For a FIXED varifocal this is not simultaneous coverage:
  it is "what some installation setting could give here".
* :func:`optimize_layout` -- many mounts: fixed cameras' (focal, tilt[, pan])
  chosen by greedy coordinate ascent on the UNION coverage of a region of
  interest; PTZs scored as on-demand reach (their envelope). Each mount's
  current setting is always a candidate, so the result never scores below
  the starting plan.

Objectives (cumulative levels, z >= L):
``"area"``     -- area at >= the target level (higher levels break ties);
``"balanced"`` -- each DORI level reached counts 1 (detect..identify = 1..4).

No Tk. numpy only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .calculations import camera_optics
from .models import CameraConfig
from .mount_visibility import MountVisibility, mount_visibility, view_layers
from .terrain_loader import TerrainData
from .viewshed_3d import (
    PPM_DETECT,
    PPM_IDENT,
    PPM_OBSERVE,
    PPM_RECOG,
    ZONE_DETECT,
    ZONE_IDENT,
    ZONE_OBSERVE,
    ZONE_RECOG,
)

LEVELS = (ZONE_DETECT, ZONE_OBSERVE, ZONE_RECOG, ZONE_IDENT)
LEVEL_PPM = {ZONE_DETECT: PPM_DETECT, ZONE_OBSERVE: PPM_OBSERVE,
             ZONE_RECOG: PPM_RECOG, ZONE_IDENT: PPM_IDENT}
LEVEL_NAME = {ZONE_DETECT: "Algılama", ZONE_OBSERVE: "Gözlem",
              ZONE_RECOG: "Tanıma", ZONE_IDENT: "Teşhis"}
MAX_RANGE_CAP_M = 1500.0


def level_weights(objective: str, level: int) -> Dict[int, float]:
    if objective == "balanced":
        return {lv: 1.0 for lv in LEVELS}
    return {lv: (1.0 if lv == level else (1e-3 if lv > level else 0.0)) for lv in LEVELS}


def focal_steps(camera: CameraConfig, n: int = 12) -> List[float]:
    """Log-spaced focals from the wide to the tele end (both ends included)."""
    lo, hi = float(camera.focal_min_mm), float(max(camera.focal_max_mm, camera.focal_min_mm))
    if hi - lo < 0.05 or n <= 1:
        return [lo]
    vals = np.geomspace(lo, hi, max(n, 2))
    out: List[float] = []
    for v in vals:
        v = round(float(v), 2)
        if not out or v - out[-1] > 0.02:
            out.append(v)
    out[0], out[-1] = lo, hi
    return out


def auto_tilts(camera: CameraConfig, focal_mm: float, mast_m: float, level: int,
               max_range_m: float) -> List[float]:
    """Three tilt candidates per focal, from where the target level ends:
    frame top on that distance (most near-field), frame centre on 55 % of it
    (the perimeter planner's rule) and frame top just under the horizon
    (most reach). The layer evaluation picks the winner on real terrain."""
    optics = camera_optics(camera, focal_mm)
    slant = optics.distance_for_ppm(LEVEL_PPM[level])
    d = max(5.0, min(math.sqrt(max(slant * slant - mast_m * mast_m, 1.0)), max_range_m))
    half_v = optics.vfov_deg / 2.0
    top_on_d = -math.degrees(math.atan2(mast_m, d)) - half_v
    centre = -math.degrees(math.atan2(mast_m, 0.55 * d))
    reach = -half_v + 0.5
    out = []
    for t in (top_on_d, centre, reach):
        t = round(max(-89.0, min(10.0, t)), 1)
        if all(abs(t - o) > 0.3 for o in out):
            out.append(t)
    return out


def _pan_grid(pan_deg: float, search_deg: float, step_deg: float) -> List[float]:
    if search_deg <= 0:
        return [pan_deg % 360.0]
    k = int(search_deg // step_deg)
    return [(pan_deg + i * step_deg) % 360.0 for i in range(-k, k + 1)]


def _cone(center_az_rad: np.ndarray, pan_deg: float, hfov_deg: float) -> np.ndarray:
    pan = math.radians(pan_deg)
    d = np.arctan2(np.sin(center_az_rad - pan), np.cos(center_az_rad - pan))
    return np.abs(d) <= math.radians(hfov_deg / 2.0)


def level_areas(zone: np.ndarray, roi: Optional[np.ndarray], cell_area: float) -> Dict[int, float]:
    inside = np.ones(zone.shape, bool) if roi is None else roi
    return {lv: float(((zone >= lv) & inside).sum()) * cell_area for lv in LEVELS}


def _score(zone: np.ndarray, roi: Optional[np.ndarray], weights: Dict[int, float]) -> float:
    inside = np.ones(zone.shape, bool) if roi is None else roi
    return float(sum(w * ((zone >= lv) & inside).sum() for lv, w in weights.items() if w))


@dataclass
class ViewChoice:
    focal_mm: float
    tilt_deg: float
    pan_deg: Optional[float]          # None = any pan (PTZ envelope)
    hfov_deg: float
    areas_m2: Dict[int, float]        # cumulative: area at >= level
    score: float


@dataclass
class SweepReport:
    curve: List[ViewChoice]           # best (tilt[, pan]) per focal, wide -> tele
    best: ViewChoice
    wide: ViewChoice                  # the lens ends, for "how much better is the optimum"
    tele: ViewChoice
    level: int
    objective: str
    reach_m: float
    elapsed_note: str = ""


def _mount_range(camera: CameraConfig, max_range_m: Optional[float]) -> float:
    if max_range_m:
        return float(min(max_range_m, MAX_RANGE_CAP_M))
    tele = camera_optics(camera, float(max(camera.focal_max_mm, camera.focal_min_mm)))
    return float(min(tele.distance_for_ppm(PPM_DETECT), MAX_RANGE_CAP_M))


def sweep_focals(terrain: TerrainData, x_m: float, y_m: float, mast_m: float, camera: CameraConfig, *,
                 pan_deg: Optional[float] = 0.0, pan_search_deg: float = 0.0, pan_step_deg: float = 10.0,
                 level: int = ZONE_RECOG, objective: str = "area", n_focals: int = 12,
                 tilts: Optional[Sequence[float]] = None, roi: Optional[np.ndarray] = None,
                 max_range_m: Optional[float] = None, visibility_km: float = 40.0,
                 weather: str = "", mv: Optional[MountVisibility] = None) -> SweepReport:
    """Score every focal (x tilt candidates x pans) of one mount.
    ``pan_deg=None`` scores all pans at once (PTZ: what it can reach)."""
    rng = _mount_range(camera, max_range_m)
    mv = mv or mount_visibility(terrain, x_m, y_m, mast_m, max_range_m=rng)
    roi_w = None if roi is None else roi[mv.window]
    cell_area = terrain.cell_size_m ** 2
    weights = level_weights(objective, level)
    curve: List[ViewChoice] = []
    for f in focal_steps(camera, n_focals):
        best_f: Optional[ViewChoice] = None
        for t in (tilts if tilts is not None else auto_tilts(camera, f, mast_m, level, rng)):
            lay = view_layers(mv, camera, tilt_deg=t, focal_mm=f, max_range_m=rng,
                              visibility_km=visibility_km, weather=weather)
            zone_vis = np.where(lay.visible, lay.zone, 0)
            pans = [None] if pan_deg is None else _pan_grid(pan_deg, pan_search_deg, pan_step_deg)
            for p in pans:
                z = zone_vis if p is None else np.where(_cone(lay.center_az_rad, p, lay.hfov_deg), zone_vis, 0)
                sc = _score(z, roi_w, weights)
                if best_f is None or sc > best_f.score + 1e-9:
                    best_f = ViewChoice(f, t, p, lay.hfov_deg, level_areas(z, roi_w, cell_area), sc)
        curve.append(best_f)
    best = max(curve, key=lambda c: c.score)
    return SweepReport(curve=curve, best=best, wide=curve[0], tele=curve[-1], level=level,
                       objective=objective, reach_m=rng)


def envelope(terrain: TerrainData, x_m: float, y_m: float, mast_m: float, camera: CameraConfig, *,
             pan_deg: Optional[float] = None, level: int = ZONE_RECOG, n_focals: int = 12,
             max_range_m: Optional[float] = None, visibility_km: float = 40.0, weather: str = "",
             mv: Optional[MountVisibility] = None) -> np.ndarray:
    """Full-grid ZONE_* (int8): the best DORI each cell gets at ANY focal and
    tilt candidate -- and any pan when ``pan_deg`` is None (PTZ). Cells no
    setting sees are 0. Not simultaneous for a fixed lens (see module doc)."""
    rng = _mount_range(camera, max_range_m)
    mv = mv or mount_visibility(terrain, x_m, y_m, mast_m, max_range_m=rng)
    best = np.zeros(mv.written.shape, np.int8)
    for f in focal_steps(camera, n_focals):
        for lv in (ZONE_DETECT, level, ZONE_IDENT):
            for t in auto_tilts(camera, f, mast_m, lv, rng):
                lay = view_layers(mv, camera, tilt_deg=t, focal_mm=f, max_range_m=rng,
                                  visibility_km=visibility_km, weather=weather)
                z = np.where(lay.visible, lay.zone, 0)
                if pan_deg is not None:
                    z = np.where(_cone(lay.center_az_rad, pan_deg, lay.hfov_deg), z, 0)
                np.maximum(best, z.astype(np.int8), out=best)
    full = np.zeros(mv.grid_shape, np.int8)
    full[mv.window] = best
    return full


# ── many mounts ─────────────────────────────────────────────────────────────
@dataclass
class Mount:
    x_m: float
    y_m: float
    mast_m: float
    camera: CameraConfig
    pan_deg: float = 0.0
    tilt_deg: float = -5.0
    focal_mm: Optional[float] = None      # current setting (None = wide end)
    ptz: bool = False
    pan_search_deg: float = 0.0           # fixed only: also search the heading
    label: str = ""


@dataclass
class MountChoice:
    label: str
    ptz: bool
    focal_mm: float
    tilt_deg: float
    pan_deg: float
    hfov_deg: float
    vfov_deg: float
    changed: bool


@dataclass
class LayoutResult:
    choices: List[MountChoice]
    before_m2: Dict[int, float]           # fixed cameras, continuous, starting settings
    after_m2: Dict[int, float]            # fixed cameras, continuous, optimised
    ptz_on_demand_m2: Dict[int, float]    # PTZ envelopes (any pan / tilt / focal)
    combined_m2: Dict[int, float]         # fixed continuous OR PTZ on demand
    roi_area_m2: float
    fixed_zone: np.ndarray                # full grid ZONE_*, optimised fixed union
    ptz_zone: np.ndarray                  # full grid ZONE_*, PTZ on-demand union
    level: int
    objective: str
    passes_run: int
    notes: List[str] = field(default_factory=list)


@dataclass
class _Cand:
    focal: float
    tilt: float
    pan: float
    hfov: float
    vfov: float
    sl: Tuple[slice, slice]               # absolute grid slices of the crop below
    zone: np.ndarray                      # int8, cropped to the view's own footprint


def _crop(zone: np.ndarray, window: Tuple[slice, slice]):
    """Keep only the bounding box a view covers: a tele view is a thin
    wedge of a big window; storing whole windows for every candidate of
    every pole ran to hundreds of MB."""
    rows = np.flatnonzero(zone.any(axis=1))
    if rows.size == 0:
        r = window[0].start
        c = window[1].start
        return (slice(r, r), slice(c, c)), np.zeros((0, 0), np.int8)
    cols = np.flatnonzero(zone.any(axis=0))
    r0, r1, c0, c1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
    sl = (slice(window[0].start + r0, window[0].start + r1),
          slice(window[1].start + c0, window[1].start + c1))
    return sl, np.ascontiguousarray(zone[r0:r1, c0:c1], dtype=np.int8)


def _candidates(terrain, m: Mount, level, n_focals, rng, vis, weather) -> List[_Cand]:
    mv = mount_visibility(terrain, m.x_m, m.y_m, m.mast_m, max_range_m=rng)
    cands: List[_Cand] = []

    def add(f, t, pans):
        lay = view_layers(mv, m.camera, tilt_deg=t, focal_mm=f, max_range_m=rng,
                          visibility_km=vis, weather=weather)
        zv = np.where(lay.visible, lay.zone, 0)
        for p in pans:
            sl, z = _crop(np.where(_cone(lay.center_az_rad, p, lay.hfov_deg), zv, 0), mv.window)
            cands.append(_Cand(f, t, p, lay.hfov_deg, lay.vfov_deg, sl, z))

    current = m.focal_mm if m.focal_mm is not None else float(m.camera.focal_min_mm)
    add(current, m.tilt_deg, [m.pan_deg % 360.0])          # index 0 = the starting setting
    pans = _pan_grid(m.pan_deg, m.pan_search_deg, 10.0)
    for f in focal_steps(m.camera, n_focals):
        for t in auto_tilts(m.camera, f, m.mast_m, level, rng):
            add(f, t, pans)
    return cands


def optimize_layout(terrain: TerrainData, mounts: Sequence[Mount], *, roi: Optional[np.ndarray] = None,
                    level: int = ZONE_DETECT, objective: str = "area", n_focals: int = 8,
                    passes: int = 2, visibility_km: float = 40.0, weather: str = "",
                    progress: Optional[Callable[[int, int], None]] = None) -> LayoutResult:
    """Greedy coordinate ascent over the fixed mounts' settings on the union
    coverage of ``roi`` (whole grid if None); PTZ mounts as on-demand reach."""
    shape = terrain.z_grid.shape
    cell_area = terrain.cell_size_m ** 2
    roi_full = np.ones(shape, bool) if roi is None else roi.astype(bool)
    weights = level_weights(objective, level)
    fixed = [m for m in mounts if not m.ptz]
    ptzs = [m for m in mounts if m.ptz]
    total = len(fixed) + len(ptzs)

    # per-level coverage counts of the CURRENT choices (fixed only)
    cnt = {lv: np.zeros(shape, np.int32) for lv in LEVELS}
    cands = []
    for i, m in enumerate(fixed):
        cs = _candidates(terrain, m, level, n_focals, _mount_range(m.camera, None),
                         visibility_km, weather)
        cands.append(cs)
        for lv in LEVELS:
            cnt[lv][cs[0].sl] += cs[0].zone >= lv
        if progress:
            progress(i + 1, total)
    choice = [0] * len(fixed)

    def union_zone():
        z = np.zeros(shape, np.int8)
        for lv in LEVELS:
            z[cnt[lv] > 0] = lv
        return z

    before_zone = union_zone()
    passes_run = 0
    for _ in range(max(passes, 1)):
        passes_run += 1
        moved = False
        for i in range(len(fixed)):
            cs = cands[i]
            cur = cs[choice[i]]
            for lv in LEVELS:
                cnt[lv][cur.sl] -= cur.zone >= lv
            gains = []
            for c in cs:
                g = 0.0
                if c.zone.size:
                    roi_c = roi_full[c.sl]
                    for lv, w in weights.items():
                        if w:
                            g += w * ((cnt[lv][c.sl] == 0) & roi_c & (c.zone >= lv)).sum()
                gains.append(g)
            k = int(np.argmax(gains))
            if gains[k] > gains[choice[i]] + 1e-9:       # strictly better, else keep
                choice[i], moved = k, True
            new = cs[choice[i]]
            for lv in LEVELS:
                cnt[lv][new.sl] += new.zone >= lv
        if not moved:
            break
    after_zone = union_zone()

    ptz_zone = np.zeros(shape, np.int8)
    for j, m in enumerate(ptzs):
        env = envelope(terrain, m.x_m, m.y_m, m.mast_m, m.camera, pan_deg=None, level=level,
                       n_focals=n_focals, visibility_km=visibility_km, weather=weather)
        np.maximum(ptz_zone, env, out=ptz_zone)
        if progress:
            progress(len(fixed) + j + 1, total)
    combined = np.maximum(after_zone, ptz_zone)

    choices: List[MountChoice] = []
    for m, cs, k in zip(fixed, cands, choice):
        c = cs[k]
        choices.append(MountChoice(m.label, False, c.focal, c.tilt, c.pan, c.hfov, c.vfov, k != 0))
    for m in ptzs:
        choices.append(MountChoice(m.label, True, float(m.camera.focal_min_mm), m.tilt_deg,
                                   m.pan_deg, 0.0, 0.0, False))
    notes = []
    if ptzs:
        notes.append("PTZ alanı talep üzerine erişimdir (herhangi bir yön / eğim / odakta) — "
                     "aynı anda değil; sürekli kapsama yalnız sabit kameralardan gelir.")
    return LayoutResult(
        choices=choices,
        before_m2=level_areas(before_zone, roi_full, cell_area),
        after_m2=level_areas(after_zone, roi_full, cell_area),
        ptz_on_demand_m2=level_areas(ptz_zone, roi_full, cell_area),
        combined_m2=level_areas(combined, roi_full, cell_area),
        roi_area_m2=float(roi_full.sum()) * cell_area,
        fixed_zone=after_zone, ptz_zone=ptz_zone, level=level, objective=objective,
        passes_run=passes_run, notes=notes,
    )


def fence_roi(terrain: TerrainData, fence_points: Sequence[Tuple[float, float]], closed: bool,
              band_m: float = 40.0) -> Optional[np.ndarray]:
    """Cells within ``band_m`` of the fence line (the band a perimeter plan
    protects; same rule as perimeter_planner's coverage denominator)."""
    from .perimeter_planner import _fence_band_mask
    pts = list(fence_points)
    if closed and len(pts) > 2 and tuple(pts[0]) != tuple(pts[-1]):
        pts = pts + [pts[0]]
    cols = terrain.origin_x + np.arange(terrain.cols) * terrain.cell_size_m
    rows = terrain.origin_y + np.arange(terrain.rows) * terrain.cell_size_m
    mx, my = np.meshgrid(cols, rows)
    return _fence_band_mask(mx, my, pts, band_m)
