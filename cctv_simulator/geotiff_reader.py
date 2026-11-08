"""Minimal georeferenced GeoTIFF reader (no rasterio / GDAL / pyproj).

Enough to place a local surface model (LiDAR nDSM / DSM) on a downloaded
terrain, or to open a local DEM as measured terrain: pixels via Pillow (or
tifffile for tiled / compressed files -- COG), georeference from the GeoTIFF
tags, and a handful of coordinate systems with closed-form formulas (forward
and inverse).

Supported CRS (EPSG):
* 4326 / 4258 / 5252 -- geographic lat/lon (WGS84, ETRS89, TUREF);
* 326zz / 327zz (WGS84 UTM N/S), 258zz (ETRS89 UTM), 5253-5259 (TUREF
  TM27-TM45: 3-degree Transverse Mercator, k0 = 1, central meridian
  27..45 E -- the Turkish national grid);
* 3857 Web Mercator.
WGS84 / GRS80 / ETRS89 / ITRF96 / TUREF differ by centimetres at this
scale, treated as one ellipsoid. Other datums (ED50: ~100 m off) are
REFUSED rather than silently shifted.

Tags: ModelPixelScale (33550) + ModelTiepoint (33922) or
ModelTransformation (34264); GeoKeyDirectory (34735); GDAL_NODATA (42113).
PixelIsPoint rasters (RasterType 2) are shifted half a pixel.

No Tk.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

_A = 6378137.0
_F = 1.0 / 298.257223563
_E2 = _F * (2.0 - _F)
_EP2 = _E2 / (1.0 - _E2)


class UnsupportedCRS(ValueError):
    pass


@dataclass
class Projection:
    epsg: int
    kind: str                 # "geographic" | "tm" | "webmercator"
    lon0_deg: float = 0.0
    k0: float = 1.0
    false_e: float = 0.0
    false_n: float = 0.0

    def forward(self, lat_deg: np.ndarray, lon_deg: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        lat = np.asarray(lat_deg, dtype=np.float64)
        lon = np.asarray(lon_deg, dtype=np.float64)
        if self.kind == "geographic":
            return lon, lat
        if self.kind == "webmercator":
            x = _A * np.radians(lon)
            y = _A * np.log(np.tan(np.pi / 4.0 + np.radians(lat) / 2.0))
            return x, y
        return _tm_forward(lat, lon, self.lon0_deg, self.k0, self.false_e, self.false_n)

    def inverse(self, x: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Projected (x, y) -> (lat, lon) degrees."""
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        if self.kind == "geographic":
            return y, x
        if self.kind == "webmercator":
            return np.degrees(2.0 * np.arctan(np.exp(y / _A)) - np.pi / 2.0), np.degrees(x / _A)
        return _tm_inverse(x, y, self.lon0_deg, self.k0, self.false_e, self.false_n)


def _meridian_arc(phi: np.ndarray) -> np.ndarray:
    e2, e4, e6 = _E2, _E2 ** 2, _E2 ** 3
    return _A * ((1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
                 - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * np.sin(2 * phi)
                 + (15 * e4 / 256 + 45 * e6 / 1024) * np.sin(4 * phi)
                 - (35 * e6 / 3072) * np.sin(6 * phi))


def _tm_forward(lat, lon, lon0, k0, fe, fn):
    """Transverse Mercator (Snyder 1987, eq. 8-9/8-10), mm-level inside a zone."""
    phi = np.radians(lat)
    n = _A / np.sqrt(1 - _E2 * np.sin(phi) ** 2)
    t = np.tan(phi) ** 2
    c = _EP2 * np.cos(phi) ** 2
    a = np.radians(lon - lon0) * np.cos(phi)
    m = _meridian_arc(phi)
    x = k0 * n * (a + (1 - t + c) * a ** 3 / 6 + (5 - 18 * t + t * t + 72 * c - 58 * _EP2) * a ** 5 / 120)
    y = k0 * (m + n * np.tan(phi) * (a * a / 2 + (5 - t + 9 * c + 4 * c * c) * a ** 4 / 24
                                    + (61 - 58 * t + t * t + 600 * c - 330 * _EP2) * a ** 6 / 720))
    return x + fe, y + fn


def _tm_inverse(x, y, lon0, k0, fe, fn):
    """Inverse Transverse Mercator (Snyder 1987, eq. 8-18 .. 8-25)."""
    e1 = (1 - math.sqrt(1 - _E2)) / (1 + math.sqrt(1 - _E2))
    m = (y - fn) / k0
    mu = m / (_A * (1 - _E2 / 4 - 3 * _E2 ** 2 / 64 - 5 * _E2 ** 3 / 256))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * np.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * np.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * np.sin(6 * mu) + (1097 * e1 ** 4 / 512) * np.sin(8 * mu))
    s1, c1_ = np.sin(phi1), np.cos(phi1)
    c1 = _EP2 * c1_ ** 2
    t1 = np.tan(phi1) ** 2
    n1 = _A / np.sqrt(1 - _E2 * s1 ** 2)
    r1 = _A * (1 - _E2) / (1 - _E2 * s1 ** 2) ** 1.5
    d = (x - fe) / (n1 * k0)
    lat = phi1 - (n1 * np.tan(phi1) / r1) * (
        d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * _EP2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * _EP2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = lon0 + np.degrees((d - (1 + 2 * t1 + c1) * d ** 3 / 6
                             + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * _EP2 + 24 * t1 ** 2) * d ** 5 / 120) / c1_)
    return np.degrees(lat), lon


def projection_for_epsg(epsg: int) -> Projection:
    if epsg in (4326, 4258, 5252):
        return Projection(epsg, "geographic")
    if epsg == 3857:
        return Projection(epsg, "webmercator")
    if 32601 <= epsg <= 32660 or 25828 <= epsg <= 25838:
        zone = epsg % 100
        return Projection(epsg, "tm", lon0_deg=zone * 6 - 183, k0=0.9996, false_e=500000.0)
    if 32701 <= epsg <= 32760:
        zone = epsg % 100
        return Projection(epsg, "tm", lon0_deg=zone * 6 - 183, k0=0.9996, false_e=500000.0,
                          false_n=10000000.0)
    if 5253 <= epsg <= 5259:                       # TUREF / TM27 ... TM45
        return Projection(epsg, "tm", lon0_deg=27 + 3 * (epsg - 5253), k0=1.0, false_e=500000.0)
    if 23028 <= epsg <= 23038 or epsg in (4230, 2319, 2320, 2321, 2322, 2323, 2324, 2325):
        raise UnsupportedCRS(f"EPSG:{epsg} ED50 datumunda — WGS84'e göre ~100 m kayık. Dosyayı "
                             "WGS84 / TUREF / ITRF96 (ör. EPSG:5254 TM30, 32636 UTM 36N) olarak "
                             "dışa aktarın (gdalwarp -t_srs).")
    raise UnsupportedCRS(f"EPSG:{epsg} desteklenmiyor. Desteklenen: 4326, 3857, UTM (326xx/327xx, "
                         "258xx), TUREF TM27–TM45 (5253–5259).")


@dataclass
class GeoRaster:
    data: np.ndarray          # float32 (rows, cols), row 0 = NORTH, NaN = nodata
    x0: float                 # projected X of the upper-left pixel CORNER
    y0: float
    sx: float                 # pixel size, X (positive)
    sy: float                 # pixel size, Y (positive; rows go south)
    projection: Projection
    path: str = ""

    @property
    def pixel_size_m(self) -> float:
        if self.projection.kind == "geographic":
            return min(self.sx, self.sy) * 111320.0 * 0.7
        return min(self.sx, self.sy)

    def sample(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Nearest pixel at projected coordinates; NaN outside / nodata."""
        col = np.floor((np.asarray(x) - self.x0) / self.sx).astype(np.int64)
        row = np.floor((self.y0 - np.asarray(y)) / self.sy).astype(np.int64)
        rows, cols = self.data.shape
        ok = (row >= 0) & (row < rows) & (col >= 0) & (col < cols)
        out = np.full(np.shape(x), np.nan, np.float32)
        out[ok] = self.data[row[ok], col[ok]]
        return out


def _geokeys(tag) -> dict:
    vals = list(tag or [])
    if len(vals) < 4:
        return {}
    keys = {}
    n = vals[3]
    for i in range(n):
        k, loc, _count, off = vals[4 + 4 * i: 8 + 4 * i]
        if loc == 0:
            keys[k] = off
    return keys


def read_geotiff(path: str | Path) -> GeoRaster:
    """Single-band GeoTIFF -> GeoRaster. Raises ValueError (Turkish) when the
    file has no usable georeference or an unsupported CRS."""
    p = Path(path)
    arr, tags = _pixels_and_tags(p)
    if arr.ndim == 3:
        arr = arr[..., 0]
    keys = _geokeys(tags.get(34735))
    epsg = keys.get(3072) or keys.get(2048)
    if not epsg:
        raise ValueError(f"{p.name}: koordinat sistemi (GeoKey) yok — georeferanslı GeoTIFF gerekli.")
    proj = projection_for_epsg(int(epsg))
    if 34264 in tags:
        m = list(tags[34264])
        if abs(m[1]) > 1e-12 or abs(m[4]) > 1e-12:
            raise ValueError(f"{p.name}: döndürülmüş raster desteklenmiyor (kuzey yukarı olmalı).")
        sx, sy, x0, y0 = float(m[0]), float(-m[5]), float(m[3]), float(m[7])
    elif 33550 in tags and 33922 in tags:
        sc, tp = list(tags[33550]), list(tags[33922])
        sx, sy = float(sc[0]), float(sc[1])
        i, j, x, y = float(tp[0]), float(tp[1]), float(tp[3]), float(tp[4])
        x0, y0 = x - i * sx, y + j * sy
    else:
        raise ValueError(f"{p.name}: piksel ölçeği / bağlantı noktası etiketi yok.")
    if keys.get(1025) == 2:                          # PixelIsPoint: tie point is a pixel centre
        x0, y0 = x0 - sx / 2.0, y0 + sy / 2.0
    nodata = tags.get(42113)
    if nodata is not None:
        try:
            nd = float(str(nodata).strip().strip("\x00"))
            arr = np.where(np.isclose(arr, nd), np.nan, arr)
        except ValueError:
            pass
    arr = np.where(np.isfinite(arr) & (arr > -1000.0) & (arr < 9000.0), arr, np.nan).astype(np.float32)
    return GeoRaster(arr, x0, y0, sx, sy, proj, str(p))


def _pixels_and_tags(p: Path):
    """(float32 band 0, {tag code: value}). Pillow first; tifffile for what
    Pillow cannot decode (tiled / deflate / LZW / predictor: cloud-optimised
    GeoTIFFs)."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None                   # LiDAR tiles are big by design
    first_err = None
    try:
        img = Image.open(p)
        tags = dict(img.tag_v2)
        if getattr(img, "n_frames", 1) > 1:
            img.seek(0)
        arr = np.asarray(img, dtype=np.float32)
        if arr.ndim == 3:
            arr = arr[..., 0]
        return arr, tags
    except FileNotFoundError:
        raise
    except Exception as exc:                        # noqa: BLE001 -- try the next reader
        first_err = exc
    try:
        import tifffile
    except ImportError:
        raise ValueError(f"GeoTIFF okunamadı ({p.name}): {first_err}. Döşemeli / sıkıştırılmış dosyalar için "
                         "'tifffile' paketi gerekir (pip install tifffile) ya da dosyayı şeritli yazın: "
                         "gdal_translate -co TILED=NO -co COMPRESS=NONE") from None
    try:
        with tifffile.TiffFile(p) as tf:
            page = tf.pages[0]
            arr = page.asarray().astype(np.float32)
            tags = {t.code: t.value for t in page.tags.values()}
    except Exception as exc:                        # noqa: BLE001
        raise ValueError(f"GeoTIFF okunamadı ({p.name}): {exc}") from None
    if arr.ndim == 3:
        arr = arr[..., 0] if arr.shape[-1] <= 4 else arr[0]
    return arr, tags


def terrain_from_geotiff(path: str | Path, max_cells: int = 1200):
    """A local DEM GeoTIFF as MEASURED TerrainData, without rasterio.

    Local frame like a downloaded DEM: origin 0,0 at the south-west corner,
    row 0 = south (the raster is flipped), square cells. A projected raster
    (UTM / TUREF TM / Web Mercator) keeps its grid (grid convergence of a few
    tenths of a degree is ignored); a geographic one is resampled to square
    metres at its centre latitude. ``lat/lon_center`` and ``georef`` keep the
    real-world position. Rasters wider than ``max_cells`` are averaged down.
    """
    from .terrain_loader import TerrainData

    r = read_geotiff(path)
    rows, cols = r.data.shape
    cx, cy = r.x0 + cols * r.sx / 2.0, r.y0 - rows * r.sy / 2.0
    lat_c, lon_c = (float(v) for v in r.projection.inverse(cx, cy))
    data = r.data
    if r.projection.kind == "geographic":
        m_lat = 111132.954 - 559.822 * math.cos(2 * math.radians(lat_c))
        m_lon = 111412.84 * math.cos(math.radians(lat_c))
        dx, dy = r.sx * m_lon, r.sy * m_lat
    elif r.projection.kind == "webmercator":
        k = math.cos(math.radians(lat_c))           # Web Mercator scale at the centre latitude
        dx, dy = r.sx * k, r.sy * k
    else:
        dx, dy = r.sx, r.sy
    cell = min(dx, dy)
    width_m, height_m = cols * dx, rows * dy
    step = max(1, math.ceil(max(width_m, height_m) / cell / max_cells))
    cell *= step
    n_c, n_r = max(2, int(round(width_m / cell))), max(2, int(round(height_m / cell)))
    resampled = (abs(dx - dy) > 1e-6 * max(dx, dy)) or step > 1
    if resampled:
        # bilinear on the source pixel centres (NaN-aware: nodata stays NaN)
        xs = (np.arange(n_c) + 0.5) * cell / dx - 0.5
        ys = (np.arange(n_r) + 0.5) * cell / dy - 0.5            # from the NORTH edge
        gx, gy = np.meshgrid(np.clip(xs, 0, cols - 1), np.clip(ys, 0, rows - 1))
        c0 = np.minimum(gx.astype(int), cols - 2) if cols > 1 else np.zeros_like(gx, int)
        r0 = np.minimum(gy.astype(int), rows - 2) if rows > 1 else np.zeros_like(gy, int)
        fx, fy = gx - c0, gy - r0
        c1, r1 = np.minimum(c0 + 1, cols - 1), np.minimum(r0 + 1, rows - 1)
        data = ((data[r0, c0] * (1 - fx) + data[r0, c1] * fx) * (1 - fy)
                + (data[r1, c0] * (1 - fx) + data[r1, c1] * fx) * fy).astype(np.float32)
    valid = np.isfinite(data)
    if not valid.any():
        raise ValueError(f"{Path(path).name}: geçerli yükselti değeri yok (hepsi nodata).")
    z = np.flipud(np.where(valid, data, np.nan)).astype(np.float64)
    nodata_share = 1.0 - float(valid.mean())
    note = (f"Yerel GeoTIFF DEM ({Path(path).name}, EPSG:{r.projection.epsg}, {cell:.1f} m hücre"
            + (", yeniden örneklendi" if resampled else "") + ")")
    if nodata_share > 0.001:
        note += f" · %{nodata_share * 100:.1f} nodata ortalama ile dolduruldu"
    return TerrainData(z_grid=z, cell_size_m=float(cell), name=Path(path).stem, lat_center=lat_c, lon_center=lon_c,
                       is_measured=True, source_note=note,
                       georef={"epsg": r.projection.epsg, "x0": r.x0, "y0": r.y0 - rows * r.sy, "sx": r.sx,
                               "sy": r.sy, "resampled": resampled})


def tm_meridian_arc(lat_deg: float) -> float:
    """Meridian arc length from the equator (m) -- exposed for tests."""
    return float(_meridian_arc(np.radians(np.asarray(lat_deg, dtype=np.float64))))


def guess_is_ndsm(values: np.ndarray, ground: np.ndarray) -> Optional[bool]:
    """True = heights above ground (nDSM), False = absolute surface (DSM),
    None = cannot tell. A DSM tracks the DEM (median gap small); an nDSM sits
    near 0 while the DEM is hundreds of metres up."""
    v = values[np.isfinite(values)]
    g = ground[np.isfinite(values)]
    if v.size < 10:
        return None
    gap = float(np.median(v - g))
    if abs(gap) < 40.0:
        return False
    if float(np.median(v)) < 40.0 and float(np.median(g)) > 60.0:
        return True
    return None
