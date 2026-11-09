"""Map pictures without Tk: relief / photo base, building layer, layer bands.

Shared by the classic map window (``ui/map_3d_window``) and the core service
(``service/methods``) so both draw the same colours from the same tables.
Pictures only: no area, percentage or distance is ever read back from these
images (Rule 1) — the numbers come from the engine grids.

Every image is north-up (row 0 = north); ``TerrainData.z_grid`` row 0 is
south, hence the ``np.flipud`` everywhere.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
from PIL import Image

from . import surface_obstacles as _obs
from .design_tokens import rgba as _rgba
from .raster_smooth import clip_to_wedge, smooth_overlay
from .terrain_loader import TerrainData
from .viewshed_3d import ZONE_DETECT, ZONE_IDENT, ZONE_OBSERVE, ZONE_OCCLUDED, ZONE_RECOG

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


def legend(mode: str):
    """(title, [(text, (r, g, b, a)), ...]) for a layer."""
    title, bands = LAYER_BANDS[mode]
    return title, [(text, tuple(int(c) for c in rgba)) for _lo, _hi, rgba, text in bands]


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


def dim_outside(img: Image.Image, mask: np.ndarray, factor: float = 0.3) -> Image.Image:
    """Fade an overlay outside ``mask`` (terrain grid, row 0 = south): what a
    plan does not protect stays visible but no longer reads as coverage."""
    m = Image.fromarray(np.flipud(mask).astype(np.uint8) * 255).resize(img.size, Image.Resampling.NEAREST)
    keep = np.asarray(m) > 0
    arr = np.array(img.convert("RGBA"))
    arr[..., 3] = np.where(keep, arr[..., 3], (arr[..., 3] * factor).astype(np.uint8))
    return Image.fromarray(arr, "RGBA")


def zone_overlay(dori_grid: np.ndarray, size: Tuple[int, int], *, mode: str = "dori") -> Image.Image:
    """A ZONE_* (or any banded) grid as a smoothed RGBA image of ``size``."""
    g = np.flipud(dori_grid)
    ov = np.zeros(g.shape + (4,), dtype=np.uint8)
    paint_bands(ov, g, LAYER_BANDS[mode][1], mask=True if mode in ("dori", "envelope", "fused", "overlap", "approach", "city", "streets") else None)
    return smooth_overlay(ov, size)


def single_view_overlay(terrain: TerrainData, dori_grid: np.ndarray, size: Tuple[int, int], *,
                        cam_x_m: float, cam_y_m: float, pan_deg: float, hfov_deg: float,
                        max_range_m: float) -> Image.Image:
    """One camera's DORI fan, smoothed and cut to its optical cone."""
    img = zone_overlay(dori_grid, size)
    sx = size[0] / terrain.width_m
    sy = size[1] / terrain.height_m
    apex = ((cam_x_m - terrain.origin_x) * sx, (terrain.height_m - (cam_y_m - terrain.origin_y)) * sy)
    return clip_to_wedge(img, apex, (sx, sy), pan_deg, hfov_deg, max_range_m + terrain.cell_size_m)
