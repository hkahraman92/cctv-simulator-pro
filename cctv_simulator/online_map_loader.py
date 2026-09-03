"""
Online satellite imagery and real DEM elevation fetcher.

Two independent tile pipelines share one downloader:

* **Orthophoto** - Esri World Imagery / OpenStreetMap / OpenTopoMap raster tiles,
  stitched into a mosaic and cropped to the requested metric bounding box.
* **Elevation** - Terrarium RGB-encoded terrain tiles (AWS Open Data, ~30 m SRTM
  / 3DEP source), decoded to real metres above sea level.

Design rules that must not be broken:

1. **No invented topography behind a real-looking name.** A viewshed is nothing
   but terrain occlusion, so fabricated relief produces a confident, wrong
   coverage report. When real elevation cannot be fetched, the returned
   ``TerrainData`` is flagged ``is_measured=False`` and named so the UI can say
   so out loud.
2. **A failed download must fail loudly.** Silently dropped tiles used to leave a
   flat grey mosaic that the UI reported as a successful download.
3. **Be a good tile citizen.** Honest User-Agent, per-server concurrency limits
   (OSM policy allows 2), an on-disk cache, and a hard tile budget.
"""
from __future__ import annotations

import concurrent.futures
import io
import json
import math
import re
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

import numpy as np
from PIL import Image

from .terrain_loader import TerrainData

# A real, identifying User-Agent. Spoofing a browser string is explicitly against
# the OpenStreetMap tile usage policy and is what gets an IP range banned.
USER_AGENT = "CCTV-Simulator/2.0 (kurumsal CCTV tasarim araci; harun_738@hotmail.com)"

# Slippy-map tiles are 256 px square on every server used here.
TILE_PX = 256

# Upper bound on a single mosaic. 400 tiles = 26 MB of PNG, ~10 s on a warm
# cache. Beyond this the user wants a GIS package, not this dialog.
MAX_TILES = 400


# Presets for Critical Facilities & Border Areas in Turkey
PRESET_LOCATIONS: Dict[str, Tuple[float, float, str]] = {
    "Hakkari - Çukurca Sınır Hattı": (37.2483, 43.6150, "Dağlık Sınır Bölgesi"),
    "Hakkari - Şemdinli / Derecik": (37.2280, 44.3410, "Sarp Arazi"),
    "Şırnak - Uludere Sınır Bölgesi": (37.3820, 43.0850, "Karakol ve Vadi"),
    "Hatay - Yayladağı Sınır Hattı": (35.9030, 36.0600, "Akdeniz Sınır Kuşağı"),
    "Van - Başkale Sınır Hattı": (38.0450, 44.0150, "Yüksek Plato & Dağlık"),
    "Ankara - TUSAŞ / Savunma Sanayii": (40.0985, 32.5855, "Sanayi ve Havacılık Yerleşkesi"),
    "İstanbul - Havalimanı Çevre Güvenliği": (41.2750, 28.7519, "Havalimanı Perimetresi"),
    "İzmir - Aliağa Rafineri / Liman": (38.8020, 26.9650, "Kritik Enerji & Liman Tesisi"),
}

TILE_SERVERS: Dict[str, Dict] = {
    "esri": {
        "name": "🛰️ Esri World Imagery (Yüksek Çözünürlüklü Gerçek Uydu)",
        # NOTE the axis order: this endpoint is /tile/{z}/{y}/{x}, not {z}/{x}/{y}.
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "ext": "jpg",
        "max_workers": 6,
        "max_zoom": 19,
        "attribution": "Kaynak: Esri, Maxar, Earthstar Geographics, GIS User Community",
    },
    "osm": {
        "name": "🗺️ OpenStreetMap (Cadde, Bina ve Tesis Planı)",
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "ext": "png",
        # OSM tile usage policy: at most 2 concurrent connections.
        "max_workers": 2,
        "max_zoom": 19,
        "attribution": "© OpenStreetMap katkıda bulunanları",
    },
    "opentopo": {
        "name": "⛰️ OpenTopoMap (Topoğrafik Kabartma ve Eşyükselti)",
        "url": "https://tile.opentopomap.org/{z}/{x}/{y}.png",
        "ext": "png",
        "max_workers": 2,
        "max_zoom": 17,
        "attribution": "© OpenTopoMap (CC-BY-SA), © OpenStreetMap katkıda bulunanları",
    },
    # Not offered as a basemap; used by the elevation pipeline.
    "terrarium": {
        "name": "Terrarium DEM (AWS Open Data)",
        "url": "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png",
        "ext": "png",
        "max_workers": 6,
        "max_zoom": 15,
        "attribution": "Yükselti: Terrain Tiles (AWS Open Data) - SRTM / 3DEP / GMTED",
    },
}

# Sources a user may pick as a basemap in the UI.
BASEMAP_SOURCES = ("esri", "osm", "opentopo")


# ──────────────────────────────────────────────────────────────────────────────
# Web Mercator
# ──────────────────────────────────────────────────────────────────────────────
def mercator_x(lon: float) -> float:
    """Longitude -> normalised Mercator X in [0, 1]."""
    return (lon + 180.0) / 360.0


def mercator_y(lat: float) -> float:
    """Latitude -> normalised Mercator Y in [0, 1]. Not linear in latitude."""
    lat = max(-85.05112878, min(85.05112878, lat))
    return (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0


def lat_lon_to_tile(lat: float, lon: float, zoom: int) -> Tuple[int, int]:
    """Converts latitude and longitude to Slippy Map tile coordinates (x, y)."""
    n = 2 ** zoom
    return int(mercator_x(lon) * n), int(mercator_y(lat) * n)


def tile_to_lat_lon(x: int, y: int, zoom: int) -> Tuple[float, float]:
    """Converts Slippy Map tile coordinate (x, y) to North-West corner (lat, lon)."""
    n = 2 ** zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lat, lon


def calculate_bbox(center_lat: float, center_lon: float,
                   width_m: float, height_m: float) -> Tuple[float, float, float, float]:
    """Calculates (min_lat, min_lon, max_lat, max_lon) from center and metric dimensions."""
    meters_per_deg_lat = 111320.0
    meters_per_deg_lon = 111320.0 * math.cos(math.radians(center_lat))
    if meters_per_deg_lon <= 0:
        meters_per_deg_lon = meters_per_deg_lat

    delta_lat = (height_m / 2.0) / meters_per_deg_lat
    delta_lon = (width_m / 2.0) / meters_per_deg_lon
    return (center_lat - delta_lat, center_lon - delta_lon,
            center_lat + delta_lat, center_lon + delta_lon)


def zoom_resolution_m_px(zoom: int, lat: float) -> float:
    """Ground sample distance in metres per pixel at this zoom and latitude.

    Equatorial circumference / (2**zoom tiles * 256 px), narrowed by cos(lat).
    """
    return 40075016.686 * math.cos(math.radians(lat)) / float(2 ** zoom * TILE_PX)


def select_optimal_zoom(width_m: float, quality: str = "hd",
                        lat: float = 39.0, source: str = "esri") -> int:
    """Highest zoom whose tile count fits the budget, capped by the quality profile.

    The old table returned a fixed zoom per (quality, width) bucket, so "Ultra HD
    ~0.5 m/px" silently became 3.8 m/px at 10 km. This derives the zoom from the
    requested ground sample distance instead, then clamps it to what the server
    serves and to :data:`MAX_TILES`.
    """
    target_m_px = {"ultra_hd": 0.5, "hd": 1.0}.get(quality, 2.0)
    server_max = int(TILE_SERVERS.get(source, TILE_SERVERS["esri"]).get("max_zoom", 19))

    # Climb until the ground sample distance is at least as fine as requested.
    zoom = 10
    while zoom < server_max and zoom_resolution_m_px(zoom, lat) > target_m_px:
        zoom += 1

    # Then walk back down until the mosaic fits the tile budget.
    while zoom > 10 and estimate_tile_count(width_m, width_m, zoom, lat) > MAX_TILES:
        zoom -= 1
    return zoom


def estimate_tile_count(width_m: float, height_m: float, zoom: int, lat: float) -> int:
    """Tiles needed to cover this extent, including partial edge tiles."""
    res = zoom_resolution_m_px(zoom, lat)
    cols = int(width_m / max(res * TILE_PX, 1e-6)) + 2
    rows = int(height_m / max(res * TILE_PX, 1e-6)) + 2
    return cols * rows


def describe_quality(width_m: float, quality: str, lat: float, source: str = "esri") -> str:
    """One-line, honest summary for the download dialog."""
    zoom = select_optimal_zoom(width_m, quality, lat, source)
    res = zoom_resolution_m_px(zoom, lat)
    tiles = estimate_tile_count(width_m, width_m, zoom, lat)
    px = int(width_m / max(res, 1e-6))
    return f"Zoom {zoom} · ~{res:.2f} m/piksel · ~{px}×{px} px · ~{tiles} kare"


# ──────────────────────────────────────────────────────────────────────────────
# Tile cache + fetch
# ──────────────────────────────────────────────────────────────────────────────
def tile_cache_dir() -> Optional[Path]:
    """Per-user tile cache. Returns None if no writable location exists."""
    try:
        from .config import user_data_dir
        path = user_data_dir() / "tile-cache"
        path.mkdir(parents=True, exist_ok=True)
        return path
    except Exception:
        return None


def clear_tile_cache() -> int:
    """Deletes every cached tile. Returns the number of files removed."""
    base = tile_cache_dir()
    if base is None:
        return 0
    removed = 0
    for path in base.rglob("*"):
        if path.is_file():
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    # Drop the now-empty source/zoom/x directory tree, deepest first.
    for path in sorted(base.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    return removed


def tile_cache_usage() -> Tuple[int, int]:
    """Returns ``(file_count, total_bytes)`` for the on-disk tile cache."""
    base = tile_cache_dir()
    if base is None:
        return 0, 0
    files = 0
    total = 0
    for path in base.rglob("*"):
        if path.is_file():
            files += 1
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return files, total


def _cache_path(source: str, zoom: int, x: int, y: int, ext: str) -> Optional[Path]:
    base = tile_cache_dir()
    if base is None:
        return None
    safe = re.sub(r"[^a-z0-9_-]", "", source.lower()) or "tiles"
    return base / safe / str(zoom) / str(x) / f"{y}.{ext}"


def _fetch_tile(source: str, zoom: int, x: int, y: int) -> Optional[Tuple[int, int, Image.Image]]:
    """One tile, cache first. Returns None only when the tile is truly unavailable."""
    cfg = TILE_SERVERS[source]
    path = _cache_path(source, zoom, x, y, cfg["ext"])

    if path is not None and path.is_file():
        try:
            with Image.open(path) as cached:
                return x, y, cached.convert("RGB")
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass

    url = cfg["url"].format(z=zoom, x=x, y=y)
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "image/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None

    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError:
            pass
    return x, y, img


def _download_mosaic(source: str, zoom: int, bbox: Tuple[float, float, float, float],
                     progress_callback: Optional[Callable[[int, int], None]] = None,
                     min_success_ratio: float = 0.7) -> Tuple[Image.Image, Dict]:
    """Fetches every tile covering ``bbox`` and crops the mosaic exactly to it.

    Raises RuntimeError when too few tiles arrived, instead of returning a
    plausible-looking grey rectangle.
    """
    min_lat, min_lon, max_lat, max_lon = bbox
    cfg = TILE_SERVERS[source]

    x_min, y_min = lat_lon_to_tile(max_lat, min_lon, zoom)   # NW corner
    x_max, y_max = lat_lon_to_tile(min_lat, max_lon, zoom)   # SE corner
    if x_min > x_max:
        x_min, x_max = x_max, x_min
    if y_min > y_max:
        y_min, y_max = y_max, y_min

    cols_tiles = x_max - x_min + 1
    rows_tiles = y_max - y_min + 1
    total_tiles = cols_tiles * rows_tiles
    if total_tiles > MAX_TILES:
        raise RuntimeError(
            f"İstenen alan bu çözünürlükte {total_tiles} kare gerektiriyor "
            f"(üst sınır {MAX_TILES}). Alanı küçültün veya kaliteyi düşürün."
        )

    tasks = [(x, y) for y in range(y_min, y_max + 1) for x in range(x_min, x_max + 1)]
    fetched: Dict[Tuple[int, int], Image.Image] = {}
    workers = int(cfg.get("max_workers", 4))

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_fetch_tile, source, zoom, x, y) for x, y in tasks]
        done = 0
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res is not None:
                tx, ty, img = res
                fetched[(tx, ty)] = img
            done += 1
            if progress_callback is not None:
                # May raise (e.g. the dialog was closed); let it abort the download.
                progress_callback(done, total_tiles)

    if not fetched:
        raise RuntimeError(
            f"Hiçbir harita karesi indirilemedi ({cfg['name']}). "
            f"İnternet bağlantısını, güvenlik duvarını veya vekil sunucuyu kontrol edin."
        )
    if len(fetched) < total_tiles * min_success_ratio:
        raise RuntimeError(
            f"Kareler eksik indirildi: {len(fetched)}/{total_tiles}. "
            f"Eksik veriyle harita üretilmedi; tekrar deneyin."
        )

    mosaic = Image.new("RGB", (cols_tiles * TILE_PX, rows_tiles * TILE_PX), color=(30, 34, 42))
    for (tx, ty), img in fetched.items():
        if img.size != (TILE_PX, TILE_PX):
            img = img.resize((TILE_PX, TILE_PX), Image.Resampling.BILINEAR)
        mosaic.paste(img, ((tx - x_min) * TILE_PX, (ty - y_min) * TILE_PX))

    # Exact crop in global Mercator pixel space. Interpolating linearly in
    # latitude (the previous approach) is wrong: Mercator Y is asinh(tan(lat)).
    world_px = float(2 ** zoom * TILE_PX)
    ox = x_min * TILE_PX
    oy = y_min * TILE_PX
    gx0 = mercator_x(min_lon) * world_px - ox
    gx1 = mercator_x(max_lon) * world_px - ox
    gy0 = mercator_y(max_lat) * world_px - oy       # north edge -> smaller Y
    gy1 = mercator_y(min_lat) * world_px - oy

    box = (
        max(0, min(int(round(gx0)), mosaic.width - 1)),
        max(0, min(int(round(gy0)), mosaic.height - 1)),
        max(1, min(int(round(gx1)), mosaic.width)),
        max(1, min(int(round(gy1)), mosaic.height)),
    )
    if box[2] > box[0] and box[3] > box[1]:
        mosaic = mosaic.crop(box)

    meta = {
        "source": source,
        "zoom": zoom,
        "tiles_ok": len(fetched),
        "tiles_total": total_tiles,
        "attribution": cfg.get("attribution", ""),
        "m_per_px": zoom_resolution_m_px(zoom, (min_lat + max_lat) * 0.5),
    }
    return mosaic, meta


# ──────────────────────────────────────────────────────────────────────────────
# Orthophoto
# ──────────────────────────────────────────────────────────────────────────────
def download_satellite_mosaic(
    center_lat: float,
    center_lon: float,
    width_m: float,
    height_m: float,
    source: str = "esri",
    zoom: Optional[int] = None,
    quality: str = "hd",
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Image.Image:
    """Downloads and stitches basemap tiles, cropped exactly to the metric bbox.

    Raises RuntimeError if the mosaic could not be completed. It never returns a
    partially-grey image that looks like a successful download.
    """
    if source not in TILE_SERVERS:
        source = "esri"
    if zoom is None:
        zoom = select_optimal_zoom(width_m, quality=quality, lat=center_lat, source=source)

    bbox = calculate_bbox(center_lat, center_lon, width_m, height_m)
    image, meta = _download_mosaic(source, zoom, bbox, progress_callback)
    # Attached so the caller can show attribution and the real GSD.
    image.info.update(meta)
    return image


def download_satellite_mosaic_with_meta(
    center_lat: float,
    center_lon: float,
    width_m: float,
    height_m: float,
    source: str = "esri",
    zoom: Optional[int] = None,
    quality: str = "hd",
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Tuple[Image.Image, Dict]:
    """Same as :func:`download_satellite_mosaic` but also returns the metadata dict."""
    img = download_satellite_mosaic(center_lat, center_lon, width_m, height_m,
                                    source, zoom, quality, progress_callback)
    return img, dict(img.info)


# ──────────────────────────────────────────────────────────────────────────────
# Elevation - real DEM
# ──────────────────────────────────────────────────────────────────────────────
def _decode_terrarium(img: Image.Image) -> np.ndarray:
    """Terrarium RGB -> metres above sea level: (R * 256 + G + B / 256) - 32768."""
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    return (arr[:, :, 0] * 256.0 + arr[:, :, 1] + arr[:, :, 2] / 256.0) - 32768.0


def _select_dem_zoom(width_m: float, lat: float, grid_size: int) -> int:
    """Zoom whose GSD is near the grid cell size, capped by the source's real
    resolution (~30 m) and by the tile budget."""
    target = max(width_m / float(max(grid_size, 1)), 20.0)
    zoom = TILE_SERVERS["terrarium"]["max_zoom"]
    while zoom > 8 and zoom_resolution_m_px(zoom, lat) < target:
        zoom -= 1
    while zoom > 8 and estimate_tile_count(width_m, width_m, zoom, lat) > MAX_TILES:
        zoom -= 1
    return zoom


def download_terrain_dem(
    center_lat: float,
    center_lon: float,
    width_m: float,
    height_m: float,
    grid_size: int = 200,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> TerrainData:
    """Real elevation model from Terrarium terrain tiles.

    Raises RuntimeError when the DEM cannot be fetched. Callers that want a
    graceful degradation should use :func:`fetch_online_elevation_grid`.
    """
    bbox = calculate_bbox(center_lat, center_lon, width_m, height_m)
    zoom = _select_dem_zoom(width_m, center_lat, grid_size)
    mosaic, meta = _download_mosaic("terrarium", zoom, bbox, progress_callback,
                                    min_success_ratio=0.9)

    elev = _decode_terrarium(mosaic)                    # row 0 = north
    elev[(elev < -500.0) | (elev > 9000.0)] = np.nan    # ocean/void sentinels
    if np.isnan(elev).all():
        raise RuntimeError("Yükselti karelerinde geçerli veri yok.")
    if np.isnan(elev).any():
        elev = np.where(np.isnan(elev), np.nanmean(elev), elev)

    # Resample to the working grid.
    resampled = np.asarray(
        Image.fromarray(elev.astype(np.float32), mode="F")
             .resize((grid_size, grid_size), Image.Resampling.BILINEAR),
        dtype=np.float32,
    )

    # TerrainData indexes rows by increasing +Y (north), and the renderer does
    # np.flipud() on the way to an image. The mosaic's row 0 is north, so it has
    # to be flipped here or the whole terrain comes out mirrored north-south.
    z_grid = np.flipud(resampled).copy()

    return TerrainData(
        z_grid=z_grid,
        cell_size_m=float(width_m) / float(grid_size),
        origin_x=0.0,
        origin_y=0.0,
        name=f"DEM {center_lat:.4f}°K {center_lon:.4f}°D (Terrarium z{zoom}, ~{meta['m_per_px']:.0f} m)",
        lat_center=center_lat,
        lon_center=center_lon,
        is_measured=True,
        source_note=TILE_SERVERS["terrarium"]["attribution"],
    )


def _open_elevation_grid(center_lat: float, center_lon: float,
                         width_m: float, height_m: float,
                         grid_size: int, probe: int = 24) -> TerrainData:
    """Fallback DEM: a coarse real grid from the public Open-Elevation API."""
    min_lat, min_lon, max_lat, max_lon = calculate_bbox(center_lat, center_lon, width_m, height_m)
    lats = np.linspace(max_lat, min_lat, probe)   # north -> south
    lons = np.linspace(min_lon, max_lon, probe)

    locations = [{"latitude": float(la), "longitude": float(lo)} for la in lats for lo in lons]
    payload = json.dumps({"locations": locations}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.open-elevation.com/api/v1/lookup",
        data=payload,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        results = json.loads(resp.read().decode("utf-8")).get("results", [])
    if len(results) != probe * probe:
        raise RuntimeError(f"Open-Elevation eksik yanıt verdi ({len(results)}/{probe * probe}).")

    coarse = np.array([float(r.get("elevation") or 0.0) for r in results],
                      dtype=np.float32).reshape(probe, probe)
    resampled = np.asarray(
        Image.fromarray(coarse, mode="F")
             .resize((grid_size, grid_size), Image.Resampling.BICUBIC),
        dtype=np.float32,
    )
    return TerrainData(
        z_grid=np.flipud(resampled).copy(),
        cell_size_m=float(width_m) / float(grid_size),
        name=f"DEM {center_lat:.4f}°K {center_lon:.4f}°D (Open-Elevation {probe}×{probe})",
        lat_center=center_lat,
        lon_center=center_lon,
        is_measured=True,
        source_note="Yükselti: Open-Elevation (SRTM tabanlı, kaba ızgara)",
    )


def _representative_terrain(center_lat: float, center_lon: float,
                            width_m: float, grid_size: int,
                            reason: str) -> TerrainData:
    """Last resort. Synthetic relief around a plausible base elevation.

    This is NOT a measurement. It is flagged ``is_measured=False`` and named in
    Turkish as "TEMSİLİ" so that neither the UI nor an exported report can pass
    it off as survey data.
    """
    if center_lon > 42.0:
        base = 1800.0
    elif center_lon > 35.0:
        base = 1100.0
    elif center_lon > 30.0:
        base = 850.0
    else:
        base = 120.0

    x = np.linspace(-1.0, 1.0, grid_size)
    y = np.linspace(-1.0, 1.0, grid_size)
    xx, yy = np.meshgrid(x, y)
    relief = (np.exp(-((xx - 0.25) ** 2 * 3.5 + (yy - 0.25) ** 2 * 2.0)) * 140.0
              + np.exp(-((xx + 0.35) ** 2 * 2.0 + (yy - 0.45) ** 2 * 4.0)) * 95.0
              - np.exp(-(xx * 4.0 - yy * 2.0) ** 2) * 60.0
              + np.sin(xx * 12.0) * np.cos(yy * 10.0) * 15.0)

    return TerrainData(
        z_grid=(base + relief).astype(np.float32),
        cell_size_m=float(width_m) / float(grid_size),
        name="⚠️ TEMSİLİ ARAZİ - ölçüm değildir",
        lat_center=center_lat,
        lon_center=center_lon,
        is_measured=False,
        source_note=f"Gerçek yükselti verisi alınamadı ({reason}). "
                    f"Görüş alanı analizi bu arazi üzerinde bağlayıcı değildir.",
    )


def fetch_online_elevation_grid(
    center_lat: float,
    center_lon: float,
    width_m: float,
    height_m: float,
    grid_size: int = 200,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    allow_synthetic_fallback: bool = True,
) -> TerrainData:
    """Real elevation grid for the given extent.

    Order: Terrarium tiles -> Open-Elevation -> (optionally) flagged synthetic
    terrain. Check ``result.is_measured`` before presenting the output as data.
    """
    reasons = []
    try:
        return download_terrain_dem(center_lat, center_lon, width_m, height_m,
                                    grid_size, progress_callback)
    except Exception as exc:
        reasons.append(f"Terrarium: {exc}")

    try:
        return _open_elevation_grid(center_lat, center_lon, width_m, height_m, grid_size)
    except Exception as exc:
        reasons.append(f"Open-Elevation: {exc}")

    if not allow_synthetic_fallback:
        raise RuntimeError("Yükselti verisi alınamadı. " + " | ".join(reasons))
    return _representative_terrain(center_lat, center_lon, width_m, grid_size,
                                   " | ".join(reasons))
