"""Site plan to GIS / CAD: KML / KMZ (Google Earth, QGIS) and DXF R12 (AutoCAD, NetCAD).

What leaves the app is what the engine planned -- poles, FOV wedges at the
plan's own reach, PTZ patrol arcs, the fence, gaps, PoE switches and (KMZ)
the combined DORI raster as a ground overlay. Nothing is recomputed here.

Coordinates. Local frame = metres from the terrain's south-west corner, x
east, y north; headings are compass bearings (0 = north, clockwise), as in
the planner and the viewshed engine.

- KML needs lat/lon: a terrain with a projected georeference (local DEM in
  UTM / TUREF TM, not resampled) is inverted exactly; anything else uses the
  same linear frame as ``online_map_loader.calculate_bbox`` (the frame the
  terrain was downloaded and the OSM buildings rasterised in).
- DXF stays in metres: local by default, or the raster's projected CRS
  (``real_world=True``) when the terrain has a projected georeference, so a
  surveyor's drawing in that CRS lines up.

No dependencies: KML is plain XML, DXF R12 is ASCII; both are written by hand.
"""
from __future__ import annotations

import io
import math
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

from .terrain_loader import TerrainData

XY = Tuple[float, float]


class GeoFrame:
    """Local metres <-> geographic / projected coordinates of one terrain."""

    def __init__(self, terrain: TerrainData):
        self.terrain = terrain
        g = terrain.georef or {}
        self.proj = None
        if g.get("epsg") and not g.get("resampled"):
            from .geotiff_reader import projection_for_epsg
            p = projection_for_epsg(int(g["epsg"]))
            if p.kind == "tm":
                self.proj, self.x0, self.y0 = p, float(g["x0"]), float(g["y0"])
        self.has_latlon = terrain.lat_center is not None and terrain.lon_center is not None
        if self.has_latlon:
            from .online_map_loader import calculate_bbox
            self.s, self.w, _n, _e = calculate_bbox(terrain.lat_center, terrain.lon_center,
                                                    terrain.width_m, terrain.height_m)
            self.m_lat = 111320.0
            self.m_lon = 111320.0 * math.cos(math.radians(terrain.lat_center)) or self.m_lat

    @property
    def exact(self) -> bool:
        return self.proj is not None

    def latlon(self, x: float, y: float) -> Tuple[float, float]:
        if self.proj is not None:
            la, lo = self.proj.inverse(self.x0 + x, self.y0 + y)
            return float(la), float(lo)
        if not self.has_latlon:
            raise ValueError("Bu arazinin coğrafi konumu yok (prosedürel arazi): KML için çevrimiçi indirilen, "
                             "kayıtlı ya da georeferanslı GeoTIFF bir arazi gerekir. DXF yerel metrede yazılabilir.")
        return self.s + y / self.m_lat, self.w + x / self.m_lon

    def projected(self, x: float, y: float) -> XY:
        """Projected CRS metres when the terrain has one, else local metres."""
        if self.proj is not None:
            return self.x0 + x, self.y0 + y
        return x, y


# ------------------------------------------------------------------ geometry
def wedge(x: float, y: float, pan_deg: float, hfov_deg: float, reach_m: float, near_m: float = 0.0,
          n: int = 24) -> List[XY]:
    """Closed FOV polygon (compass pan): near arc (or the pole) -> far arc."""
    hf = max(min(hfov_deg, 359.9), 0.5)
    angs = [pan_deg - hf / 2 + hf * i / n for i in range(n + 1)]
    far = [(x + reach_m * math.sin(math.radians(a)), y + reach_m * math.cos(math.radians(a))) for a in angs]
    if near_m > 0.5 and hfov_deg < 359.9:
        near = [(x + near_m * math.sin(math.radians(a)), y + near_m * math.cos(math.radians(a)))
                for a in reversed(angs)]
    else:
        near = [(x, y)] if hfov_deg < 359.9 else []
    ring = far + near
    return ring + [ring[0]]


def _cams(plan) -> List:
    return list(plan.placed_cameras) + list(getattr(plan, "ptz_cameras", []) or [])


def _pole_rows(plan):
    """One row per pole: (pole_id, x, y, mast, [fixed cams], [ptz cams])."""
    poles = {}
    for c in plan.placed_cameras:
        poles.setdefault(c.pole_id, [c.x_m, c.y_m, c.mast_height_m, [], []])[3].append(c)
    for c in getattr(plan, "ptz_cameras", []) or []:
        poles.setdefault(c.pole_id, [c.x_m, c.y_m, c.mast_height_m, [], []])[4].append(c)
    return [(pid, *v) for pid, v in sorted(poles.items())]


# ------------------------------------------------------------------ KML
_STYLES = """
<Style id="pole"><IconStyle><color>ff2f9e3a</color><scale>0.8</scale>
<Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon></IconStyle>
<LabelStyle><scale>0.7</scale></LabelStyle></Style>
<Style id="switch"><IconStyle><color>ffd08a2a</color><scale>0.9</scale>
<Icon><href>http://maps.google.com/mapfiles/kml/shapes/square.png</href></Icon></IconStyle>
<LabelStyle><scale>0.7</scale></LabelStyle></Style>
<Style id="fov"><LineStyle><color>c0b7d36a</color><width>1</width></LineStyle>
<PolyStyle><color>40b7d36a</color></PolyStyle></Style>
<Style id="ptz"><LineStyle><color>d0c8509a</color><width>1.5</width></LineStyle>
<PolyStyle><color>28c8509a</color></PolyStyle></Style>
<Style id="fence"><LineStyle><color>ff00d7ff</color><width>3</width></LineStyle></Style>
<Style id="gap"><LineStyle><color>ff2020e0</color><width>5</width></LineStyle></Style>
"""


def _coords(frame: GeoFrame, pts: Iterable[XY], z: float = 0.0) -> str:
    out = []
    for x, y in pts:
        la, lo = frame.latlon(x, y)
        out.append(f"{lo:.8f},{la:.8f},{z:.1f}")
    return " ".join(out)


def _placemark(name: str, style: str, geom: str, desc: str = "") -> str:
    d = f"<description>{escape(desc)}</description>" if desc else ""
    return f"<Placemark><name>{escape(name)}</name>{d}<styleUrl>#{style}</styleUrl>{geom}</Placemark>"


def _polygon(frame, ring) -> str:
    return (f"<Polygon><tessellate>1</tessellate><outerBoundaryIs><LinearRing><coordinates>"
            f"{_coords(frame, ring)}</coordinates></LinearRing></outerBoundaryIs></Polygon>")


def _line(frame, pts) -> str:
    return f"<LineString><tessellate>1</tessellate><coordinates>{_coords(frame, pts)}</coordinates></LineString>"


def _point(frame, x, y) -> str:
    return f"<Point><coordinates>{_coords(frame, [(x, y)])}</coordinates></Point>"


def _cam_desc(c, ptz: bool) -> str:
    s = (f"{c.camera_model} · {c.focal_mm:.1f} mm · HFOV {c.hfov_deg:.1f}° · direk {c.mast_height_m:.1f} m · "
         f"yön {c.pan_deg % 360:.0f}° · eğim {c.tilt_deg:.1f}° · menzil {c.effective_range_m:.0f} m")
    if ptz:
        span = getattr(c, "patrol_span_deg", 0.0)
        s += f" · devriye {'360° dönüş' if span >= 359.9 else f'{span:.0f}° sektör'}"
    return s


def build_kml(terrain: TerrainData, plan, network=None, *, name: str = "CCTV yerleşim",
              overlay_href: Optional[str] = None, overlay_note: str = "") -> str:
    """KML document text. ``overlay_href`` (KMZ only) adds the coverage ground overlay."""
    f = GeoFrame(terrain)
    f.latlon(0.0, 0.0)                                  # fails loudly on a terrain with no position
    parts = [f'<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2" '
             f'xmlns:gx="http://www.google.com/kml/ext/2.2"><Document><name>{escape(name)}</name>',
             f"<description>{escape(_source_line(terrain, f))}</description>", _STYLES]
    if overlay_href:
        parts.append(_ground_overlay(f, terrain, overlay_href, overlay_note))
    if plan is not None:
        fence = list(plan.fence_points)
        parts.append("<Folder><name>Çit / hat</name>")
        parts.append(_placemark(f"Çit ({plan.total_fence_length_m:.0f} m)", "fence", _line(f, fence)))
        parts.append("</Folder>")
        if plan.gaps:
            parts.append("<Folder><name>Kapsama boşlukları</name>")
            for i, g in enumerate(plan.gaps, 1):
                parts.append(_placemark(f"Boşluk {i} ({g.length_m:.0f} m)", "gap",
                                        _line(f, [(g.start_x, g.start_y), (g.end_x, g.end_y)]), g.reason))
            parts.append("</Folder>")
        parts.append("<Folder><name>Direkler</name>")
        for pid, x, y, mast, fixed, ptz in _pole_rows(plan):
            desc = "\n".join([_cam_desc(c, False) for c in fixed] + [_cam_desc(c, True) for c in ptz])
            label = f"D{pid}" + (" +PTZ" if ptz else "")
            parts.append(_placemark(label, "pole", _point(f, x, y), desc))
        parts.append("</Folder><Folder><name>Görüş alanları (sabit)</name>")
        for c in plan.placed_cameras:
            ring = wedge(c.x_m, c.y_m, c.pan_deg, c.hfov_deg, c.effective_range_m, c.dead_zone_m)
            parts.append(_placemark(f"D{c.pole_id} FOV", "fov", _polygon(f, ring), _cam_desc(c, False)))
        parts.append("</Folder>")
        ptzs = list(getattr(plan, "ptz_cameras", []) or [])
        if ptzs:
            parts.append("<Folder><name>PTZ devriye</name>")
            for c in ptzs:
                span = getattr(c, "patrol_span_deg", 0.0) or 360.0
                ring = wedge(c.x_m, c.y_m, c.pan_deg, span, c.effective_range_m, n=48)
                parts.append(_placemark(f"D{c.pole_id} PTZ", "ptz", _polygon(f, ring), _cam_desc(c, True)))
            parts.append("</Folder>")
    if network is not None and network.switches:
        parts.append("<Folder><name>PoE switch</name>")
        for s in network.switches:
            desc = (f"{len(s.poles)} direk · {s.ports_used} port · PoE {s.poe_w:.0f} W · bakır {s.copper_m:.0f} m"
                    + (" · BÜTÇE AŞILDI" if s.over_budget else ""))
            parts.append(_placemark(f"SW{s.index}", "switch", _point(f, s.x_m, s.y_m), desc))
        parts.append("</Folder>")
    parts.append("</Document></kml>")
    return "\n".join(parts)


def _source_line(terrain: TerrainData, f: GeoFrame) -> str:
    src = terrain.source_note or terrain.name or "arazi"
    meas = "ölçülmüş DEM" if terrain.is_measured else "TEMSİLİ arazi — ölçüm değil, bağlayıcı değil"
    geo = ("koordinatlar raster projeksiyonundan tam ters dönüşümle" if f.exact
           else "koordinatlar arazi merkezinden doğrusal çerçeveyle (birkaç km'de metre mertebesi)")
    return f"{src} · {meas} · {geo}"


def _ground_overlay(f: GeoFrame, terrain: TerrainData, href: str, note: str) -> str:
    W, H = terrain.width_m, terrain.height_m
    if f.exact:                                         # projected grid: corners are not a lat/lon box
        quad = _coords(f, [(0, 0), (W, 0), (W, H), (0, H)])
        geom = f"<gx:LatLonQuad><coordinates>{quad}</coordinates></gx:LatLonQuad>"
    else:
        s, w = f.latlon(0, 0)
        n, e = f.latlon(W, H)
        geom = f"<LatLonBox><north>{n:.8f}</north><south>{s:.8f}</south><east>{e:.8f}</east><west>{w:.8f}</west></LatLonBox>"
    return (f"<GroundOverlay><name>Birleşik DORI kapsaması</name><description>{escape(note)}</description>"
            f"<color>ccffffff</color><Icon><href>{href}</href></Icon>{geom}</GroundOverlay>")


def write_kml(path: str | Path, terrain: TerrainData, plan, network=None, *, coverage=None,
              name: str = "CCTV yerleşim", max_overlay_px: int = 2048) -> Path:
    """``.kml`` (vectors) or ``.kmz`` (vectors + coverage PNG when ``coverage``
    -- a ``MultiViewshedResult`` / ``ViewshedResult`` -- is given)."""
    p = Path(path)
    if p.suffix.lower() == ".kmz":
        png = None
        note = ""
        if coverage is not None:
            from .map_render import zone_overlay
            rows, cols = coverage.dori_grid.shape
            k = min(1.0, max_overlay_px / max(rows, cols))
            k = max(k, min(4.0, 1024 / max(rows, cols)))
            img = zone_overlay(coverage.dori_grid, (max(1, round(cols * k)), max(1, round(rows * k))))
            buf = io.BytesIO()
            img.save(buf, "PNG")
            png = buf.getvalue()
            note = "Teşhis / Tanıma / Gözlem / Algılama (EN 62676-4). Görüntü yalnız gösterim; alan ve yüzdeler raporda."
        doc = build_kml(terrain, plan, network, name=name, overlay_href="kapsama.png" if png else None,
                        overlay_note=note)
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("doc.kml", doc)                  # first entry: Google Earth reads it as the root
            if png:
                z.writestr("kapsama.png", png)
    else:
        p.write_text(build_kml(terrain, plan, network, name=name), encoding="utf-8")
    return p


# ------------------------------------------------------------------ DXF R12
_LAYERS = (("CIT", 2), ("DIREK", 3), ("FOV", 4), ("PTZ", 6), ("BOSLUK", 1), ("SWITCH", 5), ("ETIKET", 7))


class _Dxf:
    def __init__(self):
        self.out = io.StringIO()

    def g(self, code: int, value) -> None:
        if isinstance(value, float):
            value = f"{value:.4f}"
        self.out.write(f"{code}\n{value}\n")

    def line(self, layer, a: XY, b: XY):
        self.g(0, "LINE"); self.g(8, layer)
        self.g(10, float(a[0])); self.g(20, float(a[1])); self.g(30, 0.0)
        self.g(11, float(b[0])); self.g(21, float(b[1])); self.g(31, 0.0)

    def polyline(self, layer, pts: Sequence[XY], closed: bool = False):
        self.g(0, "POLYLINE"); self.g(8, layer); self.g(66, 1); self.g(70, 1 if closed else 0)
        self.g(10, 0.0); self.g(20, 0.0); self.g(30, 0.0)
        for x, y in pts:
            self.g(0, "VERTEX"); self.g(8, layer)
            self.g(10, float(x)); self.g(20, float(y)); self.g(30, 0.0)
        self.g(0, "SEQEND"); self.g(8, layer)

    def circle(self, layer, c: XY, r: float):
        self.g(0, "CIRCLE"); self.g(8, layer)
        self.g(10, float(c[0])); self.g(20, float(c[1])); self.g(30, 0.0); self.g(40, float(r))

    def text(self, layer, at: XY, h: float, s: str):
        self.g(0, "TEXT"); self.g(8, layer)
        self.g(10, float(at[0])); self.g(20, float(at[1])); self.g(30, 0.0); self.g(40, float(h))
        self.g(1, s)


def _dxf_ascii(s: str) -> str:
    """R12 text is code-page bound; write Turkish letters as \\U+XXXX (AutoCAD / LibreCAD / NetCAD read it)."""
    return "".join(ch if ord(ch) < 128 else f"\\U+{ord(ch):04X}" for ch in s)


def build_dxf(terrain: TerrainData, plan, network=None, *, real_world: bool = False,
              text_h: float = 0.0) -> str:
    f = GeoFrame(terrain)
    tr = f.projected if (real_world and f.exact) else (lambda x, y: (x, y))
    pts = lambda seq: [tr(x, y) for x, y in seq]                     # noqa: E731
    W, H = terrain.width_m, terrain.height_m
    th = text_h or max(1.5, min(W, H) / 250.0)
    d = _Dxf()
    lo, hi = tr(0, 0), tr(W, H)
    d.g(0, "SECTION"); d.g(2, "HEADER")
    d.g(9, "$ACADVER"); d.g(1, "AC1009")
    d.g(9, "$INSUNITS"); d.g(70, 6)                                 # metres
    d.g(9, "$EXTMIN"); d.g(10, float(lo[0])); d.g(20, float(lo[1])); d.g(30, 0.0)
    d.g(9, "$EXTMAX"); d.g(10, float(hi[0])); d.g(20, float(hi[1])); d.g(30, 0.0)
    d.g(0, "ENDSEC")
    d.g(0, "SECTION"); d.g(2, "TABLES")
    d.g(0, "TABLE"); d.g(2, "LTYPE"); d.g(70, 1)
    d.g(0, "LTYPE"); d.g(2, "CONTINUOUS"); d.g(70, 0); d.g(3, "Solid line"); d.g(72, 65); d.g(73, 0); d.g(40, 0.0)
    d.g(0, "ENDTAB")
    d.g(0, "TABLE"); d.g(2, "LAYER"); d.g(70, len(_LAYERS))
    for nm, col in _LAYERS:
        d.g(0, "LAYER"); d.g(2, nm); d.g(70, 0); d.g(62, col); d.g(6, "CONTINUOUS")
    d.g(0, "ENDTAB"); d.g(0, "ENDSEC")
    d.g(0, "SECTION"); d.g(2, "ENTITIES")
    if plan is not None:
        fence = list(plan.fence_points)
        closed = len(fence) > 2 and fence[0] == fence[-1]
        d.polyline("CIT", pts(fence[:-1] if closed else fence), closed=closed)
        for g in plan.gaps:
            d.line("BOSLUK", tr(g.start_x, g.start_y), tr(g.end_x, g.end_y))
        for pid, x, y, mast, fixed, ptz in _pole_rows(plan):
            d.circle("DIREK", tr(x, y), th * 0.6)
            label = f"D{pid} {mast:.0f}m" + (" +PTZ" if ptz else "")
            d.text("ETIKET", tr(x + th, y + th), th, _dxf_ascii(label))
        for c in plan.placed_cameras:
            ring = wedge(c.x_m, c.y_m, c.pan_deg, c.hfov_deg, c.effective_range_m, c.dead_zone_m)
            d.polyline("FOV", pts(ring[:-1]), closed=True)
        for c in getattr(plan, "ptz_cameras", []) or []:
            span = getattr(c, "patrol_span_deg", 0.0) or 360.0
            if span >= 359.9:
                d.circle("PTZ", tr(c.x_m, c.y_m), c.effective_range_m)
            else:
                d.polyline("PTZ", pts(wedge(c.x_m, c.y_m, c.pan_deg, span, c.effective_range_m, n=48)[:-1]),
                           closed=True)
    if network is not None:
        for s in network.switches:
            x, y = s.x_m, s.y_m
            d.polyline("SWITCH", pts([(x - th, y - th), (x + th, y - th), (x + th, y + th), (x - th, y + th)]),
                       closed=True)
            d.text("ETIKET", tr(x + 1.5 * th, y - th), th, f"SW{s.index} {s.ports_used}p {s.poe_w:.0f}W")
    d.g(0, "ENDSEC"); d.g(0, "EOF")
    return d.out.getvalue()


def write_dxf(path: str | Path, terrain: TerrainData, plan, network=None, *, real_world: bool = False) -> Path:
    p = Path(path)
    p.write_text(build_dxf(terrain, plan, network, real_world=real_world), encoding="ascii", newline="\r\n")
    return p
