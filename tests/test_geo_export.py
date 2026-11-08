"""KML / KMZ / DXF site plan export: geometry from the plan, coordinates from the terrain frame."""
import math
import re
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import pytest

from cctv_simulator import geo_export as ge
from cctv_simulator.geotiff_reader import projection_for_epsg
from cctv_simulator.models import CameraConfig
from cctv_simulator.network_plan import plan_network
from cctv_simulator.online_map_loader import calculate_bbox
from cctv_simulator.perimeter_planner import generate_perimeter_plan
from cctv_simulator.terrain_loader import TerrainData

CAM = CameraConfig(focal_min_mm=2.8, focal_max_mm=12.0)
LAT, LON = 39.92, 32.85
KML = "{http://www.opengis.net/kml/2.2}"


def _terrain(**kw):
    return TerrainData(np.zeros((120, 120)), cell_size_m=5.0, lat_center=LAT, lon_center=LON, is_measured=True, **kw)


@pytest.fixture
def setup():
    t = _terrain()
    plan = generate_perimeter_plan(t, [(100.0, 100.0), (500.0, 100.0), (500.0, 500.0), (100.0, 500.0)], CAM,
                                   target_ppm=40, mast_height_m=6, is_closed_loop=True)
    return t, plan, plan_network(plan)


def test_wedge_is_a_compass_sector():
    ring = ge.wedge(0.0, 0.0, 90.0, 60.0, 100.0)               # east
    assert ring[0] == ring[-1]
    far = ring[:25]
    assert all(math.hypot(x, y) == pytest.approx(100.0) for x, y in far)
    assert all(x > 0 for x, _ in far)                           # all on the east side
    assert far[12][1] == pytest.approx(0.0, abs=1e-9)           # centre ray due east


def test_kml_holds_every_pole_fov_and_switch_at_the_frame_position(setup, tmp_path):
    t, plan, net = setup
    p = ge.write_kml(tmp_path / "saha.kml", t, plan, net)
    root = ET.parse(p).getroot()
    names = [e.text for e in root.iter(f"{KML}name")]
    poles = {c.pole_id for c in plan.placed_cameras}
    assert sum(1 for n in names if re.fullmatch(r"D\d+( \+PTZ)?", n or "")) == len(poles)
    assert sum(1 for n in names if (n or "").endswith(" FOV")) == len(plan.placed_cameras)
    assert sum(1 for n in names if re.fullmatch(r"SW\d+", n or "")) == len(net.switches)
    # a pole lands where calculate_bbox's linear frame puts it
    c = plan.placed_cameras[0]
    s, w, _n, _e = calculate_bbox(LAT, LON, t.width_m, t.height_m)
    want = (w + c.x_m / (111320.0 * math.cos(math.radians(LAT))), s + c.y_m / 111320.0)
    pm = next(pm for pm in root.iter(f"{KML}Placemark") if pm.find(f"{KML}name").text.startswith(f"D{c.pole_id}")
              and pm.find(f".//{KML}Point") is not None)
    lon, lat, _z = map(float, pm.find(f".//{KML}coordinates").text.split(","))
    assert (lon, lat) == pytest.approx(want, abs=1e-8)


def test_kmz_carries_the_coverage_overlay(setup, tmp_path):
    from cctv_simulator.perimeter_ptz import plan_placements
    t, plan, net = setup
    from cctv_simulator.viewshed_3d import calculate_multi_camera_viewshed
    mv = calculate_multi_camera_viewshed(t, plan_placements(plan, CAM))
    p = ge.write_kml(tmp_path / "saha.kmz", t, plan, net, coverage=mv)
    with zipfile.ZipFile(p) as z:
        assert z.namelist()[0] == "doc.kml" and "kapsama.png" in z.namelist()
        doc = z.read("doc.kml").decode("utf-8")
        assert z.read("kapsama.png")[:8] == b"\x89PNG\r\n\x1a\n"
    assert "<GroundOverlay>" in doc and "<LatLonBox>" in doc


def test_procedural_terrain_refuses_kml_but_writes_dxf(tmp_path):
    t = TerrainData(np.zeros((60, 60)), cell_size_m=5.0)
    plan = generate_perimeter_plan(t, [(20.0, 150.0), (280.0, 150.0)], CAM, target_ppm=40, mast_height_m=6,
                                   is_closed_loop=False)
    with pytest.raises(ValueError, match="coğrafi konumu yok"):
        ge.build_kml(t, plan)
    assert ge.write_dxf(tmp_path / "a.dxf", t, plan).read_text(encoding="ascii").rstrip().endswith("EOF")


def _dxf_pairs(text):
    lines = text.splitlines()
    return [(int(lines[i].strip()), lines[i + 1]) for i in range(0, len(lines) - 1, 2)]


def test_dxf_is_well_formed_r12_with_one_circle_per_pole(setup, tmp_path):
    t, plan, net = setup
    text = ge.write_dxf(tmp_path / "saha.dxf", t, plan, net).read_text(encoding="ascii")
    pairs = _dxf_pairs(text)
    ents = [v for c, v in pairs if c == 0]
    assert ents.count("SECTION") == ents.count("ENDSEC") == 3 and ents[-1] == "EOF"
    assert ents.count("POLYLINE") == ents.count("SEQEND")
    layers_of_circles = [pairs[i + 1][1] for i, (c, v) in enumerate(pairs) if c == 0 and v == "CIRCLE"]
    assert layers_of_circles.count("DIREK") == len({c.pole_id for c in plan.placed_cameras})
    assert ("1", "AC1009") in [(str(c), v) for c, v in pairs]


def test_turkish_label_is_escaped_for_r12():
    assert ge._dxf_ascii("Kuzey Kapı Ş") == "Kuzey Kap\\U+0131 \\U+015E"


def test_projected_georeference_is_exact_in_kml_and_real_world_in_dxf(tmp_path):
    proj = projection_for_epsg(32636)
    x0, y0 = 480000.0, 4417000.0                               # SW corner, UTM 36N
    lat_c, lon_c = (float(v) for v in proj.inverse(x0 + 300.0, y0 + 300.0))
    t = TerrainData(np.zeros((120, 120)), cell_size_m=5.0, lat_center=lat_c, lon_center=lon_c, is_measured=True,
                    georef={"epsg": 32636, "x0": x0, "y0": y0, "sx": 5.0, "sy": 5.0, "resampled": False})
    f = ge.GeoFrame(t)
    assert f.exact
    la, lo = f.latlon(123.0, 456.0)
    bx, by = proj.forward(np.array(la), np.array(lo))
    assert (float(bx), float(by)) == pytest.approx((x0 + 123.0, y0 + 456.0), abs=1e-3)
    plan = generate_perimeter_plan(t, [(100.0, 300.0), (500.0, 300.0)], CAM, target_ppm=40, mast_height_m=6,
                                   is_closed_loop=False)
    xs = [float(v) for c, v in _dxf_pairs(ge.build_dxf(t, plan, real_world=True))
          if c == 10 and float(v) != 0.0]                     # 0 = the POLYLINE header's dummy point
    assert min(xs) >= x0 - 1.0 and max(xs) <= x0 + 700.0
    assert "<gx:LatLonQuad>" in ge.build_kml(t, plan, overlay_href="x.png")
