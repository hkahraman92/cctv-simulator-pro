"""Local surface model (nDSM / DSM GeoTIFF): projections, placement, datum, merge."""
from __future__ import annotations

import math

import numpy as np
import pytest
from PIL import Image
from PIL.TiffImagePlugin import ImageFileDirectory_v2

from cctv_simulator import surface_obstacles as so
from cctv_simulator.geotiff_reader import (
    UnsupportedCRS,
    projection_for_epsg,
    read_geotiff,
    tm_meridian_arc,
)
from cctv_simulator.terrain_loader import TerrainData

LAT, LON = 39.92, 32.85


def _terrain(n=100, cell=5.0, z=850.0):
    return TerrainData(z_grid=np.full((n, n), z, np.float32), cell_size_m=cell,
                       lat_center=LAT, lon_center=LON, is_measured=True)


def _latlon(terr, x_m, y_m):
    s, w, _n, _e = so.terrain_bbox(terr)
    return s + y_m / 111320.0, w + x_m / (111320.0 * math.cos(math.radians(LAT)))


def _write(path, arr, epsg, x0, y0, sx, sy, nodata=None, point=False):
    ifd = ImageFileDirectory_v2()
    ifd[33550] = (float(sx), float(sy), 0.0)
    ifd.tagtype[33550] = 12
    ifd[33922] = (0.0, 0.0, 0.0, float(x0), float(y0), 0.0)
    ifd.tagtype[33922] = 12
    model = 2 if epsg in (4326, 4258, 5252) else 1
    geokey = 2048 if model == 2 else 3072
    ifd[34735] = (1, 1, 0, 3, 1024, 0, 1, model, 1025, 0, 1, 2 if point else 1, geokey, 0, 1, epsg)
    ifd.tagtype[34735] = 3
    if nodata is not None:
        ifd[42113] = str(nodata)
        ifd.tagtype[42113] = 2
    Image.fromarray(arr.astype(np.float32), mode="F").save(path, tiffinfo=ifd)


def _block_raster(terr, proj, px_m, block=(200.0, 300.0, 260.0, 340.0), h=18.0, base=0.0, pad=60.0):
    """Raster in ``proj`` covering the terrain, ``h`` m above ``base`` inside the
    local-metre block (x0, y0, x1, y1), ``base`` elsewhere."""
    W = terr.width_m
    corners = [_latlon(terr, x, y) for x in (-pad, W + pad) for y in (-pad, W + pad)]
    xs, ys = proj.forward(np.array([c[0] for c in corners]), np.array([c[1] for c in corners]))
    sx = px_m / (111320.0 * math.cos(math.radians(LAT))) if proj.kind == "geographic" else px_m
    sy = px_m / 111320.0 if proj.kind == "geographic" else px_m
    x0, y1, x1, y0 = xs.min(), ys.min(), xs.max(), ys.max()
    cols, rows = int(math.ceil((x1 - x0) / sx)), int(math.ceil((y0 - y1) / sy))
    arr = np.full((rows, cols), base, np.float32)
    bx0, by0, bx1, by1 = block
    bl = [_latlon(terr, x, y) for x, y in ((bx0, by0), (bx1, by1))]
    (ex0, ey0), (ex1, ey1) = [tuple(map(float, proj.forward(np.array(la), np.array(lo)))) for la, lo in bl]
    c0, c1 = int((min(ex0, ex1) - x0) / sx), int((max(ex0, ex1) - x0) / sx)
    r0, r1 = int((y0 - max(ey0, ey1)) / sy), int((y0 - min(ey0, ey1)) / sy)
    arr[r0:r1, c0:c1] = base + h
    return arr, x0, y0, sx, sy


def test_tm_against_known_values():
    # meridian arc at 40 deg (WGS84) and UTM on its central meridian
    assert tm_meridian_arc(40.0) == pytest.approx(4429529.03, abs=0.5)
    utm36 = projection_for_epsg(32636)
    e, n = utm36.forward(np.array(40.0), np.array(33.0))
    assert float(e) == pytest.approx(500000.0, abs=1e-6)
    assert float(n) == pytest.approx(0.9996 * 4429529.03, abs=0.5)
    e1, _ = utm36.forward(np.array(40.0), np.array(34.0))
    e2, _ = utm36.forward(np.array(40.0), np.array(32.0))
    assert float(e1) - 500000 == pytest.approx(500000 - float(e2), abs=1e-6)   # symmetric
    tm30 = projection_for_epsg(5254)
    assert tm30.lon0_deg == 30 and tm30.k0 == 1.0


def test_ed50_and_unknown_crs_are_refused():
    with pytest.raises(UnsupportedCRS, match="ED50"):
        projection_for_epsg(23036)
    with pytest.raises(UnsupportedCRS):
        projection_for_epsg(2100)


@pytest.mark.parametrize("epsg,px_m", [(4326, 1.0), (32636, 1.0), (5254, 0.5)])
def test_ndsm_block_lands_on_the_right_cells(tmp_path, epsg, px_m):
    terr = _terrain()
    proj = projection_for_epsg(epsg)
    arr, x0, y0, sx, sy = _block_raster(terr, proj, px_m)
    f = tmp_path / f"ndsm_{epsg}.tif"
    _write(f, arr, epsg, x0, y0, sx, sy)
    ras = read_geotiff(f)
    assert ras.projection.epsg == epsg
    layer, info = so.layer_from_raster(terr, ras, mode="ndsm")
    h = layer.height_grid
    # block x 200..260, y 300..340 -> cols 40..52, rows 60..68 (row 0 = south)
    assert h[64, 46] == pytest.approx(18.0)
    assert h[64, 30] == 0.0 and h[40, 46] == 0.0 and h[80, 46] == 0.0      # not shifted / mirrored
    assert layer.kind_grid[64, 46] == so.KIND_SURFACE
    assert info["coverage_frac"] == pytest.approx(1.0)
    assert "Ölçülmüş yüzey" in layer.summary()


def test_dsm_subtracts_ground_and_corrects_the_datum_offset(tmp_path):
    terr = _terrain(z=850.0)
    proj = projection_for_epsg(4326)
    # absolute DSM, 2.5 m above the DEM everywhere (another geoid), block +18 m
    arr, x0, y0, sx, sy = _block_raster(terr, proj, 1.0, base=852.5)
    f = tmp_path / "dsm.tif"
    _write(f, arr, 4326, x0, y0, sx, sy)
    layer, info = so.layer_from_raster(terr, read_geotiff(f), mode="auto")
    assert info["mode"] == "dsm"
    assert info["datum_offset_m"] == pytest.approx(2.5, abs=0.01)
    assert layer.height_grid[64, 46] == pytest.approx(18.0, abs=0.01)
    assert layer.height_grid[20, 20] == 0.0                  # open ground, offset removed
    assert "dikey referans" in layer.summary()


def test_measured_surface_replaces_osm_only_where_it_has_data(tmp_path):
    terr = _terrain()
    osm = so.rasterize(terr, [so.Feature(so.KIND_BUILDING, 9.0, "default",
                                         [[_latlon(terr, x, y) for x, y in
                                           ((20, 20), (60, 20), (60, 60), (20, 60))]])])
    proj = projection_for_epsg(4326)
    arr, x0, y0, sx, sy = _block_raster(terr, proj, 1.0, pad=0.0)
    arr[:, : arr.shape[1] // 2] = -9999.0                    # west half: nodata
    f = tmp_path / "half.tif"
    _write(f, arr, 4326, x0, y0, sx, sy, nodata=-9999)
    layer, info = so.layer_from_raster(terr, read_geotiff(f), mode="ndsm", base=osm)
    assert 0.4 < info["coverage_frac"] < 0.6
    assert layer.height_grid[8, 8] == pytest.approx(9.0)      # OSM building kept (west, no data)
    assert layer.kind_grid[8, 8] == so.KIND_BUILDING
    # the block spans x 200..260 m; only its part east of 250 m has data
    assert layer.height_grid[64, 51] == pytest.approx(18.0)   # measured (east half)
    assert layer.kind_grid[64, 51] == so.KIND_SURFACE


def test_no_georeference_is_a_clear_error(tmp_path):
    f = tmp_path / "plain.tif"
    Image.fromarray(np.zeros((10, 10), np.float32), mode="F").save(f)
    with pytest.raises(ValueError, match="GeoKey"):
        read_geotiff(f)
