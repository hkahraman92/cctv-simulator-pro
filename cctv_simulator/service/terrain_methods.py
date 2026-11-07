"""API v1: terrain, surface model, viewshed, perimeter planning.

Grids live in the core session (``session.SESSION``); the shell receives
summaries plus pictures (base64 PNG/JPEG) sized for display. Every number is
read from the engine's grids, never from a picture (Rule 1). Long calls report
``$/progress`` and stop at the next progress point when cancelled.

Coordinates: local metres, origin south-west (``TerrainData`` frame); pictures
are north-up and span exactly ``width_m`` x ``height_m``.
"""
from __future__ import annotations

import base64
import dataclasses
import io
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import session as _s
from .params import _camera_from, _require
from .protocol import APP_ERROR, INVALID_PARAMS, Cancelled, Context, RpcError

_DISPLAY_PX = 1400            # longest side of returned pictures (default)

PROCEDURAL_PRESETS = [
    ("ridge_and_valley", "Hâkim Tepe & Vadi"),
    ("border_outpost", "Sınır Karakolu & Dağlık Arazi"),
    ("canyon", "Kanyon & Geçit"),
]
MAP_SOURCES = [("esri", "Esri uydu fotoğrafı"), ("osm", "OpenStreetMap"), ("opentopo", "OpenTopoMap")]
QUALITIES = [("standard", "Standart"), ("hd", "HD"), ("ultra", "Ultra")]


# ── helpers ──────────────────────────────────────────────────────────────
def _num(params: Dict[str, Any], key: str, default: Optional[float] = None, *,
         lo: Optional[float] = None, hi: Optional[float] = None) -> float:
    v = params.get(key, default)
    if v is None:
        raise RpcError(INVALID_PARAMS, f"'{key}' parametresi gerekli.")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise RpcError(INVALID_PARAMS, f"'{key}' sayı olmalı.")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise RpcError(INVALID_PARAMS, f"'{key}' {lo}–{hi} aralığında olmalı.")
    return float(v)


def _image(img, *, max_px: int, fmt: str = "PNG") -> Dict[str, Any]:
    from PIL import Image

    scale = min(1.0, max_px / max(img.width, img.height))
    if scale < 1.0:
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                         Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    if fmt == "JPEG":
        img.convert("RGB").save(buf, "JPEG", quality=88)
        mime = "image/jpeg"
    else:
        img.save(buf, "PNG", optimize=False)
        mime = "image/png"
    return {"data": f"data:{mime};base64," + base64.b64encode(buf.getvalue()).decode("ascii"),
            "width": img.width, "height": img.height}


def _info(entry: _s.TerrainEntry) -> Dict[str, Any]:
    t = entry.bare
    surf = entry.surface
    return {
        "terrain_id": entry.id, "label": entry.label or t.name, "name": t.name, "source": entry.source,
        "region_id": entry.region_id, "width_m": t.width_m, "height_m": t.height_m,
        "cell_size_m": t.cell_size_m, "rows": t.rows, "cols": t.cols,
        "z_min_m": t.min_elev_m, "z_max_m": t.max_elev_m,
        "lat_center": t.lat_center, "lon_center": t.lon_center,
        "is_measured": bool(t.is_measured), "source_note": t.source_note,
        "has_photo": entry.photo is not None, "attribution": entry.attribution,
        "has_obstacles": surf is not None,
        "obstacle_note": surf.obstacle_note if surf is not None else "",
        # how it was loaded, so a project file can reopen it
        "preset": entry.extra.get("preset", ""), "file": entry.extra.get("file", ""),
    }


def _register(terrain, photo=None, **kw) -> _s.TerrainEntry:
    from .. import surface_obstacles as obs

    surface = None
    if terrain.has_obstacles:                     # e.g. a saved region with its surface model
        surface, terrain = terrain, obs.bare_earth(terrain)
    return _s.SESSION.add(dict(bare=terrain, photo=photo, surface=surface, **kw))


def _terrain_of(params: Dict[str, Any]):
    entry = _s.SESSION.get(params.get("terrain_id"))
    return entry, entry.terrain(bool(params.get("use_obstacles", True)))


# ── catalog ──────────────────────────────────────────────────────────────
def terrain_catalog(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Procedural presets, map sources, weather presets and named sites."""
    from ..atmosphere import WEATHER_PRESETS
    from ..online_map_loader import PRESET_LOCATIONS

    return {
        "presets": [{"id": i, "label": label} for i, label in PROCEDURAL_PRESETS],
        "map_sources": [{"id": i, "label": label} for i, label in MAP_SOURCES],
        "qualities": [{"id": i, "label": label} for i, label in QUALITIES],
        "weather": [{"label": k, "visibility_km": v} for k, v in WEATHER_PRESETS.items()],
        "locations": [{"label": k, "lat": la, "lon": lo, "note": note}
                      for k, (la, lo, note) in PRESET_LOCATIONS.items()],
    }


# ── loading ──────────────────────────────────────────────────────────────
def terrain_procedural(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Synthetic relief (NOT a measurement; ``is_measured`` is false)."""
    from ..terrain_loader import generate_procedural_terrain

    preset = params.get("preset", "ridge_and_valley")
    if preset not in dict(PROCEDURAL_PRESETS):
        raise RpcError(INVALID_PARAMS, f"Bilinmeyen önayar: {preset}")
    grid = int(_num(params, "grid", 200, lo=50, hi=800))
    width = _num(params, "width_m", 2000.0, lo=200, hi=20000)
    t = generate_procedural_terrain(preset, grid_size=grid, cell_size_m=width / grid)
    return _info(_register(t, label=dict(PROCEDURAL_PRESETS)[preset], source="procedural",
                           extra={"preset": preset, "grid": grid, "width_m": width}))


def terrain_geotiff(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """A local DEM GeoTIFF (measured)."""
    from ..terrain_loader import load_geotiff_or_dem

    path = _require(params, "path", str)
    try:
        t = load_geotiff_or_dem(path)
    except FileNotFoundError:
        raise RpcError(APP_ERROR, f"Dosya bulunamadı: {path}") from None
    except Exception as exc:
        raise RpcError(APP_ERROR, f"Yükselti dosyası okunamadı: {exc}") from None
    return _info(_register(t, label=t.name, source="geotiff", extra={"file": path}))


def terrain_download(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Orthophoto mosaic + measured DEM for a site; optionally saved as a region."""
    from ..online_map_loader import download_satellite_mosaic, fetch_online_elevation_grid

    lat = _num(params, "lat", lo=-85, hi=85)
    lon = _num(params, "lon", lo=-180, hi=180)
    width = _num(params, "width_m", 2000.0, lo=200, hi=6000)
    source = params.get("source", "esri")
    if source not in dict(MAP_SOURCES):
        raise RpcError(INVALID_PARAMS, f"Bilinmeyen harita kaynağı: {source}")
    quality = params.get("quality", "hd")
    if quality not in dict(QUALITIES):
        raise RpcError(INVALID_PARAMS, f"Bilinmeyen kalite: {quality}")
    grid = int(_num(params, "grid", 400, lo=100, hi=800))
    label = str(params.get("label") or f"{lat:.4f}, {lon:.4f}")

    try:
        photo = download_satellite_mosaic(
            center_lat=lat, center_lon=lon, width_m=width, height_m=width, source=source, quality=quality,
            progress_callback=lambda d, n: ctx.progress(d, n * 2, f"Uydu kareleri {d}/{n}"))
        meta = dict(photo.info)
        terrain = fetch_online_elevation_grid(
            center_lat=lat, center_lon=lon, width_m=width, height_m=width, grid_size=grid,
            progress_callback=lambda d, n: ctx.progress(n + d, n * 2, f"Yükselti kareleri {d}/{n}"))
    except (RpcError, Cancelled):
        raise
    except Exception as exc:
        raise RpcError(APP_ERROR, f"Harita indirilemedi: {exc}") from None

    region_id, save_error = "", ""
    if params.get("save"):
        ctx.progress(1, 1, "Bölge diske kaydediliyor")
        try:
            from ..region_library import save_region
            saved = save_region(str(params.get("save_name") or label), terrain, photo, source=source,
                                quality=quality, zoom=meta.get("zoom"), m_per_px=meta.get("m_per_px"),
                                attribution=str(meta.get("attribution", "")),
                                extra={"request_lat": lat, "request_lon": lon, "request_width_m": width})
            region_id = saved.region_id
        except Exception as exc:          # a failed save never fails the download
            save_error = str(exc) or type(exc).__name__
    entry = _register(terrain, photo.convert("RGBA"), label=label, source="online", region_id=region_id,
                      attribution=str(meta.get("attribution", "")),
                      extra={"m_per_px": meta.get("m_per_px"), "zoom": meta.get("zoom")})
    out = _info(entry)
    out["save_error"] = save_error
    return out


def regions_list(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Saved regions (DEM + orthophoto on disk, open offline)."""
    from ..region_library import list_regions

    return {"regions": [{**dataclasses.asdict(r), "label": r.label} for r in list_regions()]}


def regions_open(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Open a saved region (with its surface model, if one was saved)."""
    from ..region_library import load_region

    rid = _require(params, "region_id", str)
    try:
        terrain, img, info = load_region(rid)
    except (FileNotFoundError, ValueError) as exc:
        raise RpcError(APP_ERROR, str(exc)) from None
    entry = _register(terrain, img.convert("RGBA") if img is not None else None, label=info.name,
                      source="region", region_id=rid, attribution=info.attribution)
    return _info(entry)


def regions_save(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Save the current terrain (surface model included) as a region."""
    from ..region_library import save_region

    entry = _s.SESSION.get(params.get("terrain_id"))
    name = _require(params, "name", str)
    info = save_region(name, entry.terrain(True), entry.photo, source=entry.source,
                       attribution=entry.attribution)
    entry.region_id = info.region_id
    return {"region_id": info.region_id, "size_bytes": info.size_bytes}


def regions_delete(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Delete a saved region from disk (cannot be undone)."""
    from ..region_library import delete_region

    rid = _require(params, "region_id", str)
    try:
        delete_region(rid)
    except (FileNotFoundError, ValueError) as exc:
        raise RpcError(APP_ERROR, str(exc)) from None
    return {"deleted": rid}


# ── surface model ────────────────────────────────────────────────────────
def surface_osm(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Buildings / trees from OpenStreetMap as the surface model (heights partly estimated)."""
    from .. import surface_obstacles as obs

    entry = _s.SESSION.get(params.get("terrain_id"))
    try:
        bbox = obs.terrain_bbox(entry.bare)
    except ValueError as exc:
        raise RpcError(APP_ERROR, str(exc)) from None
    ctx.progress(0, 2, "OpenStreetMap sorgulanıyor")
    try:
        feats = obs.fetch_osm_obstacles(bbox)
    except RuntimeError as exc:
        raise RpcError(APP_ERROR, f"Bina / ağaç verisi indirilemedi: {exc}") from None
    ctx.progress(1, 2, "Izgaraya işleniyor")
    if not feats:
        return {**_info(entry), "features": 0, "estimated_share": 0.0,
                "message": "OpenStreetMap'te bu alanda bina / ağaç kaydı yok; görüş çıplak arazi üzerinden."}
    layer = obs.rasterize(entry.bare, feats)
    entry.surface = obs.with_obstacles(entry.bare, layer)
    entry.results.clear()
    return {**_info(entry), "features": len(feats), "estimated_share": float(layer.estimated_share),
            "summary": layer.summary(), "covered_fraction": float(layer.covered_fraction)}


def surface_raster(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """A measured LiDAR nDSM / DSM GeoTIFF on top of the DEM (``mode``: auto | ndsm | dsm)."""
    from .. import surface_obstacles as obs
    from ..geotiff_reader import UnsupportedCRS, read_geotiff

    entry = _s.SESSION.get(params.get("terrain_id"))
    if entry.bare.lat_center is None:
        raise RpcError(APP_ERROR, "Yüzey modeli yalnız konumu bilinen bir araziye (indirilen ya da kayıtlı "
                                  "bölge) oturtulabilir.")
    path = _require(params, "path", str)
    mode = params.get("mode", "auto")
    if mode not in ("auto", "ndsm", "dsm"):
        raise RpcError(INVALID_PARAMS, "'mode' auto, ndsm ya da dsm olmalı.")
    surf = entry.surface
    base = (obs.ObstacleLayer(height_grid=surf.obstacle_grid, kind_grid=surf.obstacle_kind, counts={})
            if surf is not None and surf.obstacle_grid is not None else None)
    try:
        ras = read_geotiff(path)
        layer, info = obs.layer_from_raster(entry.bare, ras, mode=mode, base=base)
    except FileNotFoundError:
        raise RpcError(APP_ERROR, f"Dosya bulunamadı: {path}") from None
    except UnsupportedCRS as exc:
        raise RpcError(APP_ERROR, str(exc)) from None
    except ValueError as exc:
        if mode == "auto" and "anlaşılamadı" in str(exc):
            raise RpcError(APP_ERROR, str(exc), {"needs_mode": True}) from None
        raise RpcError(APP_ERROR, str(exc)) from None
    if surf is not None and surf.obstacle_note and info["coverage_frac"] < 0.999:
        layer.measured_note += " · kapsam dışı: " + surf.obstacle_note
    entry.surface = obs.with_obstacles(entry.bare, layer)
    entry.results.clear()
    return {**_info(entry), "summary": layer.summary(), "coverage_frac": float(info["coverage_frac"])}


# ── pictures ─────────────────────────────────────────────────────────────
def terrain_image(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Base map picture: ``mode`` hybrid | satellite | elevation, with buildings."""
    from PIL import Image

    from .. import map_render

    entry, t = _terrain_of(params)
    mode = params.get("mode", "hybrid")
    if mode not in ("hybrid", "satellite", "elevation"):
        raise RpcError(INVALID_PARAMS, "'mode' hybrid, satellite ya da elevation olmalı.")
    opacity = _num(params, "photo_opacity", 0.8, lo=0, hi=1)
    max_px = int(_num(params, "max_px", _DISPLAY_PX, lo=200, hi=4096))
    key = ("base", id(t), mode, round(opacity, 2), max_px)
    cached = entry.results.get("_image")
    if cached and cached[0] == key:
        return cached[1]
    img = map_render.relief_image(t, mode, entry.photo, opacity, palette="console")
    layer = map_render.obstacle_layer(t, img.size, entry.photo is not None and mode != "elevation")
    if layer is not None:
        img = Image.alpha_composite(img.convert("RGBA"), layer)
    out = {**_image(img, max_px=max_px, fmt="JPEG"), "width_m": t.width_m, "height_m": t.height_m,
           "procedural_texture": entry.photo is None and mode != "elevation"}
    entry.results["_image"] = (key, out)
    return out


def _overlay_size(t, max_px: int) -> Tuple[int, int]:
    k = max_px / max(t.width_m, t.height_m)
    return max(1, round(t.width_m * k)), max(1, round(t.height_m * k))


def _zone_areas(dori: np.ndarray, cell_area: float, mask: Optional[np.ndarray] = None) -> Dict[str, float]:
    from ..viewshed_3d import ZONE_DETECT, ZONE_IDENT, ZONE_OBSERVE, ZONE_OCCLUDED, ZONE_RECOG

    m = np.ones(dori.shape, bool) if mask is None else mask
    return {name: float(((dori == z) & m).sum() * cell_area) for name, z in (
        ("identify", ZONE_IDENT), ("recognize", ZONE_RECOG), ("observe", ZONE_OBSERVE),
        ("detect", ZONE_DETECT), ("occluded", ZONE_OCCLUDED))}


# ── analyses ─────────────────────────────────────────────────────────────
def viewshed_single(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """One camera on the terrain: authoritative viewshed + DORI map + sight profile."""
    from .. import map_render, surface_obstacles as obs
    from ..atmosphere import WEATHER_PRESETS
    from ..viewshed_3d import calculate_3d_viewshed

    entry, t = _terrain_of(params)
    cam = _camera_from(params.get("camera", {}))
    x = _num(params, "x_m"); y = _num(params, "y_m")
    mast = _num(params, "mast_m", 6.0, lo=0.5, hi=200)
    pan = _num(params, "pan_deg", 0.0) % 360.0
    tilt = _num(params, "tilt_deg", -5.0, lo=-89, hi=30)
    rng = _num(params, "range_m", 1500.0, lo=20, hi=20000)
    weather = str(params.get("weather", "")) if params.get("weather") in WEATHER_PRESETS else ""
    vis = WEATHER_PRESETS.get(weather, 40.0)
    lens_mode = params.get("lens_mode", "min")
    if lens_mode not in ("min", "max"):
        raise RpcError(INVALID_PARAMS, "'lens_mode' min ya da max olmalı.")
    focal = params.get("focal_mm")
    if focal is not None:
        focal = _num(params, "focal_mm", lo=cam.focal_min_mm, hi=cam.focal_max_mm)
    res = calculate_3d_viewshed(t, x, y, mast, cam, lens_mode=lens_mode, pan_deg=pan, tilt_deg=tilt,
                                max_range_m=rng, visibility_km=vis, weather=weather,
                                focal_mm_override=focal)
    entry.results["single"] = res
    max_px = int(_num(params, "max_px", _DISPLAY_PX, lo=200, hi=4096))
    overlay = map_render.single_view_overlay(t, res.dori_grid, _overlay_size(t, max_px), cam_x_m=x, cam_y_m=y,
                                             pan_deg=pan, hfov_deg=res.hfov_deg, max_range_m=res.max_range_m)
    step = max(1, len(res.profile_dists_m) // 400)
    warnings = []
    blocked = obs.mount_obstruction_m(t, x, y, mast)
    if blocked > 0:
        warnings.append(f"Direk hücresindeki engel lensten {blocked:.1f} m yüksek; kamera bina/ağaç içinde kalıyor.")
    if not t.is_measured:
        warnings.append("Arazi ölçüm değil (prosedürel); sonuç bağlayıcı değildir.")
    return {
        "hfov_deg": res.hfov_deg, "vfov_deg": res.vfov_deg, "cam_ground_z_m": res.cam_ground_z_m,
        "cam_total_z_m": res.cam_total_z_m, "max_range_m": res.max_range_m,
        "optical_limit_m": res.optical_limit_m, "atmospheric_limit_m": res.atmospheric_limit_m,
        "fov_area_m2": res.fov_area_m2, "visible_area_m2": res.visible_area_m2,
        "occluded_area_m2": res.occluded_area_m2, "coverage_pct": res.coverage_pct,
        "max_los_reach_m": res.max_los_reach_m,
        "zone_areas_m2": _zone_areas(res.dori_grid, t.cell_size_m ** 2),
        "profile": {"dist_m": res.profile_dists_m[::step].tolist(),
                    "terrain_m": res.profile_terrain_elev_m[::step].tolist(),
                    "ray_m": res.profile_ray_elev_m[::step].tolist(),
                    "visible": res.profile_is_visible[::step].astype(bool).tolist()},
        "overlay": _image(overlay, max_px=max_px),
        "legend": map_render.legend("dori")[1],
        "warnings": warnings,
    }


def _plan_kwargs(params: Dict[str, Any]) -> Dict[str, Any]:
    from ..atmosphere import WEATHER_PRESETS

    weather = str(params.get("weather", "")) if params.get("weather") in WEATHER_PRESETS else ""
    line_mode = params.get("line_mode", "facility")
    if line_mode not in ("facility", "border", "highway"):
        raise RpcError(INVALID_PARAMS, "'line_mode' facility, border ya da highway olmalı.")
    watch_side = params.get("watch_side", "right")
    if watch_side not in ("left", "right"):
        raise RpcError(INVALID_PARAMS, "'watch_side' left ya da right olmalı.")
    kw: Dict[str, Any] = {
        "target_ppm": _num(params, "target_ppm", 40.0, lo=1, hi=2000),
        "mast_height_m": _num(params, "mast_m", 6.0, lo=0.5, hi=200),
        "overlap_pct": _num(params, "overlap_pct", 0.15, lo=0, hi=0.9),
        "lens_mode": params.get("lens_mode", "min") if params.get("lens_mode") in ("min", "max") else "min",
        "is_closed_loop": bool(params.get("closed", True)),
        "visibility_km": WEATHER_PRESETS.get(weather, 40.0), "weather": weather,
        "line_mode": line_mode, "watch_side": watch_side,
    }
    if params.get("ptz_camera"):
        kw["ptz_camera"] = _camera_from(params["ptz_camera"])
        kw["ptz_every_n"] = int(_num(params, "ptz_every_n", 4, lo=1, hi=100))
        kw["ptz_patrol_span_deg"] = _num(params, "ptz_patrol_span_deg", 360.0, lo=10, hi=360)
    return kw


def _fence(params: Dict[str, Any]) -> List[Tuple[float, float]]:
    pts = params.get("fence")
    if not isinstance(pts, list) or len(pts) < 2:
        raise RpcError(INVALID_PARAMS, "'fence' en az iki [x, y] noktası olmalı.")
    out = []
    for p in pts:
        if (not isinstance(p, (list, tuple)) or len(p) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in p)):
            raise RpcError(INVALID_PARAMS, "Her çit noktası [x, y] (metre) olmalı.")
        out.append((float(p[0]), float(p[1])))
    return out


def _plan_json(plan) -> Dict[str, Any]:
    return {
        "poles": [dataclasses.asdict(c) for c in plan.placed_cameras],
        "ptz": [dataclasses.asdict(c) for c in plan.ptz_cameras],
        "gaps": [dataclasses.asdict(g) for g in plan.gaps],
        "total_fence_length_m": plan.total_fence_length_m, "camera_count": plan.camera_count,
        "pole_count": plan.pole_count, "target_ppm": plan.target_ppm, "avg_spacing_m": plan.avg_spacing_m,
        "coverage_percentage": plan.coverage_percentage,
        "estimated_bandwidth_mbps": plan.estimated_bandwidth_mbps,
        "estimated_storage_30days_tb": plan.estimated_storage_30days_tb,
        "line_mode": plan.line_mode, "watch_side": plan.watch_side,
    }


def perimeter_plan(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Poles along a fence / border / road line (planner + fence-line gap analysis)."""
    from ..perimeter_planner import generate_perimeter_plan

    entry, t = _terrain_of(params)
    cam = _camera_from(params.get("camera", {}))
    fence = _fence(params)
    kw = _plan_kwargs(params)
    if params.get("spacing_m") is not None:
        kw["spacing_m"] = _num(params, "spacing_m", lo=2, hi=5000)
    plan = generate_perimeter_plan(t, fence, cam, **kw)
    entry.results["plan"] = (plan, cam, kw, fence)
    return _plan_json(plan)


def perimeter_coverage(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """The last plan through the authoritative multi-camera viewshed: DORI map + fence-band shares."""
    from .. import map_render
    from ..focal_optimizer import band_area, fence_roi
    from ..viewshed_3d import ZONE_DETECT, ZONE_IDENT, ZONE_OBSERVE, ZONE_RECOG

    entry, t = _terrain_of(params)
    if "plan" not in entry.results:
        raise RpcError(APP_ERROR, "Önce bir çit planı oluşturun.")
    plan, cam, kw, fence = entry.results["plan"]
    if not plan.placed_cameras:
        raise RpcError(APP_ERROR, "Planda kamera yok.")
    ctx.progress(0, 1, f"{len(plan.placed_cameras)} kamera hesaplanıyor")
    band = _num(params, "band_m", 40.0, lo=5, hi=500)
    roi = fence_roi(t, fence, kw["is_closed_loop"], band)
    # Each pole is evaluated out to its Detection reach: the plan caps it at
    # the fence target (e.g. 40 px/m), which would cut the lower DORI bands.
    mv, _ = band_area(t, plan, cam, roi, ZONE_DETECT, visibility_km=kw["visibility_km"], weather=kw["weather"])
    entry.results["multi"] = mv
    cell = t.cell_size_m ** 2
    roi_area = float(roi.sum() * cell)
    band_share = {name: (float(((mv.dori_grid >= z) & roi).sum() * cell) / roi_area if roi_area else 0.0)
                  for name, z in (("identify", ZONE_IDENT), ("recognize", ZONE_RECOG),
                                  ("observe", ZONE_OBSERVE), ("detect", ZONE_DETECT))}
    max_px = int(_num(params, "max_px", _DISPLAY_PX, lo=200, hi=4096))
    layer = params.get("layer", "dori")
    if layer not in ("dori", "overlap"):
        raise RpcError(INVALID_PARAMS, "'layer' dori ya da overlap olmalı.")
    grid = mv.dori_grid if layer == "dori" else mv.seen_count_grid
    overlay = map_render.zone_overlay(grid, _overlay_size(t, max_px), mode=layer)
    return {
        "camera_count": mv.camera_count, "fov_area_m2": mv.fov_area_m2, "visible_area_m2": mv.visible_area_m2,
        "occluded_area_m2": mv.occluded_area_m2, "coverage_pct": mv.coverage_pct,
        "overlap_area_m2": mv.overlap_area_m2, "single_cover_area_m2": mv.single_cover_area_m2,
        "zone_areas_m2": _zone_areas(mv.dori_grid, cell),
        "band_m": band, "band_area_m2": roi_area, "band_share": band_share,
        "overlay": _image(overlay, max_px=max_px), "legend": map_render.legend(layer)[1],
        "measured": bool(t.is_measured),
    }


def perimeter_minimize_poles(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Fewest poles that still hold the fence-band goal (spacing search + per-pole focal)."""
    from ..pole_optimizer import minimize_poles
    from ..viewshed_3d import ZONE_DETECT, ZONE_IDENT, ZONE_OBSERVE, ZONE_RECOG

    entry, t = _terrain_of(params)
    cam = _camera_from(params.get("camera", {}))
    fence = _fence(params)
    kw = _plan_kwargs(params)
    levels = {"detect": ZONE_DETECT, "observe": ZONE_OBSERVE, "recognize": ZONE_RECOG, "identify": ZONE_IDENT}
    level = levels.get(params.get("level", "detect"))
    if level is None:
        raise RpcError(INVALID_PARAMS, "'level' detect, observe, recognize ya da identify olmalı.")
    goal = _num(params, "goal_frac", 0.95, lo=0.05, hi=1.0)
    res = minimize_poles(t, fence, cam, plan_kwargs=kw, goal_frac=goal, level=level,
                         band_m=_num(params, "band_m", 40.0, lo=5, hi=500),
                         progress=lambda d, n: ctx.progress(d, n, f"Deneme {d}/{n}"))
    plan = None
    if res.met and res.best_plan is not None:
        # The search scored per-pole focal / tilt choices: apply them, so the
        # plan shown is the one that met the goal (fence gaps re-analysed).
        plan = res.best_plan
        if res.best_layout is not None:
            from ..focal_optimizer import apply_layout
            plan = apply_layout(plan, res.best_layout, cam, kw["target_ppm"], terrain=t,
                                visibility_km=kw["visibility_km"], weather=kw["weather"])
        entry.results["plan"] = (plan, cam, kw, fence)
    return {
        "met": res.met, "goal_frac": res.goal_frac, "roi_area_m2": res.roi_area_m2, "notes": res.notes,
        "base": dataclasses.asdict(res.base),
        "best": dataclasses.asdict(res.best) if res.best else None,
        "trials": [dataclasses.asdict(tr) for tr in res.trials],
        "plan": _plan_json(plan) if plan is not None else None,
    }


def focal_sweep(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Varifocal: DORI area at every focal step for one mount (best tilt / pan per focal)."""
    from ..atmosphere import WEATHER_PRESETS
    from ..focal_optimizer import sweep_focals
    from ..viewshed_3d import ZONE_DETECT, ZONE_IDENT, ZONE_OBSERVE, ZONE_RECOG

    entry, t = _terrain_of(params)
    cam = _camera_from(params.get("camera", {}))
    levels = {"detect": ZONE_DETECT, "observe": ZONE_OBSERVE, "recognize": ZONE_RECOG, "identify": ZONE_IDENT}
    level = levels.get(params.get("level", "recognize"))
    if level is None:
        raise RpcError(INVALID_PARAMS, "'level' detect, observe, recognize ya da identify olmalı.")
    weather = str(params.get("weather", "")) if params.get("weather") in WEATHER_PRESETS else ""
    rep = sweep_focals(t, _num(params, "x_m"), _num(params, "y_m"), _num(params, "mast_m", 6.0, lo=0.5, hi=200),
                       cam, pan_deg=_num(params, "pan_deg", 0.0) % 360.0,
                       pan_search_deg=_num(params, "pan_search_deg", 0.0, lo=0, hi=180),
                       level=level, objective=params.get("objective", "area"),
                       n_focals=int(_num(params, "n_focals", 12, lo=2, hi=40)),
                       max_range_m=params.get("range_m"), visibility_km=WEATHER_PRESETS.get(weather, 40.0),
                       weather=weather)
    names = {ZONE_IDENT: "identify", ZONE_RECOG: "recognize", ZONE_OBSERVE: "observe", ZONE_DETECT: "detect"}

    def choice(c):
        return {"focal_mm": c.focal_mm, "tilt_deg": c.tilt_deg, "pan_deg": c.pan_deg, "hfov_deg": c.hfov_deg,
                "score": c.score, "areas_m2": {names[k]: v for k, v in c.areas_m2.items() if k in names}}
    return {"curve": [choice(c) for c in rep.curve], "best": choice(rep.best), "wide": choice(rep.wide),
            "tele": choice(rep.tele), "level": names[rep.level], "reach_m": rep.reach_m}


METHODS = {
    "terrain.catalog": terrain_catalog,
    "terrain.procedural": terrain_procedural,
    "terrain.geotiff": terrain_geotiff,
    "terrain.download": terrain_download,
    "terrain.image": terrain_image,
    "regions.list": regions_list,
    "regions.open": regions_open,
    "regions.save": regions_save,
    "regions.delete": regions_delete,
    "surface.osm": surface_osm,
    "surface.raster": surface_raster,
    "viewshed.single": viewshed_single,
    "perimeter.plan": perimeter_plan,
    "perimeter.coverage": perimeter_coverage,
    "perimeter.minimize_poles": perimeter_minimize_poles,
    "focal.sweep": focal_sweep,
}
