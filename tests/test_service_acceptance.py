"""delivery.acceptance: the site acceptance sheet through the core service."""
from __future__ import annotations

import dataclasses
import io
import json
import zipfile

import pytest

from cctv_simulator.models import CameraConfig
from cctv_simulator.service import session
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import APP_ERROR, INVALID_PARAMS, Server

FIX = dataclasses.asdict(CameraConfig(name="Sabit", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                                      focal_min_mm=2.8, focal_max_mm=12.0))
FENCE = [[300, 300], [700, 300], [700, 700], [300, 700]]


def call(method, **params):
    out = io.StringIO()
    server = Server(METHODS, out=out)
    server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
    server.wait_idle()
    return next(json.loads(x) for x in out.getvalue().splitlines() if json.loads(x).get("id") == 1)


def ok(method, **params):
    r = call(method, **params)
    assert "result" in r, r.get("error")
    return r["result"]


@pytest.fixture
def planned():
    session.reset()
    tid = ok("terrain.procedural", preset="ridge_and_valley", grid=100, width_m=1000.0)["terrain_id"]
    ok("perimeter.plan", terrain_id=tid, camera=FIX, fence=FENCE, target_ppm=40, mast_m=8, target_height_m=1.8)
    return tid


def test_registered_and_needs_a_plan(tmp_path):
    assert "delivery.acceptance" in METHODS
    session.reset()
    tid = ok("terrain.procedural", preset="ridge_and_valley", grid=100, width_m=1000.0)["terrain_id"]
    err = call("delivery.acceptance", terrain_id=tid, path=str(tmp_path / "f.csv"), format="csv")["error"]
    assert err["code"] == APP_ERROR and err["message"] == "Önce bir çit planı oluşturun."


@pytest.mark.parametrize("params,code,fragment", [
    ({"format": "doc"}, INVALID_PARAMS, "'format'"),
    ({"format": "csv", "points_per_camera": 0}, INVALID_PARAMS, "'points_per_camera'"),
    ({"format": "csv", "points_per_camera": 7}, INVALID_PARAMS, "'points_per_camera'"),
    ({"format": "csv", "points_per_camera": 2.5}, INVALID_PARAMS, "'points_per_camera'"),
    ({"format": "csv", "tolerance": 0.0}, INVALID_PARAMS, "'tolerance'"),
    ({"format": "csv", "tolerance": 0.9}, INVALID_PARAMS, "'tolerance'"),
    ({"format": "csv", "path_ext": ".pdf"}, INVALID_PARAMS, "'path' uzantısı"),
])
def test_validation_errors_are_turkish(planned, tmp_path, params, code, fragment):
    params = dict(params)
    ext = params.pop("path_ext", f".{params['format']}")
    err = call("delivery.acceptance", terrain_id=planned, path=str(tmp_path / f"f{ext}"), **params)["error"]
    assert err["code"] == code and fragment in err["message"] and "olmalı" in err["message"]
    assert list(tmp_path.iterdir()) == []                       # nothing written, no temp left


def test_csv_pdf_dxf_are_written_and_the_summary_counts(planned, tmp_path):
    r = ok("delivery.acceptance", terrain_id=planned, path=str(tmp_path / "f.csv"), format="csv", points_per_camera=3)
    plan = session.SESSION.get(planned).results["plan"][0]
    assert r["cameras"] == len(plan.placed_cameras) and 0 < r["points"] <= 3 * r["cameras"]
    assert r["measured"] is False and r["has_latlon"] is False and r["uncertain_points"] == 0
    assert r["cameras_without_target_point"] == len(r["no_target_cameras"])
    text = (tmp_path / "f.csv").read_text(encoding="utf-8-sig")
    assert "TEMSİLİ arazi" in text and "ölçüm değil" in text
    ok("delivery.acceptance", terrain_id=planned, path=str(tmp_path / "f.pdf"), format="pdf", points_per_camera=2)
    assert (tmp_path / "f.pdf").read_bytes().startswith(b"%PDF-")
    ok("delivery.acceptance", terrain_id=planned, path=str(tmp_path / "f.dxf"), format="dxf")
    assert (tmp_path / "f.dxf").read_text(encoding="ascii").rstrip().endswith("EOF")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.csv", "f.dxf", "f.pdf"]      # temp files renamed away


def test_kml_is_refused_on_procedural_terrain_with_the_site_exports_message(planned, tmp_path):
    mine = call("delivery.acceptance", terrain_id=planned, path=str(tmp_path / "f.kml"), format="kml")["error"]
    theirs = call("export.site", terrain_id=planned, path=str(tmp_path / "a.kml"))["error"]
    assert mine["code"] == APP_ERROR and mine["message"] == theirs["message"] and "coğrafi konumu yok" in mine["message"]
    assert not (tmp_path / "f.kml").exists()
    t = session.SESSION.get(planned).bare
    t.lat_center, t.lon_center = 39.92, 32.85
    ok("delivery.acceptance", terrain_id=planned, path=str(tmp_path / "f.kml"), format="kml")
    assert "<Placemark>" in (tmp_path / "f.kml").read_text(encoding="utf-8")


def test_reuses_the_coverage_run_and_matches_computing_it(planned, tmp_path):
    entry = session.SESSION.get(planned)
    a = ok("delivery.acceptance", terrain_id=planned, path=str(tmp_path / "a.csv"), format="csv")   # computed
    assert "multi" not in entry.results                                  # the sheet is not the coverage report
    ok("perimeter.coverage", terrain_id=planned, max_px=200)
    b = ok("delivery.acceptance", terrain_id=planned, path=str(tmp_path / "b.csv"), format="csv")   # reused
    assert (tmp_path / "a.csv").read_bytes().split(b"\n", 3)[3] == (tmp_path / "b.csv").read_bytes().split(b"\n", 3)[3]
    assert {k: v for k, v in a.items() if k != "path"} == {k: v for k, v in b.items() if k != "path"}


def test_a_named_level_changes_the_target(planned, tmp_path):
    ok("delivery.acceptance", terrain_id=planned, path=str(tmp_path / "d.csv"), format="csv", level="observe")
    head = (tmp_path / "d.csv").read_text(encoding="utf-8-sig")
    assert "Gözlem — 62,5 px/m" in head


def test_points_in_uncertain_cells_are_counted_from_the_sensitivity_grid(planned, tmp_path):
    ok("perimeter.sensitivity", terrain_id=planned, n_runs=4, max_px=200)
    entry = session.SESSION.get(planned)
    grid = entry.results["sensitivity"]["class_grid"]
    r = ok("delivery.acceptance", terrain_id=planned, path=str(tmp_path / "s.csv"), format="csv")
    rows = [ln.split(";") for ln in (tmp_path / "s.csv").read_text(encoding="utf-8-sig").splitlines()]
    assert grid.shape == entry.terrain(True).z_grid.shape                # the engine's own grid
    flagged = sum(1 for row in rows if len(row) > 14 and row[14] == "▲")
    assert flagged == r["uncertain_points"]
    text = (tmp_path / "s.csv").read_text(encoding="utf-8-sig")
    assert "Veri hassasiyeti" in text


def test_offer_package_carries_the_sheet_when_coverage_ran_else_lists_it_missing(planned, tmp_path):
    first = ok("export.package", terrain_id=planned, path=str(tmp_path / "a.zip"), project_name="Tesis")
    with zipfile.ZipFile(tmp_path / "a.zip") as z:
        names, readme = set(z.namelist()), z.read("00-BENIOKU.txt").decode("utf-8-sig")
    assert not any(n.startswith("09-") for n in names)
    assert any("saha kabul föyü" in s for s in first["skipped"]) and "saha kabul föyü eklenmedi" in readme
    ok("perimeter.coverage", terrain_id=planned, max_px=200)
    second = ok("export.package", terrain_id=planned, path=str(tmp_path / "b.zip"), project_name="Tesis")
    with zipfile.ZipFile(tmp_path / "b.zip") as z:
        names = set(z.namelist())
        pdf, csv_bytes = z.read("09-saha-kabul-foyu.pdf"), z.read("09-saha-kabul-foyu.csv")
    assert {"09-saha-kabul-foyu.pdf", "09-saha-kabul-foyu.csv"} <= names and pdf.startswith(b"%PDF-")
    assert not any("saha kabul" in s for s in second["skipped"]) and b"Saha kabul" in csv_bytes
