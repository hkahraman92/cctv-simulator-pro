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
from dataclasses import dataclass
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

# Zone Codes
ZONE_OUT_OF_FOV = 0
ZONE_OCCLUDED = 1       # Kör Nokta / Tepe Arkası Gölge
ZONE_DETECT = 2         # Algılama (>= 25 px/m)
ZONE_OBSERVE = 3        # Gözlem (>= 62.5 px/m)
ZONE_RECOG = 4          # Tanıma (>= 125 px/m)
ZONE_IDENT = 5          # Teşhis (>= 250 px/m)


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
    step_dists = step_dists[:_steps_inside(terrain, cam_x_m, cam_y_m, ray_angles, step_dists)]
    d = step_dists[None, :]                                       # (1,S)
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
    curv_drop = (step_dists * step_dists) / (2.0 * _R_EARTH) * (1.0 - _K_REFRACT) if earth_curvature else 0.0
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
        drop_prev = curv_drop[:-1] if earth_curvature else 0.0
        num = (bound - drop_prev) - cam_z
        # the crossing sits between the two samples: below the eye the larger
        # distance gives the larger angle, above it the smaller one
        tan_ub = num / np.where(num < 0, step_dists[None, 1:], step_dists[None, :-1])
        horizon_incl = np.maximum(horizon_before[:, 1:], tan_for_max[:, 1:])
        need = np.zeros(valid.shape, bool)
        need[:, 1:] = (tan_ub > horizon_incl) & valid[:, 1:]
        cross = np.full(valid.shape, -1e18)
        n_need = int(need.sum())
        if n_need:
            if n_need > 0.25 * need.size:                 # dense: cheaper than gathering
                cross[:, 1:] = _node_line_crossings(terrain, fc[:, :-1], fc[:, 1:], fr[:, :-1], fr[:, 1:],
                                                    step_dists[None, :-1], step_dists[None, 1:], cam_z,
                                                    earth_curvature)
                cross[~need] = -1e18
            else:
                ri, si = np.nonzero(need)
                cross[ri, si] = _node_line_crossings(terrain, fc[ri, si - 1], fc[ri, si], fr[ri, si - 1], fr[ri, si],
                                                     step_dists[si - 1], step_dists[si], cam_z, earth_curvature)
        # a crossing between samples i-1 and i lies before sample i
        horizon_before[:, 1:] = np.maximum(horizon_before[:, 1:], np.maximum.accumulate(cross, axis=1)[:, 1:])
    # the target's head (target_h above the sample) against the horizon of the
    # ground before it; tan_angle itself stays the ground's (it builds the horizon)
    los = (tan_angle + target_h / d) >= horizon_before if target_h > 0 else tan_angle >= horizon_before
    return RayMarch(valid, rr, cc, tan_angle, slant, los)


_CELL_MAX_CACHE: Dict[Tuple[int, Tuple[int, int]], np.ndarray] = {}
# A crossing must rise this much above the sightline it could block to count:
# far below any DEM's accuracy, it stops every gently curved cell from
# triggering the exact (slower) crossing evaluation.
_CROSSING_TOL_M = 0.05


def _cell_max(terrain: TerrainData) -> np.ndarray:
    """Highest of each cell's four corner nodes, (rows-1, cols-1); cached per grid."""
    z = terrain.z_grid
    key = (id(z), z.shape)
    hit = _CELL_MAX_CACHE.get(key)
    if hit is not None:
        return hit
    if z.shape[0] < 2 or z.shape[1] < 2:
        cm = np.asarray(z, float)
    else:
        cm = np.maximum(np.maximum(z[:-1, :-1], z[:-1, 1:]), np.maximum(z[1:, :-1], z[1:, 1:]))
    if len(_CELL_MAX_CACHE) > 8:
        _CELL_MAX_CACHE.clear()
    _CELL_MAX_CACHE[key] = cm
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
    the other way. Thermal: Johnson; ZONE_IDENT is never reached (inf)."""
    if thermal:
        return {ZONE_DETECT: THERMAL_DETECT_PPM, ZONE_OBSERVE: THERMAL_RECOG_PPM,
                ZONE_RECOG: THERMAL_IDENT_PPM}.get(zone, math.inf)
    return {ZONE_DETECT: PPM_DETECT, ZONE_OBSERVE: PPM_OBSERVE, ZONE_RECOG: PPM_RECOG, ZONE_IDENT: PPM_IDENT}[zone]


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


def view_limits(camera: CameraConfig, focal_mm: float, max_range_m: float,
                visibility_km: float = 40.0, weather: str = "") -> ViewLimits:
    """Pixel-density floor and range caps of one camera view -- shared by
    calculate_3d_viewshed and the per-mount view masks (mount_visibility) so
    the two can never apply different rules."""
    optics = camera_optics(camera, focal_mm)
    is_thermal = is_thermal_camera(camera)
    # Floor PPM below which a cell is not worth showing. Non-thermal: the
    # EN 62676-4 Detection threshold (25 px/m) — the viewshed then covers exactly
    # the DORI-meaningful band (Detection and above), no arbitrary cut-off.
    min_detect_ppm = THERMAL_DETECT_PPM if is_thermal else PPM_DETECT
    optical_limit_m = optics.distance_for_ppm(min_detect_ppm)
    # Fog / rain / haze cap the range where target contrast survives the path.
    atm_band = band_for_camera(camera.sensor_name, camera.model_name)
    atmospheric_limit_m = usable_range_m(optical_limit_m, visibility_km, atm_band, weather)
    # Effective raymarching range cannot exceed optical or atmospheric limits
    effective = min(max_range_m, optical_limit_m * 1.15, atmospheric_limit_m)
    return ViewLimits(optics, min_detect_ppm, optical_limit_m, atmospheric_limit_m, effective, is_thermal)


def zone_codes(visible, framed, ppm, thermal: bool = False):
    """ZONE_* per sample/cell from visibility, framing and px/m -- the one
    DORI classification both the engine and the mount view masks use.
    ``thermal``: Johnson thresholds, never ZONE_IDENT (see THERMAL_*)."""
    zone = np.zeros(np.shape(ppm), dtype=np.int32)                # ZONE_OUT_OF_FOV
    zone = np.where(framed & ~visible, ZONE_OCCLUDED, zone)       # framed but ridge-blocked
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
                          with_profile: bool = True) -> ViewshedResult:
    """Computes comprehensive 3D viewshed, terrain occlusion, and DORI mapping.

    ``focal_mm_override`` sets an explicit focal length (mm) — for a PTZ preset
    at an intermediate zoom, instead of the min/max lens_mode ends.
    ``with_profile=False`` skips the (unused-in-batch) elevation cross-section.
    """
    rows, cols = terrain.rows, terrain.cols
    cell_size = terrain.cell_size_m
    target_h = current_target_height()

    # 1. Camera 3D Position
    cam_ground_z = terrain.ground_elevation_at(cam_x_m, cam_y_m)   # mast on the ground, not a roof
    cam_z = cam_ground_z + mast_height_m

    # 2. Camera Optics & FOV
    focal_mm = view_focal_mm(camera, lens_mode, focal_mm_override)
    lim = view_limits(camera, focal_mm, max_range_m, visibility_km, weather)
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

    # 5. Raymarching Radial Scan
    # Ray count so that even at the far edge the angular spacing lands < ~0.6
    # cell apart — otherwise diverging rays skip grid cells inside the cone and
    # coverage_pct reads low. Capped so the (R x S) work stays bounded.
    span_need = half_hfov_rad * 2.0 * effective_max_range / max(cell_size * 0.6, 0.5)
    num_rays = int(min(2200, max(180, hfov_deg * 4, span_need)))
    ray_angles = np.linspace(pan_rad - half_hfov_rad, pan_rad + half_hfov_rad, num_rays)

    R_EARTH = _R_EARTH
    K_REFRACT = _K_REFRACT

    # Vertical framing: a ground cell is only *seen* if its line from the lens
    # falls between the bottom and top rays (tilt +/- vfov/2). Without this the
    # grid reports coverage the camera never frames (near dead zone, far sky).
    v_lo = math.radians(tilt_deg - vfov_deg / 2.0)     # bottom ray (steeper down)
    v_hi = math.radians(tilt_deg + vfov_deg / 2.0)     # top ray

    step_size = ray_step_size(cell_size, ray_step_m)
    num_steps = max(int(effective_max_range / step_size), 1)
    step_dists = np.arange(1, num_steps + 1) * step_size          # (S,)

    # Blocks of rays (ray_chunk), each marched, classified and scattered in
    # ray order: ray-major then step-ascending -> last write wins, matching
    # the old loop's ordering.
    chunk = ray_chunk(len(step_dists))
    for start in range(0, len(ray_angles), chunk):
        march = march_rays(terrain, cam_x_m, cam_y_m, cam_z, ray_angles[start:start + chunk],
                           step_dists, earth_curvature, target_h=target_h)
        valid, rr, cc = march.valid, march.rr, march.cc
        tan_angle, slant = march.tan_angle, march.slant
        ppm = optics.ppm_at(np.maximum(slant, 1.0))
        ppm_ok = ppm >= min_detect_ppm

        # framed = the head inside the vertical frame (the ground when target_h = 0)
        elev_angle = np.arctan(tan_angle + target_h / step_dists[None, :tan_angle.shape[1]]
                               if target_h > 0 else tan_angle)    # (-pi/2, pi/2)
        in_vfov = (elev_angle >= v_lo) & (elev_angle <= v_hi)
        framed = valid & ppm_ok & in_vfov
        visible = framed & march.los_clear

        zone = zone_codes(visible, framed, ppm, lim.thermal)

        m = valid
        vr, vc = rr[m], cc[m]
        slant_dist_grid[vr, vc] = slant[m].astype(np.float32)
        ppm_grid[vr, vc] = ppm[m].astype(np.float32)
        dori_grid[vr, vc] = zone[m]
        vis_mask[vr, vc] = visible[m]

    # Narrow the analytic cone to what the camera can actually FRAME (bottom/top
    # ray), so coverage_pct is "of the frameable area" rather than of the whole
    # horizontal wedge including the near dead zone and the far sky.
    cell_dz = terrain.z_grid[win].astype(np.float64) - cam_z
    if earth_curvature:
        cell_dz -= (ground_dist ** 2) / (2.0 * R_EARTH) * (1.0 - K_REFRACT)
    cell_elev_angle = np.arctan2(cell_dz, np.maximum(ground_dist, 0.1))
    in_cone_w &= (cell_elev_angle >= v_lo) & (cell_elev_angle <= v_hi)
    in_fov_cone = np.zeros((rows, cols), dtype=bool)
    in_fov_cone[win] = in_cone_w

    # Mask out anything not in the (now vertically-bounded) FOV cone
    dori_grid[~in_fov_cone] = ZONE_OUT_OF_FOV
    vis_mask[~in_fov_cone] = False

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
                                    weather: str = "") -> Optional[MultiViewshedResult]:
    """Runs the authoritative single-camera engine for each placement and merges
    the grids: the combined map shows the best DORI level reachable at every
    ground cell, plus how many cameras overlap there."""
    if not placements:
        return None

    rows, cols = terrain.rows, terrain.cols
    per: List[ViewshedResult] = []
    for p in placements:
        per.append(calculate_3d_viewshed(
            terrain=terrain, cam_x_m=p.x_m, cam_y_m=p.y_m, mast_height_m=p.mast_height_m,
            camera=p.camera, lens_mode=p.lens_mode, pan_deg=p.pan_deg, tilt_deg=p.tilt_deg,
            max_range_m=p.max_range_m, earth_curvature=earth_curvature,
            visibility_km=visibility_km, weather=weather,
            focal_mm_override=p.focal_mm_override, with_profile=False,
        ))

    ppm = np.zeros((rows, cols), np.float32)
    best_cam = np.zeros((rows, cols), np.int32)
    vis = np.zeros((rows, cols), bool)
    union_cone = np.zeros((rows, cols), bool)
    # BUGFIX: redundancy used to count VIEWS, so a PTZ whose 4 presets all
    # frame a cell read as "4 cameras" there -- inflating overlap and hiding
    # that the cell is unwatched whenever the PTZ looks elsewhere. Views of
    # one physical camera (same camera_id) are OR-ed and counted once.
    cam_vis_by_id: Dict[object, np.ndarray] = {}

    for i, (p, r) in enumerate(zip(placements, per), 1):
        cam_vis = r.visibility_mask
        better = cam_vis & (r.ppm_grid > ppm)
        ppm = np.where(better, r.ppm_grid, ppm)
        best_cam = np.where(better, i, best_cam)
        vis |= cam_vis
        key = p.camera_id if p.camera_id is not None else ("view", i)
        if key in cam_vis_by_id:
            cam_vis_by_id[key] = cam_vis_by_id[key] | cam_vis
        else:
            cam_vis_by_id[key] = cam_vis.copy()
        union_cone |= (r.dori_grid != ZONE_OUT_OF_FOV)

    seen = np.zeros((rows, cols), np.int32)
    for cam_vis in cam_vis_by_id.values():
        seen += cam_vis.astype(np.int32)

    occ = union_cone & ~vis
    dori = np.zeros((rows, cols), np.int32)
    dori = np.where(occ, ZONE_OCCLUDED, dori)
    dori = np.where(vis, ZONE_DETECT, dori)
    dori = np.where(vis & (ppm >= PPM_OBSERVE), ZONE_OBSERVE, dori)
    dori = np.where(vis & (ppm >= PPM_RECOG), ZONE_RECOG, dori)
    dori = np.where(vis & (ppm >= PPM_IDENT), ZONE_IDENT, dori)

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
