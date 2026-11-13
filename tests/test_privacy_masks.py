"""KVKK privacy masks: residential cells in a camera's view -> polygons -> KML / DXF."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import numpy as np
import pytest

from cctv_simulator import city, geo_export as ge
from cctv_simulator.terrain_loader import TerrainData

KML = "{http://www.opengis.net/kml/2.2}"


def _terrain(n=40, cell=2.0, **kw):
    return TerrainData(z_grid=np.zeros((n, n), np.float32), cell_size_m=cell, **kw)


def _area(ring):
    """Signed shoelace area of a closed ring (m2)."""
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring[:-1], ring[1:]))


def test_block_becomes_one_rectangle_around_its_cells():
    t = _terrain()
    m = np.zeros((40, 40), bool)
    m[10:15, 20:28] = True                              # rows 10..14 (y), cols 20..27 (x)
    rings = city.mask_rings(t, m)
    assert len(rings) == 1 and rings[0][0] == rings[0][-1]
    assert len(rings[0]) == 5                           # four corners: collinear cell corners are dropped
    xs, ys = [p[0] for p in rings[0]], [p[1] for p in rings[0]]
    # a cell is the square around its node: the outline runs half a cell outside the outer nodes
    assert (min(xs), max(xs)) == pytest.approx((19.5 * 2, 27.5 * 2)) and (min(ys), max(ys)) == pytest.approx((9.5 * 2, 14.5 * 2))
    assert abs(_area(rings[0])) == pytest.approx(m.sum() * 4.0)


def test_area_is_kept_for_any_shape_and_holes_are_their_own_rings():
    t = _terrain()
    m = np.zeros((40, 40), bool)
    m[5:20, 5:20] = True
    m[10:14, 10:14] = False                             # a courtyard
    m[30:33, 30:31] = True                              # a second, separate building
    m[25, 8] = True                                     # a single cell
    rings = city.mask_rings(t, m)
    assert len(rings) == 4
    # outer rings and the hole wind in opposite directions: the signed areas add up to the mask
    assert abs(sum(_area(r) for r in rings)) == pytest.approx(m.sum() * 4.0)
    assert city.mask_rings(t, np.zeros((40, 40), bool)) == []


def test_only_cameras_that_see_homes_get_masks_and_the_flag_is_kept():
    t = _terrain()
    a, b = np.zeros((40, 40), bool), np.zeros((40, 40), bool)
    a[3:6, 3:6] = True
    res = city.CityResult(street_area_m2=0, share_by_level={}, share_by_class={}, level=2, face_share=0, pois=[],
                          privacy=[city.CameraPrivacy("Direk 1 · 90°", 400.0, 36.0, 0.09, False),
                                   city.CameraPrivacy("Direk 2 · 0°", 400.0, 0.0, 0.0, False)],
                          private_seen_m2=36.0, private_masks=[a, b])
    masks = city.privacy_masks(t, res)
    assert [m["label"] for m in masks] == ["Direk 1 · 90°"] and masks[0]["area_m2"] == 36.0 and len(masks[0]["rings"]) == 1
    assert city.privacy_masks(t, res, only_flagged=True) == []


def test_masks_are_written_to_kml_and_dxf():
    t = _terrain(lat_center=39.92, lon_center=32.85, is_measured=True)
    ring = [(10.0, 10.0), (20.0, 10.0), (20.0, 18.0), (10.0, 18.0), (10.0, 10.0)]
    privacy = [{"label": "Direk 3 · 45°", "share": 0.22, "flagged": True, "area_m2": 80.0, "rings": [ring]}]
    root = ET.fromstring(ge.build_kml(t, None, privacy=privacy))
    folder = next(f for f in root.iter(f"{KML}Folder") if "KVKK" in f.find(f"{KML}name").text)
    pm = folder.findall(f"{KML}Placemark")
    assert len(pm) == 1 and "Direk 3" in pm[0].find(f"{KML}name").text
    assert "%22" in pm[0].find(f"{KML}description").text and "maske tanımlayın" in pm[0].find(f"{KML}description").text
    coords = pm[0].find(f".//{KML}coordinates").text.split()
    assert len(coords) == 5
    lon, lat = (float(v) for v in coords[0].split(",")[:2])
    assert (lat, lon) == pytest.approx(ge.GeoFrame(t).latlon(10.0, 10.0), abs=1e-7)
    assert "KVKK" not in ge.build_kml(t, None)                       # no city analysis: no folder

    dxf = ge.build_dxf(t, None, privacy=privacy)
    assert dxf.count("\nKVKK\n") >= 6                                # layer table + polyline + 4 vertices + seqend
    assert "KVKK Direk 3" in dxf and dxf.rstrip().endswith("EOF")
    dxf.encode("ascii")                                              # R12: ASCII only
