"""Minimal georeferenced GeoTIFF reader (no rasterio / GDAL / pyproj).

Enough to place a local surface model (LiDAR nDSM / DSM) on a downloaded
terrain: pixels via Pillow, georeference from the GeoTIFF tags, and a
handful of coordinate systems projected with closed-form formulas.

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
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None                   # LiDAR tiles are big by design
    p = Path(path)
    try:
        img = Image.open(p)
        tags = dict(img.tag_v2)
        if getattr(img, "n_frames", 1) > 1:
            img.seek(0)
        arr = np.asarray(img, dtype=np.float32)
    except Exception as exc:
        raise ValueError(f"GeoTIFF okunamadı ({p.name}): {exc}. Döşemeli / sıkıştırılmış bir dosyaysa "
                         "şeritli olarak yeniden yazın: gdal_translate -co TILED=NO -co COMPRESS=NONE") from None
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
