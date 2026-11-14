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
    if "target_height_m" in params:      # a call may carry it (the live single view does)
        entry.set_target_height(_num(params, "target_height_m", lo=0.0, hi=5.0))
    _activate(entry)
    return entry, entry.terrain(bool(params.get("use_obstacles", True)))


def _activate(entry) -> None:
    """Every engine path in this request scores a person of the terrain's
    target height (viewshed_3d context variable; worker threads are reused,
    so it is set on every call, never inherited)."""
    from ..viewshed_3d import set_target_height
    set_target_height(entry.target_height_m)


def terrain_settings(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Analysis settings kept with the terrain: ``target_height_m`` (a person
    of this height standing on each cell: sight line and framing to the head;
    0 = the ground point). Changing it drops results computed with the old one."""
    entry = _s.SESSION.get(params.get("terrain_id"))
    changed = False
    if "target_height_m" in params:
        changed = entry.set_target_height(_num(params, "target_height_m", lo=0.0, hi=5.0))
    return {"target_height_m": entry.target_height_m, "changed": changed}


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
    from ..viewshed_3d import ZONE_DETECT, ZONE_IDENT, ZONE_MONITOR, ZONE_OBSERVE, ZONE_OCCLUDED, ZONE_RECOG

    m = np.ones(dori.shape, bool) if mask is None else mask
    out = {name: float(((dori == z) & m).sum() * cell_area) for name, z in (
        ("identify", ZONE_IDENT), ("recognize", ZONE_RECOG), ("observe", ZONE_OBSERVE),
        ("detect", ZONE_DETECT), ("occluded", ZONE_OCCLUDED))}
    if (dori == ZONE_MONITOR).any():       # only a monitor-floor run has such cells; default output unchanged
        out["monitor"] = float(((dori == ZONE_MONITOR) & m).sum() * cell_area)
    return out


# ── analyses ─────────────────────────────────────────────────────────────
def viewshed_single(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """One camera on the terrain: authoritative viewshed + DORI map + sight profile."""
    from .. import map_render, surface_obstacles as obs
    from ..atmosphere import WEATHER_PRESETS, band_for_camera, usable_range_m
    from ..viewshed_3d import ZONE_MONITOR, calculate_3d_viewshed, is_thermal_camera

    entry, t = _terrain_of(params)
    cam = _camera_from(params.get("camera", {}))
    # "level": "monitor" asks for the Monitoring floor (12.5 px/m, visible cameras); any other
    # named level keeps today's Detection floor; an unknown name or a thermal monitor is an error
    monitor = params.get("level") is not None and _level_of(params, cam, monitor_ok=True) == ZONE_MONITOR
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
                                focal_mm_override=focal, monitor_floor=monitor)
    entry.results["single"] = res
    max_px = int(_num(params, "max_px", _DISPLAY_PX, lo=200, hi=4096))
    overlay = map_render.single_view_overlay(t, res.dori_grid, _overlay_size(t, max_px), cam_x_m=x, cam_y_m=y,
                                             pan_deg=pan, hfov_deg=res.hfov_deg, max_range_m=res.max_range_m,
                                             mode="dori_monitor" if monitor else "dori")
    step = max(1, len(res.profile_dists_m) // 400)
    warnings = []
    blocked = obs.mount_obstruction_m(t, x, y, mast)
    if blocked > 0:
        warnings.append(f"Direk hücresindeki engel lensten {blocked:.1f} m yüksek; kamera bina/ağaç içinde kalıyor.")
    if not t.is_measured:
        warnings.append("Arazi ölçüm değil (prosedürel); sonuç bağlayıcı değildir.")
    # Why a control did nothing (both were reported as suspected bugs): the weather's own contrast
    # range (the engine's relation, uncapped by the optics) and whether anything in frame is hidden.
    notes = []
    weather_range = usable_range_m(math.inf, vis, band_for_camera(cam.sensor_name, cam.model_name), weather)
    weather_range = float(weather_range) if math.isfinite(weather_range) else None
    if weather and vis < 39.9 and weather_range is not None and weather_range >= res.optical_limit_m:
        notes.append(f"{weather}: kontrast {weather_range:.0f} m'ye kadar yetiyor; bu lensin optik sınırı "
                     f"({res.optical_limit_m:.0f} m) daha kısa, hava bu görüşü kısaltmıyor.")
    if res.visible_area_m2 > 0 and res.occluded_area_m2 < 0.01 * res.visible_area_m2:
        notes.append("Kadrajda arazi / bina gölgesi yok: direği yükseltmek görünür alanı artırmaz, yalnız "
                     "kadrajın yakın kenarını uzaklaştırır.")
    # Farthest ground the frame holds (seen or hidden): a steep tilt frames
    # only the foot of the mast, however far the optics could resolve.
    rr, cc = np.nonzero(res.dori_grid != 0)
    framed_reach = float(min(np.hypot(t.origin_x + cc * t.cell_size_m - x,
                                      t.origin_y + rr * t.cell_size_m - y).max(), res.max_range_m)) if rr.size else 0.0
    return {
        "hfov_deg": res.hfov_deg, "vfov_deg": res.vfov_deg, "cam_ground_z_m": res.cam_ground_z_m,
        "cam_total_z_m": res.cam_total_z_m, "max_range_m": res.max_range_m,
        "optical_limit_m": res.optical_limit_m, "atmospheric_limit_m": res.atmospheric_limit_m,
        "fov_area_m2": res.fov_area_m2, "visible_area_m2": res.visible_area_m2,
        "occluded_area_m2": res.occluded_area_m2, "coverage_pct": res.coverage_pct,
        "max_los_reach_m": res.max_los_reach_m, "framed_reach_m": framed_reach,
        "zone_areas_m2": _zone_areas(res.dori_grid, t.cell_size_m ** 2),
        "profile": {"dist_m": res.profile_dists_m[::step].tolist(),
                    "terrain_m": res.profile_terrain_elev_m[::step].tolist(),
                    "ray_m": res.profile_ray_elev_m[::step].tolist(),
                    "visible": res.profile_is_visible[::step].astype(bool).tolist()},
        "overlay": _image(overlay, max_px=max_px),
        "legend": map_render.legend("dori_thermal" if is_thermal_camera(cam)
                                    else "dori_monitor" if monitor else "dori")[1],
        "thermal": is_thermal_camera(cam), "monitor_floor": monitor,
        "warnings": warnings,
        "weather_range_m": weather_range, "notes": notes,
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
    if params.get("back_to_back"):
        kw["back_to_back"] = True
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
        "ptz_count": len(plan.ptz_cameras),
    }


def _plan_roi(t, fence, kw: Dict[str, Any], band_m: float, inside_m: Optional[float] = None) -> np.ndarray:
    """What a plan protects: the drawn polygon for an area plan, else the fence
    band (``band_m`` outside, ``inside_m`` inside a closed fence; None = same)."""
    if kw.get("line_mode") == "area":
        from ..area_planner import target_mask
        roi = target_mask(t, fence)
        if kw.get("roi_mask") is not None:          # street-only area plan: its streets
            roi &= kw["roi_mask"]
        return roi
    from ..focal_optimizer import fence_roi
    side = kw.get("watch_side") if kw.get("line_mode") == "border" else None
    return fence_roi(t, fence, kw["is_closed_loop"], band_m, inside_m, side)


def _band_params(params: Dict[str, Any]) -> Tuple[float, Optional[float]]:
    """(outside / symmetric band width, inside width or None) from a request."""
    band = _num(params, "band_m", 40.0, lo=5, hi=3000)
    inside = params.get("band_in_m")
    return band, (None if inside is None else _num(params, "band_in_m", lo=0, hi=500))


def _ptz_main_warning(cam) -> List[str]:
    """A PTZ chosen as the main (fixed) camera is planned as a fixed lens: say so."""
    from ..database import is_ptz_camera
    if is_ptz_camera({"camera_type": cam.camera_type, "model_name": cam.model_name}):
        return [f"{cam.model_name or cam.name} bir PTZ: plan onu sabit kamera gibi (tek odak, tek yön) "
                "dizer. PTZ'yi 'Karma dizilim' ile sabit direklere ekleyin."]
    return []


def _levels_table() -> Dict[str, int]:
    from ..viewshed_3d import ZONE_DETECT, ZONE_IDENT, ZONE_MONITOR, ZONE_OBSERVE, ZONE_RECOG
    return {"monitor": ZONE_MONITOR, "detect": ZONE_DETECT, "observe": ZONE_OBSERVE, "recognize": ZONE_RECOG,
            "identify": ZONE_IDENT}


def perimeter_plan(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Poles along a fence / border / road line (planner + fence-line gap analysis)."""
    from ..perimeter_planner import generate_perimeter_plan

    entry, t = _terrain_of(params)
    cam = _camera_from(params.get("camera", {}))
    fence = _fence(params)
    kw = _plan_kwargs(params)
    if params.get("spacing_m") is not None:
        kw["spacing_m"] = _num(params, "spacing_m", lo=2, hi=5000)
    try:
        plan = generate_perimeter_plan(t, fence, cam, **kw)
    except ValueError as exc:
        raise RpcError(APP_ERROR, str(exc)) from None
    entry.set_plan((plan, cam, kw, fence))
    return {**_plan_json(plan), "warnings": _ptz_main_warning(cam)}


_LEVEL_LABEL = {"monitor": "İzleme", "detect": "Algılama", "observe": "Gözlem", "recognize": "Tanıma",
                "identify": "Teşhis"}
# A thermal is classed with Johnson: the same three zone codes mean other things, and there is no fourth.
_LEVEL_LABEL_THERMAL = {"detect": "Johnson algılama", "observe": "Johnson tanıma", "recognize": "Johnson teşhis"}
_MONITOR_UNSUPPORTED = ("İzleme (12,5 px/m) seviyesi bu işlem için henüz desteklenmiyor; Algılama ya da üst "
                        "bir seviye seçin.")
_THERMAL_NO_IDENT = ("Termal kamerada Teşhis (yüz, 250 px/m) seviyesi yoktur; Johnson algılama, tanıma ya da "
                     "teşhis seviyesini seçin.")


def _level_of(params: Dict[str, Any], cam, default: str = "detect", *, monitor_ok: bool = False) -> int:
    """DORI zone named by ``params["level"]``; a thermal camera has no fourth level and
    no monitoring level. ``monitor_ok``: the method carries the monitor floor through
    (the ones that do not reject "monitor" instead of answering at the Detection floor)."""
    from ..viewshed_3d import THERMAL_NO_MONITOR, ZONE_IDENT, ZONE_MONITOR, is_thermal_camera

    level = _levels_table().get(params.get("level", default))
    if level is None:
        raise RpcError(INVALID_PARAMS, "'level' monitor, detect, observe, recognize ya da identify olmalı.")
    if level == ZONE_IDENT and is_thermal_camera(cam):
        raise RpcError(APP_ERROR, _THERMAL_NO_IDENT)
    if level == ZONE_MONITOR:
        if is_thermal_camera(cam):
            raise RpcError(APP_ERROR, THERMAL_NO_MONITOR)
        if not monitor_ok:
            raise RpcError(APP_ERROR, _MONITOR_UNSUPPORTED)
    return level


def _target_level(params: Dict[str, Any], plan, kw: Dict[str, Any], cam=None, ptz_cam=None) -> Tuple[str, float, int]:
    """(label, px/m, DORI zone a PTZ must reach) for the coverage target.

    ``level="plan"`` (default): an area plan's own DORI level, a fence plan's
    target px/m. A named level means the main camera's own scale -- EN 62676-4
    for a visible camera, Johnson for a thermal. A PTZ patrol is judged by DORI
    zone only (no px/m grid), at the lowest level OF ITS OWN SCALE that is at
    least the target -- never in its favour."""
    from ..viewshed_3d import THERMAL_NO_MONITOR, ZONE_DETECT, ZONE_IDENT, ZONE_MONITOR, is_thermal_camera, zone_ppm

    thermal = cam is not None and is_thermal_camera(cam)
    ptz_thermal = is_thermal_camera(ptz_cam) if ptz_cam is not None else thermal
    labels = _LEVEL_LABEL_THERMAL if thermal else _LEVEL_LABEL
    levels = _levels_table()
    zones = sorted(z for z in levels.values() if z != ZONE_MONITOR)    # a PTZ patrol has no monitor floor

    def ptz_zone(ppm: float) -> int:
        return max(next((z for z in zones if zone_ppm(z, ptz_thermal) >= ppm - 1e-9), ZONE_IDENT), ZONE_DETECT)

    want = params.get("level", "plan")
    if want == "plan":
        if kw.get("line_mode") == "area" and kw.get("level") in zones:
            z = kw["level"]
            name = next(k for k, v in levels.items() if v == z)
            if name in labels:
                ppm = zone_ppm(z, thermal)
                return labels[name], ppm, ptz_zone(ppm)
        ppm = float(plan.target_ppm)
        return f"{ppm:g} px/m".replace(".", ","), ppm, ptz_zone(ppm)
    if want not in levels:
        raise RpcError(INVALID_PARAMS, "'level' plan, monitor, detect, observe, recognize ya da identify olmalı.")
    if want == "monitor" and thermal:
        raise RpcError(APP_ERROR, THERMAL_NO_MONITOR)
    if want not in labels:
        raise RpcError(APP_ERROR, _THERMAL_NO_IDENT)
    ppm = zone_ppm(levels[want], thermal)
    return labels[want], ppm, ptz_zone(ppm)


def perimeter_coverage(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """The last plan through the authoritative multi-camera viewshed.

    Layers: ``target`` (inside what the plan protects: target held by fixed
    cameras / only by a PTZ patrol / seen below target / not seen), ``dori``
    and ``overlap`` (faded outside the protected area). Shares: DORI levels
    over the band (fixed = continuous; ``band_share_ptz`` = only a PTZ
    patrol reaches it), the target split, and the fence line itself."""
    from .. import map_render
    from ..focal_optimizer import band_area, fence_roi
    from ..viewshed_3d import PPM_DETECT, ZONE_DETECT, ZONE_IDENT, ZONE_MONITOR, ZONE_OBSERVE, ZONE_RECOG, \
        is_thermal_camera

    entry, t = _terrain_of(params)
    if "plan" not in entry.results:
        raise RpcError(APP_ERROR, "Önce bir çit planı oluşturun.")
    plan, cam, kw, fence = entry.results["plan"]
    if not plan.placed_cameras:
        raise RpcError(APP_ERROR, "Planda kamera yok.")
    is_area = kw.get("line_mode") == "area"
    band, inside = (0.0, None) if is_area else _band_params(params)
    roi = _plan_roi(t, fence, kw, band, inside)
    ptz_cam = kw.get("ptz_camera")
    n_ptz = len(plan.ptz_cameras) if ptz_cam is not None else 0
    # The requirement this report judges (named level, or the plan's own target). Under
    # Detection (25 px/m) the report needs the Monitoring floor, else those cells are never
    # in the engine's grids at all; a thermal camera has no such level (its target is Johnson).
    label, ppm, zone = _target_level(params, plan, kw, cam, ptz_cam)
    monitor_run = not is_thermal_camera(cam) and ppm < PPM_DETECT - 1e-9
    ctx.progress(0, 1 + n_ptz, f"{len(plan.placed_cameras)} kamera hesaplanıyor")
    # Each pole is evaluated out to its Detection reach: the plan caps it at
    # the fence target (e.g. 40 px/m), which would cut the lower DORI bands.
    mv, _ = band_area(t, plan, cam, roi, ZONE_MONITOR if monitor_run else ZONE_DETECT,
                      visibility_km=kw["visibility_km"], weather=kw["weather"])
    entry.results["multi"] = mv
    cell = t.cell_size_m ** 2
    roi_area = float(roi.sum() * cell)
    named = (("identify", ZONE_IDENT), ("recognize", ZONE_RECOG), ("observe", ZONE_OBSERVE), ("detect", ZONE_DETECT))
    band_share = {name: (float(((mv.dori_grid >= z) & roi).sum() * cell) / roi_area if roi_area else 0.0)
                  for name, z in named}
    if monitor_run:        # only from a monitor-floor run: a default run has no such cells to count
        band_share["monitor"] = (float(((mv.dori_grid >= ZONE_MONITOR) & roi).sum() * cell) / roi_area
                                 if roi_area else 0.0)
    entry.results["band_share"] = (band, band_share)

    # PTZ patrols: intermittent coverage on top of the fixed (continuous) one
    ptz_zone = np.zeros(mv.dori_grid.shape, np.int8)       # best DORI zone any PTZ reliably reaches
    worst_revisit = 0.0
    if n_ptz:
        from ..perimeter_ptz import ptz_sweeps
        ctx.progress(1, 1 + n_ptz, f"{n_ptz} PTZ devriyesi hesaplanıyor")
        for sw in ptz_sweeps(t, plan, ptz_cam, speed_deg_s=_num(params, "ptz_speed_deg_s", 20.0, lo=1, hi=360),
                             end_dwell_s=_num(params, "ptz_end_dwell_s", 2.0, lo=0, hi=600),
                             visibility_km=kw["visibility_km"], weather=kw["weather"]):
            seen = np.isfinite(sw.revisit_grid) & (sw.dori_grid >= ZONE_DETECT)
            ptz_zone = np.where(seen, np.maximum(ptz_zone, sw.dori_grid), ptz_zone).astype(np.int8)
            if (seen & roi).any():
                worst_revisit = max(worst_revisit, float(sw.revisit_grid[seen & roi].max()))
    band_share_ptz = {name: (float(((ptz_zone >= z) & ~(mv.dori_grid >= z) & roi).sum() * cell) / roi_area
                             if roi_area else 0.0) for name, z in named}

    # Opt-in third column: what a PTZ can be TURNED to (any pan / tilt / zoom) -- neither continuous
    # nor patrolled, but reachable when an operator or an alarm asks for it.
    band_share_reach = None
    if n_ptz and params.get("ptz_reach"):
        from ..focal_optimizer import envelope
        from ..perimeter_ptz import _ptz_range, _sweep_reach
        reach_zone = entry.results.get("ptz_reach")
        if reach_zone is None:
            reach_zone = np.zeros(mv.dori_grid.shape, np.int8)
            far = _sweep_reach(ptz_cam, kw["visibility_km"], kw["weather"])
            for i, p in enumerate(plan.ptz_cameras):
                ctx.progress(1 + i, 1 + n_ptz, f"PTZ erişimi {i + 1}/{n_ptz}")
                np.maximum(reach_zone, envelope(t, p.x_m, p.y_m, p.mast_height_m, ptz_cam, level=ZONE_RECOG,
                                                n_focals=6, max_range_m=_ptz_range(p, far, 3000.0),
                                                visibility_km=kw["visibility_km"], weather=kw["weather"]),
                           out=reach_zone)
            entry.results["ptz_reach"] = reach_zone
        band_share_reach = {name: (float(((reach_zone >= z) & ~(mv.dori_grid >= z) & ~(ptz_zone >= z) & roi).sum()
                                         * cell) / roi_area if roi_area else 0.0) for name, z in named}

    held = (mv.ppm_grid >= ppm - 1e-6) & mv.visibility_mask
    ptz_only = ~held & (ptz_zone >= zone)
    seen_any = mv.visibility_mask | (ptz_zone >= ZONE_DETECT)
    cls = np.zeros(roi.shape, np.int8)
    cls[roi & held] = 1
    cls[roi & ptz_only] = 2
    cls[roi & ~held & ~ptz_only & seen_any] = 3
    cls[roi & (cls == 0)] = 4
    n_held = np.zeros(roi.shape, np.int32)                  # physical fixed cameras holding the target
    for r in mv.per_camera:
        n_held += (r.ppm_grid >= ppm - 1e-6) & r.visibility_mask
    target = {"label": label, "ppm": ppm,
              **{k: (float((cls == c).sum() * cell) / roi_area if roi_area else 0.0)
                 for k, c in (("held", 1), ("ptz_only", 2), ("below", 3), ("unseen", 4))},
              "held_2": float(((n_held >= 2) & roi).sum() * cell) / roi_area if roi_area else 0.0}
    fence_line = None
    line = None
    if not is_area:
        line = fence_roi(t, fence, kw["is_closed_loop"], max(0.75 * t.cell_size_m, 1.0))
        n = int(line.sum())
        if n:
            share = float((held & line).sum()) / n
            fence_line = {"held": share,
                          "with_ptz": float(((held | ptz_only) & line).sum()) / n,
                          "detect": float(((mv.dori_grid >= ZONE_DETECT) & line).sum()) / n,
                          **({"monitor": float(((mv.dori_grid >= ZONE_MONITOR) & line).sum()) / n}
                             if monitor_run else {}),
                          # metres of fence below THIS target (the plan's own gap list is at the plan target)
                          "uncovered_m": (1.0 - share) * float(plan.total_fence_length_m)}
    # what this report scored, for the plan conformity statement (same masks, no second definition)
    entry.results["coverage_masks"] = {"roi": roi, "line": line, "ptz_zone": ptz_zone if n_ptz else None,
                                       "band": band, "inside": inside, "monitor": monitor_run}

    max_px = int(_num(params, "max_px", _DISPLAY_PX, lo=200, hi=4096))
    layer = params.get("layer", "dori")
    if layer not in ("target", "dori", "overlap"):
        raise RpcError(INVALID_PARAMS, "'layer' target, dori ya da overlap olmalı.")
    size = _overlay_size(t, max_px)
    if layer == "target":
        overlay = map_render.zone_overlay(cls, size, mode="target")
    else:
        grid = mv.dori_grid if layer == "dori" else mv.seen_count_grid
        overlay = map_render.dim_outside(map_render.zone_overlay(
            grid, size, mode="dori_monitor" if layer == "dori" and monitor_run else layer), roi)
    # the Monitoring row only where this layer can hold it (a monitor-floor run)
    legend = map_render.legend("dori_thermal" if layer == "dori" and is_thermal_camera(cam)
                               else "dori_monitor" if layer == "dori" and monitor_run else layer)[1]
    if layer == "target":
        legend = [(f"{txt} — {label}" if i == 0 else txt, rgba) for i, (txt, rgba) in enumerate(legend)]
        if not n_ptz:
            legend = [row for i, row in enumerate(legend) if i != 1]
    return {
        "camera_count": mv.camera_count, "fov_area_m2": mv.fov_area_m2, "visible_area_m2": mv.visible_area_m2,
        "occluded_area_m2": mv.occluded_area_m2, "coverage_pct": mv.coverage_pct,
        "overlap_area_m2": mv.overlap_area_m2, "single_cover_area_m2": mv.single_cover_area_m2,
        "zone_areas_m2": _zone_areas(mv.dori_grid, cell),
        "band_m": band, "band_in_m": inside, "band_area_m2": roi_area, "band_share": band_share,
        "band_share_ptz": band_share_ptz, "band_share_reach": band_share_reach, "ptz_count": n_ptz, "ptz_worst_revisit_s": worst_revisit,
        "target": target, "fence_line": fence_line,
        "roi_kind": "area" if is_area else "side" if kw.get("line_mode") == "border" else "band",
        "watch_side": kw.get("watch_side"),
        "overlay": _image(overlay, max_px=max_px), "legend": legend,
        "measured": bool(t.is_measured), "monitor_floor": monitor_run,
    }


def perimeter_minimize_poles(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Fewest poles that still hold the fence-band goal (spacing search + per-pole focal)."""
    from ..pole_optimizer import minimize_poles

    entry, t = _terrain_of(params)
    cam = _camera_from(params.get("camera", {}))
    fence = _fence(params)
    kw = _plan_kwargs(params)
    level = _level_of(params, cam, monitor_ok=True)
    goal = _num(params, "goal_frac", 0.95, lo=0.05, hi=1.0)
    band, inside = _band_params(params)
    try:
        res = minimize_poles(t, fence, cam, plan_kwargs=kw, goal_frac=goal, level=level,
                             band_m=band, inside_m=inside,
                             position_steps=int(_num(params, "position_steps", 0, lo=0, hi=4)),
                             progress=lambda d, n: ctx.progress(d, n, f"Deneme {d}/{n}"))
    except ValueError as exc:
        raise RpcError(APP_ERROR, str(exc)) from None
    plan = None
    if res.met and res.best_plan is not None:
        # The search scored per-pole focal / tilt choices: apply them, so the
        # plan shown is the one that met the goal (fence gaps re-analysed).
        plan = res.best_plan
        if res.best_layout is not None:
            from ..focal_optimizer import apply_layout
            plan = apply_layout(plan, res.best_layout, cam, kw["target_ppm"], terrain=t,
                                visibility_km=kw["visibility_km"], weather=kw["weather"])
        entry.set_plan((plan, cam, kw, fence))
    return {
        "met": res.met, "goal_frac": res.goal_frac, "roi_area_m2": res.roi_area_m2, "notes": res.notes,
        "base": dataclasses.asdict(res.base),
        "best": dataclasses.asdict(res.best) if res.best else None,
        "trials": [dataclasses.asdict(tr) for tr in res.trials],
        "plan": _plan_json(plan) if plan is not None else None,
    }


def perimeter_plan_assisted(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Fewest poles + PTZs for a PTZ-assisted target: fixed cameras detect continuously and a PTZ
    holds ``level`` within ``max_s`` of the alarm. The session plan changes only when it is met."""
    from ..pole_optimizer import minimize_poles_assisted

    entry, t = _terrain_of(params)
    cam = _camera_from(params.get("camera", {}))
    if not params.get("ptz_camera"):
        raise RpcError(INVALID_PARAMS, "'ptz_camera' (PTZ modeli) gerekli.")
    ptz_cam = _camera_from(params["ptz_camera"])
    fence = _fence(params)
    kw = _plan_kwargs(params)
    level = _level_of(params, ptz_cam, "recognize")
    goal = _num(params, "goal_frac", 0.95, lo=0.05, hi=1.0)
    max_s = _num(params, "max_s", 10.0, lo=1.0, hi=600.0)
    band, inside = _band_params(params)
    try:
        res = minimize_poles_assisted(
            t, fence, cam, ptz_cam, level=level, goal_frac=goal, max_s=max_s, plan_kwargs=kw,
            band_m=band, inside_m=inside, position_steps=int(_num(params, "position_steps", 0, lo=0, hi=4)),
            speed_deg_s=_num(params, "speed_deg_s", 20.0, lo=0.5, hi=400),
            end_dwell_s=_num(params, "end_dwell_s", 2.0, lo=0, hi=600),
            progress=lambda d, n: ctx.progress(d, n, f"PTZ yoğunluğu {d}/{n}"))
    except ValueError as exc:
        raise RpcError(APP_ERROR, str(exc)) from None
    plan = None
    if res.met and res.plan is not None:
        # the session keeps the plan the mixed evaluation / statement will judge: PTZ model + the chosen density
        kw_plan = dict(kw, ptz_camera=ptz_cam, ptz_every_n=res.ptz_every_n,
                       ptz_patrol_span_deg=kw.get("ptz_patrol_span_deg", 360.0))
        plan = res.plan
        entry.set_plan((plan, cam, kw_plan, fence))
    s = res.pole_search
    names = {v: k for k, v in _levels_table().items()}
    return {
        "met": res.met, "level": names[res.level], "goal_frac": res.goal_frac, "max_s": res.max_s,
        "target_ppm": res.target_ppm, "roi_area_m2": res.roi_area_m2, "notes": res.notes, "capped": res.capped,
        "pole_count": res.pole_count, "fixed_cameras": res.fixed_cameras, "ptz_count": res.ptz_count,
        "ptz_every_n": res.ptz_every_n, "detect_share": res.detect_share, "assisted_share": res.assisted_share,
        "trials": [dataclasses.asdict(x) for x in res.trials],
        "fixed": {"met": s.met, "base": dataclasses.asdict(s.base), "best": dataclasses.asdict(s.best) if s.best else None,
                  "trials": [dataclasses.asdict(x) for x in s.trials]} if s is not None else None,
        "plan": _plan_json(plan) if plan is not None else None,
    }


def perimeter_optimize(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Optimise the last plan's poles in place: focal / tilt (/ heading) and optionally
    their place along the fence; both plans re-scored by the authoritative engine."""
    from ..focal_optimizer import (apply_layout, apply_layout_keeping_fence, band_area, mounts_from_plan,
                                   optimize_layout)
    from ..perimeter_planner import uncovered_fence_m

    entry, t = _terrain_of(params)
    if "plan" not in entry.results:
        raise RpcError(APP_ERROR, "Önce bir çit planı oluşturun.")
    plan, cam, kw, fence = entry.results["plan"]
    level = _level_of(params, cam, monitor_ok=True)
    is_area = kw.get("line_mode") == "area"
    # A facility / highway pole watches the fence LINE; the band score alone may re-aim it off the
    # line. A border pole looks across the line by design and an area plan has no line to keep.
    guard = not is_area and kw.get("line_mode") != "border"
    # an area plan's poles stand inside the polygon too: sliding "along the fence" would drag them to its edge
    steps = 0 if is_area else int(_num(params, "position_steps", 0, lo=0, hi=4))
    step_m = _num(params, "position_step_m", max(plan.avg_spacing_m / (2 * steps + 2), 1.0) if steps else 0.0,
                  lo=0, hi=500)
    band, inside = _band_params(params)
    roi = _plan_roi(t, fence, kw, band, inside)
    mounts = mounts_from_plan(plan, cam, pan_search_deg=_num(params, "pan_search_deg", 0.0, lo=0, hi=90),
                              position_step_m=step_m, position_steps=steps)
    res = optimize_layout(t, mounts, roi=roi, level=level, n_focals=int(_num(params, "n_focals", 6, lo=2, hi=16)),
                          visibility_km=kw["visibility_km"], weather=kw["weather"],
                          progress=lambda d, n: ctx.progress(d, n + 2, f"Direk {d}/{n}"))
    notes = list(res.notes)
    reverted = 0
    if guard:
        new_plan, res, reverted = apply_layout_keeping_fence(
            plan, res, cam, kw["target_ppm"], terrain=t, visibility_km=kw["visibility_km"], weather=kw["weather"])
        if reverted:
            notes.append(f"{reverted} direk eski ayarında bırakıldı: yeni ayarı çit çizgisinde boşluk açıyordu.")
    else:
        new_plan = apply_layout(plan, res, cam, kw["target_ppm"], terrain=t,
                                visibility_km=kw["visibility_km"], weather=kw["weather"])
    ctx.progress(1, 1, "Otoriter motorla doğrulanıyor")
    _, a0 = band_area(t, plan, cam, roi, level, visibility_km=kw["visibility_km"], weather=kw["weather"])
    _, a1 = band_area(t, new_plan, cam, roi, level, visibility_km=kw["visibility_km"], weather=kw["weather"])
    if is_area:          # apply_layout re-ran the FENCE gap analysis; an area plan reports its own share
        new_plan = dataclasses.replace(new_plan, gaps=[], coverage_percentage=round(
            100.0 * a1 / max(float(roi.sum()) * t.cell_size_m ** 2, 1e-9), 1))
    # The estimate chose the layout; the engine has the last word. Worse by more than half a cell
    # (rounding of the applied settings) means the plan stays as it was.
    better = a1 >= a0 - 0.5 * t.cell_size_m ** 2
    if not better:
        notes.append(f"Otoriter motor yeni dizilimi daha iyi bulmadı ({_ha(a0)} → {_ha(a1)}); plan değiştirilmedi.")
    applied = bool(params.get("apply", True)) and better
    if applied:
        entry.set_plan((new_plan, cam, kw, fence))
    fence_line = None
    if guard:
        fence_line = {"before_pct": plan.coverage_percentage, "after_pct": new_plan.coverage_percentage,
                      "before_uncovered_m": uncovered_fence_m(plan), "after_uncovered_m": uncovered_fence_m(new_plan),
                      "before_gaps": len(plan.gaps), "after_gaps": len(new_plan.gaps)}
    moved = [c for c in res.choices if c.moved_m > 0.05]
    return {"before_m2": a0, "after_m2": a1, "band_area_m2": float(roi.sum()) * t.cell_size_m ** 2,
            "estimate_before_m2": res.before_m2[level], "estimate_after_m2": res.after_m2[level],
            "changed": sum(1 for c in res.choices if c.changed), "moved": len(moved),
            "max_move_m": max((c.moved_m for c in moved), default=0.0), "notes": notes,
            "applied": applied, "reverted": reverted, "fence_line": fence_line,
            "plan": _plan_json(new_plan if better else plan)}


def perimeter_ptz_patrol(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Search each planned PTZ's patrol focal / tilt (instead of the planner's fixed wide end)
    for the area the patrols add at ``level`` beyond what the fixed cameras hold; both sets
    of patrols are scored by the sweep engine and the plan changes only if it gained."""
    from ..focal_optimizer import band_area
    from ..perimeter_ptz import optimize_patrols

    entry, t = _terrain_of(params)
    if "plan" not in entry.results:
        raise RpcError(APP_ERROR, "Önce PTZ'li bir plan oluşturun.")
    plan, cam, kw, fence = entry.results["plan"]
    ptz_cam = kw.get("ptz_camera")
    if ptz_cam is None or not plan.ptz_cameras:
        raise RpcError(APP_ERROR, "Planda PTZ yok: PTZ modeli ve 'her N direkte bir' seçip planı yeniden kurun.")
    level = _level_of(params, ptz_cam)
    is_area = kw.get("line_mode") == "area"
    band, inside = (0.0, None) if is_area else _band_params(params)
    roi = _plan_roi(t, fence, kw, band, inside)
    exclude = np.zeros(roi.shape, bool)
    if plan.placed_cameras:
        ctx.progress(0, len(plan.ptz_cameras) + 1, "Sabit kameraların tuttuğu alan")
        mv, _ = band_area(t, plan, cam, roi, level, visibility_km=kw["visibility_km"], weather=kw["weather"])
        if mv is not None:
            exclude = mv.dori_grid >= level
    n = len(plan.ptz_cameras)
    res = optimize_patrols(t, plan, ptz_cam, roi=roi, exclude=exclude, level=level,
                           speed_deg_s=_num(params, "speed_deg_s", 20.0, lo=0.5, hi=400),
                           end_dwell_s=_num(params, "end_dwell_s", 2.0, lo=0, hi=600),
                           n_focals=int(_num(params, "n_focals", 6, lo=2, hi=16)),
                           visibility_km=kw["visibility_km"], weather=kw["weather"],
                           progress=lambda d, total: ctx.progress(d, total + 1, f"PTZ {d}/{total}"))
    applied = bool(params.get("apply", True)) and res.applied
    if applied:
        entry.set_plan((res.plan, cam, kw, fence))
    notes = []
    if not res.applied:
        notes.append("Tarama motoru yeni devriyeleri daha iyi bulmadı; plan değiştirilmedi.")
    elif res.worst_revisit_after_s > res.worst_revisit_before_s + 0.5:
        notes.append(f"Dar kadraj daha seyrek geçer: en uzun izlenmeyen süre {res.worst_revisit_before_s:.0f} → "
                     f"{res.worst_revisit_after_s:.0f} sn.")
    return {"before_m2": res.before_m2, "after_m2": res.after_m2, "band_area_m2": res.roi_area_m2,
            "worst_revisit_before_s": res.worst_revisit_before_s, "worst_revisit_after_s": res.worst_revisit_after_s,
            "changed": sum(1 for c in res.choices if c.changed), "ptz_count": n, "applied": applied, "notes": notes,
            "choices": [{"pole_id": c.pole_id, "focal_mm": c.focal_mm, "tilt_deg": c.tilt_deg, "hfov_deg": c.hfov_deg,
                         "changed": c.changed} for c in res.choices],
            "plan": _plan_json(res.plan if applied else plan)}


def area_plan(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Area goal: fewest cameras / poles holding a DORI level over a polygon (verified by the engine)."""
    from ..area_planner import plan_area
    from ..atmosphere import WEATHER_PRESETS

    entry, t = _terrain_of(params)
    cam = _camera_from(params.get("camera", {}))
    poly = _fence(params)
    if len(poly) < 3:
        raise RpcError(INVALID_PARAMS, "Alan için en az üç köşe gerekir.")
    levels = _levels_table()
    level = _level_of(params, cam, "recognize")
    weather = str(params.get("weather", "")) if params.get("weather") in WEATHER_PRESETS else ""
    vis = WEATHER_PRESETS.get(weather, 40.0)
    mast = _num(params, "mast_m", 6.0, lo=0.5, hi=200)
    roi_mask, sites_fn = None, None
    if params.get("streets_only"):
        if entry.city is None:
            raise RpcError(APP_ERROR, "Önce sokakları indirin (Şehir → OpenStreetMap).")
        from ..city import street_sites, street_target
        roi_mask = street_target(entry.city, t)
        city = entry.city

        def sites_fn(step):
            return street_sites(city, poly, step)
    ptz_cam = _camera_from(params["ptz_camera"]) if params.get("ptz_camera") else None
    max_hole = params.get("max_hole_m2")
    try:
        res = plan_area(t, poly, cam, level=level, goal_frac=_num(params, "goal_frac", 0.95, lo=0.05, hi=1.0),
                        roi_mask=roi_mask, sites_fn=sites_fn,
                        max_hole_m2=None if max_hole is None else _num(params, "max_hole_m2", lo=1, hi=1e6),
                        min_cameras=int(_num(params, "min_cameras", 1, lo=1, hi=4)),
                        ptz_camera=ptz_cam, ptz_every_n=int(_num(params, "ptz_every_n", 4, lo=1, hi=100)) if ptz_cam else 0,
                        ptz_span_deg=_num(params, "ptz_patrol_span_deg", 360.0, lo=10, hi=360),
                        mast_height_m=mast, interior=bool(params.get("interior", True)),
                        max_per_pole=int(_num(params, "max_per_pole", 2, lo=1, hi=6)),
                        max_cameras=int(_num(params, "max_cameras", 120, lo=1, hi=500)),
                        visibility_km=vis, weather=weather,
                        progress=lambda d, n: ctx.progress(d, n, "Aday direkler" if d <= n - 3 else "Motorla doğrulanıyor"))
    except ValueError as exc:
        raise RpcError(APP_ERROR, str(exc)) from None
    kw = {"target_ppm": res.plan.target_ppm, "mast_height_m": mast, "is_closed_loop": True,
          "visibility_km": vis, "weather": weather, "line_mode": "area", "watch_side": "right", "level": level,
          "roi_mask": roi_mask}
    if ptz_cam is not None:
        kw.update(ptz_camera=ptz_cam, ptz_every_n=int(_num(params, "ptz_every_n", 4, lo=1, hi=100)),
                  ptz_patrol_span_deg=_num(params, "ptz_patrol_span_deg", 360.0, lo=10, hi=360))
    entry.set_plan((res.plan, cam, kw, poly))
    names = {v: k for k, v in levels.items()}
    return {"met": res.met, "level": names[res.level], "goal_frac": res.goal_frac, "area_m2": res.area_m2,
            "engine_frac": res.engine_frac, "estimate_frac": res.estimate_frac,
            "sites_considered": res.sites_considered, "sites_skipped_obstacle": res.sites_skipped_obstacle,
            "curve": [dataclasses.asdict(st) for st in res.steps], "notes": res.notes,
            "holes_count": res.holes_count, "largest_hole_m2": res.largest_hole_m2,
            "uncovered_m2": res.uncovered_m2, "hole_cameras": res.hole_cameras,
            "warnings": _ptz_main_warning(cam), "plan": _plan_json(res.plan)}


def city_osm(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Streets, residential buildings and priority places from OpenStreetMap for this terrain."""
    from .. import map_render
    from ..city import CLASS_LABEL, fetch_city

    entry = _s.SESSION.get(params.get("terrain_id"))
    _activate(entry)
    t = entry.bare
    if t.lat_center is None:
        raise RpcError(APP_ERROR, "Sokak verisi yalnız konumu bilinen bir arazide (indirilen / kayıtlı bölge) alınabilir.")
    ctx.progress(0, 2, "OpenStreetMap sorgulanıyor")
    try:
        layer = fetch_city(t)
    except (RuntimeError, ValueError) as exc:
        raise RpcError(APP_ERROR, f"Sokak / bina verisi indirilemedi: {exc}") from None
    ctx.progress(1, 2, "Izgaraya işleniyor")
    entry.city = layer
    cell = t.cell_size_m ** 2
    max_px = int(_num(params, "max_px", _DISPLAY_PX, lo=200, hi=4096))
    return {
        "streets": layer.counts.get("streets", 0), "residential": layer.counts.get("residential", 0),
        "pois": [{"name": p.name, "kind": p.kind, "x_m": p.x_m, "y_m": p.y_m} for p in layer.pois],
        "street_area_m2": {CLASS_LABEL[c]: float((layer.street_class == c).sum() * cell) for c in CLASS_LABEL},
        "residential_area_m2": float(layer.residential.sum() * cell), "attribution": layer.attribution,
        "overlay": _image(map_render.zone_overlay(layer.street_class, _overlay_size(t, max_px), mode="streets"),
                          max_px=max_px),
        "legend": map_render.legend("streets")[1],
    }


def city_analyse(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """The plan in a city: street coverage by class, face-capable streets, priority places, privacy per camera."""
    from .. import map_render
    from ..city import CLASS_LABEL, analyse_city
    from ..focal_optimizer import band_area
    from ..viewshed_3d import ZONE_DETECT, ZONE_MONITOR

    entry, t = _terrain_of(params)
    if entry.city is None:
        raise RpcError(APP_ERROR, "Önce sokakları indirin (Şehir → OpenStreetMap).")
    if "plan" not in entry.results:
        raise RpcError(APP_ERROR, "Önce bir çit / alan planı oluşturun.")
    plan, cam, kw, fence = entry.results["plan"]
    if not plan.placed_cameras:
        raise RpcError(APP_ERROR, "Planda sabit kamera yok.")
    levels = _levels_table()
    level = levels.get(params.get("level", "observe"))
    if level is None:
        raise RpcError(INVALID_PARAMS, "'level' monitor, detect, observe, recognize ya da identify olmalı.")
    if level == ZONE_MONITOR:
        raise RpcError(APP_ERROR, _MONITOR_UNSUPPORTED)
    ctx.progress(0, 2, f"{len(plan.placed_cameras)} kamera (otoriter motor)")
    mv, _ = band_area(t, plan, cam, None, ZONE_DETECT, visibility_km=kw["visibility_km"], weather=kw["weather"])
    ctx.progress(1, 2, "Sokak, yüz ve gizlilik")
    roi = None
    if params.get("inside_plan", True) and kw.get("line_mode") == "area":
        from ..area_planner import polygon_mask
        roi = polygon_mask(t, fence)
    labels = [f"Direk {c.pole_id} · {c.pan_deg:.0f}°" for c in plan.placed_cameras]
    res = analyse_city(t, entry.city, mv, level=level, roi=roi, labels=labels,
                       flag_share=_num(params, "flag_share", 0.10, lo=0.0, hi=1.0))
    # for the site export (KML / DXF): each camera's privacy-mask polygons, dropped with the plan
    from ..city import privacy_masks
    entry.results["privacy"] = privacy_masks(t, res)
    if t.obstacle_grid is None:
        res.notes.insert(0, "Bina / ağaç yüzey modeli yüklü değil: kameralar binaların içinden görüyor sayılır, "
                            "sokak kapsaması olduğundan yüksek çıkar. Önce 'Bina & Ağaç İndir (OSM)'.")
    names = {v: k for k, v in levels.items()}
    max_px = int(_num(params, "max_px", _DISPLAY_PX, lo=200, hi=4096))
    return {
        "level": names[level], "street_area_m2": res.street_area_m2,
        "share_by_level": {names[k]: v for k, v in res.share_by_level.items()},
        "by_class": [{"label": CLASS_LABEL[c], "area_m2": a, "share": s} for c, (a, s) in res.share_by_class.items()],
        "face_share": res.face_share,
        "pois": [{"name": p.name, "kind": p.kind, "x_m": p.x_m, "y_m": p.y_m,
                  "best": names.get(p.best_zone), "face": p.face} for p in res.pois],
        "privacy": [{"label": c.label, "seen_m2": c.seen_m2, "private_m2": c.private_m2, "share": c.share,
                     "flagged": c.flagged} for c in res.privacy],
        "private_seen_m2": res.private_seen_m2, "notes": res.notes,
        "overlay": _image(map_render.zone_overlay(res.layer, _overlay_size(t, max_px), mode="city"), max_px=max_px),
        "legend": map_render.legend("city")[1], "measured": bool(t.is_measured),
    }


def _ha(m2: float) -> str:
    return f"{m2 / 1e4:.2f}".replace(".", ",") + " ha"


def focal_sweep(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Varifocal: DORI area at every focal step for one mount (best tilt / pan per focal).

    The curve is the mount-raster estimate. The optimum is then put through the
    AUTHORITATIVE engine, next to the current setting when the caller sends it:
    "apply" must not swap a view that sees ground for one that sees none because
    the estimate found a cell or two at a level the mast cannot really reach."""
    from ..atmosphere import WEATHER_PRESETS
    from ..focal_optimizer import sweep_focals
    from ..viewshed_3d import (ZONE_DETECT, ZONE_IDENT, ZONE_MONITOR, ZONE_OBSERVE, ZONE_RECOG,
                               calculate_3d_viewshed, is_thermal_camera)

    entry, t = _terrain_of(params)
    cam = _camera_from(params.get("camera", {}))
    level = _level_of(params, cam, "recognize", monitor_ok=True)
    weather = str(params.get("weather", "")) if params.get("weather") in WEATHER_PRESETS else ""
    vis = WEATHER_PRESETS.get(weather, 40.0)
    x = _num(params, "x_m"); y = _num(params, "y_m")
    mast = _num(params, "mast_m", 6.0, lo=0.5, hi=200)
    pan = _num(params, "pan_deg", 0.0) % 360.0
    rng = None if params.get("range_m") is None else _num(params, "range_m", lo=20, hi=20000)
    rep = sweep_focals(t, x, y, mast, cam, pan_deg=pan,
                       pan_search_deg=_num(params, "pan_search_deg", 0.0, lo=0, hi=180),
                       level=level, objective=params.get("objective", "area"),
                       n_focals=int(_num(params, "n_focals", 12, lo=2, hi=40)),
                       max_range_m=rng, visibility_km=vis, weather=weather)
    names = {ZONE_IDENT: "identify", ZONE_RECOG: "recognize", ZONE_OBSERVE: "observe", ZONE_DETECT: "detect",
             ZONE_MONITOR: "monitor"}
    cell_area = t.cell_size_m ** 2

    def choice(c):
        return {"focal_mm": c.focal_mm, "tilt_deg": c.tilt_deg, "pan_deg": c.pan_deg, "hfov_deg": c.hfov_deg,
                "score": c.score, "areas_m2": {names[k]: v for k, v in c.areas_m2.items() if k in names}}

    def engine(focal: float, tilt: float, pan_deg: float) -> Dict[str, Any]:
        res = calculate_3d_viewshed(t, x, y, mast, cam, pan_deg=pan_deg, tilt_deg=tilt, max_range_m=rep.reach_m,
                                    visibility_km=vis, weather=weather, focal_mm_override=focal,
                                    with_profile=False, monitor_floor=level == ZONE_MONITOR)
        return {"focal_mm": focal, "tilt_deg": tilt, "pan_deg": pan_deg,
                "area_m2": float((res.dori_grid >= level).sum()) * cell_area,
                "visible_area_m2": float(res.visible_area_m2), "max_los_reach_m": float(res.max_los_reach_m)}

    best = engine(rep.best.focal_mm, rep.best.tilt_deg, pan if rep.best.pan_deg is None else rep.best.pan_deg)
    current = None
    if params.get("current_tilt_deg") is not None and params.get("current_focal_mm") is not None:
        current = engine(_num(params, "current_focal_mm", lo=cam.focal_min_mm, hi=cam.focal_max_mm),
                         _num(params, "current_tilt_deg", lo=-89, hi=30), pan)
    label = (_LEVEL_LABEL_THERMAL if is_thermal_camera(cam) else _LEVEL_LABEL)[names[level]]
    gain = None if current is None else best["area_m2"] - current["area_m2"]
    marginal = gain is not None and 0 < gain < 4 * cell_area
    worth, note = True, ""
    if best["area_m2"] <= 0:
        worth = False
        note = (f"Bu direkten {label} seviyesi hiçbir odakta zemine ulaşmıyor (motor: 0 m²). Uygulanacak bir "
                f"optimum yok; direği yükseltin, hedefi yaklaştırın ya da daha düşük bir seviye seçin.")
    elif gain is not None and gain <= 0:
        worth = False
        note = (f"Motor bu ayarı şimdikinden iyi bulmuyor ({label}: {_ha(current['area_m2'])} → "
                f"{_ha(best['area_m2'])}); mevcut ayar korunur.")
    elif marginal and best["visible_area_m2"] < 0.5 * current["visible_area_m2"]:
        worth = False
        note = (f"Kazanç ızgara çözünürlüğünde ({gain:.0f} m²), buna karşılık görünen alan "
                f"{_ha(current['visible_area_m2'])} → {_ha(best['visible_area_m2'])} düşüyor; ayar uygulanmaz.")
    elif marginal:
        note = f"Kazanç ızgara çözünürlüğünde ({gain:.0f} m²); fark ölçüm hassasiyetinin altında olabilir."
    return {"curve": [choice(c) for c in rep.curve], "best": choice(rep.best), "wide": choice(rep.wide),
            "tele": choice(rep.tele), "level": names[rep.level], "reach_m": rep.reach_m,
            "verified": best, "current": current, "gain_m2": gain, "marginal": bool(marginal),
            "worth_applying": worth, "note": note}


METHODS = {
    "terrain.settings": terrain_settings,
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
    "perimeter.plan_assisted": perimeter_plan_assisted,
    "perimeter.optimize": perimeter_optimize,
    "area.plan": area_plan,
    "city.osm": city_osm,
    "city.analyse": city_analyse,
    "focal.sweep": focal_sweep,
    "perimeter.ptz_patrol": perimeter_ptz_patrol,
}
