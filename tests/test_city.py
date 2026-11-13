"""City surveillance: OSM street / residential / place layer, street poles, face angle, privacy."""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from cctv_simulator import city
from cctv_simulator import surface_obstacles as so
from cctv_simulator.area_planner import plan_area
from cctv_simulator.models import CameraConfig
from cctv_simulator.terrain_loader import TerrainData
from cctv_simulator.viewshed_3d import (
    ZONE_DETECT,
    ZONE_IDENT,
    ZONE_OBSERVE,
    CameraPlacement,
    calculate_multi_camera_viewshed,
)

LAT, LON = 39.92, 32.85
CELL = 2.0
CAM = CameraConfig(name="S", focal_min_mm=2.8, focal_max_mm=12.0)


def located(n=150):
    return TerrainData(z_grid=np.zeros((n, n), np.float32), cell_size_m=CELL, lat_center=LAT, lon_center=LON,
                       is_measured=True)


def ll(t, x, y):
    s, w, _n, _e = so.terrain_bbox(t)
    return {"lat": s + y / 111320.0, "lon": w + x / (111320.0 * math.cos(math.radians(LAT)))}


def osm(t):
    """A residential street along y = 150, a main road along x = 150, a block of flats, a school and a bus stop."""
    way = lambda i, tags, pts: {"type": "way", "id": i, "tags": tags, "geometry": [ll(t, x, y) for x, y in pts]}  # noqa: E731
    node = lambda i, tags, x, y: {"type": "node", "id": i, "tags": tags, **ll(t, x, y)}  # noqa: E731
    return {"elements": [
        way(1, {"highway": "residential"}, [(20, 150), (280, 150)]),
        way(2, {"highway": "primary"}, [(150, 10), (150, 290)]),
        way(3, {"building": "apartments"}, [(200, 170), (240, 170), (240, 200), (200, 200), (200, 170)]),
        way(4, {"building": "office"}, [(60, 170), (90, 170), (90, 200), (60, 200), (60, 170)]),
        node(5, {"amenity": "school", "name": "Okul A"}, 100, 160),
        node(6, {"highway": "bus_stop"}, 250, 145),
    ]}


def cell(x, y):
    return int(round(y / CELL)), int(round(x / CELL))


def test_layer_rasterises_streets_by_class_residential_and_places():
    t = located()
    lay = city.build_layer(t, osm(t))
    assert lay.street_class[cell(100, 150)] == 2                 # residential street
    assert lay.street_class[cell(150, 50)] == 4                  # main road
    assert lay.street_class[cell(150, 150)] == 4                 # the main road wins the junction
    assert lay.street_class[cell(100, 100)] == 0
    assert lay.residential[cell(220, 185)] and not lay.residential[cell(75, 185)]   # offices are not homes
    assert {p.kind for p in lay.pois} == {"Okul", "Otobüs durağı"}
    school = next(p for p in lay.pois if p.kind == "Okul")
    assert school.name == "Okul A" and abs(school.x_m - 100) < 0.5 and abs(school.y_m - 160) < 0.5
    code, pts = next(s for s in lay.streets if s[0] == 2)
    assert abs(pts[0][0] - 20) < 0.5 and abs(pts[0][1] - 150) < 0.5


def test_fetch_city_goes_through_the_shared_overpass_client(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    t = located()
    seen = []

    def fake(url, query, timeout):
        seen.append(query)
        return json.dumps(osm(t)).encode("utf-8")
    lay = city.fetch_city(t, fetch=fake, use_cache=False)
    assert seen and "highway" in seen[0] and "apartments" in seen[0]
    assert lay.counts == {"streets": 2, "residential": 1, "pois": 2}


def test_street_sites_stay_on_streets_inside_the_polygon():
    t = located()
    lay = city.build_layer(t, osm(t))
    poly = [(100.0, 100.0), (200.0, 100.0), (200.0, 200.0), (100.0, 200.0)]
    sites = city.street_sites(lay, poly, 10.0)
    assert sites
    for x, y in sites:
        assert 100 <= x <= 200 and 100 <= y <= 200
        assert abs(y - 150) < 1e-6 or abs(x - 150) < 1e-6
    for i, a in enumerate(sites):
        for b in sites[i + 1:]:
            assert math.dist(a, b) >= 5.0 - 1e-6


def _mv(t, mast, pan=90.0, x=40.0, y=150.0, focal=12.0, tilt=-3.0):
    return calculate_multi_camera_viewshed(t, [CameraPlacement(x, y, mast, CAM, pan_deg=pan, tilt_deg=tilt,
                                                               max_range_m=200, focal_mm_override=focal, label="K1")])


def test_face_capture_needs_a_shallow_look_down():
    t = located()
    low = _mv(t, 3.0)
    face = city.face_mask(t, low, face_max_deg=15.0)
    assert face.any() and not (face & ~(low.dori_grid >= ZONE_IDENT)).any()
    rr, cc = np.nonzero(face)
    horiz = np.hypot(cc * CELL - 40.0, rr * CELL - 150.0)
    assert (horiz >= (3.0 - 1.6) / math.tan(math.radians(15.0)) - 1e-6).all()
    # 10 m mast: Teşhis ends ~21 m out, a face needs >= 31 m to be seen at <= 15 deg
    high = _mv(t, 10.0, tilt=-28.0)
    assert (high.dori_grid >= ZONE_IDENT).any()
    assert not city.face_mask(t, high, face_max_deg=15.0).any()   # Teşhis, but only the tops of heads


def test_city_analysis_counts_streets_places_and_flags_a_camera_looking_at_homes():
    t = located()
    lay = city.build_layer(t, osm(t))
    pls = [CameraPlacement(40, 150, 4.0, CAM, pan_deg=90, tilt_deg=-3, max_range_m=200, focal_mm_override=6, label="sokak"),
           CameraPlacement(205, 140, 4.0, CAM, pan_deg=40, tilt_deg=-2, max_range_m=200, focal_mm_override=4, label="konut")]
    mv = calculate_multi_camera_viewshed(t, pls)
    r = city.analyse_city(t, lay, mv, level=ZONE_OBSERVE)
    assert 0 < r.share_by_level[ZONE_OBSERVE] < 1 and set(r.share_by_class) == {2, 4}
    assert r.street_area_m2 == pytest.approx((lay.street_class > 0).sum() * CELL * CELL)
    by = {c.label: c for c in r.privacy}
    assert by["konut"].flagged and by["konut"].private_m2 > 0
    assert not by["sokak"].flagged
    school = next(p for p in r.pois if p.kind == "Okul")
    assert school.best_zone >= ZONE_DETECT          # 10 m off the street, ~60 m out: seen, not observed
    assert set(np.unique(r.layer)) <= {0, 1, 2, 3, 4}
    assert any("KVKK" in n for n in r.notes)


def test_a_street_only_area_plan_puts_poles_on_streets_and_covers_them():
    t = located()
    lay = city.build_layer(t, osm(t))
    poly = [(100.0, 100.0), (200.0, 100.0), (200.0, 200.0), (100.0, 200.0)]
    rep = plan_area(t, poly, CAM, level=ZONE_OBSERVE, goal_frac=0.9, mast_height_m=4.0,
                    roi_mask=lay.street_class > 0, sites_fn=lambda step: city.street_sites(lay, poly, step))
    assert rep.met and rep.plan.placed_cameras
    for c in rep.plan.placed_cameras:
        assert abs(c.y_m - 150) < 0.06 or abs(c.x_m - 150) < 0.06
    assert any("sokak" in n for n in rep.notes)


def test_street_cells_under_a_roof_are_not_a_target():
    t = located()
    lay = city.build_layer(t, osm(t))
    h = np.zeros_like(t.z_grid)
    r, c = cell(100, 150)
    h[r - 3:r + 4, c - 3:c + 4] = 12.0                    # a building straddling the residential street
    surf = so.with_obstacles(t, so.ObstacleLayer(height_grid=h, kind_grid=(h > 0).astype(np.uint8), counts={}))
    assert (city.street_target(lay, t) == (lay.street_class > 0)).all()      # no surface model: every street cell
    tgt = city.street_target(lay, surf)
    assert not tgt[r, c] and tgt[cell(60, 150)] and tgt.sum() < (lay.street_class > 0).sum()


def test_privacy_mask_polygons_cover_exactly_the_homes_a_camera_sees():
    t = located()
    lay = city.build_layer(t, osm(t))
    pls = [CameraPlacement(40, 150, 4.0, CAM, pan_deg=90, tilt_deg=-3, max_range_m=200, focal_mm_override=6, label="sokak"),
           CameraPlacement(205, 140, 4.0, CAM, pan_deg=40, tilt_deg=-2, max_range_m=200, focal_mm_override=4, label="konut")]
    r = city.analyse_city(t, lay, calculate_multi_camera_viewshed(t, pls), level=ZONE_OBSERVE)
    masks = {m["label"]: m for m in city.privacy_masks(t, r)}
    by = {c.label: c for c in r.privacy}
    assert masks["konut"]["flagged"] and masks["konut"]["rings"]
    shoelace = lambda ring: 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring[:-1], ring[1:]))  # noqa: E731
    for label, m in masks.items():                    # signed ring areas (holes negative) add up to the cells
        assert abs(sum(shoelace(ring) for ring in m["rings"])) == pytest.approx(by[label].private_m2)
    assert [m["label"] for m in city.privacy_masks(t, r, only_flagged=True)] == ["konut"]
