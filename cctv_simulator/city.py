"""City surveillance: street coverage, face-capture angle, privacy (KVKK).

A city plan is not judged by area coverage. Three different questions:

* **Streets** -- what share of the street network (by class: main road,
  street, pedestrian / square) a plan's fixed cameras see at a DORI level;
  where the unseen streets are. Priority places (school, hospital, market,
  station, bus stop, square) are listed with the best level reached around them.
* **Faces** -- 250 px/m is not enough for a face: a camera high on a pole
  sees the top of heads. A cell counts as *face-capable* when some camera
  reaches Teşhis there AND looks down at a face (``face_height_m``) by at most
  ``face_max_deg`` (default 15 deg).
* **Privacy (KVKK)** -- how much of each camera's view falls on residential
  buildings (OSM building=residential / apartments / house ...); a camera
  above ``flag_share`` of its view is named for a privacy mask.

Street / building / POI data come from OpenStreetMap (Overpass, same mirrors
and cache as the surface model, ``surface_obstacles.fetch_overpass``). The
street raster draws each way at its class's typical width. Numbers come only
from the authoritative engine grids (the caller's ``MultiViewshedResult``).

No Tk.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .terrain_loader import TerrainData

# highway tag -> (class code, typical width m); class codes below
ROAD_WIDTH_M = {
    "motorway": (4, 20.0), "trunk": (4, 16.0), "primary": (4, 14.0), "secondary": (3, 10.0), "tertiary": (3, 8.0),
    "unclassified": (2, 6.0), "residential": (2, 6.0), "living_street": (2, 5.0), "service": (2, 4.0),
    "pedestrian": (1, 8.0), "footway": (1, 3.0), "path": (1, 2.0), "steps": (1, 2.0), "cycleway": (1, 2.5),
}
CLASS_LABEL = {4: "Ana yol / cadde", 3: "Cadde", 2: "Sokak", 1: "Yaya yolu / meydan"}
RESIDENTIAL = ("residential", "apartments", "house", "detached", "semidetached_house", "terrace",
               "dormitory", "bungalow")
POI_KINDS = {
    ("amenity", "school"): "Okul", ("amenity", "kindergarten"): "Anaokulu", ("amenity", "university"): "Üniversite",
    ("amenity", "hospital"): "Hastane", ("amenity", "clinic"): "Sağlık ocağı", ("amenity", "marketplace"): "Pazar yeri",
    ("amenity", "place_of_worship"): "İbadethane", ("highway", "bus_stop"): "Otobüs durağı",
    ("railway", "subway_entrance"): "Metro girişi", ("railway", "station"): "İstasyon",
    ("public_transport", "station"): "İstasyon", ("place", "square"): "Meydan",
}
ATTRIBUTION = "Sokak / bina / yer: © OpenStreetMap katkıda bulunanlar (ODbL)"
POI_RADIUS_M = 15.0


def _hidden_way(tags: Dict[str, str]) -> bool:
    """Underground / indoor / roofed ways (metro passages, arcades): no camera
    on the street sees them. Kızılay alone maps ~85 tunnel footways and stairs."""
    if tags.get("tunnel", "no") not in ("no", "") or tags.get("indoor", "no") not in ("no", ""):
        return True
    if tags.get("covered", "no") not in ("no", ""):
        return True
    try:
        return float(str(tags.get("layer", "0")).replace(",", ".")) < 0
    except ValueError:
        return False


def _road_width(tags: Dict[str, str]) -> Tuple[int, float]:
    """Class and drawn width. A dual carriageway is two one-way ways in OSM:
    each is half the road (at least 6 m), or the pair spills onto the buildings."""
    code, width = ROAD_WIDTH_M[tags["highway"]]
    if code >= 3 and tags.get("oneway") in ("yes", "-1", "1"):
        width = max(width / 2.0, 6.0)
    return code, width


@dataclass
class Poi:
    name: str
    kind: str
    x_m: float
    y_m: float


@dataclass
class CityLayer:
    street_class: np.ndarray                       # uint8, z_grid orientation; 0 = no street
    residential: np.ndarray                        # bool: residential building footprint
    streets: List[Tuple[int, List[Tuple[float, float]]]]   # (class, local-metre polyline)
    pois: List[Poi]
    counts: Dict[str, int] = field(default_factory=dict)
    attribution: str = ATTRIBUTION


# ── Overpass ────────────────────────────────────────────────────────────────
def build_query(bbox: Tuple[float, float, float, float], timeout_s: int = 90) -> str:
    s, w, n, e = bbox
    b = f"({s:.6f},{w:.6f},{n:.6f},{e:.6f})"
    hw = "|".join(ROAD_WIDTH_M)
    res = "|".join(RESIDENTIAL)
    amen = "|".join(v for (k, v) in POI_KINDS if k == "amenity")
    return (f"[out:json][timeout:{timeout_s}];("
            f'way["highway"~"^({hw})$"]{b};'
            f'way["building"~"^({res})$"]{b};relation["building"~"^({res})$"]{b};'
            f'node["amenity"~"^({amen})$"]{b};way["amenity"~"^({amen})$"]{b};'
            f'node["highway"="bus_stop"]{b};node["railway"~"^(subway_entrance|station)$"]{b};'
            f'node["public_transport"="station"]{b};node["place"="square"]{b};way["place"="square"]{b};'
            f");out body geom;")


def _latlon(points) -> List[Tuple[float, float]]:
    return [(float(p["lat"]), float(p["lon"])) for p in points or [] if "lat" in p and "lon" in p]


def build_layer(terrain: TerrainData, data: dict) -> CityLayer:
    """Overpass JSON -> street / residential rasters + street polylines + POIs."""
    from PIL import Image, ImageDraw

    from .surface_obstacles import _Projector

    proj = _Projector(terrain)
    rows, cols, cell = terrain.rows, terrain.cols, terrain.cell_size_m
    street_img = Image.new("L", (cols, rows), 0)
    sdraw = ImageDraw.Draw(street_img)
    res_img = Image.new("L", (cols, rows), 0)
    rdraw = ImageDraw.Draw(res_img)
    streets: List[Tuple[int, List[Tuple[float, float]]]] = []
    pois: List[Poi] = []
    counts = {"streets": 0, "residential": 0, "pois": 0}
    ways = []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        t = el.get("type")
        hw = tags.get("highway")
        if t == "way" and hw in ROAD_WIDTH_M:
            pts = _latlon(el.get("geometry"))
            if _hidden_way(tags):
                counts["hidden"] = counts.get("hidden", 0) + 1
            elif len(pts) >= 2:
                ways.append((_road_width(tags), pts))
            continue
        if tags.get("building") in RESIDENTIAL:
            rings = [_latlon(el.get("geometry"))] if t == "way" else [
                _latlon(m.get("geometry")) for m in el.get("members", []) if m.get("role") != "inner"]
            for r in rings:
                if len(r) >= 3:
                    rdraw.polygon([proj.px(la, lo) for la, lo in r], fill=1, outline=1)
            counts["residential"] += 1
            continue
        kind = next((lab for (k, v), lab in POI_KINDS.items() if tags.get(k) == v), None)
        if kind:
            if t == "node" and "lat" in el:
                la, lo = float(el["lat"]), float(el["lon"])
            else:
                pts = _latlon(el.get("geometry"))
                if not pts:
                    continue
                la, lo = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
            x, y = proj.xy(la, lo)
            pois.append(Poi(str(tags.get("name") or kind), kind, float(x), float(y)))
            counts["pois"] += 1
    # draw lower classes first so a main road wins where two meet
    for (code, width), pts in sorted(ways, key=lambda w: w[0][0]):
        sdraw.line([proj.px(la, lo) for la, lo in pts], fill=code, width=max(1, int(round(width / cell))))
        streets.append((code, [tuple(map(float, proj.xy(la, lo))) for la, lo in pts]))
        counts["streets"] += 1
    return CityLayer(street_class=np.flipud(np.asarray(street_img, dtype=np.uint8)).copy(),
                     residential=np.flipud(np.asarray(res_img, dtype=np.uint8)).astype(bool),
                     streets=streets, pois=pois, counts=counts)


def fetch_city(terrain: TerrainData, *, fetch=None, use_cache: bool = True) -> CityLayer:
    from .surface_obstacles import check_area, fetch_overpass, terrain_bbox

    bbox = terrain_bbox(terrain)
    check_area(bbox, "sokak / bina")
    return build_layer(terrain, fetch_overpass(build_query(bbox), fetch=fetch, use_cache=use_cache))


def street_target(layer: CityLayer, terrain: TerrainData, code: Optional[int] = None) -> np.ndarray:
    """Street cells a camera can look at: drawn at full width, a street can run
    under a roof (covered passage, a wide footway touching a building);
    with a surface model loaded those cells are left out."""
    m = (layer.street_class > 0) if code is None else (layer.street_class == code)
    if terrain.obstacle_kind is not None:
        from .surface_obstacles import KIND_BUILDING
        m = m & (terrain.obstacle_kind != KIND_BUILDING)
    return m


# ── poles on the streets (area plan) ────────────────────────────────────────
def street_sites(layer: CityLayer, polygon: Sequence[Tuple[float, float]], step_m: float,
                 min_class: int = 1) -> List[Tuple[float, float]]:
    """Points every ``step_m`` along the streets inside ``polygon`` (lamp-post
    style pole places). Duplicates closer than step/2 are dropped."""
    from .area_planner import _inside, _ring

    ring = _ring(polygon)
    out: List[Tuple[float, float]] = []
    for code, pts in layer.streets:
        if code < min_class:
            continue
        carry = 0.0
        for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
            seg = math.hypot(x1 - x0, y1 - y0)
            d = carry
            while d <= seg:
                out.append((x0 + (x1 - x0) * d / max(seg, 1e-9), y0 + (y1 - y0) * d / max(seg, 1e-9)))
                d += step_m
            carry = d - seg
    if not out:
        return []
    arr = np.array(out)
    keep = _inside(arr[:, 0], arr[:, 1], ring)
    kept: List[Tuple[float, float]] = []
    for x, y in arr[keep]:
        if all((x - a) ** 2 + (y - b) ** 2 >= (step_m / 2) ** 2 for a, b in kept[-50:]):
            kept.append((float(x), float(y)))
    return kept


# ── analysis ────────────────────────────────────────────────────────────────
@dataclass
class CameraPrivacy:
    label: str
    seen_m2: float                 # camera's own visible area (>= Algılama)
    private_m2: float              # of which on residential buildings
    share: float
    flagged: bool


@dataclass
class PoiStatus:
    name: str
    kind: str
    x_m: float
    y_m: float
    best_zone: int                 # best ZONE_* within POI_RADIUS_M (0 = not seen)
    face: bool


@dataclass
class CityResult:
    street_area_m2: float
    share_by_level: Dict[int, float]                 # ZONE_* -> share of street area at >= level
    share_by_class: Dict[int, Tuple[float, float]]   # class -> (street m2, share at >= level)
    level: int
    face_share: float                                # share of street area that is face-capable
    pois: List[PoiStatus]
    privacy: List[CameraPrivacy]
    private_seen_m2: float
    # per camera (same order as ``privacy``): the residential cells it sees -- what a privacy mask must cover
    private_masks: List[np.ndarray] = field(default_factory=list)
    layer: Optional[np.ndarray] = None               # 1 street unseen, 2 seen at level, 3 face-capable, 4 residential seen
    notes: List[str] = field(default_factory=list)


def face_mask(terrain: TerrainData, mv, *, face_height_m: float = 1.6, face_max_deg: float = 15.0) -> np.ndarray:
    """Cells where some camera reaches Teşhis AND looks down at a face by
    at most ``face_max_deg`` (it sees the face, not the top of the head)."""
    from .viewshed_3d import ZONE_IDENT

    cs = terrain.cell_size_m
    xs = terrain.origin_x + np.arange(terrain.cols) * cs
    ys = terrain.origin_y + np.arange(terrain.rows) * cs
    mx, my = np.meshgrid(xs, ys)
    ground = terrain.ground_z_grid if terrain.ground_z_grid is not None else terrain.z_grid
    out = np.zeros(ground.shape, bool)
    tan_max = math.tan(math.radians(face_max_deg))
    for v in mv.per_camera:
        ident = v.dori_grid >= ZONE_IDENT
        if not ident.any():
            continue
        horiz = np.hypot(mx - v.cam_x_m, my - v.cam_y_m)
        drop = v.cam_total_z_m - (ground + face_height_m)
        out |= ident & (drop <= tan_max * horiz)
    return out


def analyse_city(terrain: TerrainData, layer: CityLayer, mv, *, level: int, roi: Optional[np.ndarray] = None,
                 face_height_m: float = 1.6, face_max_deg: float = 15.0, flag_share: float = 0.10,
                 labels: Optional[Sequence[str]] = None) -> CityResult:
    from .focal_optimizer import LEVELS
    from .viewshed_3d import ZONE_DETECT

    cs2 = terrain.cell_size_m ** 2
    inside = np.ones(layer.street_class.shape, bool) if roi is None else roi
    street = street_target(layer, terrain) & inside
    n_st = int(street.sum())
    dori = mv.dori_grid
    share = {lv: (float(((dori >= lv) & street).sum()) / n_st if n_st else 0.0) for lv in LEVELS}
    by_class = {}
    for code in sorted(CLASS_LABEL, reverse=True):
        m = street_target(layer, terrain, code) & inside
        n = int(m.sum())
        if n:
            by_class[code] = (n * cs2, float(((dori >= level) & m).sum()) / n)
    face = face_mask(terrain, mv, face_height_m=face_height_m, face_max_deg=face_max_deg)
    face_share = float((face & street).sum()) / n_st if n_st else 0.0

    cs = terrain.cell_size_m
    pois = []
    rr = int(math.ceil(POI_RADIUS_M / cs))
    for p in layer.pois:
        c = int(round((p.x_m - terrain.origin_x) / cs))
        r = int(round((p.y_m - terrain.origin_y) / cs))
        if not (0 <= r < terrain.rows and 0 <= c < terrain.cols) or not inside[r, c]:
            continue
        sl = (slice(max(0, r - rr), r + rr + 1), slice(max(0, c - rr), c + rr + 1))
        yy, xx = np.mgrid[sl]
        disk = (yy - r) ** 2 + (xx - c) ** 2 <= rr * rr
        pois.append(PoiStatus(p.name, p.kind, p.x_m, p.y_m, int(dori[sl][disk].max(initial=0)),
                              bool(face[sl][disk].any())))

    privacy = []
    masks: List[np.ndarray] = []
    names = list(labels) if labels is not None else list(mv.labels)
    private_any = np.zeros(dori.shape, bool)
    for i, v in enumerate(mv.per_camera):
        seen = v.dori_grid >= ZONE_DETECT
        priv = seen & layer.residential
        private_any |= priv
        masks.append(priv)
        a, p_ = float(seen.sum()) * cs2, float(priv.sum()) * cs2
        s = p_ / a if a else 0.0
        privacy.append(CameraPrivacy(names[i] if i < len(names) else f"Kamera {i + 1}", a, p_, s, s >= flag_share))

    out = np.zeros(dori.shape, np.int8)
    out[street] = 1
    out[street & (dori >= level)] = 2
    out[street & face] = 3
    out[private_any] = 4
    notes = []
    if not n_st:
        notes.append("Bu alanda OpenStreetMap'te sokak yok.")
    flagged = sum(1 for c in privacy if c.flagged)
    if flagged:
        notes.append(f"{flagged} kamerada görüşün konut binasına düşen payı %{flag_share * 100:.0f} sınırını aşıyor: "
                     f"KVKK için gizlilik maskesi tanımlayın ya da açıyı değiştirin.")
    if share.get(level, 0) and face_share < 0.05:
        notes.append(f"Yüz yakalanabilen sokak payı yalnız %{face_share * 100:.0f}: kameralar yüze "
                     f"{face_max_deg:.0f}°'den dik bakıyor ya da Teşhis'e ulaşmıyor. Daha alçak montaj "
                     f"(~3 m) ve dar odak deneyin.")
    return CityResult(street_area_m2=n_st * cs2, share_by_level=share, share_by_class=by_class, level=level,
                      face_share=face_share, pois=pois, privacy=privacy,
                      private_seen_m2=float(private_any.sum()) * cs2, private_masks=masks, layer=out, notes=notes)


def mask_rings(terrain: TerrainData, mask: np.ndarray, max_rings: int = 400) -> List[List[Tuple[float, float]]]:
    """Outline(s) of a cell mask as closed rings in local metres (cell edges,
    collinear points dropped) -- the raster a privacy analysis produces, as
    the polygons a KML / DXF / VMS privacy mask is drawn from.

    Every edge between a mask cell and a non-mask cell is taken once, with
    the mask on its right, and the edges are chained into loops. A cell is
    the square around its node (TerrainData's node convention)."""
    m = np.asarray(mask, bool)
    if not m.any():
        return []
    rows, cols = m.shape
    pad = np.zeros((rows + 2, cols + 2), bool)
    pad[1:-1, 1:-1] = m
    edges = {}                                    # start corner -> [end corner, ...]; corners in (col, row) cell-edge units

    def add(a, b):
        edges.setdefault(a, []).append(b)

    rr, cc = np.nonzero(m)
    for r, c in zip(rr.tolist(), cc.tolist()):
        if not pad[r, c + 1]:
            add((c, r), (c + 1, r))               # south side, heading east
        if not pad[r + 1, c + 2]:
            add((c + 1, r), (c + 1, r + 1))       # east side, heading north
        if not pad[r + 2, c + 1]:
            add((c + 1, r + 1), (c, r + 1))       # north side, heading west
        if not pad[r + 1, c]:
            add((c, r + 1), (c, r))               # west side, heading south
    cs = terrain.cell_size_m
    rings: List[List[Tuple[float, float]]] = []
    while edges and len(rings) < max_rings:
        start = next(iter(edges))
        ring, cur = [start], start
        while True:
            nxt = edges[cur].pop()
            if not edges[cur]:
                del edges[cur]
            cur = nxt
            if cur == start:
                break
            ring.append(cur)
            if cur not in edges:                  # cannot happen for a consistent edge set; never loop forever
                break
        keep = [p for i, p in enumerate(ring)
                if (ring[i - 1][0] - p[0]) * (ring[(i + 1) % len(ring)][1] - p[1])
                != (ring[i - 1][1] - p[1]) * (ring[(i + 1) % len(ring)][0] - p[0])]
        if len(keep) >= 3:
            pts = [(terrain.origin_x + (c - 0.5) * cs, terrain.origin_y + (r - 0.5) * cs) for c, r in keep]
            rings.append(pts + [pts[0]])
    return rings


def privacy_masks(terrain: TerrainData, result: CityResult, only_flagged: bool = False) -> List[Dict]:
    """Per camera with residential cells in view: ``{"label", "share", "flagged", "area_m2", "rings"}``."""
    out = []
    for c, m in zip(result.privacy, result.private_masks):
        if c.private_m2 <= 0 or (only_flagged and not c.flagged):
            continue
        out.append({"label": c.label, "share": c.share, "flagged": c.flagged, "area_m2": c.private_m2,
                    "rings": mask_rings(terrain, m)})
    return out


__all__ = ["CityLayer", "CityResult", "analyse_city", "mask_rings", "privacy_masks", "build_layer", "build_query", "face_mask", "fetch_city",
           "street_sites", "street_target", "CLASS_LABEL"]

