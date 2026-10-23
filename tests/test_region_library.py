"""Saved map regions: roundtrip, listing, safety, duplicate lookup."""
from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from cctv_simulator.region_library import (
    delete_region,
    find_region,
    list_regions,
    load_region,
    save_region,
)
from cctv_simulator.terrain_loader import TerrainData


def _terrain(measured=True):
    z = np.random.default_rng(5).normal(800.0, 40.0, (64, 80)).astype(np.float32)
    return TerrainData(z_grid=z, cell_size_m=12.5, origin_x=3.0, origin_y=-4.0,
                       name="Terrarium DEM", lat_center=37.2483, lon_center=43.615,
                       is_measured=measured, source_note="AWS Terrarium")


def _image(w=120, h=96):
    arr = np.random.default_rng(1).integers(0, 255, (h, w, 3), dtype=np.uint8)
    return Image.fromarray(arr, "RGB").convert("RGBA")


def test_roundtrip_keeps_the_terrain_bit_for_bit(tmp_path):
    t, img = _terrain(), _image()
    info = save_region("Hakkari - Çukurca Sınır Hattı", t, img, source="osm", zoom=16,
                       m_per_px=1.2, attribution="© OSM", base=tmp_path)
    t2, img2, info2 = load_region(info.region_id, base=tmp_path)
    assert t2.z_grid.dtype == t.z_grid.dtype and np.array_equal(t2.z_grid, t.z_grid)
    assert (t2.cell_size_m, t2.origin_x, t2.origin_y) == (t.cell_size_m, t.origin_x, t.origin_y)
    assert (t2.lat_center, t2.lon_center, t2.is_measured, t2.source_note) == \
        (t.lat_center, t.lon_center, True, "AWS Terrarium")
    assert info2.name == "Hakkari - Çukurca Sınır Hattı"          # Turkish kept for display
    assert info.region_id.startswith("hakkari-cukurca-sinir-hatti-")
    # map sources are stored lossless
    assert np.array_equal(np.asarray(img2), np.asarray(img))
    assert info2.zoom == 16 and info2.image_size == (120, 96) and info2.size_bytes > 0


def test_photo_imagery_is_jpeg_and_synthetic_relief_stays_flagged(tmp_path):
    info = save_region("Esri", _terrain(measured=False), _image(), source="esri", base=tmp_path)
    assert info.image_file == "ortho.jpg"
    t2, img2, _ = load_region(info.region_id, base=tmp_path)
    assert t2.is_measured is False                                    # never upgraded on reload
    assert img2.size == (120, 96)


def test_region_without_image(tmp_path):
    info = save_region("Yalnız DEM", _terrain(), None, source="local", base=tmp_path)
    t2, img2, info2 = load_region(info.region_id, base=tmp_path)
    assert img2 is None and info2.image_file == ""


def test_listing_newest_first_skips_broken_and_half_written(tmp_path):
    a = save_region("A", _terrain(), None, base=tmp_path)
    b = save_region("B", _terrain(), None, base=tmp_path)
    (tmp_path / ".tmp-yarim-20260101-000000").mkdir()                # interrupted write
    bad = tmp_path / "bozuk-20260101-000000"
    bad.mkdir()
    (bad / "region.json").write_text("{not json", encoding="utf-8")
    meta = json.loads((tmp_path / a.region_id / "region.json").read_text(encoding="utf-8"))
    meta["created"] = "2000-01-01T00:00:00"
    (tmp_path / a.region_id / "region.json").write_text(json.dumps(meta), encoding="utf-8")
    assert [r.region_id for r in list_regions(base=tmp_path)] == [b.region_id, a.region_id]
    assert list_regions(base=tmp_path / "yok") == []


def test_delete_and_path_traversal_guard(tmp_path):
    info = save_region("Silinecek", _terrain(), _image(), base=tmp_path)
    delete_region(info.region_id, base=tmp_path)
    assert list_regions(base=tmp_path) == []
    with pytest.raises(FileNotFoundError):
        load_region(info.region_id, base=tmp_path)
    for evil in ("../x", "..", "a/b", "C:\\x", ""):
        with pytest.raises(ValueError):
            load_region(evil, base=tmp_path)
        with pytest.raises(ValueError):
            delete_region(evil, base=tmp_path)


def test_same_name_twice_gets_distinct_ids(tmp_path):
    a = save_region("Aynı", _terrain(), None, base=tmp_path)
    b = save_region("Aynı", _terrain(), None, base=tmp_path)
    assert a.region_id != b.region_id and len(list_regions(base=tmp_path)) == 2


def test_find_region_matches_the_request_not_the_rounded_extent(tmp_path):
    extra = {"request_lat": 37.2483, "request_lon": 43.6150, "request_width_m": 2000.0}
    info = save_region("Çukurca", _terrain(), None, source="esri", extra=extra, base=tmp_path)
    hit = find_region(37.24831, 43.61501, 2000.0, "esri", base=tmp_path)
    assert hit is not None and hit.region_id == info.region_id
    assert find_region(37.2483, 43.6150, 2000.0, "osm", base=tmp_path) is None      # other source
    assert find_region(37.2483, 43.6150, 5000.0, "esri", base=tmp_path) is None     # other size
    assert find_region(37.2583, 43.6150, 2000.0, "esri", base=tmp_path) is None     # ~1.1 km away


def test_project_and_cli_reopen_the_saved_region(tmp_path, monkeypatch):
    # The project names the region; project_io keeps it and the headless CLI
    # rebuilds exactly the saved grid (user data dir redirected to tmp).
    from cctv_simulator.__main__ import _build_terrain
    from cctv_simulator.models import CameraConfig
    from cctv_simulator.project_io import ProjectData, load_project, save_project

    monkeypatch.setenv("APPDATA", str(tmp_path))
    t = _terrain()
    info = save_region("Proje bölgesi", t, None, source="esri")
    proj = ProjectData(cameras=[CameraConfig()], terrain_source="region",
                       terrain_region_id=info.region_id)
    path = tmp_path / "plan.json"
    save_project(path, proj)
    back = load_project(path)
    assert (back.terrain_source, back.terrain_region_id) == ("region", info.region_id)
    rebuilt = _build_terrain(back)
    assert np.array_equal(rebuilt.z_grid, t.z_grid) and rebuilt.is_measured
    delete_region(info.region_id)
    with pytest.raises(FileNotFoundError):                 # never a silent procedural stand-in
        _build_terrain(back)
