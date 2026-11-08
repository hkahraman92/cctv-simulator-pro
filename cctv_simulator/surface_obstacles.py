"""Buildings and trees as a surface model on top of the DEM.

A DEM is bare earth: a viewshed over it sees straight through every building
and forest. This module downloads OpenStreetMap features for the terrain's
area (Overpass API), turns them into a height-above-ground raster on the
terrain grid, and merges it into a new TerrainData whose ``z_grid`` is the
surface (ground + obstacle) while ``ground_z_grid`` keeps the bare earth.
Every engine (viewshed, mount raster, PTZ sweep, perimeter LOS) then tests
sightlines against the surface; masts stand on the ground
(``TerrainData.ground_elevation_at``).

Heights are only as good as the map. Per feature, in order:
``height`` tag (surveyed / mapped, "tagged") -> ``building:levels`` x 3 m
("levels") -> a class default ("default": building 6 m, tree 8 m, wood /
forest canopy 12 m). The layer counts each source so the UI can say how much
of the surface is an estimate -- like ``is_measured`` for the DEM, an
estimated surface must not be reported as a survey.

Rasterisation is conservative: each footprint fills the cells it covers AND
its outline, so a building smaller than one cell still occupies one cell
instead of vanishing. Coarse grids therefore over-state small occluders.

Local frame = the terrain's: origin (0, 0) at the south-west corner, metres
east/north, the same linear lat/lon conversion as
``online_map_loader.calculate_bbox``. ``z_grid`` row 0 is SOUTH; the PIL
raster row 0 is north, hence the flipud.

No Tk. Network only in :func:`fetch_osm_obstacles` (injectable ``fetch``).
"""
from __future__ import annotations

import gzip
from pathlib import Path
import hashlib
import json
import math
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .terrain_loader import TerrainData

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
ATTRIBUTION = "Bina / ağaç: © OpenStreetMap katkıda bulunanlar (ODbL)"
LEVEL_HEIGHT_M = 3.0
DEFAULT_BUILDING_M = 6.0
DEFAULT_TREE_M = 8.0
DEFAULT_TREE_CROWN_M = 5.0
DEFAULT_CANOPY_M = 12.0
TREE_ROW_WIDTH_M = 5.0
MAX_AREA_KM2 = 36.0          # Overpass courtesy: a 6 x 6 km box is plenty for a site

KIND_NONE, KIND_BUILDING, KIND_VEGETATION, KIND_SURFACE = 0, 1, 2, 3   # 3 = measured, unclassified

_NUM = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


@dataclass
class Feature:
    kind: int                                   # KIND_BUILDING / KIND_VEGETATION
    height_m: float
    source: str                                 # tagged | levels | default
    outer: List[List[Tuple[float, float]]]      # rings of (lat, lon)
    inner: List[List[Tuple[float, float]]] = field(default_factory=list)
    point: Optional[Tuple[float, float]] = None  # lone tree: (lat, lon)
    radius_m: float = 0.0                        # lone tree crown / tree-row half width
    line: Optional[List[Tuple[float, float]]] = None


@dataclass
class ObstacleLayer:
    height_grid: np.ndarray       # m above ground, z_grid orientation (row 0 = south)
    kind_grid: np.ndarray         # uint8 KIND_*
    counts: Dict[str, int]        # buildings / trees / woods + height sources
    attribution: str = ATTRIBUTION

    @property
    def covered_fraction(self) -> float:
        return float((self.height_grid > 0).mean()) if self.height_grid.size else 0.0

    measured_note: str = ""       # set when a local nDSM / DSM covers part of the grid

    @property
    def estimated_share(self) -> float:
        """Share of features whose height is a class default (no tag at all);
        cells covered by a measured surface are not features and not counted."""
        n = self.counts.get("tagged", 0) + self.counts.get("levels", 0) + self.counts.get("default", 0)
        return self.counts.get("default", 0) / n if n else 0.0

    def summary(self) -> str:
        c = self.counts
        osm = (f"{c.get('buildings', 0)} bina, {c.get('trees', 0)} ağaç, "
               f"{c.get('woods', 0)} orman/koru alanı · yükseklik: {c.get('tagged', 0)} etiketli, "
               f"{c.get('levels', 0)} kat sayısından, {c.get('default', 0)} varsayılan")
        if self.measured_note:
            return self.measured_note + ("" if not any(c.get(k) for k in ("buildings", "trees", "woods"))
                                         else " · kapsam dışı OSM: " + osm)
        return osm


# ── tags -> height ───────────────────────────────────────────────────────────
def parse_height(value) -> Optional[float]:
    """'12', '12 m', '12,5', '40 ft' -> metres; None if unusable."""
    if value is None:
        return None
    text = str(value).strip().lower()
    m = _NUM.search(text)
    if not m:
        return None
    num = float(m.group(0).replace(",", "."))
    if "ft" in text or "'" in text:
        num *= 0.3048
    return num if 0.5 <= num <= 800.0 else None


def feature_height(tags: Dict[str, str], kind: int) -> Tuple[float, str]:
    h = parse_height(tags.get("height"))
    if h is not None:
        return h, "tagged"
    if kind == KIND_BUILDING:
        lv = parse_height(tags.get("building:levels"))
        if lv is not None:
            roof = parse_height(tags.get("roof:levels")) or 0.0
            return (lv + roof) * LEVEL_HEIGHT_M, "levels"
        return DEFAULT_BUILDING_M, "default"
    if tags.get("natural") in ("tree", "tree_row"):
        return DEFAULT_TREE_M, "default"
    return DEFAULT_CANOPY_M, "default"


def _underground(tags: Dict[str, str]) -> bool:
    """Below the surface: a metro station complex is mapped as a building
    (Kızılay: building=train_station, layer=-1, location=underground) and was
    raised as a 6 m wall across Atatürk Bulvarı and the square."""
    if tags.get("location") in ("underground", "underwater") or tags.get("building") == "underground":
        return True
    try:
        return float(str(tags.get("layer", "0")).replace(",", ".")) < 0
    except ValueError:
        return False


def _kind(tags: Dict[str, str]) -> int:
    if "building" in tags or "building:part" in tags:
        return KIND_NONE if _underground(tags) else KIND_BUILDING
    if tags.get("natural") in ("tree", "tree_row", "wood") or tags.get("landuse") == "forest":
        return KIND_VEGETATION
    return KIND_NONE


# ── Overpass ────────────────────────────────────────────────────────────────
def build_query(bbox: Tuple[float, float, float, float], timeout_s: int = 90) -> str:
    s, w, n, e = bbox
    b = f"({s:.6f},{w:.6f},{n:.6f},{e:.6f})"
    return (f"[out:json][timeout:{timeout_s}];("
            f'way["building"]{b};relation["building"]{b};way["building:part"]{b};'
            f'node["natural"="tree"]{b};way["natural"="tree_row"]{b};'
            f'way["natural"="wood"]{b};relation["natural"="wood"]{b};'
            f'way["landuse"="forest"]{b};relation["landuse"="forest"]{b};'
            f");out body geom;")


def _geom(points) -> List[Tuple[float, float]]:
    return [(float(p["lat"]), float(p["lon"])) for p in points or [] if "lat" in p and "lon" in p]


def parse_overpass(data: dict) -> List[Feature]:
    """Overpass JSON (``out body geom``) -> features. Unknown elements skipped."""
    out: List[Feature] = []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        kind = _kind(tags)
        if kind == KIND_NONE:
            continue
        h, src = feature_height(tags, kind)
        t = el.get("type")
        if t == "node" and "lat" in el:
            crown = parse_height(tags.get("diameter_crown")) or DEFAULT_TREE_CROWN_M
            out.append(Feature(kind, h, src, [], point=(float(el["lat"]), float(el["lon"])),
                               radius_m=crown / 2.0))
        elif t == "way":
            pts = _geom(el.get("geometry"))
            if len(pts) < 2:
                continue
            if tags.get("natural") == "tree_row":
                out.append(Feature(kind, h, src, [], line=pts, radius_m=TREE_ROW_WIDTH_M / 2.0))
            elif len(pts) >= 3:
                out.append(Feature(kind, h, src, [pts]))
        elif t == "relation":
            outer, inner = [], []
            for mem in el.get("members", []):
                pts = _geom(mem.get("geometry"))
                if len(pts) >= 3:
                    (inner if mem.get("role") == "inner" else outer).append(pts)
            if outer:
                out.append(Feature(kind, h, src, outer, inner))
    return out


def _cache_file(query: str):
    try:
        from .config import user_data_dir
        base = user_data_dir() / "osm-cache"
        base.mkdir(parents=True, exist_ok=True)
        return base / (hashlib.sha1(query.encode("utf-8")).hexdigest()[:20] + ".json.gz")
    except Exception:
        return None


def _http_fetch(url: str, query: str, timeout_s: float) -> bytes:
    from .online_map_loader import USER_AGENT
    body = urllib.parse.urlencode({"data": query}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return resp.read()


def check_area(bbox: Tuple[float, float, float, float], what: str = "bina/ağaç") -> None:
    s, w, n, e = bbox
    km2 = ((n - s) * 111.32) * ((e - w) * 111.32 * math.cos(math.radians((s + n) / 2)))
    if km2 > MAX_AREA_KM2:
        raise RuntimeError(f"Alan çok büyük ({km2:.0f} km²); {what} indirmesi en fazla "
                           f"{MAX_AREA_KM2:.0f} km² için yapılır.")


def fetch_osm_obstacles(bbox: Tuple[float, float, float, float], *,
                        fetch: Optional[Callable[[str, str, float], bytes]] = None,
                        use_cache: bool = True, timeout_s: float = 120.0) -> List[Feature]:
    """Download + parse buildings / trees (see ``fetch_overpass``)."""
    check_area(bbox)
    return parse_overpass(fetch_overpass(build_query(bbox), fetch=fetch, use_cache=use_cache, timeout_s=timeout_s))


def fetch_overpass(query: str, *, fetch: Optional[Callable[[str, str, float], bytes]] = None,
                   use_cache: bool = True, timeout_s: float = 120.0) -> dict:
    """Overpass JSON for ``query``: disk cache (``osm-cache``), then each mirror.
    Raises RuntimeError (Turkish message) when every endpoint fails -- a silent
    empty result would read as "nothing here"."""
    path = _cache_file(query) if use_cache else None
    if path is not None and path.is_file():
        try:
            return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        except (OSError, ValueError):
            pass
    fetch = fetch or _http_fetch
    errors = []
    for url in OVERPASS_URLS:
        try:
            raw = fetch(url, query, timeout_s)
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:          # next mirror
            errors.append(f"{urllib.parse.urlparse(url).netloc}: {type(exc).__name__}: {exc}")
            continue
        if path is not None:
            try:
                path.write_bytes(gzip.compress(raw))
            except OSError:
                pass
        return data
    raise RuntimeError("OpenStreetMap (Overpass) verisi alınamadı:\n" + "\n".join(errors))


# ── raster ──────────────────────────────────────────────────────────────────
def terrain_bbox(terrain: TerrainData) -> Tuple[float, float, float, float]:
    """(south, west, north, east) of a downloaded terrain (needs lat/lon centre)."""
    if terrain.lat_center is None or terrain.lon_center is None:
        raise ValueError("Bu arazinin coğrafi konumu yok; bina/ağaç verisi yalnız "
                         "çevrimiçi indirilen ya da kayıtlı bölgeden açılan arazi için alınabilir.")
    from .online_map_loader import calculate_bbox
    s, w, n, e = calculate_bbox(terrain.lat_center, terrain.lon_center,
                                terrain.width_m, terrain.height_m)
    return s, w, n, e


class _Projector:
    """lat/lon -> raster pixel (col, row-from-north) on the terrain grid."""

    def __init__(self, terrain: TerrainData):
        s, w, n, e = terrain_bbox(terrain)
        self.s, self.w = s, w
        self.m_lat = 111320.0
        self.m_lon = 111320.0 * math.cos(math.radians(terrain.lat_center))
        self.cell = terrain.cell_size_m
        self.rows = terrain.rows

    def xy(self, lat: float, lon: float) -> Tuple[float, float]:
        """Local metres (x east, y north) from the SW corner."""
        return (lon - self.w) * self.m_lon, (lat - self.s) * self.m_lat

    def px(self, lat: float, lon: float) -> Tuple[float, float]:
        x, y = self.xy(lat, lon)
        return x / self.cell, self.rows - y / self.cell


def rasterize(terrain: TerrainData, features: Sequence[Feature]) -> ObstacleLayer:
    """Feature list -> height + kind grids matching ``terrain`` (row 0 south).
    Overlaps keep the taller feature; inner rings (courtyards) are cleared."""
    from PIL import Image, ImageDraw

    proj = _Projector(terrain)
    rows, cols = terrain.rows, terrain.cols
    height = np.zeros((rows, cols), np.float32)
    kind = np.zeros((rows, cols), np.uint8)
    counts = {"buildings": 0, "trees": 0, "woods": 0, "tagged": 0, "levels": 0, "default": 0}

    def ring(pts):
        return [proj.px(la, lo) for la, lo in pts]

    # One canvas per feature class, drawn shortest first so the taller one
    # wins an overlap; vegetation and buildings merged by max afterwards.
    for k in (KIND_VEGETATION, KIND_BUILDING):
        img = Image.new("F", (cols, rows), 0.0)
        draw = ImageDraw.Draw(img)
        for f in sorted((f for f in features if f.kind == k), key=lambda f: f.height_m):
            hval = float(f.height_m)
            if f.point is not None:
                cx, cy = proj.px(*f.point)
                r = max(f.radius_m / proj.cell, 0.5)
                draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=hval, outline=hval)
                counts["trees"] += 1
            elif f.line is not None:
                width = max(1, int(round(2 * f.radius_m / proj.cell)))
                draw.line(ring(f.line), fill=hval, width=width)
                counts["trees"] += 1
            else:
                for pts in f.outer:
                    draw.polygon(ring(pts), fill=hval, outline=hval)
                for pts in f.inner:
                    draw.polygon(ring(pts), fill=0.0)
                counts["buildings" if k == KIND_BUILDING else "woods"] += 1
            counts[f.source] += 1
        layer = np.flipud(np.asarray(img, dtype=np.float32))     # row 0 = south
        taller = layer > height
        height = np.where(taller, layer, height)
        kind = np.where(taller & (layer > 0), np.uint8(k), kind)
    return ObstacleLayer(height_grid=height, kind_grid=kind, counts=counts)


def layer_from_raster(terrain: TerrainData, raster, *, mode: str = "auto",
                      base: Optional[ObstacleLayer] = None, noise_floor_m: float = 1.0,
                      max_subsamples: int = 6) -> Tuple[ObstacleLayer, Dict[str, object]]:
    """A measured surface (``geotiff_reader.GeoRaster``) on the terrain grid.

    ``mode``: "ndsm" (heights above ground), "dsm" (absolute surface; the
    bare DEM is subtracted) or "auto" (guess; ValueError if undecidable).
    Each grid node takes the MAX of up to ``max_subsamples``^2 raster samples
    over its cell, so a narrow building in fine LiDAR is not lost on a
    coarse grid (same conservative rule as the OSM raster). A DSM's vertical
    datum may differ from the DEM's: the 10th percentile of DSM - DEM over
    the covered area (open ground) is taken as the offset and removed.
    Heights under ``noise_floor_m`` (cars, kerbs, LiDAR noise) are dropped.
    Where the raster has data it REPLACES ``base`` (OSM); elsewhere ``base``
    is kept. Returns the layer and an info dict for the UI / report."""
    from .geotiff_reader import guess_is_ndsm
    ground = terrain.ground_z_grid if terrain.ground_z_grid is not None else terrain.z_grid
    proj = _Projector(terrain)
    rows, cols, cell = terrain.rows, terrain.cols, terrain.cell_size_m
    k = int(max(1, min(max_subsamples, math.ceil(cell / max(raster.pixel_size_m, 1e-3)))))
    offs = (np.arange(k) + 0.5) / k - 0.5
    gx = terrain.origin_x + np.arange(cols) * cell
    gy = terrain.origin_y + np.arange(rows) * cell
    best = np.full((rows, cols), np.nan, np.float32)
    for oy in offs:
        for ox in offs:
            mx, my = np.meshgrid(gx + ox * cell, gy + oy * cell)
            lat = proj.s + my / proj.m_lat
            lon = proj.w + mx / proj.m_lon
            px, py = raster.projection.forward(lat, lon)
            v = raster.sample(px, py)
            best = np.where(np.isnan(best), v, np.where(np.isnan(v), best, np.maximum(best, v)))
    covered = np.isfinite(best)
    if not covered.any():
        raise ValueError("Yüzey modeli bu arazinin hiçbir yerine düşmüyor (kapsam / koordinat sistemi?).")
    if mode == "auto":
        guess = guess_is_ndsm(best[covered], ground[covered].astype(np.float32))
        if guess is None:
            raise ValueError("Dosyanın nDSM (zemin üstü yükseklik) mi DSM (mutlak yüzey) mi olduğu "
                             "anlaşılamadı; türü elle seçin.")
        mode = "ndsm" if guess else "dsm"
    offset = 0.0
    if mode == "dsm":
        diff = best[covered] - ground[covered]
        offset = float(np.percentile(diff, 10))
        heights = best - ground - offset
    else:
        heights = best
    heights = np.where(covered & (heights >= noise_floor_m), heights, 0.0).astype(np.float32)

    if base is not None and base.height_grid.shape == heights.shape:
        h = np.where(covered, heights, base.height_grid).astype(np.float32)
        kind = np.where(covered, np.where(heights > 0, KIND_SURFACE, KIND_NONE), base.kind_grid).astype(np.uint8)
        counts = dict(base.counts)
    else:
        h = heights
        kind = np.where(heights > 0, KIND_SURFACE, KIND_NONE).astype(np.uint8)
        counts = {"buildings": 0, "trees": 0, "woods": 0, "tagged": 0, "levels": 0, "default": 0}
    cover_frac = float(covered.mean())
    name = Path(getattr(raster, "path", "") or "yüzey").name
    info = {"mode": mode, "coverage_frac": cover_frac, "datum_offset_m": offset,
            "subsamples": k * k, "raster_px_m": raster.pixel_size_m, "file": name}
    note = (f"Ölçülmüş yüzey ({'nDSM' if mode == 'ndsm' else 'DSM − DEM'}, {name}, EPSG:"
            f"{raster.projection.epsg}) alanın %{cover_frac * 100:.0f}'ini kapsıyor"
            + (f"; dikey referans farkı {offset:+.1f} m düzeltildi" if mode == "dsm" and abs(offset) >= 0.5 else ""))
    layer = ObstacleLayer(height_grid=h, kind_grid=kind, counts=counts,
                          attribution=(base.attribution if base is not None else ""), measured_note=note)
    return layer, info


def with_obstacles(terrain: TerrainData, layer: ObstacleLayer) -> TerrainData:
    """A new TerrainData whose z_grid is the surface (ground + obstacles).
    The input stays untouched (bare earth), so the UI can switch back."""
    ground = terrain.ground_z_grid if terrain.ground_z_grid is not None else terrain.z_grid
    if layer.height_grid.shape != ground.shape:
        raise ValueError("Engel ızgarası arazi ızgarasıyla aynı boyutta değil.")
    surface = (ground.astype(np.float64) + layer.height_grid).astype(ground.dtype)
    note = layer.summary() if layer.measured_note else f"{ATTRIBUTION} · {layer.summary()}"
    return replace(terrain, z_grid=surface, ground_z_grid=ground.copy(),
                   obstacle_grid=layer.height_grid.copy(), obstacle_kind=layer.kind_grid.copy(),
                   obstacle_note=note)


def bare_earth(terrain: TerrainData) -> TerrainData:
    if terrain.ground_z_grid is None:
        return terrain
    return replace(terrain, z_grid=terrain.ground_z_grid.copy(), ground_z_grid=None,
                   obstacle_grid=None, obstacle_kind=None, obstacle_note="")


def mount_obstruction_m(terrain: TerrainData, x_m: float, y_m: float, mast_m: float) -> float:
    """How far an obstacle at the mast's own cell rises above the lens (> 0 =
    the camera sits inside a building / canopy and the first samples of
    every ray hit it)."""
    if terrain.obstacle_grid is None:
        return 0.0
    c = int(round((x_m - terrain.origin_x) / terrain.cell_size_m))
    r = int(round((y_m - terrain.origin_y) / terrain.cell_size_m))
    if not (0 <= r < terrain.rows and 0 <= c < terrain.cols):
        return 0.0
    return float(terrain.obstacle_grid[r, c]) - mast_m

