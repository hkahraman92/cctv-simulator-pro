"""
3D Viewshed, Line-of-Sight (LOS) Raymarching and DORI Occlusion Analysis Engine.

Performs:
1. Fast 3D raymarching over topographical DEM matrices.
2. Obstacle & hill ridge occlusion detection (Kör Nokta / Blind Spot Masking).
3. Earth curvature and atmospheric refraction correction for long-range surveillance (>3 km).
4. Continuous 3D Slant-Range DORI / Johnson criteria calculation across terrain cells.
5. Sightline cross-section elevation profile generation.
"""
from __future__ import annotations

import contextlib
import contextvars
import math
from dataclasses import dataclass, fields, replace
from typing import Dict, List, Optional, Tuple

import numpy as np

from .terrain_loader import TerrainData
from .models import CameraConfig
from .calculations import CameraOptics, camera_optics
from .atmosphere import band_for_camera, usable_range_m


# DORI PPM Thresholds (EN 62676-4:2015, Tablo B.1 — hedef düzleminde px/m)
PPM_IDENT = 250.0       # Identification / Teşhis
PPM_RECOG = 125.0       # Recognition / Tanıma
PPM_OBSERVE = 62.5      # Observation / Gözlem
PPM_DETECT = 25.0       # Detection / Algılama
PPM_MONITOR = 12.5      # Monitoring and control / İzleme (DORI'nin en düşük seviyesi)
PPM_OVERVIEW = PPM_MONITOR  # geriye dönük ad

# Zone Codes -- monotonic: a higher code is a finer level, so ``zone >= level``
# means "at least that level". Always use the names; the integers have moved
# once already (ZONE_MONITOR was inserted below Detection).
# ZONE_MONITOR exists only in a run that asks for the monitor floor
# (``monitor_floor=True``); the default floor is Detection and no cell is ever
# coded ZONE_MONITOR then.
ZONE_OUT_OF_FOV = 0
ZONE_OCCLUDED = 1       # Kör Nokta / Tepe Arkası Gölge
ZONE_MONITOR = 2        # İzleme (>= 12.5 px/m) -- opt-in, visible cameras only
ZONE_DETECT = 3         # Algılama (>= 25 px/m)
ZONE_OBSERVE = 4        # Gözlem (>= 62.5 px/m)
ZONE_RECOG = 5          # Tanıma (>= 125 px/m)
ZONE_IDENT = 6          # Teşhis (>= 250 px/m)

THERMAL_NO_MONITOR = ("Termal kamerada İzleme (12,5 px/m) seviyesi yoktur; Johnson algılama, tanıma ya da "
                      "teşhis seviyesini seçin.")


@dataclass
class ViewshedResult:
    """Complete 3D Viewshed & Occlusion Analysis Output."""
    visibility_mask: np.ndarray      # 2D boolean array (True = visible, False = occluded or out of FOV)
    dori_grid: np.ndarray            # 2D int array of ZONE_* codes
    ppm_grid: np.ndarray             # 2D float array with px/m values
    slant_dist_grid: np.ndarray      # 2D float array of 3D distance from camera lens

    # Optical Center Elevation Profile (Kesit Grafiği)
    profile_dists_m: np.ndarray
    profile_terrain_elev_m: np.ndarray
    profile_ray_elev_m: np.ndarray
    profile_is_visible: np.ndarray

    # Telemetry & Statistics
    cam_x_m: float
    cam_y_m: float
    cam_ground_z_m: float
    cam_total_z_m: float
    mast_height_m: float
    hfov_deg: float
    vfov_deg: float
    pan_deg: float
    tilt_deg: float
    max_range_m: float
    optical_limit_m: float

    # Area Metrics
    fov_area_m2: float
    visible_area_m2: float
    occluded_area_m2: float
    coverage_pct: float
    max_los_reach_m: float

    # Atmosphere
    atmospheric_limit_m: float = 0.0     # range where path transmission hits tau_min
    visibility_km: float = 40.0


_GRID_FIELDS = (("visibility_mask", np.bool_), ("dori_grid", np.int32),
                ("ppm_grid", np.float32), ("slant_dist_grid", np.float32))
_GRID_NAMES = tuple(n for n, _ in _GRID_FIELDS)


def _grid_property(name: str, dtype) -> property:
    def getter(self):
        full = self.__dict__.get("_full")
        if full is not None and name in full:           # explicitly assigned by a caller
            return full[name]
        out = np.zeros(self.__dict__["_shape"], dtype=dtype)     # transient: never cached
        r0, r1, c0, c1 = self.__dict__["_window"]
        out[r0:r1, c0:c1] = self.__dict__["_win"][name]
        return out

    def setter(self, value):
        self.__dict__.setdefault("_full", {})[name] = value

    return property(getter, setter, doc=f"Full-size ``{name}``, materialised on every access.")


class WindowedViewshedResult(ViewshedResult):
    """A ``ViewshedResult`` that stores its four terrain-size grids only inside
    the bounding box of the cells it actually wrote (``window`` = r0, r1, c0, c1
    in grid rows / columns, ``full_shape`` the terrain shape).

    ``visibility_mask`` / ``dori_grid`` / ``ppm_grid`` / ``slant_dist_grid``
    stay available as properties that build the full array (same dtype, shape
    and values as the unwindowed result) on EVERY access -- nothing is cached,
    so the memory is transient. New code reads the cheap ``*_window`` arrays.
    A multi-camera run keeps one of these per placement: a short-range fence
    camera costs its window, not 13 bytes x every terrain cell."""

    visibility_mask = _grid_property("visibility_mask", np.bool_)
    dori_grid = _grid_property("dori_grid", np.int32)
    ppm_grid = _grid_property("ppm_grid", np.float32)
    slant_dist_grid = _grid_property("slant_dist_grid", np.float32)

    @classmethod
    def from_full(cls, res: ViewshedResult) -> "WindowedViewshedResult":
        """Window ``res`` (copies the window, so ``res``'s big arrays can be freed)."""
        arrs = {n: getattr(res, n) for n in _GRID_NAMES}
        shape = arrs["dori_grid"].shape
        touched = ((arrs["dori_grid"] != 0) | arrs["visibility_mask"]
                   | (arrs["ppm_grid"] != 0) | (arrs["slant_dist_grid"] != 0))
        rows_any = np.flatnonzero(touched.any(axis=1))
        if rows_any.size:
            cols_any = np.flatnonzero(touched.any(axis=0))
            window = (int(rows_any[0]), int(rows_any[-1]) + 1, int(cols_any[0]), int(cols_any[-1]) + 1)
        else:
            window = (0, 0, 0, 0)
        r0, r1, c0, c1 = window
        obj = object.__new__(cls)
        for f in fields(ViewshedResult):
            if f.name not in _GRID_NAMES:
                obj.__dict__[f.name] = getattr(res, f.name)
        obj.__dict__["_shape"] = tuple(shape)
        obj.__dict__["_window"] = window
        obj.__dict__["_win"] = {n: np.array(a[r0:r1, c0:c1]) for n, a in arrs.items()}
        return obj

    @property
    def window(self) -> Tuple[int, int, int, int]:
        """(r0, r1, c0, c1): rows r0:r1, columns c0:c1 of the terrain grid; everything outside is zero."""
        return self.__dict__["_window"]

    @property
    def full_shape(self) -> Tuple[int, int]:
        return self.__dict__["_shape"]

    @property
    def visibility_window(self) -> np.ndarray:
        return self.__dict__["_win"]["visibility_mask"]

    @property
    def dori_window(self) -> np.ndarray:
        return self.__dict__["_win"]["dori_grid"]

    @property
    def ppm_window(self) -> np.ndarray:
        return self.__dict__["_win"]["ppm_grid"]

    @property
    def slant_window(self) -> np.ndarray:
        return self.__dict__["_win"]["slant_dist_grid"]

    @property
    def window_nbytes(self) -> int:
        """Bytes the stored grids hold (what this result keeps alive)."""
        return int(sum(a.nbytes for a in self.__dict__["_win"].values()))


_R_EARTH = 6371000.0
_K_REFRACT = 0.13


# ── target height ───────────────────────────────────────────────────────────
# A DORI cell is a PERSON standing there, not the ground. With a target height
# h the line of sight is tested to the head (a person behind a 1 m wall is
# seen) and the person counts as framed only if the head is inside the
# vertical frame (a shallow frame whose top ray dips below 1.8 m used to paint
# "Tanıma" where only legs are in the picture). 0 = the ground cell (the
# engine's behaviour before, bit-identical). A context variable so every
# engine path (single, multi, mount raster, sweeps, planners) uses the same
# value without threading a parameter through each; the service sets it per
# request from the terrain session (``target_height(h)``).
_TARGET_H = contextvars.ContextVar("cctv_target_height_m", default=0.0)


def current_target_height() -> float:
    return float(_TARGET_H.get())


def set_target_height(h: float):
    """Set for the current context; returns the token (``_TARGET_H.reset``)."""
    return _TARGET_H.set(max(0.0, float(h)))


@contextlib.contextmanager
def target_height(h: float):
    tok = set_target_height(h)
    try:
        yield
    finally:
        _TARGET_H.reset(tok)


def ray_step_size(cell_size_m: float, ray_step_m: float = 3.0) -> float:
    """0.35-0.5 of a cell per step so a one-cell-wide ridge cannot fall
    between two samples on a diagonal ray (the old 0.7-cell step could skip it)."""
    return max(min(ray_step_m, cell_size_m * 0.5), cell_size_m * 0.35)


@dataclass
class RayMarch:
    """Per-sample (ray, step) output of :func:`march_rays`."""
    valid: np.ndarray        # in-bounds and not past the ray's first exit
    rr: np.ndarray           # grid row / column of each sample
    cc: np.ndarray
    tan_angle: np.ndarray    # (terrain - eye) / ground distance, curvature-corrected
    slant: np.ndarray        # 3D lens-to-sample distance
    los_clear: np.ndarray    # no earlier terrain sample on the ray rises above it
    horizon: Optional[np.ndarray] = None   # running horizon (tan) of the samples BEFORE each one, crossings included


_CHUNK_ELEMS = 32_000


def ray_chunk(num_steps: int) -> int:
    """Rays per march block. PERF: the march builds ~15 (rays x steps) float
    temporaries; at the old fixed 512 rays a 1.6 km raster's block was ~28 MB
    and memory-bound. ~32k samples per block stays in cache: a 1.6 km mount
    raster 97 -> 58 ms (10 m cells), 986 -> 476 ms (2 m). Rays are independent
    and blocks run in ray order, so results are bit-identical."""
    return int(min(512, max(8, _CHUNK_ELEMS // max(num_steps, 1))))


def _steps_inside(terrain: TerrainData, x0: float, y0: float, ray_angles: np.ndarray,
                  step_dists: np.ndarray) -> int:
    """Number of leading ``step_dists`` that can still be inside the grid on
    any of ``ray_angles`` (conservative: never cuts a valid sample)."""
    cell = terrain.cell_size_m
    lo_x, hi_x = terrain.origin_x - cell, terrain.origin_x + terrain.cols * cell
    lo_y, hi_y = terrain.origin_y - cell, terrain.origin_y + terrain.rows * cell
    sx, sy = np.sin(ray_angles), np.cos(ray_angles)
    with np.errstate(divide="ignore", invalid="ignore"):
        tx = np.where(sx > 0, (hi_x - x0) / sx, np.where(sx < 0, (lo_x - x0) / sx, np.inf))
        ty = np.where(sy > 0, (hi_y - y0) / sy, np.where(sy < 0, (lo_y - y0) / sy, np.inf))
    t_exit = float(np.max(np.minimum(tx, ty))) if len(ray_angles) else 0.0
    n = int(np.searchsorted(step_dists, t_exit, side="right")) + 1
    return int(min(max(n, 1), len(step_dists)))


def march_rays(terrain: TerrainData, cam_x_m: float, cam_y_m: float, cam_z: float,
               ray_angles: np.ndarray, step_dists: np.ndarray,
               earth_curvature: bool = True, occluder_crossings: bool = True,
               target_h: float = 0.0) -> RayMarch:
    """Vectorised radial ray march with a running horizon -- the line-of-sight
    core shared by calculate_3d_viewshed (rays inside one FOV) and the per-mount
    360-degree raster (mount_visibility), so occlusion is decided one way.

    Same result as the old ray/step double loop, ~10x faster. Each ray walks
    step_dists; a running horizon angle (np.maximum.accumulate over prior valid
    steps) decides LOS. Terrain occlusion is physical -- a ridge blocks the
    sightline whether or not it sits inside the vertical frame or resolves
    enough pixels, so the horizon uses every valid step.
    BUGFIX (kept): it used to also require ppm_ok, so a close, steep ridge whose
    own slant range pushed its pixel density below the floor dropped out of the
    horizon -- cells behind it could then read as visible.
    """
    rows, cols, cell_size = terrain.rows, terrain.cols, terrain.cell_size_m
    # PERF: a ray is invalid from its first out-of-grid sample on, so steps
    # past the farthest grid exit of this batch of rays are pure waste (a
    # 1.6 km thermal mount raster on a 2 km DEM marched ~40 % of its samples
    # off the map). Cut there; results are bit-identical. The box is one cell
    # wider on the low side: astype(intp) truncates toward zero, so samples in
    # (origin - cell, origin) still land in column / row 0.
    # ``step_dists`` is (S,) shared by every ray, or (R, S) -- each ray its own distances (an exact
    # line of sight that must END on its target); only the shared form is cut at the grid exit.
    if step_dists.ndim == 1:
        step_dists = step_dists[:_steps_inside(terrain, cam_x_m, cam_y_m, ray_angles, step_dists)]
    sd = np.atleast_2d(step_dists)                                # (1,S) or (R,S)
    d = sd
    cur_xs = cam_x_m + d * np.sin(ray_angles)[:, None]            # (R,S)
    cur_ys = cam_y_m + d * np.cos(ray_angles)[:, None]
    cc = ((cur_xs - terrain.origin_x) / cell_size).astype(np.intp)
    rr = ((cur_ys - terrain.origin_y) / cell_size).astype(np.intp)
    in_bounds = (cc >= 0) & (cc < cols) & (rr >= 0) & (rr < rows)
    valid = np.logical_and.accumulate(in_bounds, axis=1)          # ray stops at first exit

    # BUGFIX: bilinear elevation at the sample point (node convention, as
    # TerrainData.get_elevation_at), not the nearest cell's. Nearest-cell
    # sampling turns every slope into terraces; on ground falling away from
    # the camera, the far end of each terrace rose above the start of the
    # next, so a perfectly planar 8 % down-slope read 18-45 % "occluded"
    # (a plane seen from above never hides itself). Cell attribution (rr/cc)
    # is unchanged.
    fc = np.clip((cur_xs - terrain.origin_x) / cell_size, 0.0, cols - 1.0)
    fr = np.clip((cur_ys - terrain.origin_y) / cell_size, 0.0, rows - 1.0)
    c0 = np.minimum(fc.astype(np.intp), max(cols - 2, 0))
    r0 = np.minimum(fr.astype(np.intp), max(rows - 2, 0))
    fx = fc - c0
    fy = fr - r0
    # PERF: one flat index + take() for the four corners (2x faster than
    # four fancy 2D gathers); the +1 neighbours are in range because c0/r0
    # stop one short of the edge (a 1-wide grid repeats its only column).
    zf = terrain.z_grid.ravel()
    i00 = r0 * cols + c0
    dc = 1 if cols > 1 else 0
    dr = cols if rows > 1 else 0
    z00, z01 = zf.take(i00), zf.take(i00 + dc)
    z10, z11 = zf.take(i00 + dr), zf.take(i00 + dr + dc)
    elev = (z00 + (z01 - z00) * fx) * (1.0 - fy) + (z10 + (z11 - z10) * fx) * fy
    curv_drop = (sd * sd) / (2.0 * _R_EARTH) * (1.0 - _K_REFRACT) if earth_curvature else 0.0
    dz = (elev - curv_drop) - cam_z
    tan_angle = dz / d
    slant = np.hypot(d, dz)

    tan_for_max = np.where(valid, tan_angle, -1e18)
    horizon_before = np.empty_like(tan_for_max)
    horizon_before[:, 0] = -1e18
    horizon_before[:, 1:] = np.maximum.accumulate(tan_for_max, axis=1)[:, :-1]
    if occluder_crossings and tan_for_max.shape[1] > 1:
        # BUGFIX: the bilinear surface bends only on the grid's node lines, and
        # between two samples (<= 0.5 cell apart) a ray can cross one node line
        # per axis whose height it never sampled: a one-cell ridge or wall read
        # up to 25 % low on a diagonal ray. Its height ON the crossing is exact
        # (linear between the two nodes), so add those points to the horizon.
        # PERF: a crossing lies on an edge of one of the two cells its samples
        # are in, so its height is at most the higher of those cells' corner
        # maxima -- and it can only matter where that bound's angle (seen from
        # the nearer sample, an upper bound) beats the horizon already reached.
        cm = _cell_max(terrain)
        cmax = cm.ravel().take(r0 * cm.shape[1] + c0) if cm.size else elev
        bound = np.maximum(cmax[:, 1:], cmax[:, :-1]) - _CROSSING_TOL_M
        drop_prev = curv_drop[:, :-1] if earth_curvature else 0.0
        num = (bound - drop_prev) - cam_z
        # the crossing sits between the two samples: below the eye the larger
        # distance gives the larger angle, above it the smaller one
        tan_ub = num / np.where(num < 0, sd[:, 1:], sd[:, :-1])
        horizon_incl = np.maximum(horizon_before[:, 1:], tan_for_max[:, 1:])
        need = np.zeros(valid.shape, bool)
        need[:, 1:] = (tan_ub > horizon_incl) & valid[:, 1:]
        cross = np.full(valid.shape, -1e18)
        n_need = int(need.sum())
        if n_need:
            if n_need > 0.25 * need.size:                 # dense: cheaper than gathering
                cross[:, 1:] = _node_line_crossings(terrain, fc[:, :-1], fc[:, 1:], fr[:, :-1], fr[:, 1:],
                                                    sd[:, :-1], sd[:, 1:], cam_z,
                                                    earth_curvature)
                cross[~need] = -1e18
            else:
                ri, si = np.nonzero(need)
                sdb = np.broadcast_to(sd, valid.shape)
                cross[ri, si] = _node_line_crossings(terrain, fc[ri, si - 1], fc[ri, si], fr[ri, si - 1], fr[ri, si],
                                                     sdb[ri, si - 1], sdb[ri, si], cam_z, earth_curvature)
        # a crossing between samples i-1 and i lies before sample i
        horizon_before[:, 1:] = np.maximum(horizon_before[:, 1:], np.maximum.accumulate(cross, axis=1)[:, 1:])
    # the target's head (target_h above the sample) against the horizon of the
    # ground before it; tan_angle itself stays the ground's (it builds the horizon)
    los = (tan_angle + target_h / d) >= horizon_before if target_h > 0 else tan_angle >= horizon_before
    return RayMarch(valid, rr, cc, tan_angle, slant, los, horizon_before)


_CELL_MAX_CACHE: Dict[Tuple[int, Tuple[int, int]], Tuple[np.ndarray, np.ndarray]] = {}
# A crossing must rise this much above the sightline it could block to count:
# far below any DEM's accuracy, it stops every gently curved cell from
# triggering the exact (slower) crossing evaluation.
_CROSSING_TOL_M = 0.05


def _cell_max(terrain: TerrainData) -> np.ndarray:
    """Highest of each cell's four corner nodes, (rows-1, cols-1); cached per grid."""
    z = terrain.z_grid
    key = (id(z), z.shape)
    # BUGFIX: the entry keeps the grid it was computed from and is used only for that
    # very object. Keyed on id() alone, a NEW terrain allocated at a freed grid's address
    # read the old terrain's maxima and the crossing pre-filter dropped real occluders:
    # a viewshed depended on what had run before (48 of 60 same-shape terrains differed).
    hit = _CELL_MAX_CACHE.get(key)
    if isinstance(hit, tuple) and hit[0] is z:
        return hit[1]
    if z.shape[0] < 2 or z.shape[1] < 2:
        cm = np.asarray(z, float)
    else:
        cm = np.maximum(np.maximum(z[:-1, :-1], z[:-1, 1:]), np.maximum(z[1:, :-1], z[1:, 1:]))
    if len(_CELL_MAX_CACHE) > 8:
        _CELL_MAX_CACHE.clear()
    _CELL_MAX_CACHE[key] = (z, cm)
    return cm


def _node_line_crossings(terrain: TerrainData, c0: np.ndarray, c1: np.ndarray, r0: np.ndarray, r1: np.ndarray,
                         d_prev, d_cur, cam_z: float, earth_curvature: bool) -> np.ndarray:
    """Tan-angle of the highest node-line crossing on the segment from
    (c0, r0) to (c1, r1) (fractional grid coordinates; any matching shapes;
    -1e18 = none). x-lines are integer columns, y-lines integer rows; on such
    a line the bilinear surface is linear between two nodes: exact heights."""
    rows, cols = terrain.rows, terrain.cols
    z = terrain.z_grid

    def axis(a0, a1, b0, b1, line_is_col: bool):
        k = np.floor(np.maximum(a0, a1))
        has = (k > np.minimum(a0, a1)) & (k >= 0)
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.where(has, (k - a0) / (a1 - a0), 0.0)
        # a crossing ON a sample is that sample (already in the horizon); counting
        # it again let a 1e-15 rounding difference hide a plane from itself
        has &= (t > 1e-6) & (t < 1.0 - 1e-6)
        t = np.clip(t, 0.0, 1.0)
        other = b0 + (b1 - b0) * t
        n_other = rows if line_is_col else cols
        o0 = np.clip(np.floor(other), 0, max(n_other - 2, 0)).astype(np.intp)
        fo = np.clip(other - o0, 0.0, 1.0)
        kk = np.clip(k, 0, (cols if line_is_col else rows) - 1).astype(np.intp)
        o1 = np.minimum(o0 + 1, n_other - 1)
        if line_is_col:
            h = z[o0, kk] + (z[o1, kk] - z[o0, kk]) * fo
        else:
            h = z[kk, o0] + (z[kk, o1] - z[kk, o0]) * fo
        dc = d_prev + (d_cur - d_prev) * t
        drop = dc * dc / (2.0 * _R_EARTH) * (1.0 - _K_REFRACT) if earth_curvature else 0.0
        with np.errstate(divide="ignore", invalid="ignore"):
            tan = np.where(has & (dc > 0), ((h - drop) - cam_z) / dc, -1e18)
        return tan

    return np.maximum(axis(c0, c1, r0, r1, True), axis(r0, r1, c0, c1, False))


def view_focal_mm(camera: CameraConfig, lens_mode: str = "min",
                  focal_mm_override: Optional[float] = None) -> float:
    """Focal length a view uses: an explicit (PTZ) zoom clamped to the lens
    range, else the min/max end."""
    if focal_mm_override and focal_mm_override > 0:
        return float(max(min(focal_mm_override, camera.focal_max_mm), camera.focal_min_mm))
    return camera.focal_min_mm if lens_mode == "min" else camera.focal_max_mm


# Thermal (Johnson / NATO STANAG 4347, human, models.DEFAULT_LEVELS): a thermal
# image never resolves a face, so its cells are classed on Johnson thresholds
# and never reach ZONE_IDENT. Codes keep their order: DETECT = Johnson
# detection, OBSERVE = Johnson recognition, RECOG = Johnson identification.
THERMAL_DETECT_PPM = 1.3        # Johnson: human detection
THERMAL_RECOG_PPM = 6.67        # Johnson: human recognition (6 cycles)
THERMAL_IDENT_PPM = 13.33       # Johnson: human identification (12 cycles)


def is_thermal_camera(camera: CameraConfig) -> bool:
    return ("LWIR" in camera.sensor_name.upper() or "MWIR" in camera.sensor_name.upper()
            or "TERMAL" in camera.model_name.upper())


def zone_ppm(zone: int, thermal: bool = False) -> float:
    """px/m a cell needs to be classed ``zone`` -- zone_codes' thresholds read
    the other way. Thermal: Johnson; ZONE_IDENT is never reached (inf) and
    ZONE_MONITOR does not exist (ValueError: a thermal has no monitoring level)."""
    if thermal:
        if zone == ZONE_MONITOR:
            raise ValueError(THERMAL_NO_MONITOR)
        return {ZONE_DETECT: THERMAL_DETECT_PPM, ZONE_OBSERVE: THERMAL_RECOG_PPM,
                ZONE_RECOG: THERMAL_IDENT_PPM}.get(zone, math.inf)
    return {ZONE_MONITOR: PPM_MONITOR, ZONE_DETECT: PPM_DETECT, ZONE_OBSERVE: PPM_OBSERVE,
            ZONE_RECOG: PPM_RECOG, ZONE_IDENT: PPM_IDENT}[zone]


def reject_monitor_level(level: int, what: str) -> None:
    """Raise a Turkish ValueError when ``level`` is ZONE_MONITOR and the feature
    ``what`` does not carry the monitor floor through. Without this a monitor
    target would be scored with the Detection floor and silently report
    Detection numbers as Monitoring."""
    if level == ZONE_MONITOR:
        raise ValueError(f"İzleme (12,5 px/m) seviyesi {what} için henüz desteklenmiyor; Algılama ya da "
                         f"üst bir seviye seçin.")


def identify_ppm(camera: CameraConfig) -> float:
    """The camera's own identification level: EN 62676-4 Identification
    (250 px/m) for a visible camera, Johnson identification (13.33) for a
    thermal -- a thermal never resolves 250 px/m worth of face detail, so
    judging it against the EN figure reports reachable ground as unreachable."""
    return THERMAL_IDENT_PPM if is_thermal_camera(camera) else PPM_IDENT


@dataclass(frozen=True)
class ViewLimits:
    optics: CameraOptics
    min_detect_ppm: float           # px/m floor a cell must reach to be shown
    optical_limit_m: float          # slant range where px/m falls to that floor
    atmospheric_limit_m: float      # range where path transmission hits tau_min
    effective_max_range_m: float    # what the view actually reaches
    thermal: bool = False           # zones on Johnson thresholds (zone_codes)
    monitor: bool = False           # floor is Monitoring: cells 12.5..25 px/m are ZONE_MONITOR (zone_codes)


def view_limits(camera: CameraConfig, focal_mm: float, max_range_m: float,
                visibility_km: float = 40.0, weather: str = "",
                monitor_floor: bool = False) -> ViewLimits:
    """Pixel-density floor and range caps of one camera view -- shared by
    calculate_3d_viewshed and the per-mount view masks (mount_visibility) so
    the two can never apply different rules.

    ``monitor_floor`` (opt-in, default = today's behaviour): lower the floor
    from Detection to EN 62676-4 Monitoring (12.5 px/m), which doubles the
    optical reach. A thermal camera has no monitoring level and its floor is
    already Johnson detection, so the flag changes nothing for it."""
    optics = camera_optics(camera, focal_mm)
    is_thermal = is_thermal_camera(camera)
    monitor = bool(monitor_floor) and not is_thermal
    # Floor PPM below which a cell is not worth showing. Non-thermal: the
    # EN 62676-4 Detection threshold (25 px/m) — the viewshed then covers exactly
    # the DORI-meaningful band (Detection and above), no arbitrary cut-off.
    # With the monitor floor: Monitoring (12.5 px/m).
    min_detect_ppm = THERMAL_DETECT_PPM if is_thermal else (PPM_MONITOR if monitor else PPM_DETECT)
    optical_limit_m = optics.distance_for_ppm(min_detect_ppm)
    # Fog / rain / haze cap the range where target contrast survives the path.
    atm_band = band_for_camera(camera.sensor_name, camera.model_name)
    atmospheric_limit_m = usable_range_m(optical_limit_m, visibility_km, atm_band, weather)
    # Effective raymarching range cannot exceed optical or atmospheric limits
    effective = min(max_range_m, optical_limit_m * 1.15, atmospheric_limit_m)
    return ViewLimits(optics, min_detect_ppm, optical_limit_m, atmospheric_limit_m, effective, is_thermal, monitor)


def zone_codes(visible, framed, ppm, thermal: bool = False, monitor: bool = False):
    """ZONE_* per sample/cell from visibility, framing and px/m -- the one
    DORI classification both the engine and the mount view masks use.
    ``thermal``: Johnson thresholds, never ZONE_IDENT (see THERMAL_*).
    ``monitor`` (visible cameras, set by a monitor-floor run): a visible cell
    under Detection is ZONE_MONITOR; from Detection up nothing changes."""
    zone = np.zeros(np.shape(ppm), dtype=np.int32)                # ZONE_OUT_OF_FOV
    zone = np.where(framed & ~visible, ZONE_OCCLUDED, zone)       # framed but ridge-blocked
    if monitor and not thermal:
        zone = np.where(visible, ZONE_MONITOR, zone)
        zone = np.where(visible & (ppm >= PPM_DETECT), ZONE_DETECT, zone)
    else:
        zone = np.where(visible, ZONE_DETECT, zone)
    if thermal:
        zone = np.where(visible & (ppm >= THERMAL_RECOG_PPM), ZONE_OBSERVE, zone)
        return np.where(visible & (ppm >= THERMAL_IDENT_PPM), ZONE_RECOG, zone)
    zone = np.where(visible & (ppm >= PPM_OBSERVE), ZONE_OBSERVE, zone)
    zone = np.where(visible & (ppm >= PPM_RECOG), ZONE_RECOG, zone)
    zone = np.where(visible & (ppm >= PPM_IDENT), ZONE_IDENT, zone)
    return zone


def calculate_3d_viewshed(terrain: TerrainData,
                          cam_x_m: float,
                          cam_y_m: float,
                          mast_height_m: float,
                          camera: CameraConfig,
                          lens_mode: str = "min",
                          pan_deg: float = 0.0,
                          tilt_deg: float = -5.0,
                          max_range_m: float = 2000.0,
                          earth_curvature: bool = True,
                          ray_step_m: float = 3.0,
                          visibility_km: float = 40.0,
                          weather: str = "",
                          focal_mm_override: Optional[float] = None,
                          with_profile: bool = True,
                          monitor_floor: bool = False) -> ViewshedResult:
    """Computes comprehensive 3D viewshed, terrain occlusion, and DORI mapping.

    ``focal_mm_override`` sets an explicit focal length (mm) — for a PTZ preset
    at an intermediate zoom, instead of the min/max lens_mode ends.
    ``with_profile=False`` skips the (unused-in-batch) elevation cross-section.
    ``monitor_floor=True`` (opt-in, default False) adds EN 62676-4 Monitoring
    (12.5 px/m) below Detection for a visible camera: cells between 12.5 and
    25 px/m become ZONE_MONITOR and the analysed range grows to match. It is
    the default run EXTENDED, never a different classification: every cell the
    default run codes (Detection and up, or blocked at >= 25 px/m) keeps that
    code, so the ``>= ZONE_DETECT`` set is identical to a default run's. (One
    pass could not promise that: the monitor floor lengthens the range and
    the cells at the old edge must keep their Detection codes.) A thermal camera has no
    monitoring level and ignores the flag.
    """
    kw = dict(terrain=terrain, cam_x_m=cam_x_m, cam_y_m=cam_y_m, mast_height_m=mast_height_m, camera=camera,
              lens_mode=lens_mode, pan_deg=pan_deg, tilt_deg=tilt_deg, max_range_m=max_range_m,
              earth_curvature=earth_curvature, ray_step_m=ray_step_m, visibility_km=visibility_km,
              weather=weather, focal_mm_override=focal_mm_override)
    extend = bool(monitor_floor) and not is_thermal_camera(camera)
    base = _viewshed_core(**kw, with_profile=with_profile and not extend, monitor_floor=False)
    if not extend:
        return base
    ext = _viewshed_core(**kw, with_profile=with_profile, monitor_floor=True)
    return _merge_monitor_extension(terrain, base, ext)


def _merge_monitor_extension(terrain: TerrainData, base: ViewshedResult, ext: ViewshedResult) -> ViewshedResult:
    """``base`` (Detection floor) with the Monitoring band of ``ext`` (monitor
    floor) added where ``base`` has no opinion (zone 0). A cell ``base`` codes
    (blocked or Detection+) keeps its code; ``ext`` may only add ZONE_MONITOR /
    ZONE_OCCLUDED, so the >= Detection set stays the default run's."""
    take = (base.dori_grid == ZONE_OUT_OF_FOV) & (ext.dori_grid != ZONE_OUT_OF_FOV) & (ext.dori_grid < ZONE_DETECT)
    dori = np.where(take, ext.dori_grid, base.dori_grid).astype(np.int32)
    vis = base.visibility_mask | (take & ext.visibility_mask)
    ppm = np.where(take, ext.ppm_grid, base.ppm_grid)
    slant = np.where(take, ext.slant_dist_grid, base.slant_dist_grid)
    cell_a = terrain.cell_size_m ** 2
    vis_cells = int(np.count_nonzero(vis))
    rr, cc = np.nonzero(vis)
    reach = float(np.max(np.hypot(terrain.origin_x + cc * terrain.cell_size_m - ext.cam_x_m,
                                  terrain.origin_y + rr * terrain.cell_size_m - ext.cam_y_m))) if rr.size else 0.0
    fov_area = ext.fov_area_m2
    return replace(
        ext, visibility_mask=vis, dori_grid=dori, ppm_grid=ppm, slant_dist_grid=slant,
        visible_area_m2=vis_cells * cell_a,
        occluded_area_m2=int(np.count_nonzero(dori == ZONE_OCCLUDED)) * cell_a,
        coverage_pct=(vis_cells * cell_a / max(fov_area, 1.0)) * 100.0,
        max_los_reach_m=reach)


# How a node's line of sight is decided (see _node_los): "bracket" = from the two fan rays either side,
# a node they leave open (a shadow edge) gets its own exact ray; "exact" = every framed node gets one
# (the slow, reference-like ceiling, for measurement).
_LOS_MODE = "bracket"
_EXACT_STEP_CELLS = 0.2          # step of a node's own exact ray, in cells
_LOS_MARGIN = 1.0               # how many ray-horizon spreads a head must clear before the rays are trusted
_LOS_FLOOR_CELLS = 0.015         # height (in cells) a head must clear the fan's horizon by, on top of the rays' spread
_LOS_TOL = 1e-9                  # tan units: absorbs float rounding on a plane that grazes itself
LOS_STATS = {"nodes": 0, "open": 0, "unreached": 0, "exact": 0}


def _exact_horizon(terrain: TerrainData, cam_x_m: float, cam_y_m: float, cam_z: float, az: np.ndarray,
                   d: np.ndarray, step_size: float, earth_curvature: bool, target_h: float) -> np.ndarray:
    """Running horizon (tan) of the surface strictly before each node along the node's OWN line of
    sight: one ray per node at its exact azimuth, sampled <= ``step_size`` apart with the LAST sample
    on the node itself (march_rays with per-ray distances, crossings included)."""
    out = np.empty(len(az), np.float64)
    order = np.argsort(d)
    pos = 0
    while pos < len(order):
        n_here = max(8, 4 * _CHUNK_ELEMS // max(int(math.ceil(d[order[min(len(order) - 1, pos)]] / step_size)), 1))
        idx = order[pos:pos + n_here]
        pos += n_here
        S = max(int(math.ceil(d[idx].max() / step_size)), 2)
        frac = np.arange(1, S + 1, dtype=np.float64) / S
        for a in range(0, len(idx), ray_chunk(S)):
            sel = idx[a:a + ray_chunk(S)]
            steps = d[sel][:, None] * frac[None, :]
            m = march_rays(terrain, cam_x_m, cam_y_m, cam_z, az[sel], steps, earth_curvature, True, target_h)
            out[sel] = m.horizon[:, S - 1]
    return out


def build_fan(terrain: TerrainData, cam_x_m: float, cam_y_m: float, cam_z: float, ray_angles: np.ndarray,
              step_dists: np.ndarray, earth_curvature: bool, target_h: float) -> np.ndarray:
    """(rays, steps): the running horizon (tan) of the surface up to AND including each ray sample,
    crossings included; nan where the ray has left the grid."""
    fan = np.full((len(ray_angles), len(step_dists)), np.nan)
    chunk = ray_chunk(len(step_dists))
    for start in range(0, len(ray_angles), chunk):
        m = march_rays(terrain, cam_x_m, cam_y_m, cam_z, ray_angles[start:start + chunk], step_dists,
                       earth_curvature, True, target_h)
        n, r = m.valid.shape[1], m.valid.shape[0]
        incl = np.maximum(m.horizon, np.where(m.valid, m.tan_angle, -1e18))
        fan[start:start + r, :n] = np.where(m.valid, incl, np.nan)
    return fan


def fan_horizons(terrain: TerrainData, cam_x_m: float, cam_y_m: float, cam_z: float, fan: np.ndarray,
                 ray_angles: np.ndarray, step_size: float, ray_ix: np.ndarray, d: np.ndarray,
                 earth_curvature: bool) -> Tuple[np.ndarray, np.ndarray]:
    """Horizon (tan) of the surface strictly before nodes at ground distance ``d`` along fan rays
    ``ray_ix``: the last sample before the node plus the node-line crossings between it and the node
    (a crossing beyond the node cannot hide it). Returns (horizon, off_grid) -- ``off_grid`` where the
    ray had left the grid before the node."""
    num_steps = fan.shape[1]
    m_last = np.ceil(d / step_size).astype(np.intp) - 2           # last sample strictly before the node
    m_ix = np.clip(m_last, 0, num_steps - 1)
    d_from = np.where(m_last >= 0, (m_ix + 1) * step_size, 0.0)
    base = np.where(m_last >= 0, fan[ray_ix, m_ix], -1e18)
    off = np.isnan(base)
    sa, ca = np.sin(ray_angles[ray_ix]), np.cos(ray_angles[ray_ix])
    cell, cols, rows = terrain.cell_size_m, terrain.cols, terrain.rows
    fc0 = np.clip((cam_x_m + d_from * sa - terrain.origin_x) / cell, 0.0, cols - 1.0)
    fr0 = np.clip((cam_y_m + d_from * ca - terrain.origin_y) / cell, 0.0, rows - 1.0)
    fc1 = np.clip((cam_x_m + d * sa - terrain.origin_x) / cell, 0.0, cols - 1.0)
    fr1 = np.clip((cam_y_m + d * ca - terrain.origin_y) / cell, 0.0, rows - 1.0)
    cross = _node_line_crossings(terrain, fc0, fc1, fr0, fr1, d_from, d, cam_z, earth_curvature)
    return np.maximum(np.nan_to_num(base, nan=-1e18), cross), off


def _node_los(terrain: TerrainData, cam_x_m: float, cam_y_m: float, cam_z: float, d: np.ndarray,
              az: np.ndarray, head_tan: np.ndarray, pan_rad: float, half_hfov_rad: float, max_range_m: float,
              step_size: float, earth_curvature: bool, target_h: float, hfov_deg: float,
              cell_size: float) -> np.ndarray:
    """Line-of-sight verdict of framed nodes: the node's head against the horizon of the surface BEFORE it.

    A fan of rays (<~0.6 cell apart at the far edge, steps <= half a cell) gives each node a horizon
    from the two rays either side of it (``fan_horizons``). A head clear of both horizons by more than
    they differ from each other is visible, one below both by as much is hidden; anything else is a
    shadow edge and gets the node's own exact ray, as does a node no ray reaches (the fan left the
    grid first) -- it never silently turns visible or out of frame."""
    LOS_STATS["nodes"] += len(d)
    exact_step = min(step_size, _EXACT_STEP_CELLS * cell_size)
    if _LOS_MODE == "exact":
        LOS_STATS["exact"] += len(d)
        return head_tan >= _exact_horizon(terrain, cam_x_m, cam_y_m, cam_z, az, d, exact_step,
                                          earth_curvature, target_h) - _LOS_TOL
    span_need = half_hfov_rad * 2.0 * max_range_m / max(cell_size * 0.6, 0.5)
    num_rays = int(min(2200, max(180, hfov_deg * 4, span_need)))
    ray_angles = np.linspace(pan_rad - half_hfov_rad, pan_rad + half_hfov_rad, num_rays)
    num_steps = max(int(max_range_m / step_size), 1)
    fan = build_fan(terrain, cam_x_m, cam_y_m, cam_z, ray_angles, np.arange(1, num_steps + 1) * step_size,
                    earth_curvature, target_h)

    diff = np.arctan2(np.sin(az - pan_rad), np.cos(az - pan_rad))
    kf = np.clip((diff + half_hfov_rad) / (2.0 * half_hfov_rad) * (num_rays - 1), 0.0, num_rays - 1.0)
    i0 = np.minimum(kf.astype(np.intp), num_rays - 2)
    h_a, off_a = fan_horizons(terrain, cam_x_m, cam_y_m, cam_z, fan, ray_angles, step_size, i0, d, earth_curvature)
    h_b, off_b = fan_horizons(terrain, cam_x_m, cam_y_m, cam_z, fan, ray_angles, step_size, i0 + 1, d, earth_curvature)
    unknown = off_a | off_b
    h_hi, h_lo = np.maximum(h_a, h_b), np.minimum(h_a, h_b)
    width = np.minimum(h_hi - h_lo, 1.0) * _LOS_MARGIN + _LOS_FLOOR_CELLS * cell_size / d
    sure_vis = head_tan >= h_hi + width - _LOS_TOL
    sure_hid = head_tan < h_lo - width - _LOS_TOL
    visible = sure_vis.copy()
    redo = unknown | ~(sure_vis | sure_hid)
    LOS_STATS["unreached"] += int(unknown.sum())
    LOS_STATS["open"] += int((redo & ~unknown).sum())
    if redo.any():
        idx = np.nonzero(redo)[0]
        hor = _exact_horizon(terrain, cam_x_m, cam_y_m, cam_z, az[idx], d[idx], exact_step, earth_curvature, target_h)
        visible[idx] = head_tan[idx] >= hor - _LOS_TOL
        LOS_STATS["exact"] += len(idx)
    return visible


def _viewshed_core(terrain: TerrainData,
                   cam_x_m: float,
                   cam_y_m: float,
                   mast_height_m: float,
                   camera: CameraConfig,
                   lens_mode: str = "min",
                   pan_deg: float = 0.0,
                   tilt_deg: float = -5.0,
                   max_range_m: float = 2000.0,
                   earth_curvature: bool = True,
                   ray_step_m: float = 3.0,
                   visibility_km: float = 40.0,
                   weather: str = "",
                   focal_mm_override: Optional[float] = None,
                   with_profile: bool = True,
                   monitor_floor: bool = False) -> ViewshedResult:
    """One scatter pass at one floor (see calculate_3d_viewshed)."""
    rows, cols = terrain.rows, terrain.cols
    cell_size = terrain.cell_size_m
    target_h = current_target_height()

    # 1. Camera 3D Position
    cam_ground_z = terrain.ground_elevation_at(cam_x_m, cam_y_m)   # mast on the ground, not a roof
    cam_z = cam_ground_z + mast_height_m

    # 2. Camera Optics & FOV
    focal_mm = view_focal_mm(camera, lens_mode, focal_mm_override)
    lim = view_limits(camera, focal_mm, max_range_m, visibility_km, weather, monitor_floor)
    optics = lim.optics
    min_detect_ppm = lim.min_detect_ppm
    optical_limit_m = lim.optical_limit_m
    atmospheric_limit_m = lim.atmospheric_limit_m
    effective_max_range = lim.effective_max_range_m

    hfov_deg = optics.hfov_deg
    vfov_deg = optics.vfov_deg
    half_hfov_rad = math.radians(hfov_deg / 2.0)

    # Pan angle in math radians (0 deg = +Y / North, 90 deg = +X / East)
    pan_rad = math.radians(pan_deg)
    opt_dir_x = math.sin(pan_rad)
    opt_dir_y = math.cos(pan_rad)

    # 3. Analytic cone on the camera's range window only. PERF: it used to be
    # evaluated over the whole terrain for every camera -- a 20 m-reach fence
    # camera did 40k cells of trig on a 200x200 DEM, ~80 % of a multi-camera
    # run. Every cell outside the window fails ground_dist <= range anyway,
    # so the cone is bit-identical.
    r0 = int(np.clip(math.floor((cam_y_m - effective_max_range - terrain.origin_y) / cell_size) - 1, 0, rows))
    r1 = int(np.clip(math.ceil((cam_y_m + effective_max_range - terrain.origin_y) / cell_size) + 2, 0, rows))
    c0 = int(np.clip(math.floor((cam_x_m - effective_max_range - terrain.origin_x) / cell_size) - 1, 0, cols))
    c1 = int(np.clip(math.ceil((cam_x_m + effective_max_range - terrain.origin_x) / cell_size) + 2, 0, cols))
    win = (slice(r0, r1), slice(c0, c1))
    grid_x = terrain.origin_x + np.arange(c0, c1) * cell_size
    grid_y = terrain.origin_y + np.arange(r0, r1) * cell_size
    mesh_x, mesh_y = np.meshgrid(grid_x, grid_y)

    dx = mesh_x - cam_x_m
    dy = mesh_y - cam_y_m
    ground_dist = np.hypot(dx, dy)                                # window

    cell_azimuth_rad = np.arctan2(dx, dy)
    angle_diff_rad = np.arctan2(np.sin(cell_azimuth_rad - pan_rad),
                                np.cos(cell_azimuth_rad - pan_rad))

    in_cone_w = (np.abs(angle_diff_rad) <= half_hfov_rad) & (ground_dist <= effective_max_range) & (ground_dist > 1.0)

    # 4. Initialize Output Grids
    vis_mask = np.zeros((rows, cols), dtype=bool)
    dori_grid = np.zeros((rows, cols), dtype=np.int32)
    ppm_grid = np.zeros((rows, cols), dtype=np.float32)
    slant_dist_grid = np.zeros((rows, cols), dtype=np.float32)

    R_EARTH = _R_EARTH
    K_REFRACT = _K_REFRACT

    # Vertical framing: a ground cell is only *seen* if its line from the lens
    # falls between the bottom and top rays (tilt +/- vfov/2). Without this the
    # grid reports coverage the camera never frames (near dead zone, far sky).
    v_lo = math.radians(tilt_deg - vfov_deg / 2.0)     # bottom ray (steeper down)
    v_hi = math.radians(tilt_deg + vfov_deg / 2.0)     # top ray

    # 5. A cell is decided AT ITS NODE. Its slant, px/m, framing and zone come
    # from the node's own position (the ground point, the head for the vertical
    # frame); the rays supply only the line-of-sight verdict (see _node_los).
    # (Until now a ray SAMPLE classified the node it truncated to -- up to a
    # cell away, the last and farthest sample winning -- so every class edge
    # sat a fraction of a cell off and shares drifted ~0.6 pt per metre of cell.)
    cell_dz = terrain.z_grid[win].astype(np.float64) - cam_z
    if earth_curvature:
        cell_dz -= (ground_dist ** 2) / (2.0 * R_EARTH) * (1.0 - K_REFRACT)
    cell_elev_angle = np.arctan2(cell_dz, np.maximum(ground_dist, 0.1))
    in_fov_w = in_cone_w & (cell_elev_angle >= v_lo) & (cell_elev_angle <= v_hi)

    node_slant = np.hypot(ground_dist, cell_dz)
    node_ppm = optics.ppm_at(np.maximum(node_slant, 1.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        node_tan = cell_dz / ground_dist
        head_tan = node_tan + target_h / ground_dist if target_h > 0 else node_tan
    head_elev = np.arctan(head_tan)
    framed_w = in_fov_w & (node_ppm >= min_detect_ppm) & (head_elev >= v_lo) & (head_elev <= v_hi)

    visible_w = np.zeros(framed_w.shape, bool)
    fr_, fc_ = np.nonzero(framed_w)
    if fr_.size:
        step_size = ray_step_size(cell_size, ray_step_m)
        visible_w[fr_, fc_] = _node_los(
            terrain, cam_x_m, cam_y_m, cam_z, ground_dist[fr_, fc_], cell_azimuth_rad[fr_, fc_],
            head_tan[fr_, fc_], pan_rad, half_hfov_rad, effective_max_range, step_size, earth_curvature,
            target_h, hfov_deg, cell_size)

    zone_w = zone_codes(visible_w, framed_w, node_ppm, lim.thermal, lim.monitor)
    dori_grid[win] = np.where(in_fov_w, zone_w, ZONE_OUT_OF_FOV)
    vis_mask[win] = visible_w
    # px/m and slant: the node's own, over the whole horizontal wedge in range (framed or not)
    ppm_grid[win] = np.where(in_cone_w, node_ppm, 0.0)
    slant_dist_grid[win] = np.where(in_cone_w, node_slant, 0.0)
    in_fov_cone = np.zeros((rows, cols), dtype=bool)
    in_fov_cone[win] = in_fov_w

    # 6. Elevation profile along the central optical axis (skipped in batch runs
    # that never read the per-camera cross-section).
    if with_profile:
        prof_dists, prof_elevs, prof_coords = terrain.get_profile_between(
            cam_x_m, cam_y_m,
            cam_x_m + effective_max_range * opt_dir_x,
            cam_y_m + effective_max_range * opt_dir_y,
            num_samples=250
        )
        tilt_rad = math.radians(tilt_deg)
        prof_ray_z = cam_z + prof_dists * math.tan(tilt_rad)
        prof_vis = np.ones_like(prof_dists, dtype=bool)
        max_tan = -1e9
        for i, (d, el) in enumerate(zip(prof_dists, prof_elevs)):
            if d < 1.0:
                continue
            curv = ((d * d) / (2.0 * R_EARTH) * (1.0 - K_REFRACT)) if earth_curvature else 0.0
            tan_a = (el - curv - cam_z) / d
            if tan_a >= max_tan:
                max_tan = tan_a
                prof_vis[i] = True
            else:
                prof_vis[i] = False
    else:
        _empty = np.zeros(0, dtype=np.float64)
        prof_dists = prof_elevs = prof_ray_z = _empty
        prof_vis = np.zeros(0, dtype=bool)

    # 7. Compute Statistics
    cell_area = cell_size * cell_size
    fov_cells = int(np.sum(in_fov_cone))
    vis_cells = int(np.sum(vis_mask & in_fov_cone))
    occ_cells = int(np.sum((dori_grid == ZONE_OCCLUDED) & in_fov_cone))

    fov_area_m2 = fov_cells * cell_area
    visible_area_m2 = vis_cells * cell_area
    occluded_area_m2 = occ_cells * cell_area
    coverage_pct = (visible_area_m2 / max(fov_area_m2, 1.0)) * 100.0

    vis_dists = ground_dist[vis_mask[win] & in_cone_w]
    max_los_reach_m = float(np.max(vis_dists)) if len(vis_dists) > 0 else 0.0

    return ViewshedResult(
        visibility_mask=vis_mask,
        dori_grid=dori_grid,
        ppm_grid=ppm_grid,
        slant_dist_grid=slant_dist_grid,
        profile_dists_m=prof_dists,
        profile_terrain_elev_m=prof_elevs,
        profile_ray_elev_m=prof_ray_z,
        profile_is_visible=prof_vis,
        cam_x_m=cam_x_m,
        cam_y_m=cam_y_m,
        cam_ground_z_m=cam_ground_z,
        cam_total_z_m=cam_z,
        mast_height_m=mast_height_m,
        hfov_deg=hfov_deg,
        vfov_deg=vfov_deg,
        pan_deg=pan_deg,
        tilt_deg=tilt_deg,
        max_range_m=effective_max_range,
        optical_limit_m=optical_limit_m,
        fov_area_m2=fov_area_m2,
        visible_area_m2=visible_area_m2,
        occluded_area_m2=occluded_area_m2,
        coverage_pct=coverage_pct,
        max_los_reach_m=max_los_reach_m,
        atmospheric_limit_m=atmospheric_limit_m,
        visibility_km=visibility_km,
    )


# ── Çoklu kamera birleşik görüş alanı ────────────────────────────────────────

@dataclass
class CameraPlacement:
    """One camera pinned on the terrain for a combined-viewshed run."""
    x_m: float
    y_m: float
    mast_height_m: float
    camera: CameraConfig
    lens_mode: str = "min"
    pan_deg: float = 0.0
    tilt_deg: float = -5.0
    max_range_m: float = 2000.0
    label: str = ""
    focal_mm_override: Optional[float] = None   # explicit zoom (mm), overrides lens_mode
    # Physical camera this view belongs to. Placements sharing a camera_id are
    # time-shared views of ONE camera (a PTZ's presets): it never looks at
    # two of them at once, so redundancy counts it once. None = own camera.
    camera_id: Optional[str] = None


def placement_bounds_warnings(terrain: TerrainData,
                              placements: List["CameraPlacement"]) -> List[str]:
    """Non-fatal warnings for placements outside the terrain frame — their
    elevation is clamped to the nearest edge cell and the result is unreliable."""
    x0, y0 = terrain.origin_x, terrain.origin_y
    x1, y1 = x0 + terrain.width_m, y0 + terrain.height_m
    out = []
    for i, p in enumerate(placements):
        if not (x0 <= p.x_m <= x1 and y0 <= p.y_m <= y1):
            out.append(
                f"{p.label or f'yerleşim {i + 1}'}: ({p.x_m:.0f}, {p.y_m:.0f}) arazi "
                f"çerçevesi dışında [{x0:.0f}-{x1:.0f}] × [{y0:.0f}-{y1:.0f}] — "
                "zemin rakımı kenara sabitlendi, sonuç güvenilmez.")
    return out


@dataclass
class MultiViewshedResult:
    """Best-of-all-cameras DORI / visibility over the whole terrain grid."""
    dori_grid: np.ndarray             # ZONE_* — best level any camera achieves
    ppm_grid: np.ndarray             # best px/m from any camera
    visibility_mask: np.ndarray      # True where >= 1 camera has a clear framed LOS
    best_cam_grid: np.ndarray        # 1-based index of the strongest VIEW (placement), 0 = none
    seen_count_grid: np.ndarray      # how many PHYSICAL cameras cover the cell (redundancy)
    per_camera: List[ViewshedResult]
    labels: List[str]
    cell_size_m: float
    origin_x: float
    origin_y: float

    fov_area_m2: float               # union of every camera's frameable cone
    visible_area_m2: float
    occluded_area_m2: float          # in some cone, seen by nobody (terrain shadow)
    coverage_pct: float              # visible / union-cone
    overlap_area_m2: float           # cells seen by >= 2 physical cameras
    single_cover_area_m2: float      # cells seen by exactly 1 (no redundancy)
    pct_by_zone: Dict[str, float]    # ident/recog/observe/detect -> % of union cone
    camera_count: int = 0            # physical cameras (len(per_camera) counts views)


def calculate_multi_camera_viewshed(terrain: TerrainData,
                                    placements: List[CameraPlacement],
                                    *,
                                    earth_curvature: bool = True,
                                    visibility_km: float = 40.0,
                                    weather: str = "",
                                    monitor_floor: bool = False) -> Optional[MultiViewshedResult]:
    """Runs the authoritative single-camera engine for each placement and merges
    the grids: the combined map shows the best DORI level reachable at every
    ground cell, plus how many cameras overlap there.

    ``monitor_floor`` (default False = bit-identical to before): every visible
    camera is run with the Monitoring floor, so cells between 12.5 and 25 px/m
    become ZONE_MONITOR and ``pct_by_zone`` gains a ``"monitor"`` entry; a
    thermal placement is unaffected."""
    if not placements:
        return None

    rows, cols = terrain.rows, terrain.cols
    per: List[ViewshedResult] = []
    ppm = np.zeros((rows, cols), np.float32)
    best_cam = np.zeros((rows, cols), np.int32)
    vis = np.zeros((rows, cols), bool)
    union_cone = np.zeros((rows, cols), bool)
    best_code = np.zeros((rows, cols), np.int32)
    # BUGFIX: redundancy used to count VIEWS, so a PTZ whose 4 presets all
    # frame a cell read as "4 cameras" there -- inflating overlap and hiding
    # that the cell is unwatched whenever the PTZ looks elsewhere. Views of
    # one physical camera (same camera_id) are OR-ed and counted once.
    # Held as [r0, r1, c0, c1, window mask] (a camera only ever touches its window).
    cam_vis_by_id: Dict[object, list] = {}

    # MEMORY: each view is run, windowed and merged one at a time -- only one
    # full-size result is alive at once, and the kept per_camera entries hold
    # their window only (WindowedViewshedResult). The merge works on windows.
    # Every merged grid is bit-identical to merging the full-size arrays.
    for i, p in enumerate(placements, 1):
        full = calculate_3d_viewshed(
            terrain=terrain, cam_x_m=p.x_m, cam_y_m=p.y_m, mast_height_m=p.mast_height_m,
            camera=p.camera, lens_mode=p.lens_mode, pan_deg=p.pan_deg, tilt_deg=p.tilt_deg,
            max_range_m=p.max_range_m, earth_curvature=earth_curvature,
            visibility_km=visibility_km, weather=weather,
            focal_mm_override=p.focal_mm_override, with_profile=False,
            monitor_floor=monitor_floor,
        )
        r = WindowedViewshedResult.from_full(full)
        del full
        per.append(r)
        r0, r1, c0, c1 = r.window
        sl = (slice(r0, r1), slice(c0, c1))
        cam_vis = r.visibility_window
        cam_ppm = r.ppm_window
        cam_dori = r.dori_window
        better = cam_vis & (cam_ppm > ppm[sl])
        ppm[sl] = np.where(better, cam_ppm, ppm[sl])
        best_cam[sl] = np.where(better, i, best_cam[sl])
        vis[sl] |= cam_vis
        union_cone[sl] |= (cam_dori != ZONE_OUT_OF_FOV)
        best_code[sl] = np.maximum(best_code[sl], np.where(cam_vis, cam_dori, 0))
        key = p.camera_id if p.camera_id is not None else ("view", i)
        prev = cam_vis_by_id.get(key)
        if prev is None:
            cam_vis_by_id[key] = [r0, r1, c0, c1, cam_vis]
        elif r1 > r0 and c1 > c0:
            if prev[1] <= prev[0] or prev[3] <= prev[2]:
                cam_vis_by_id[key] = [r0, r1, c0, c1, cam_vis]
            else:
                u0, u1 = min(prev[0], r0), max(prev[1], r1)
                v0, v1 = min(prev[2], c0), max(prev[3], c1)
                both = np.zeros((u1 - u0, v1 - v0), bool)
                both[prev[0] - u0:prev[1] - u0, prev[2] - v0:prev[3] - v0] = prev[4]
                both[r0 - u0:r1 - u0, c0 - v0:c1 - v0] |= cam_vis
                cam_vis_by_id[key] = [u0, u1, v0, v1, both]

    seen = np.zeros((rows, cols), np.int32)
    for r0, r1, c0, c1, cam_vis in cam_vis_by_id.values():
        seen[r0:r1, c0:c1] += cam_vis.astype(np.int32)

    occ = union_cone & ~vis
    # BUGFIX: the merge used to re-threshold the merged best px/m with the EN 62676-4
    # constants, which erased every Johnson class of a thermal camera (recognition /
    # identification read as Detection). Each camera already classed its own cells
    # (zone_codes: EN or Johnson, monitor floor); zone codes rise with px/m inside one
    # classification, so the merged class is the best code of any camera that SEES the
    # cell (``best_code``). Bit-identical for visible cameras (the thresholds are the
    # same ones). A cell in ``occ`` is seen by nobody, so its best code is 0 there.
    dori = np.where(occ, ZONE_OCCLUDED, best_code).astype(np.int32)

    cell_a = terrain.cell_size_m ** 2
    union_cells = int(np.count_nonzero(union_cone))
    denom = float(max(union_cells, 1))
    vis_cells = int(np.count_nonzero(vis))
    pct = {
        "ident": 100.0 * np.count_nonzero(dori == ZONE_IDENT) / denom,
        "recog": 100.0 * np.count_nonzero(dori >= ZONE_RECOG) / denom,
        "observe": 100.0 * np.count_nonzero(dori >= ZONE_OBSERVE) / denom,
        "detect": 100.0 * np.count_nonzero(dori >= ZONE_DETECT) / denom,
    }
    if monitor_floor:
        pct["monitor"] = 100.0 * np.count_nonzero(dori >= ZONE_MONITOR) / denom

    return MultiViewshedResult(
        dori_grid=dori, ppm_grid=ppm, visibility_mask=vis, best_cam_grid=best_cam,
        seen_count_grid=seen, per_camera=per,
        labels=[p.label or f"K{i}" for i, p in enumerate(placements, 1)],
        cell_size_m=terrain.cell_size_m, origin_x=terrain.origin_x, origin_y=terrain.origin_y,
        fov_area_m2=union_cells * cell_a,
        visible_area_m2=vis_cells * cell_a,
        occluded_area_m2=int(np.count_nonzero(occ)) * cell_a,
        coverage_pct=100.0 * vis_cells / denom,
        overlap_area_m2=int(np.count_nonzero(seen >= 2)) * cell_a,
        single_cover_area_m2=int(np.count_nonzero(seen == 1)) * cell_a,
        pct_by_zone=pct,
        camera_count=len(cam_vis_by_id),
    )
