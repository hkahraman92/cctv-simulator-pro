"""Local DEM GeoTIFF without rasterio: measured, oriented, georeferenced; COG via tifffile; headless CLI."""
from __future__ import annotations

import json
import math
from dataclasses import asdict

import numpy as np
import pytest
from PIL import Image
from PIL.TiffImagePlugin import ImageFileDirectory_v2

from cctv_simulator import terrain_loader
from cctv_simulator.geotiff_reader import projection_for_epsg, read_geotiff, terrain_from_geotiff

LAT, LON = 39.92, 32.85


def _geokeys(epsg):
    model = 2 if epsg in (4326, 4258, 5252) else 1
    return (1, 1, 0, 3, 1024, 0, 1, model, 1025, 0, 1, 1, 2048 if model == 2 else 3072, 0, 1, epsg)


def _write_pil(path, arr, epsg, x0, y0, sx, sy):
    ifd = ImageFileDirectory_v2()
    ifd[33550] = (float(sx), float(sy), 0.0)
    ifd.tagtype[33550] = 12
    ifd[33922] = (0.0, 0.0, 0.0, float(x0), float(y0), 0.0)
    ifd.tagtype[33922] = 12
    ifd[34735] = _geokeys(epsg)
    ifd.tagtype[34735] = 3
    Image.fromarray(arr.astype(np.float32), mode="F").save(path, tiffinfo=ifd)


def _utm_dem(rows=60, cols=80, cell=10.0):
    """North-up DEM in UTM 36N centred on LAT/LON: rises 1 m per row to the NORTH."""
    proj = projection_for_epsg(32636)
    cx, cy = (float(v) for v in proj.forward(np.array(LAT), np.array(LON)))
    x0, y0 = cx - cols * cell / 2, cy + rows * cell / 2
    arr = np.tile((rows - 1 - np.arange(rows, dtype=np.float32))[:, None], (1, cols)) + 800.0
    return arr, x0, y0, cell


@pytest.mark.parametrize("epsg", [32636, 5255])          # UTM 36N, TUREF TM33
def test_tm_inverse_round_trips(epsg):
    proj = projection_for_epsg(epsg)
    lat = np.array([36.5, 39.92, 41.8])
    lon = np.array([31.1, 32.85, 34.4])
    x, y = proj.forward(lat, lon)
    la, lo = proj.inverse(x, y)
    assert np.allclose(la, lat, atol=1e-7) and np.allclose(lo, lon, atol=1e-7)


def test_utm_dem_is_measured_south_up_and_located(tmp_path, monkeypatch):
    arr, x0, y0, cell = _utm_dem()
    f = tmp_path / "dem.tif"
    _write_pil(f, arr, 32636, x0, y0, cell, cell)
    monkeypatch.setattr(terrain_loader, "_terrain_from_rasterio", lambda p: None)   # packaged core: no rasterio
    t = terrain_loader.load_geotiff_or_dem(f)
    assert t.is_measured
    assert t.cell_size_m == pytest.approx(10.0)
    assert t.z_grid.shape == (60, 80)
    assert t.z_grid[0, 0] == pytest.approx(800.0) and t.z_grid[-1, 0] == pytest.approx(859.0)   # row 0 = south
    assert t.lat_center == pytest.approx(LAT, abs=1e-6) and t.lon_center == pytest.approx(LON, abs=1e-6)
    assert t.georef["epsg"] == 32636 and t.georef["y0"] == pytest.approx(y0 - 600.0)
    assert "EPSG:32636" in t.source_note


def test_geographic_dem_is_resampled_to_square_metres(tmp_path):
    rows, cols, d = 40, 40, 1.0 / 3600.0                  # 1 arc-second
    arr = np.tile(np.arange(cols, dtype=np.float32)[None, :], (rows, 1)) + 900.0   # rises to the EAST
    f = tmp_path / "geo.tif"
    _write_pil(f, arr, 4326, LON - cols * d / 2, LAT + rows * d / 2, d, d)
    t = terrain_from_geotiff(f)
    assert t.is_measured and t.georef["resampled"]
    m_lon = 111412.84 * math.cos(math.radians(LAT))
    assert t.cell_size_m == pytest.approx(d * m_lon, rel=1e-6)            # the finer (east-west) spacing
    assert t.width_m == pytest.approx(cols * d * m_lon, rel=0.05)
    assert t.z_grid[:, -1].mean() > t.z_grid[:, 0].mean() + 30            # east stays east
    assert t.lat_center == pytest.approx(LAT, abs=1e-6)


def test_tiled_compressed_cog_reads_via_tifffile(tmp_path):
    tifffile = pytest.importorskip("tifffile")
    arr, x0, y0, cell = _utm_dem(rows=64, cols=64)
    f = tmp_path / "cog.tif"
    tifffile.imwrite(f, arr, tile=(32, 32), compression="zlib", predictor=True, extratags=[
        (33550, 12, 3, (cell, cell, 0.0), False),
        (33922, 12, 6, (0.0, 0.0, 0.0, x0, y0, 0.0), False),
        (34735, 3, 16, _geokeys(32636), False),
    ])
    r = read_geotiff(f)
    assert r.projection.epsg == 32636 and r.data.shape == (64, 64)
    assert np.array_equal(r.data, arr)
    t = terrain_from_geotiff(f)
    assert t.is_measured and t.z_grid[0, 0] < t.z_grid[-1, 0]


def test_heightmap_without_georeference_stays_unmeasured(tmp_path, monkeypatch):
    f = tmp_path / "hm.tif"
    Image.fromarray((np.random.default_rng(0).random((50, 50)) * 255).astype(np.uint8)).save(f)
    monkeypatch.setattr(terrain_loader, "_terrain_from_rasterio", lambda p: None)
    assert terrain_loader.load_geotiff_or_dem(f).is_measured is False


def test_cli_viewshed_on_a_local_geotiff(tmp_path, capsys, monkeypatch):
    from cctv_simulator.__main__ import main
    from cctv_simulator.models import DEFAULT_LEVELS, CameraConfig

    arr, x0, y0, cell = _utm_dem(rows=100, cols=100)
    _write_pil(tmp_path / "saha.tif", arr, 32636, x0, y0, cell, cell)
    monkeypatch.setattr(terrain_loader, "_terrain_from_rasterio", lambda p: None)
    proj = tmp_path / "plan.json"
    proj.write_text(json.dumps({
        "version": "2.0",
        "cameras": [asdict(CameraConfig(name="Kule", focal_min_mm=8, focal_max_mm=48, pole_height_m=12))],
        "ppm_levels": [asdict(x) for x in DEFAULT_LEVELS],
        "terrain": {"source": "geotiff", "file": "saha.tif", "viewshed_range_m": 400.0,       # relative path
                    "placements": [{"camera": "Kule", "x_m": 500, "y_m": 300, "mast_m": 12,
                                    "pan_deg": 90, "tilt_deg": -3}]},
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)                     # the file resolves next to the project, not the cwd
    assert main(["--project", str(proj), "--viewshed", "--json"]) == 0
    vs = json.loads(capsys.readouterr().out)["viewshed"]
    assert vs["terrain_measured"] is True
    assert vs["combined_coverage_pct"] > 0.0
