"""Terrain provenance and orientation invariants.

CLAUDE.md, "Arazi ve çevrimiçi harita":
  - is_measured is True ONLY for a real DEM; procedural / fallback is False.
  - z_grid row 0 = SOUTH (increasing +Y = north). A DEM mosaic has row 0 =
    north, so download_terrain_dem must np.flipud or the terrain mirrors N-S.
"""
from __future__ import annotations

import numpy as np
import pytest

from cctv_simulator import online_map_loader as oml
from cctv_simulator.terrain_loader import generate_procedural_terrain, TerrainData

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def test_procedural_terrain_is_not_measured():
    for preset in ("ridge_and_valley", "Hâkim Tepe & Vadi", "rolling_hills"):
        terr = generate_procedural_terrain(preset, grid_size=64, cell_size_m=10.0)
        assert terr.is_measured is False
        assert terr.is_synthetic is True


def test_representative_terrain_is_flagged_synthetic():
    terr = oml._representative_terrain(39.9, 32.8, 800.0, 64, "no network")
    assert terr.is_measured is False
    assert "temsil" in terr.source_note.lower() or "not a measurement" in terr.source_note.lower() \
        or terr.source_note  # some note must explain the flag


def _terrarium_encode(elev_m: np.ndarray) -> Image.Image:
    v = elev_m + 32768.0
    r = np.floor(v / 256.0)
    g = np.floor(v - r * 256.0)
    b = np.floor((v - np.floor(v)) * 256.0)
    rgb = np.stack([r, g, b], axis=-1).clip(0, 255).astype(np.uint8)
    return Image.fromarray(rgb, mode="RGB")


def test_download_terrain_dem_flips_north_south(monkeypatch):
    """North edge is encoded high; after the flip, z_grid[-1] (north) must be
    higher than z_grid[0] (south)."""
    n = 256
    # row 0 = north = 1000 m, linearly down to 0 m at the south edge.
    col = np.linspace(1000.0, 0.0, n).reshape(n, 1)
    elev = np.repeat(col, n, axis=1)
    fake_mosaic = _terrarium_encode(elev)

    def _fake_download(source, zoom, bbox, progress_callback=None, min_success_ratio=0.7):
        return fake_mosaic, {"zoom": zoom, "m_per_px": 30.0, "tiles_ok": 1, "tiles_total": 1}

    monkeypatch.setattr(oml, "_download_mosaic", _fake_download)

    terr = oml.download_terrain_dem(39.9, 32.8, 800.0, 800.0, grid_size=100)
    assert isinstance(terr, TerrainData)
    assert terr.is_measured is True
    south_mean = float(np.mean(terr.z_grid[0]))
    north_mean = float(np.mean(terr.z_grid[-1]))
    assert north_mean > south_mean + 100.0, (south_mean, north_mean)


def test_bilinear_elevation_matches_corners():
    z = np.array([[0.0, 10.0], [20.0, 30.0]], dtype=np.float32)
    terr = TerrainData(z_grid=z, cell_size_m=1.0)
    assert terr.get_elevation_at(0.0, 0.0) == pytest.approx(0.0)
    # centre of the cell = mean of the four corners
    assert terr.get_elevation_at(0.5, 0.5) == pytest.approx(15.0)


class _FakeAffine:
    # a, b, c, d, e, f — north-up raster has e (row step) < 0
    def __init__(self, a, e, c, f):
        self.a, self.b, self.c = a, 0.0, c
        self.d, self.e, self.f = 0.0, e, f


class _FakeSrc:
    def __init__(self, arr, transform, nodata=None):
        self._arr = arr
        self.transform = transform
        self.nodata = nodata
        self.height, self.width = arr.shape

    def read(self, _band):
        return self._arr.copy()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_fake_rasterio(monkeypatch, src):
    import sys
    import types
    fake = types.ModuleType("rasterio")
    fake.open = lambda *a, **k: src
    monkeypatch.setitem(sys.modules, "rasterio", fake)


def test_geotiff_north_up_is_flipped_to_south_first(monkeypatch, tmp_path):
    from cctv_simulator import terrain_loader as TL

    # projected (UTM) DEM, 30 m cells; row 0 = north = 900 m, ramp down to south.
    arr = np.repeat(np.linspace(900.0, 0.0, 4).reshape(4, 1), 3, axis=1).astype(np.float32)
    src = _FakeSrc(arr, _FakeAffine(a=30.0, e=-30.0, c=500000.0, f=4000120.0))
    _install_fake_rasterio(monkeypatch, src)

    p = tmp_path / "dem.tif"
    p.write_bytes(b"stub")
    terr = TL.load_geotiff_or_dem(p)

    assert terr.is_measured is True
    assert terr.cell_size_m == pytest.approx(30.0)
    assert terr.origin_x == 0.0 and terr.origin_y == 0.0
    # row 0 must now be the SOUTH edge (the 0 m end), last row the north (900 m)
    assert float(terr.z_grid[0].mean()) < float(terr.z_grid[-1].mean())
    assert float(terr.z_grid[-1].mean()) == pytest.approx(900.0)


def test_geotiff_nodata_is_filled_not_propagated(monkeypatch, tmp_path):
    from cctv_simulator import terrain_loader as TL

    arr = np.full((4, 4), 100.0, dtype=np.float32)
    arr[0, 0] = -9999.0
    src = _FakeSrc(arr, _FakeAffine(a=10.0, e=-10.0, c=0.0, f=40.0), nodata=-9999.0)
    _install_fake_rasterio(monkeypatch, src)

    p = tmp_path / "dem.tif"
    p.write_bytes(b"stub")
    terr = TL.load_geotiff_or_dem(p)
    assert not np.isnan(terr.z_grid).any()
    assert terr.get_elevation_at(5.0, 5.0) == terr.get_elevation_at(5.0, 5.0)  # not NaN
