"""Site acceptance sheet: engine-visible check points, px bands, geo frame, writers, sensitivity marks."""
from __future__ import annotations

import csv
import math

import numpy as np
import pytest

from cctv_simulator import acceptance as acc
from cctv_simulator import geo_export
from cctv_simulator.models import CameraConfig
from cctv_simulator.perimeter_planner import PerimeterPlanResult, PlacedCamera
from cctv_simulator.terrain_loader import TerrainData
from cctv_simulator.viewshed_3d import ZONE_DETECT, CameraPlacement, calculate_multi_camera_viewshed

CAM = CameraConfig(name="Sabit", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                   focal_min_mm=2.8, focal_max_mm=12.0)
TARGET = 40.0


def _terrain(rows=90, cols=90, cell=5.0, wall=False):
    yy, xx = np.mgrid[0:rows, 0:cols]
    z = 3.0 * np.sin(xx / 9.0) + 2.0 * np.cos(yy / 11.0)
    if wall:                                    # a 300 m wall right in front of the second camera
        z[(yy * cell >= 255) & (yy * cell <= 330) & (xx * cell >= 40) & (xx * cell <= 400)] = 300.0
    return TerrainData(z_grid=z.astype(np.float64), cell_size_m=cell, name="Test arazisi", is_measured=False)


def _placed(pole, x, y, pan, t, focal=6.0, tilt=-8.0):
    return PlacedCamera(pole_id=pole, x_m=x, y_m=y, ground_z_m=t.ground_elevation_at(x, y), mast_height_m=8.0, pan_deg=pan,
                        tilt_deg=tilt, focal_mm=focal, hfov_deg=48.0, vfov_deg=28.0, effective_range_m=250.0,
                        dead_zone_m=5.0, camera_model="Sabit", sensor_name=CAM.sensor_name,
                        resolution_name=CAM.resolution_name)


def _setup(wall=False, cams=None):
    t = _terrain(wall=wall)
    cams = cams or [(1, 100, 100, 0.0), (2, 250, 100, 45.0), (3, 120, 250, 90.0)]
    pl = [_placed(p, x, y, pan, t) for p, x, y, pan in cams]
    plan = PerimeterPlanResult(fence_points=[(50, 50), (400, 50)], total_fence_length_m=350.0, segment_lengths_m=[350.0],
                               placed_cameras=pl, camera_count=len(pl), pole_count=len(pl), target_ppm=TARGET)
    pls = [CameraPlacement(x_m=c.x_m, y_m=c.y_m, mast_height_m=c.mast_height_m, camera=CAM, pan_deg=c.pan_deg,
                           tilt_deg=c.tilt_deg, max_range_m=250.0, focal_mm_override=c.focal_mm) for c in pl]
    mv = calculate_multi_camera_viewshed(t, pls)
    roi = np.ones(mv.dori_grid.shape, bool)
    return t, plan, mv, pls, roi


def _sheet(t, plan, mv, pls, roi, **kw):
    kw.setdefault("target_ppm", TARGET)
    return acc.build_acceptance(t, plan, CAM, mv=mv, placements=pls, roi=roi, level="40 px/m", project="Deneme", **kw)


def test_points_are_engine_visible_nodes_with_the_engine_ppm():
    t, plan, mv, pls, roi = _setup()
    sheet = _sheet(t, plan, mv, pls, roi, points_per_camera=6)
    assert sheet.points
    for row in sheet.fixed_rows:
        r = mv.per_camera[row.index - 1]
        for p in row.points:
            rr, cc = p.node
            assert bool(r.visibility_mask[rr, cc])                          # the engine says visible
            assert p.ppm == float(r.ppm_grid[rr, cc])                       # the node value, unchanged (no re-derivation)
            assert p.slant_m == float(r.slant_dist_grid[rr, cc])
            assert p.level_code == int(r.dori_grid[rr, cc])
            assert (p.x_m, p.y_m) == (t.origin_x + cc * t.cell_size_m, t.origin_y + rr * t.cell_size_m)
        assert len({p.node for p in row.points}) == len(row.points)         # no node is used twice


def test_point_a_holds_the_target_near_the_axis_and_c_is_the_detection_edge():
    t, plan, mv, pls, roi = _setup()
    sheet = _sheet(t, plan, mv, pls, roi)
    for row in sheet.fixed_rows:
        by = {p.kind: p for p in row.points}
        a, c = by["A"], by["C"]
        assert a.ppm >= TARGET - 1e-6
        assert abs(a.off_axis_deg) <= row.hfov_deg / 6 + 1e-9 or a.note        # near the axis, or it says it is not
        assert c.level_code >= ZONE_DETECT
        assert c.distance_m >= a.distance_m - 1e-9                             # Detection reaches farther than the target
        b = by["B"]
        assert b.ppm >= TARGET - 1e-6 and abs(b.off_axis_deg) > abs(a.off_axis_deg)
    one = _sheet(t, plan, mv, pls, roi, points_per_camera=1)
    assert [len(r.points) for r in one.fixed_rows] == [1, 1, 1]
    with pytest.raises(ValueError):
        _sheet(t, plan, mv, pls, roi, points_per_camera=7)


def test_a_camera_staring_into_a_wall_gets_the_no_target_point_row():
    t, plan, mv, pls, roi = _setup(wall=True, cams=[(1, 100, 100, 0.0), (2, 250, 250, 0.0)])
    sheet = _sheet(t, plan, mv, pls, roi)
    open_cam, blocked = sheet.fixed_rows
    assert open_cam.points and not open_cam.no_target_point
    assert blocked.no_target_point and not blocked.points
    assert "bulgu" in blocked.note and "px/m" in blocked.note
    assert sheet.summary()["cameras_without_target_point"] == 1
    assert sheet.summary()["no_target_cameras"] == [blocked.cam_id]
    rows = acc.table_rows(sheet)
    assert any(blocked.cam_id in r and "bulgu" in r[-1] for r in rows)         # still a row in CSV / XLSX


def test_pixel_bands_are_ppm_times_size_times_tolerance():
    t, plan, mv, pls, roi = _setup()
    for tol in (0.15, 0.3):
        sheet = _sheet(t, plan, mv, pls, roi, tolerance=tol, person_height_m=1.8)
        for p in sheet.points:
            assert p.px_person == pytest.approx(p.ppm * 1.8) and p.px_board == pytest.approx(p.ppm * 0.5)
            lo, hi = p.band(p.px_person, tol)
            assert (lo, hi) == (pytest.approx(p.ppm * 1.8 * (1 - tol)), pytest.approx(p.ppm * 1.8 * (1 + tol)))
            assert (p.px_plate is None) == (p.ppm < acc.PLATE_PPM)
            if p.px_plate is not None:
                assert p.px_plate == pytest.approx(p.ppm * 0.52)
    with pytest.raises(ValueError):
        _sheet(t, plan, mv, pls, roi, tolerance=0.0)


def test_latlon_is_geo_exports_frame_and_kml_has_the_points():
    t, plan, mv, pls, roi = _setup()
    t.lat_center, t.lon_center = 39.92, 32.85
    sheet = _sheet(t, plan, mv, pls, roi)
    frame = geo_export.GeoFrame(t)
    for row in sheet.rows:
        assert (row.lat, row.lon) == frame.latlon(row.x_m, row.y_m)
        for p in row.points:
            assert (p.lat, p.lon) == frame.latlon(p.x_m, p.y_m)
    kml = acc.build_kml(sheet, t)
    assert kml.count("<Placemark>") == len(sheet.points) + len(sheet.fixed_rows)
    p = sheet.points[0]
    assert f"{p.lon:.8f},{p.lat:.8f}" in kml and p.point_id in kml
    dxf = acc.build_dxf(sheet, t)
    assert dxf.count("\nPOINT\n") == len(sheet.points) and dxf.rstrip().endswith("EOF")
    assert f"{p.x_m:.4f}" in dxf                                              # local metres


def test_kml_is_refused_without_a_location_with_the_exports_message():
    t, plan, mv, pls, roi = _setup()                                           # no lat / lon: procedural-like
    sheet = _sheet(t, plan, mv, pls, roi)
    assert all(p.lat is None for p in sheet.points) and "KML üretilemez" in sheet.header["Konum"]
    with pytest.raises(ValueError) as mine:
        acc.build_kml(sheet, t)
    with pytest.raises(ValueError) as theirs:
        geo_export.build_kml(t, plan)
    assert str(mine.value) == str(theirs.value) and "coğrafi konumu yok" in str(mine.value)
    assert acc.build_dxf(sheet, t)                                             # DXF is fine in local metres


def test_csv_and_pdf_are_written_with_turkish_text(tmp_path):
    t, plan, mv, pls, roi = _setup()
    sheet = _sheet(t, plan, mv, pls, roi)
    p = tmp_path / "foy.csv"
    acc.write_acceptance(p, sheet, "csv")
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")                                     # UTF-8 BOM
    text = raw.decode("utf-8-sig")
    assert "Saha kabul föyü" in text and "Ölçülen px" in text and "Sonuç (G/K)" in text
    rows = list(csv.reader(text.splitlines(), delimiter=";"))
    table = rows[next(i for i, r in enumerate(rows) if r and r[0] == "Direk") + 1:]
    assert len(table) == len(sheet.points) and all(len(r) == len(acc._COLS) for r in table)
    pdf = tmp_path / "foy.pdf"
    acc.write_acceptance(pdf, sheet, "pdf")
    assert pdf.read_bytes().startswith(b"%PDF-")
    fitz = pytest.importorskip("fitz")
    doc = fitz.open(str(pdf))
    page = doc[0]
    assert page.rect.width > page.rect.height                                  # landscape
    body = "\n".join(pg.get_text() for pg in doc)
    assert "SAHA KABUL FÖYÜ" in body and "Ölçüm yöntemi" in body and sheet.points[0].point_id in body


def test_xlsx_is_written(tmp_path):
    pytest.importorskip("openpyxl")
    import openpyxl
    t, plan, mv, pls, roi = _setup()
    sheet = _sheet(t, plan, mv, pls, roi)
    p = tmp_path / "foy.xlsx"
    acc.write_acceptance(p, sheet, "xlsx")
    ws = openpyxl.load_workbook(p).active
    flat = [c.value for row in ws.iter_rows() for c in row if c.value]
    assert "Saha kabul föyü — kurulum sonrası ölçüm" in flat and sheet.points[0].point_id in flat


def test_sensitivity_marks_only_points_in_uncertain_cells():
    t, plan, mv, pls, roi = _setup()
    base = _sheet(t, plan, mv, pls, roi)
    assert not any(p.uncertain for p in base.points) and "Veri hassasiyeti" not in base.header
    target = base.points[1]
    grid = np.zeros(mv.dori_grid.shape, np.int8)
    grid[target.node] = 2                                                      # sensitivity.CLASS_UNCERTAIN
    other = base.points[2]
    grid[other.node] = 1 if other.node != target.node else 2                   # certain cell: not marked
    sheet = _sheet(t, plan, mv, pls, roi, sensitivity={"line": "Kesin %90 · veriye bağlı %10", "class_grid": grid})
    expected = {p.point_id for p in sheet.points if p.node == target.node}
    assert expected and {p.point_id for p in sheet.points if p.uncertain} == expected
    assert sheet.header["Veri hassasiyeti"].startswith("Kesin %90") and f"{len(expected)} nokta" in sheet.header["Veri hassasiyeti"]
    flagged = [r for r in acc.table_rows(sheet) if "▲" in r]
    assert len(flagged) == len(expected)
    # a result without its grid cannot mark: it says so instead of guessing
    plain = _sheet(t, plan, mv, pls, roi, sensitivity={"line": "Kesin %90"})
    assert not any(p.uncertain for p in plain.points) and "işaretlenemedi" in plain.header["Veri hassasiyeti"]


def test_ptz_rows_list_the_patrol_settings_and_a_preset_note():
    t, plan, mv, pls, roi = _setup()
    plan.ptz_cameras = [_placed(1, 100, 100, 30.0, t, tilt=-5.0)]
    plan.ptz_cameras[0].patrol_span_deg = 90.0
    sheet = _sheet(t, plan, mv, pls, roi)
    ptz = [r for r in sheet.rows if r.is_ptz]
    assert len(ptz) == 1 and not ptz[0].points and "presetlerde doğrulanır" in ptz[0].note and "90° sektör" in ptz[0].note
    assert sheet.summary()["ptz"] == 1 and len(sheet.fixed_rows) == 3


def test_header_carries_the_terrain_warning_and_assumptions():
    t, plan, mv, pls, roi = _setup()
    h = _sheet(t, plan, mv, pls, roi, weather="Berrak hava", visibility_km=40.0, target_height_m=1.8).header
    assert "ölçüm değil" in h["Arazi"] and "1,8 m insan" in h["Hedef boyu"] and "40 km" in h["Hava / görüş"]
    assert h["Tolerans"].startswith("±%15") and "3 sabit kamera" in h["Plan özeti"]
    t.is_measured = True
    assert _sheet(t, plan, mv, pls, roi).header["Arazi"].startswith("ÖLÇÜLMÜŞ DEM")
    assert math.isfinite(_sheet(t, plan, mv, pls, roi).points[0].ppm)
