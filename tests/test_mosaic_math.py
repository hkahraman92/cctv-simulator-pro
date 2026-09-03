"""Mosaic / crop geometry, checked against a monkeypatched tile server.

CLAUDE.md, "Doğrulama alışkanlıkları": inject a fake server (monkeypatch
`_fetch_tile`) and measure the mosaic/crop math with tiles encoded by a
known function.
"""
from __future__ import annotations

import math

import pytest

from cctv_simulator import online_map_loader as oml

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def test_mercator_y_is_not_linear_in_latitude():
    assert oml.mercator_y(0.0) == pytest.approx(0.5, abs=1e-9)
    # Web-Mercator value at 60N is ~0.2904; the naive lat-linear map would put
    # it at (90-60)/180 = 0.1667. The gap is the whole point of the fix.
    assert oml.mercator_y(60.0) == pytest.approx(0.29043, abs=1e-3)
    assert oml.mercator_y(60.0) != pytest.approx(1.0 / 6.0, abs=0.02)
    assert oml.mercator_y(-60.0) == pytest.approx(1.0 - 0.29043, abs=1e-3)


def test_tile_roundtrip_nw_corner():
    for lat, lon, z in [(39.92, 32.85, 12), (0.0, 0.0, 3), (-33.9, 151.2, 14)]:
        x, y = oml.lat_lon_to_tile(lat, lon, z)
        clat, clon = oml.tile_to_lat_lon(x, y, z)
        # NW corner of the containing tile is up/left of the point.
        assert clat >= lat - 1e-6
        assert clon <= lon + 1e-6


def test_bbox_symmetry_and_width():
    lat, lon = 39.925, 32.866
    min_lat, min_lon, max_lat, max_lon = oml.calculate_bbox(lat, lon, 1000.0, 1000.0)
    assert (max_lat + min_lat) / 2 == pytest.approx(lat, abs=1e-9)
    assert (max_lon + min_lon) / 2 == pytest.approx(lon, abs=1e-9)
    # 1 km north-south ~ 1000 / 111320 deg
    assert (max_lat - min_lat) == pytest.approx(1000.0 / 111320.0, rel=1e-6)


def test_zoom_resolution_halves_each_level():
    a = oml.zoom_resolution_m_px(12, 40.0)
    b = oml.zoom_resolution_m_px(13, 40.0)
    assert a / b == pytest.approx(2.0, rel=1e-9)


@pytest.fixture
def fake_tiles(monkeypatch):
    """Every tile is a solid colour encoding (x, y) so paste order is verifiable."""
    calls = []

    def _fake_fetch(source, zoom, x, y):
        calls.append((source, zoom, x, y))
        img = Image.new("RGB", (oml.TILE_PX, oml.TILE_PX), color=(x % 256, y % 256, zoom % 256))
        return x, y, img

    monkeypatch.setattr(oml, "_fetch_tile", _fake_fetch)
    return calls


def test_download_mosaic_crops_inside_tile_grid(fake_tiles):
    zoom = 14
    bbox = oml.calculate_bbox(39.925, 32.866, 1200.0, 900.0)
    mosaic, meta = oml._download_mosaic("esri", zoom, bbox)

    x_min, y_min = oml.lat_lon_to_tile(bbox[2], bbox[1], zoom)
    x_max, y_max = oml.lat_lon_to_tile(bbox[0], bbox[3], zoom)
    grid_w = (abs(x_max - x_min) + 1) * oml.TILE_PX
    grid_h = (abs(y_max - y_min) + 1) * oml.TILE_PX

    assert 0 < mosaic.width <= grid_w
    assert 0 < mosaic.height <= grid_h
    assert meta["tiles_ok"] == meta["tiles_total"] == len(fake_tiles)
    assert meta["zoom"] == zoom
    # crop must be narrower than the full tile grid (bbox < grid)
    assert mosaic.width < grid_w or mosaic.height < grid_h


def test_download_mosaic_raises_when_too_many_tiles(fake_tiles):
    # A whole-country bbox at high zoom blows the MAX_TILES budget.
    bbox = oml.calculate_bbox(39.0, 33.0, 400_000.0, 400_000.0)
    with pytest.raises(RuntimeError):
        oml._download_mosaic("esri", 18, bbox)


def test_download_mosaic_raises_on_mostly_failed_tiles(monkeypatch):
    def _mostly_none(source, zoom, x, y):
        return None

    monkeypatch.setattr(oml, "_fetch_tile", _mostly_none)
    bbox = oml.calculate_bbox(39.925, 32.866, 800.0, 800.0)
    with pytest.raises(RuntimeError):
        oml._download_mosaic("esri", 14, bbox)
