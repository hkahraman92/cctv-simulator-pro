"""Buildings / trees surface model: tags, raster orientation, occlusion, fetch."""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from cctv_simulator import surface_obstacles as so
from cctv_simulator.models import CameraConfig
from cctv_simulator.mount_visibility import compute_mount_visibility
from cctv_simulator.terrain_loader import TerrainData
from cctv_simulator.viewshed_3d import ZONE_OCCLUDED, calculate_3d_viewshed

LAT, LON = 39.92, 32.85
CAM = CameraConfig(name="V", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                   focal_min_mm=6.0, focal_max_mm=30.0, pole_height_m=8.0)


def _terrain(n=100, cell=10.0, z=100.0):
    return TerrainData(z_grid=np.full((n, n), z, np.float32), cell_size_m=cell,
                       lat_center=LAT, lon_center=LON, is_measured=True)


def _latlon(terr, x_m, y_m):
    """Local metres -> lat/lon with the module's own linear frame."""
    s, w, _n, _e = so.terrain_bbox(terr)
    return s + y_m / 111320.0, w + x_m / (111320.0 * math.cos(math.radians(LAT)))


def _box(terr, x0, y0, x1, y1):
    return [_latlon(terr, x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]


@pytest.mark.parametrize("raw,expected", [
    ("12", 12.0), ("12 m", 12.0), ("12,5", 12.5), ("40 ft", 12.192), ("abc", None), ("0", None),
])
def test_parse_height(raw, expected):
    got = so.parse_height(raw)
    assert (got is None and expected is None) or got == pytest.approx(expected)


def test_height_source_order():
    assert so.feature_height({"building": "yes", "height": "21"}, so.KIND_BUILDING) == (21.0, "tagged")
    assert so.feature_height({"building": "yes", "building:levels": "4"}, so.KIND_BUILDING) == (12.0, "levels")
    assert so.feature_height({"building": "yes"}, so.KIND_BUILDING) == (so.DEFAULT_BUILDING_M, "default")
    assert so.feature_height({"natural": "tree"}, so.KIND_VEGETATION) == (so.DEFAULT_TREE_M, "default")
    assert so.feature_height({"landuse": "forest"}, so.KIND_VEGETATION)[0] == so.DEFAULT_CANOPY_M


def _geom(pts):
    return [{"lat": la, "lon": lo} for la, lo in pts]


def _overpass(terr):
    ring = _box(terr, 700, 700, 800, 800)                      # north-east block
    courtyard_outer = _box(terr, 100, 100, 300, 300)            # south-west, with a hole
    courtyard_inner = _box(terr, 150, 150, 250, 250)
    tree = _latlon(terr, 500, 120)
    return {"elements": [
        {"type": "way", "tags": {"building": "yes", "height": "30"}, "geometry": _geom(ring)},
        {"type": "relation", "tags": {"building": "yes", "building:levels": "2"},
         "members": [{"role": "outer", "geometry": _geom(courtyard_outer)},
                     {"role": "inner", "geometry": _geom(courtyard_inner)}]},
        {"type": "node", "lat": tree[0], "lon": tree[1], "tags": {"natural": "tree"}},
        {"type": "way", "tags": {"highway": "residential"}, "geometry": _geom(ring)},   # ignored
    ]}


def test_raster_is_north_up_in_z_grid_orientation():
    terr = _terrain()
    layer = so.rasterize(terr, so.parse_overpass(_overpass(terr)))
    h = layer.height_grid
    # z_grid row = y / cell (row 0 = SOUTH): the NE block sits at rows/cols 70-80.
    assert h[75, 75] == pytest.approx(30.0)
    assert h[25, 75] == 0.0 and h[75, 25] == 0.0          # not mirrored N-S or E-W
    assert layer.kind_grid[75, 75] == so.KIND_BUILDING
    # courtyard: walls built, hole cleared, levels x 3 m
    assert h[12, 12] == pytest.approx(6.0) and h[20, 20] == 0.0
    # lone tree at (500, 120) -> row 12, col 50, vegetation
    assert h[12, 50] == pytest.approx(so.DEFAULT_TREE_M)
    assert layer.kind_grid[12, 50] == so.KIND_VEGETATION
    assert layer.counts["buildings"] == 2 and layer.counts["trees"] == 1
    assert layer.counts["tagged"] == 1 and layer.counts["levels"] == 1 and layer.counts["default"] == 1


def test_building_smaller_than_a_cell_still_occupies_one():
    terr = _terrain(cell=10.0)
    tiny = so.Feature(so.KIND_BUILDING, 9.0, "tagged", [_box(terr, 503, 503, 507, 507)])
    layer = so.rasterize(terr, [tiny])
    assert (layer.height_grid > 0).sum() >= 1


def test_surface_blocks_the_view_and_mast_stays_on_the_ground():
    terr = _terrain(n=90, cell=8.0, z=0.0)
    wall = so.Feature(so.KIND_BUILDING, 40.0, "tagged", [_box(terr, 0, 336, 720, 352)])
    surf = so.with_obstacles(terr, so.rasterize(terr, [wall]))
    assert surf.has_obstacles and not terr.has_obstacles
    assert np.array_equal(surf.ground_z_grid, terr.z_grid)
    assert float(surf.z_grid.max()) == pytest.approx(40.0)
    kw = dict(cam_x_m=360, cam_y_m=80, mast_height_m=6, camera=CAM, lens_mode="max",
              pan_deg=0, tilt_deg=-2, max_range_m=600)
    bare = calculate_3d_viewshed(terrain=terr, **kw)
    built = calculate_3d_viewshed(terrain=surf, **kw)
    behind = (slice(46, 60), slice(40, 50))                 # ground right behind the wall
    assert not np.any(bare.dori_grid[behind] == ZONE_OCCLUDED)
    assert np.any(built.dori_grid[behind] == ZONE_OCCLUDED)
    # a mast standing ON the building footprint is still ground + mast
    on_roof = calculate_3d_viewshed(terrain=surf, cam_x_m=360, cam_y_m=344, mast_height_m=6,
                                    camera=CAM, pan_deg=0, tilt_deg=-2, max_range_m=200)
    assert on_roof.cam_total_z_m == pytest.approx(6.0)
    mv = compute_mount_visibility(surf, 360, 344, 6.0, max_range_m=200)
    assert mv.cam_z_m == pytest.approx(6.0)
    assert so.mount_obstruction_m(surf, 360, 344, 6.0) == pytest.approx(34.0)
    assert np.array_equal(so.bare_earth(surf).z_grid, terr.z_grid)


def test_fetch_falls_back_to_the_mirror_and_caches(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    terr = _terrain()
    calls = []

    def fake(url, query, timeout):
        calls.append(url)
        if url == so.OVERPASS_URLS[0]:
            raise OSError("429 Too Many Requests")
        return json.dumps(_overpass(terr)).encode("utf-8")

    bbox = so.terrain_bbox(terr)
    feats = so.fetch_osm_obstacles(bbox, fetch=fake)
    assert len(feats) == 3 and calls == list(so.OVERPASS_URLS)
    calls.clear()
    assert len(so.fetch_osm_obstacles(bbox, fetch=fake)) == 3 and calls == []   # disk cache


def test_fetch_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    def down(url, query, timeout):
        raise OSError("bağlantı yok")

    with pytest.raises(RuntimeError, match="Overpass"):
        so.fetch_osm_obstacles(so.terrain_bbox(_terrain()), fetch=down, use_cache=False)
    with pytest.raises(RuntimeError, match="çok büyük"):
        so.fetch_osm_obstacles((39.0, 32.0, 39.2, 32.2), fetch=down)


def test_terrain_without_location_is_refused():
    with pytest.raises(ValueError):
        so.terrain_bbox(TerrainData(z_grid=np.zeros((10, 10), np.float32)))


def test_region_keeps_bare_dem_and_restores_the_surface(tmp_path):
    from cctv_simulator.region_library import load_region, save_region
    terr = _terrain(n=60, cell=10.0, z=50.0)
    block = so.Feature(so.KIND_BUILDING, 20.0, "tagged", [_box(terr, 200, 200, 300, 300)])
    surf = so.with_obstacles(terr, so.rasterize(terr, [block]))
    info = save_region("Kampüs", surf, base=tmp_path)
    assert info.obstacle_note
    back, _img, _info = load_region(info.region_id, base=tmp_path)
    assert back.has_obstacles
    assert np.array_equal(back.ground_z_grid, terr.z_grid)      # DEM saved bare
    assert np.array_equal(back.z_grid, surf.z_grid)
    assert np.array_equal(back.obstacle_kind, surf.obstacle_kind)
    plain = save_region("Çıplak", terr, base=tmp_path)
    again, _i, _n = load_region(plain.region_id, base=tmp_path)
    assert not again.has_obstacles and np.array_equal(again.z_grid, terr.z_grid)


def test_report_says_the_surface_is_an_osm_estimate(tmp_path):
    from cctv_simulator.exporters import _provenance_line, export_engineering_report_csv
    terr = _terrain(n=40)
    surf = so.with_obstacles(terr, so.rasterize(terr, [so.Feature(
        so.KIND_BUILDING, 9.0, "default", [_box(terr, 100, 100, 150, 150)])]))
    assert "yüzey modeli" not in _provenance_line(terr).lower()
    assert "yüzey modeli" in _provenance_line(surf).lower()
    out = tmp_path / "r.csv"
    export_engineering_report_csv(str(out), project_name="P", terrain=surf, camera=CAM, weather="")
    assert "Yüzey modeli" in out.read_text(encoding="utf-8-sig")


def test_underground_buildings_do_not_rise_above_the_surface():
    """Kızılay's metro complex is mapped as building=train_station, layer=-1,
    location=underground; it used to be raised as a 6 m wall over the square."""
    terr = _terrain()
    ring = [{"lat": la, "lon": lo} for la, lo in _box(terr, 300, 300, 600, 600)]
    data = {"elements": [
        {"type": "way", "id": 1, "tags": {"building": "train_station", "layer": "-1", "location": "underground"}, "geometry": ring},
        {"type": "way", "id": 2, "tags": {"building": "yes", "layer": "-2"}, "geometry": ring},
        {"type": "way", "id": 3, "tags": {"building": "roof", "layer": "1"}, "geometry": ring},   # a canopy: above ground
    ]}
    feats = so.parse_overpass(data)
    assert len(feats) == 1 and feats[0].kind == so.KIND_BUILDING
