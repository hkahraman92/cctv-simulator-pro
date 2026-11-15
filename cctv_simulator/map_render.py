"""Map pictures without Tk: relief / photo base, building layer, layer bands.

Shared by the classic map window (``ui/map_3d_window``) and the core service
(``service/methods``) so both draw the same colours from the same tables.
Pictures only: no area, percentage or distance is ever read back from these
images (Rule 1) — the numbers come from the engine grids.

Every image is north-up (row 0 = north); ``TerrainData.z_grid`` row 0 is
south, hence the ``np.flipud`` everywhere.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from . import surface_obstacles as _obs
from .design_tokens import rgba as _rgba
from .raster_smooth import clip_to_wedge, smooth_overlay
from .terrain_loader import TerrainData
from .viewshed_3d import (
    _K_REFRACT, _R_EARTH, ZONE_DETECT, ZONE_IDENT, ZONE_MONITOR,
    ZONE_OBSERVE, ZONE_OCCLUDED, ZONE_RECOG, current_target_height, view_focal_mm, view_limits, zone_codes,
)

_INF = float("inf")

# Layer colours: (lo, hi, RGBA, legend text) bands, lo <= v < hi. The overlay
# painter and the legends read these, so a legend can never describe colours
# the map does not use. Cold DORI ramp; warm only where attention is needed.
LAYER_BANDS = {
    "dori": ("BİRLEŞİK DORI", (
        (ZONE_IDENT, ZONE_IDENT + 1, _rgba("dori-identify", 185), "Teşhis ≥ 250 px/m"),
        (ZONE_RECOG, ZONE_RECOG + 1, _rgba("dori-recognize", 175), "Tanıma ≥ 125 px/m"),
        (ZONE_OBSERVE, ZONE_OBSERVE + 1, _rgba("dori-observe", 190), "Gözlem ≥ 62,5 px/m"),
        # alpha: Algılama is the largest band and sat almost invisible on the dark relief
        (ZONE_DETECT, ZONE_DETECT + 1, _rgba("dori-detect", 215), "Algılama ≥ 25 px/m"),
        (ZONE_OCCLUDED, ZONE_OCCLUDED + 1, _rgba("bg-0", 200), "Kör nokta (arazi engeli)"))),
    # a run with the monitor floor: the same table plus the Monitoring band, the palest of the
    # cold ramp -- listed only for a layer that CAN hold it (a default run never does)
    "dori_monitor": ("BİRLEŞİK DORI · İZLEME DAHİL", (
        (ZONE_IDENT, ZONE_IDENT + 1, _rgba("dori-identify", 185), "Teşhis ≥ 250 px/m"),
        (ZONE_RECOG, ZONE_RECOG + 1, _rgba("dori-recognize", 175), "Tanıma ≥ 125 px/m"),
        (ZONE_OBSERVE, ZONE_OBSERVE + 1, _rgba("dori-observe", 190), "Gözlem ≥ 62,5 px/m"),
        (ZONE_DETECT, ZONE_DETECT + 1, _rgba("dori-detect", 215), "Algılama ≥ 25 px/m"),
        (ZONE_MONITOR, ZONE_MONITOR + 1, _rgba("dori-monitor", 200), "İzleme ≥ 12,5 px/m"),
        (ZONE_OCCLUDED, ZONE_OCCLUDED + 1, _rgba("bg-0", 200), "Kör nokta (arazi engeli)"))),
    # thermal camera: the same codes on Johnson thresholds (viewshed_3d.zone_codes)
    "dori_thermal": ("TERMAL · JOHNSON (insan)", (
        (ZONE_RECOG, ZONE_RECOG + 1, _rgba("dori-recognize", 175), "Johnson teşhis ≥ 13,3 px/m"),
        (ZONE_OBSERVE, ZONE_OBSERVE + 1, _rgba("dori-observe", 190), "Johnson tanıma ≥ 6,7 px/m"),
        (ZONE_DETECT, ZONE_DETECT + 1, _rgba("dori-detect", 215), "Johnson algılama ≥ 1,3 px/m"),
        (ZONE_OCCLUDED, ZONE_OCCLUDED + 1, _rgba("bg-0", 200), "Kör nokta (arazi engeli)"))),
    "overlap": ("YEDEKLİLİK (fiziksel kamera)", (
        (1, 2, _rgba("warn", 115), "1 kamera (yedeksiz)"),
        (2, 3, _rgba("dori-observe", 130), "2 kamera"),
        (3, _INF, _rgba("dori-identify", 140), "≥ 3 kamera"))),
    "revisit": ("REVİZİT — en uzun izlenmeyen süre", (
        (-_INF, 10, _rgba("dori-identify", 140), "< 10 sn"),
        (10, 30, _rgba("dori-observe", 130), "10–30 sn"),
        (30, 60, _rgba("warn", 120), "30–60 sn"),
        (60, _INF, _rgba("danger", 120), "≥ 60 sn"))),
    "cue": ("TEPKİ — alarm → teşhis süresi", (
        (-_INF, 3, _rgba("dori-identify", 140), "< 3 sn"),
        (3, 6, _rgba("dori-observe", 130), "3–6 sn"),
        (6, 10, _rgba("warn", 125), "6–10 sn"),
        (10, _INF, _rgba("danger", 125), "≥ 10 sn"))),
    "envelope": ("ZARF — tüm odaklarda en iyi DORI (eşzamanlı değil)", (
        (ZONE_IDENT, ZONE_IDENT + 1, _rgba("dori-identify", 185), "Teşhis ≥ 250 px/m"),
        (ZONE_RECOG, ZONE_RECOG + 1, _rgba("dori-recognize", 175), "Tanıma ≥ 125 px/m"),
        (ZONE_OBSERVE, ZONE_OBSERVE + 1, _rgba("dori-observe", 165), "Gözlem ≥ 62,5 px/m"),
        (ZONE_DETECT, ZONE_DETECT + 1, _rgba("dori-detect", 160), "Algılama ≥ 25 px/m"))),
    "envelope_monitor": ("ZARF — tüm odaklarda en iyi DORI, İzleme dahil (eşzamanlı değil)", (
        (ZONE_IDENT, ZONE_IDENT + 1, _rgba("dori-identify", 185), "Teşhis ≥ 250 px/m"),
        (ZONE_RECOG, ZONE_RECOG + 1, _rgba("dori-recognize", 175), "Tanıma ≥ 125 px/m"),
        (ZONE_OBSERVE, ZONE_OBSERVE + 1, _rgba("dori-observe", 165), "Gözlem ≥ 62,5 px/m"),
        (ZONE_DETECT, ZONE_DETECT + 1, _rgba("dori-detect", 160), "Algılama ≥ 25 px/m"),
        (ZONE_MONITOR, ZONE_MONITOR + 1, _rgba("dori-monitor", 170), "İzleme ≥ 12,5 px/m"))),
    "envelope_thermal": ("ZARF — termal, tüm odaklarda en iyi Johnson (eşzamanlı değil)", (
        (ZONE_RECOG, ZONE_RECOG + 1, _rgba("dori-recognize", 175), "Johnson teşhis ≥ 13,3 px/m"),
        (ZONE_OBSERVE, ZONE_OBSERVE + 1, _rgba("dori-observe", 165), "Johnson tanıma ≥ 6,7 px/m"),
        (ZONE_DETECT, ZONE_DETECT + 1, _rgba("dori-detect", 160), "Johnson algılama ≥ 1,3 px/m"))),
    "approach": ("YAKLAŞMA — sabit kameralara göre", (
        (3, 4, _rgba("danger", 110), "Görünmeden ilerlenebilir"),
        (2, 3, _rgba("dori-detect", 120), "Algılanır (≥ 25 px/m)"))),
    "city": ("ŞEHİR — sokak, yüz, gizlilik", (
        (4, 5, _rgba("warn", 150), "Konut binası görülüyor (KVKK)"),
        (3, 4, _rgba("dori-identify", 190), "Yüz yakalanır (Teşhis, ≤ 15°)"),
        (2, 3, _rgba("dori-observe", 165), "Sokak hedef seviyede görülüyor"),
        (1, 2, _rgba("danger", 150), "Sokak görülmüyor"))),
    "streets": ("SOKAKLAR (OpenStreetMap)", (
        (4, 5, _rgba("info", 200), "Ana yol / cadde"),
        (3, 4, _rgba("info", 165), "Cadde"),
        (2, 3, _rgba("info", 130), "Sokak"),
        (1, 2, _rgba("info", 95), "Yaya yolu / meydan"))),
    # coverage "target" layer: codes painted only inside what the plan protects
    "target": ("HEDEF — planın koruduğu alanda", (
        (1, 2, _rgba("dori-identify", 175), "Hedef tutuyor (sabit, sürekli)"),
        (2, 3, _rgba("ptz", 170), "Yalnız PTZ devriyesiyle (aralıklı)"),
        (3, 4, _rgba("warn", 165), "Görülüyor, hedefin altında"),
        (4, 5, _rgba("danger", 165), "Hiç görülmüyor"))),
    "fused": ("ALGILAMA: SABİT + PTZ", (
        (2, 3, _rgba("dori-identify", 140), "Sürekli — sabit kamera"),
        (1, 2, _rgba("warn", 125), "Aralıklı — yalnız PTZ devriyesi"))),
}


def paint_bands(ov, grid, bands, mask=None):
    """Paint ``lo <= grid < hi`` cells (finite only) in each band's RGBA, in order."""
    ok = np.isfinite(grid) if mask is None else mask
    for lo, hi, rgba, _ in bands:
        ov[ok & (grid >= lo) & (grid < hi)] = rgba


# Zone code -> level key, for the modes whose bands are DORI levels. Thermal rows keep the
# label's own level (ZONE_RECOG is "Johnson teşhis"). The Tk UI reads LAYER_BANDS directly
# (4-tuples) and never sees these keys.
_LEVEL_KEY = {ZONE_MONITOR: "monitor", ZONE_DETECT: "detect", ZONE_OBSERVE: "observe",
              ZONE_RECOG: "recognize", ZONE_IDENT: "identify"}
_THERMAL_LEVEL_KEY = {ZONE_DETECT: "detect", ZONE_OBSERVE: "recognize", ZONE_RECOG: "identify"}
_LEVEL_MODES = ("dori", "dori_monitor", "dori_thermal", "envelope", "envelope_monitor", "envelope_thermal")


def legend(mode: str, keys: bool = False):
    """(title, rows) for a layer; a row is ``(text, (r, g, b, a))``. With ``keys=True`` a DORI
    level row is ``(text, (r, g, b, a), level_key)``, the key one of monitor / detect / observe /
    recognize / identify (the service sends these so a client never matches the Turkish label);
    rows of other layers and the blind-spot row carry no key. ``keys=False`` is the 2-tuple form
    the Tk window and the share HTML read."""
    title, bands = LAYER_BANDS[mode]
    keys_on = keys
    keys = _THERMAL_LEVEL_KEY if mode.endswith("_thermal") else _LEVEL_KEY
    rows = []
    for lo, _hi, rgba, text in bands:
        row = (text, tuple(int(c) for c in rgba))
        if keys_on and mode in _LEVEL_MODES and lo in keys:
            row += (keys[lo],)
        rows.append(row)
    return title, rows


def _console_relief(terrain: TerrainData, shaded: np.ndarray, norm_z: np.ndarray) -> Image.Image:
    """Dark, low-saturation relief for the console theme: the DORI overlay
    (a cold ramp) must stay the brightest thing on the map."""
    from .design_tokens import rgba

    del shaded                                # the classic shade ignores cell size; redo it properly
    z = terrain.z_grid.astype(np.float64)
    cell = float(terrain.cell_size_m)
    gy, gx = np.gradient(z, cell)             # axis 0 = +y (north), axis 1 = +x (east)
    slope = np.arctan(2.0 * np.hypot(gx, gy))                 # z-factor 2: relief reads at a glance
    aspect = np.arctan2(gy, -gx)
    sun_az, sun_alt = np.radians(360.0 - 315.0 + 90.0), np.radians(40.0)   # NW light, cartographic convention
    hs = np.sin(sun_alt) * np.cos(slope) + np.cos(sun_alt) * np.sin(slope) * np.cos(sun_az - aspect)
    hs = np.clip(hs, 0.0, 1.0)

    lo = np.array(rgba("bg-2", 255)[:3], np.float32)          # valleys
    hi = np.array(rgba("steel", 255)[:3], np.float32) * 0.62   # ridges, muted
    tint = lo[None, None, :] + (hi - lo)[None, None, :] * norm_z[..., None] ** 0.9
    rgb = tint * (0.55 + 0.75 * hs[..., None])
    # faint contours, ~12 across the relief, at a round interval
    zr = float(np.ptp(z)) or 1.0
    step = float(10 ** np.floor(np.log10(zr / 12)))
    step = next(s * step for s in (1, 2, 5, 10) if zr / (s * step) <= 14)
    band = np.floor(z / step)
    edge = (band != np.roll(band, 1, axis=0)) | (band != np.roll(band, 1, axis=1))
    edge[0, :] = edge[:, 0] = False
    rgb[edge] = rgb[edge] * 0.78 + np.array(rgba("steel", 255)[:3], np.float32) * 0.10
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    size = (max(terrain.cols * 4, 800), max(terrain.rows * 4, 800))
    return Image.fromarray(np.flipud(rgb), mode="RGB").resize(size, Image.Resampling.BILINEAR).convert("RGBA")


def relief_image(terrain: TerrainData, layer_mode: str = "hybrid",
                 photo: Optional[Image.Image] = None, photo_opacity: float = 0.8,
                 palette: str = "classic") -> Image.Image:
    """Hillshade + photo / elevation colours for the whole terrain, north up.

    ``layer_mode``: "satellite" | "elevation" | "hybrid". Without a photo a
    procedural texture stands in for "satellite" (the UI labels it as such).
    ``palette="console"`` replaces the non-photo colours with a dark muted
    relief (new desktop shell); ``"classic"`` is the Tk map's look, unchanged.
    """
    rows, cols = terrain.rows, terrain.cols
    z_grid = terrain.z_grid            # surface: hillshade shows buildings too
    # Colour ramp from the bare ground: with buildings in it, one 76 m tower
    # stretched the ramp and the whole ground relief went one flat green.
    z_col = terrain.ground_z_grid if terrain.has_obstacles else z_grid
    z_min, z_max = float(np.nanmin(z_col)), float(np.nanmax(z_col))
    z_range = max(z_max - z_min, 1.0)
    norm_z = np.clip((z_col - z_min) / z_range, 0.0, 1.0)

    dz_dx, dz_dy = np.gradient(z_grid)
    slope = np.hypot(dz_dx, dz_dy)
    aspect = np.arctan2(-dz_dy, -dz_dx)
    sun_azimuth = np.radians(315.0)
    sun_altitude = np.radians(45.0)
    shaded = np.sin(sun_altitude) + np.cos(sun_altitude) * slope * 0.08 * np.cos(sun_azimuth - aspect)
    shaded = np.clip(shaded, 0.45, 1.35)

    if palette == "console" and (photo is None or layer_mode == "elevation"):
        return _console_relief(terrain, shaded, norm_z)

    if photo is not None:
        base_w, base_h = photo.width, photo.height
        r_elev = np.clip((norm_z * 240 + 20) * shaded, 0, 255).astype(np.uint8)
        g_elev = np.clip(((1.0 - np.abs(norm_z - 0.4) * 1.5) * 180 + 35) * shaded, 0, 255).astype(np.uint8)
        b_elev = np.clip((norm_z * 160 + 25) * shaded, 0, 255).astype(np.uint8)
        elev_arr = np.dstack((r_elev, g_elev, b_elev))
        elev_img = Image.fromarray(np.flipud(elev_arr), mode="RGB").resize(
            (base_w, base_h), Image.Resampling.BILINEAR).convert("RGBA")
        if layer_mode == "satellite":
            return photo.copy()
        if layer_mode == "elevation":
            return elev_img
        # Hybrid = the photo carrying the relief: multiplied by the (surface)
        # hillshade, so ridges and building blocks read in the photo itself;
        # the opacity then blends in the elevation colours.
        sat_rgba = photo.convert("RGBA")
        shade = Image.fromarray(np.flipud(np.clip(shaded, 0.55, 1.3)).astype(np.float32), mode="F")
        shade = np.asarray(shade.resize((base_w, base_h), Image.Resampling.BILINEAR))
        arr = np.asarray(sat_rgba, dtype=np.float32)
        arr[..., :3] = np.clip(arr[..., :3] * shade[..., None], 0, 255)
        relief = Image.fromarray(arr.astype(np.uint8), mode="RGBA")
        return Image.blend(relief, elev_img, 1.0 - photo_opacity)

    # Procedural stand-in texture at >= 800 px
    base_w, base_h = max(cols * 4, 800), max(rows * 4, 800)
    r_sat = np.clip((norm_z * 180 + 70) * shaded, 0, 255).astype(np.uint8)
    g_sat = np.clip(((1.0 - norm_z) * 130 + 65) * shaded, 0, 255).astype(np.uint8)
    b_sat = np.clip((norm_z * 100 + 45) * shaded, 0, 255).astype(np.uint8)
    sat_arr = np.dstack((r_sat, g_sat, b_sat))
    r_elev = np.clip((norm_z * 240 + 20) * shaded, 0, 255).astype(np.uint8)
    g_elev = np.clip(((1.0 - np.abs(norm_z - 0.4) * 1.5) * 180 + 35) * shaded, 0, 255).astype(np.uint8)
    b_elev = np.clip((norm_z * 160 + 25) * shaded, 0, 255).astype(np.uint8)
    elev_arr = np.dstack((r_elev, g_elev, b_elev))
    if layer_mode == "satellite":
        comp = sat_arr
    elif layer_mode == "elevation":
        comp = elev_arr
    else:
        comp = np.clip(sat_arr * photo_opacity + elev_arr * (1.0 - photo_opacity), 0, 255).astype(np.uint8)
    return Image.fromarray(np.flipud(comp), mode="RGB").resize(
        (base_w, base_h), Image.Resampling.BILINEAR).convert("RGBA")


def obstacle_layer(terrain: TerrainData, size: Tuple[int, int], photo: bool) -> Optional[Image.Image]:
    """Buildings (taller = more opaque) and vegetation as an RGBA layer, or None.

    Over a photo the fill is light and the footprint edge strong, so the real
    roofs stay visible; over the elevation colours it is solid.
    """
    t = terrain
    if not t.has_obstacles or t.obstacle_grid is None:
        return None
    h = np.flipud(t.obstacle_grid)
    k = np.flipud(t.obstacle_kind) if t.obstacle_kind is not None else (h > 0).astype(np.uint8)
    ov = np.zeros(h.shape + (4,), np.uint8)
    bld = ((k == _obs.KIND_BUILDING) | (k == _obs.KIND_SURFACE)) & (h > 0)
    veg = (k == _obs.KIND_VEGETATION) & (h > 0)
    ov[bld] = _rgba("building", 0)
    if photo:
        # constant alpha per kind: few colours, so the layer can be smoothed
        ov[bld] = _rgba("building", 70)
        ov[veg] = _rgba("vegetation", 70)
        edge = np.zeros_like(bld)
        for axis, shift in ((0, 1), (0, -1), (1, 1), (1, -1)):
            edge |= bld & ~np.roll(bld, shift, axis=axis)
        ov[edge] = _rgba("building", 150)
    else:
        # taller = more opaque, in 10 m steps (few classes -> smoothable)
        ov[..., 3][bld] = np.clip(110 + (h[bld] // 10.0) * 30.0, 110, 230).astype(np.uint8)
        ov[veg] = _rgba("vegetation", 150)
    return smooth_overlay(ov, size)


def dim_outside(img: Image.Image, mask: np.ndarray, factor: float = 0.3, *, pixel: bool = False) -> Image.Image:
    """Fade an overlay outside ``mask`` (terrain grid, row 0 = south): what a
    plan does not protect stays visible but no longer reads as coverage.

    ``pixel=True``: ``mask`` is a north-up pixel mask (bool) or a 0..1 weight of ANY size; it is
    resampled bilinearly to the picture and the fade follows it softly (no stair-step edge)."""
    arr = np.array(img.convert("RGBA"))
    if pixel:
        m = Image.fromarray(np.asarray(mask, np.float32), mode="F")
        if m.size != img.size:
            m = m.resize(img.size, Image.Resampling.BILINEAR)
        w = factor + (1.0 - factor) * _smooth01(np.asarray(m, np.float32))
        arr[..., 3] = (arr[..., 3] * w).astype(np.uint8)
        return Image.fromarray(arr, "RGBA")
    m = Image.fromarray(np.flipud(mask).astype(np.uint8) * 255).resize(img.size, Image.Resampling.NEAREST)
    keep = np.asarray(m) > 0
    arr[..., 3] = np.where(keep, arr[..., 3], (arr[..., 3] * factor).astype(np.uint8))
    return Image.fromarray(arr, "RGBA")


def _smooth01(x: np.ndarray) -> np.ndarray:
    """Smoothstep of a 0..1 weight around its 0.5 contour (a soft but narrow edge)."""
    t = np.clip((x - 0.35) / 0.3, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def zone_overlay(dori_grid: np.ndarray, size: Tuple[int, int], *, mode: str = "dori") -> Image.Image:
    """A ZONE_* (or any banded) grid as a smoothed RGBA image of ``size``."""
    g = np.flipud(dori_grid)
    ov = np.zeros(g.shape + (4,), dtype=np.uint8)
    paint_bands(ov, g, LAYER_BANDS[mode][1], mask=True if mode in ("dori", "dori_monitor", "dori_thermal", "envelope", "envelope_monitor", "envelope_thermal", "fused", "overlap", "approach", "city", "streets") else None)
    return smooth_overlay(ov, size)


def single_view_overlay(terrain: TerrainData, dori_grid: np.ndarray, size: Tuple[int, int], *,
                        cam_x_m: float, cam_y_m: float, pan_deg: float, hfov_deg: float,
                        max_range_m: float, mode: str = "dori") -> Image.Image:
    """One camera's DORI fan, smoothed and cut to its optical cone."""
    img = zone_overlay(dori_grid, size, mode=mode)
    sx = size[0] / terrain.width_m
    sy = size[1] / terrain.height_m
    apex = ((cam_x_m - terrain.origin_x) * sx, (terrain.height_m - (cam_y_m - terrain.origin_y)) * sy)
    return clip_to_wedge(img, apex, (sx, sy), pan_deg, hfov_deg, max_range_m + terrain.cell_size_m)


# ── multi-camera pictures: analytic optics, gridded occlusion ────────────────
# The analysis grid is 5-10 m while a fence camera's wedge can be 30 m long: painting
# the grid classes leaves a wedge of three cells, half of it empty. For DISPLAY the
# cone, the vertical frame, px/m, range and DORI class are evaluated per picture pixel
# from the engine's own building blocks (camera_optics / view_limits / zone_codes);
# only the occlusion verdict (a property of the lens position and the terrain, not of
# the optics) is taken from the engine's per-camera grid and smoothed up to pixels.
# Nothing in a result is ever read back from these arrays (Rule 1).

_PIXEL_BUDGET = 40_000_000          # sum of camera bounding-box pixels before the working size shrinks
_CHUNK_PX = 1_500_000


@dataclass
class MultiViewClasses:
    """Per-pixel merge of the fixed cameras (north-up, ``size`` = (width, height))."""
    zone: np.ndarray          # ZONE_* per pixel: best of the cameras (engine merge rule), int8
    seen: np.ndarray          # physical cameras with a clear framed view of the pixel, int16
    ppm: np.ndarray           # best px/m among the cameras seeing the pixel, float32
    visible: np.ndarray       # at least one camera sees the pixel
    size: Tuple[int, int]
    work_size: Tuple[int, int]    # resolution the optics were evaluated at (< size when over budget)


def _sample_grid(field: np.ndarray, terrain: TerrainData, xs: np.ndarray, ys: np.ndarray,
                 c_off: int = 0, r_off: int = 0) -> np.ndarray:
    """Bilinear sample of a terrain-grid field (node convention, as march_rays) at world
    points; ``c_off`` / ``r_off`` = the field is a window starting at that column / row."""
    rows, cols = field.shape
    fc = np.clip((xs - terrain.origin_x) / terrain.cell_size_m - c_off, 0.0, cols - 1.0)
    fr = np.clip((ys - terrain.origin_y) / terrain.cell_size_m - r_off, 0.0, rows - 1.0)
    c0 = np.minimum(fc.astype(np.intp), max(cols - 2, 0))
    r0 = np.minimum(fr.astype(np.intp), max(rows - 2, 0))
    fx, fy = fc - c0, fr - r0
    dc = 1 if cols > 1 else 0
    dr = cols if rows > 1 else 0
    flat = np.ascontiguousarray(field).ravel()
    i00 = r0 * cols + c0
    z00, z01 = flat.take(i00), flat.take(i00 + dc)
    z10, z11 = flat.take(i00 + dr), flat.take(i00 + dr + dc)
    return (z00 + (z01 - z00) * fx) * (1.0 - fy) + (z10 + (z11 - z10) * fx) * fy


def _shift(a: np.ndarray, dr: int, dc: int) -> np.ndarray:
    """``a`` moved by (dr, dc) cells, zero-filled at the edge (no wrap-around)."""
    out = np.zeros_like(a)
    n_r, n_c = a.shape
    out[max(dr, 0):n_r + min(dr, 0), max(dc, 0):n_c + min(dc, 0)] =         a[max(-dr, 0):n_r + min(-dr, 0), max(-dc, 0):n_c + min(-dc, 0)]
    return out


def _fill_unknown(val: np.ndarray, known: np.ndarray, passes: int = 3) -> np.ndarray:
    """Spread ``val`` into unknown cells from known 8-neighbours (a few dilation passes);
    cells still unknown afterwards read 0 (visible)."""
    val = np.where(known, val, 0.0)
    known = known.copy()
    for _ in range(passes):
        if known.all():
            break
        kf = known.astype(val.dtype)
        num = np.zeros_like(val)
        den = np.zeros_like(val)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr or dc:
                    num += _shift(val, dr, dc)
                    den += _shift(kf, dr, dc)
        new = ~known & (den > 0)
        val = np.where(new, num / np.maximum(den, 1e-12), val)
        known = known | new
    return val


def _dori_slice(r, rw0: int, rw1: int, cw0: int, cw1: int) -> np.ndarray:
    """``r.dori_grid[rw0:rw1, cw0:cw1]`` without building the full grid of a windowed result
    (cells outside its window are zero)."""
    win = getattr(r, "window", None)
    dw = getattr(r, "dori_window", None)
    if win is None or dw is None:
        return r.dori_grid[rw0:rw1, cw0:cw1]
    r0, r1, c0, c1 = win
    out = np.zeros((max(rw1 - rw0, 0), max(cw1 - cw0, 0)), dw.dtype)
    a0, a1, b0, b1 = max(rw0, r0), min(rw1, r1), max(cw0, c0), min(cw1, c1)
    if a1 > a0 and b1 > b0:
        out[a0 - rw0:a1 - rw0, b0 - cw0:b1 - cw0] = dw[a0 - r0:a1 - r0, b0 - c0:b1 - c0]
    return out


def _bbox_px(terrain: TerrainData, cx: float, cy: float, reach: float, w: int, h: int) -> Tuple[int, int, int, int]:
    """(row0, row1, col0, col1) of the picture pixels within ``reach`` of a point."""
    sx, sy = w / terrain.width_m, h / terrain.height_m
    top = terrain.origin_y + terrain.height_m
    j0 = int(np.clip(math.floor((cx - reach - terrain.origin_x) * sx) - 1, 0, w))
    j1 = int(np.clip(math.ceil((cx + reach - terrain.origin_x) * sx) + 1, 0, w))
    i0 = int(np.clip(math.floor((top - (cy + reach)) * sy) - 1, 0, h))
    i1 = int(np.clip(math.ceil((top - (cy - reach)) * sy) + 1, 0, h))
    return i0, i1, j0, j1


def multi_view_classes(terrain: TerrainData, mv, placements: Sequence, size: Tuple[int, int], *,
                       visibility_km: float = 40.0, weather: str = "", monitor_floor: bool = False,
                       earth_curvature: bool = True, pixel_budget: int = _PIXEL_BUDGET) -> MultiViewClasses:
    """Merge ``placements`` (the ones ``mv`` was computed from, same order) at picture-pixel
    resolution: analytic cone / frame / px/m / range / DORI class, engine occlusion.

    ``mv.per_camera[i]`` carries the camera's own grid verdict: a cell the engine framed
    is either ZONE_OCCLUDED or visible. That verdict is filled into the few cells around
    that have none, bilinearly upsampled and cut at 0.5, so shadow edges are smooth; a
    pixel with no verdict nearby counts as visible. Merging follows
    ``calculate_multi_camera_viewshed`` (best zone, ``camera_id``-aware seen count)."""
    W, H = int(size[0]), int(size[1])
    per = mv.per_camera
    if len(per) != len(placements):
        raise ValueError("placements ile mv.per_camera aynı sırada ve sayıda olmalı.")
    # working resolution: shrink when the cameras' bounding boxes would exceed the pixel budget
    total = 0
    for p, r in zip(placements, per):
        b = _bbox_px(terrain, p.x_m, p.y_m, float(r.max_range_m), W, H)
        total += (b[1] - b[0]) * (b[3] - b[2])
    w, h = W, H
    if total > pixel_budget:
        f = math.sqrt(pixel_budget / total)
        w, h = max(1, int(W * f)), max(1, int(H * f))
    sx, sy = w / terrain.width_m, h / terrain.height_m
    top = terrain.origin_y + terrain.height_m

    union = np.zeros((h, w), bool)
    vis_any = np.zeros((h, w), bool)
    best = np.zeros((h, w), np.float32)
    zmax = np.zeros((h, w), np.int8)               # best zone code of any camera that SEES the pixel
    seen = np.zeros((h, w), np.int16)
    groups: dict = {}
    for i, p in enumerate(placements):
        groups.setdefault(p.camera_id if p.camera_id is not None else ("view", i), []).append(i)

    th = current_target_height()
    cell = terrain.cell_size_m
    for members in groups.values():
        shared = np.zeros((h, w), bool) if len(members) > 1 else None
        for i in members:
            p, r = placements[i], per[i]
            focal = view_focal_mm(p.camera, p.lens_mode, p.focal_mm_override)
            lim = view_limits(p.camera, focal, p.max_range_m, visibility_km, weather, monitor_floor)
            reach = float(r.max_range_m)
            half_h = math.radians(r.hfov_deg / 2.0)
            pan = math.radians(r.pan_deg)
            v_lo = math.radians(r.tilt_deg - r.vfov_deg / 2.0)
            v_hi = math.radians(r.tilt_deg + r.vfov_deg / 2.0)
            cam_z = float(r.cam_total_z_m)
            # the engine's occlusion verdict of this camera, in a cell window around it
            pad = 3
            rw0 = int(np.clip(math.floor((p.y_m - reach - terrain.origin_y) / cell) - pad, 0, terrain.rows))
            rw1 = int(np.clip(math.ceil((p.y_m + reach - terrain.origin_y) / cell) + pad, 0, terrain.rows))
            cw0 = int(np.clip(math.floor((p.x_m - reach - terrain.origin_x) / cell) - pad, 0, terrain.cols))
            cw1 = int(np.clip(math.ceil((p.x_m + reach - terrain.origin_x) / cell) + pad, 0, terrain.cols))
            dg = _dori_slice(r, rw0, rw1, cw0, cw1)
            occ_field = _fill_unknown((dg == ZONE_OCCLUDED).astype(np.float64), dg != 0) if dg.size else dg
            i0, i1, j0, j1 = _bbox_px(terrain, p.x_m, p.y_m, reach, w, h)
            if i1 <= i0 or j1 <= j0:
                continue
            step = max(1, _CHUNK_PX // (j1 - j0))
            xs_all = terrain.origin_x + (np.arange(j0, j1) + 0.5) / sx
            for a in range(i0, i1, step):
                b = min(a + step, i1)
                ys_c = top - (np.arange(a, b) + 0.5) / sy
                dx = xs_all[None, :] - p.x_m
                dy = ys_c[:, None] - p.y_m
                gd = np.hypot(dx, dy)
                az = np.arctan2(dx, dy)
                diff = np.arctan2(np.sin(az - pan), np.cos(az - pan))
                ii, jj = np.nonzero((np.abs(diff) <= half_h) & (gd <= reach) & (gd > 1.0))
                if not ii.size:
                    continue
                d = gd[ii, jj]
                xs, ys = xs_all[jj], ys_c[ii]
                dz = _sample_grid(terrain.z_grid, terrain, xs, ys)
                if earth_curvature:
                    dz = dz - (d * d) / (2.0 * _R_EARTH) * (1.0 - _K_REFRACT)
                dz = dz - cam_z
                ppm = lim.optics.ppm_at(np.maximum(np.hypot(d, dz), 1.0))
                tan = dz / d
                elev = np.arctan(tan + th / d if th > 0 else tan)
                ground = np.arctan2(dz, np.maximum(d, 0.1))        # the engine's cone also frames the ground cell
                framed = ((ppm >= lim.min_detect_ppm) & (elev >= v_lo) & (elev <= v_hi)
                          & (ground >= v_lo) & (ground <= v_hi))
                if occ_field.size:
                    occ = _sample_grid(occ_field, terrain, xs, ys, cw0, rw0) > 0.5
                else:
                    occ = np.zeros(d.shape, bool)
                visible = framed & ~occ
                zone = zone_codes(visible, framed, ppm, lim.thermal, lim.monitor)
                gi, gj = ii + a, jj + j0
                union[gi, gj] |= framed
                vis_any[gi, gj] |= visible
                better = visible & (ppm > best[gi, gj])
                best[gi, gj] = np.where(better, ppm, best[gi, gj])
                zmax[gi, gj] = np.maximum(zmax[gi, gj], np.where(visible, zone, 0))
                if shared is not None:
                    shared[gi, gj] |= visible
                else:
                    seen[gi[visible], gj[visible]] += 1
        if shared is not None:
            seen += shared.astype(np.int16)

    # the engine's merge rule (calculate_multi_camera_viewshed): the best code of a camera that sees the
    # pixel (each camera classed on its own EN / Johnson scale), blocked where framed but unseen
    dori = zmax
    dori[union & ~vis_any] = ZONE_OCCLUDED
    if (w, h) != (W, H):
        def up(a):
            return np.asarray(Image.fromarray(a).resize((W, H), Image.Resampling.NEAREST))
        dori, seen, best, vis_any = up(dori), up(seen), up(best), up(vis_any)
    return MultiViewClasses(zone=dori, seen=seen, ppm=best, visible=vis_any, size=(W, H), work_size=(w, h))


_OUT_CAP_PX = 4096


def final_size(size: Tuple[int, int], scale: float = 2.0, cap: int = _OUT_CAP_PX) -> Tuple[int, int]:
    """Picture size the multi-camera classes are evaluated at: ``scale`` x the requested size, the long
    side capped. Smoothness comes from resolution (the optics are exact per pixel), never from blurring."""
    k = max(1.0, min(float(scale), cap / max(size)))
    return max(1, round(size[0] * k)), max(1, round(size[1] * k))


def _paint_px(arr: np.ndarray, mode: str) -> Image.Image:
    """Class pixels -> RGBA at the same size. Class colours switch hard between two classes and the
    interior keeps the band-table colour exactly. Only a coloured-to-background edge is anti-aliased:
    alpha follows a [1 2 1] / 4 coverage of the coloured mask (a fade of one pixel that straddles the
    edge, so the 0.5 contour and the geometry do not move); the faded pixels outside take the colour
    of their nearest coloured neighbour."""
    ov = np.zeros(arr.shape + (4,), np.uint8)
    paint_bands(ov, arr, LAYER_BANDS[mode][1], mask=True)
    colored = ov[..., 3] > 0
    if not colored.any():
        return Image.fromarray(ov, "RGBA")
    pad = np.pad(colored.astype(np.float32), 1, mode="edge")        # the picture edge is not a coloured edge
    cov = pad[1:-1, 1:-1]
    for axis in (0, 1):                                              # separable [1 2 1] / 4
        pad = np.pad(cov, 1, mode="edge")
        cov = (0.5 * pad[1:-1, 1:-1] + 0.25 * (pad[:-2, 1:-1] + pad[2:, 1:-1])) if axis == 0 else               (0.5 * pad[1:-1, 1:-1] + 0.25 * (pad[1:-1, :-2] + pad[1:-1, 2:]))
    out = ov.copy()
    rim = ~colored & (cov > 0)
    if rim.any():
        for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            src = np.roll(np.roll(ov, dr, axis=0), dc, axis=1)
            ok = np.roll(np.roll(colored, dr, axis=0), dc, axis=1)
            take = rim & ok & (out[..., 3] == 0)
            out[take] = src[take]
    # alpha: full inside (cov == 1), fading across the edge pixel pair
    out[..., 3] = (out[..., 3].astype(np.float32) * np.clip(cov, 0.0, 1.0)).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def multi_zone_overlay(cl: MultiViewClasses, *, mode: str = "dori") -> Image.Image:
    """The merged DORI classes of ``cl`` in a DORI band table (dori / dori_monitor / dori_thermal)."""
    return _paint_px(cl.zone, mode)


def multi_seen_overlay(cl: MultiViewClasses) -> Image.Image:
    """Physical-camera redundancy per pixel, in the ``overlap`` bands."""
    return _paint_px(cl.seen, "overlap")


def terrain_mask_px(terrain: TerrainData, mask: np.ndarray, size: Tuple[int, int], *,
                    soft: bool = False) -> np.ndarray:
    """A terrain-grid bool mask (row 0 = south) as a north-up pixel mask of ``size``: the 0.5
    contour of its bilinear surface, so the edge is smooth rather than a staircase of cells.
    ``soft=True`` returns the 0..1 bilinear weight itself (for ``dim_outside(pixel=True)``)."""
    W, H = size
    xs = terrain.origin_x + (np.arange(W) + 0.5) * (terrain.width_m / W)
    ys = terrain.origin_y + terrain.height_m - (np.arange(H) + 0.5) * (terrain.height_m / H)
    gx, gy = np.meshgrid(xs, ys)
    w = _sample_grid(mask.astype(np.float64), terrain, gx.ravel(), gy.ravel()).reshape(H, W)
    return w.astype(np.float32) if soft else w > 0.5


def multi_target_overlay(terrain: TerrainData, cl: MultiViewClasses, *, target_ppm: float, roi: np.ndarray,
                         ptz_ok: Optional[np.ndarray] = None, ptz_seen: Optional[np.ndarray] = None) -> Image.Image:
    """The coverage ``target`` layer, painted only inside ``roi``: 1 held by the fixed cameras
    (pixel-level px/m >= target and visible), 2 only by a PTZ patrol (``ptz_ok``), 3 seen
    below the target, 4 not seen. ``roi`` / ``ptz_*`` are terrain grids (smooth contour)."""
    size = cl.size
    roi_px = terrain_mask_px(terrain, roi, size)
    held = (cl.ppm >= target_ppm - 1e-6) & cl.visible
    ptz_ok_px = terrain_mask_px(terrain, ptz_ok, size) if ptz_ok is not None else np.zeros_like(held)
    ptz_seen_px = terrain_mask_px(terrain, ptz_seen, size) if ptz_seen is not None else np.zeros_like(held)
    cls = np.zeros(held.shape, np.int8)
    cls[roi_px & held] = 1
    cls[roi_px & ~held & ptz_ok_px] = 2
    cls[roi_px & ~held & ~ptz_ok_px & (cl.visible | ptz_seen_px)] = 3
    cls[roi_px & (cls == 0)] = 4
    return _paint_px(cls, "target")
