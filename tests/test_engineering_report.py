"""Viewshed / coverage / perimeter -> ASELSAN engineering report (PDF + CSV)."""
from __future__ import annotations

import numpy as np
import pytest

from cctv_simulator.exporters import (
    export_engineering_report_csv,
    export_engineering_report_pdf,
)
from cctv_simulator.models import CameraConfig
from cctv_simulator.perimeter_planner import compute_coverage_grid, generate_perimeter_plan
from cctv_simulator.terrain_loader import TerrainData, generate_procedural_terrain
from cctv_simulator.viewshed_3d import calculate_3d_viewshed

CAM = CameraConfig(name="Sınır KGYS", sensor_name='1/2.8"',
                   resolution_name="4 MP (2K - 2688x1520)",
                   focal_min_mm=6.0, focal_max_mm=30.0)


@pytest.fixture
def analyses():
    terr = generate_procedural_terrain("ridge_and_valley", grid_size=64, cell_size_m=10.0)
    vs = calculate_3d_viewshed(terrain=terr, cam_x_m=300, cam_y_m=300, mast_height_m=10,
                               camera=CAM, pan_deg=30, tilt_deg=-3, max_range_m=600)
    square = [(80.0, 80.0), (400.0, 80.0), (400.0, 400.0), (80.0, 400.0)]
    plan = generate_perimeter_plan(terr, square, CAM, target_ppm=40.0)
    cov = compute_coverage_grid(plan, CAM, cell_m=8.0, terrain=terr)
    return terr, vs, cov, plan


def test_csv_report_has_all_three_sections(tmp_path, analyses):
    terr, vs, cov, plan = analyses
    out = tmp_path / "rapor.csv"
    export_engineering_report_csv(str(out), project_name="Çukurca Hattı", terrain=terr,
                                  camera=CAM, weather="Hafif pus",
                                  viewshed=vs, coverage=cov, perimeter=plan)
    text = out.read_text(encoding="utf-8-sig")
    assert "GÖRÜŞ ALANI (VIEWSHED)" in text
    assert "ÇOK KAMERALI BİRLEŞİK KAPSAMA" in text
    assert "ÇEVRE ÇİTİ PLANI" in text
    assert "Net görüş oranı" in text
    # one data row per placed camera + the header
    assert text.count("\n") > len(plan.placed_cameras)


def test_pdf_report_is_written_and_nonempty(tmp_path, analyses):
    terr, vs, cov, plan = analyses
    out = tmp_path / "rapor.pdf"
    export_engineering_report_pdf(str(out), project_name="Çukurca Hattı", terrain=terr,
                                  camera=CAM, weather="Berrak hava",
                                  viewshed=vs, coverage=cov, perimeter=plan)
    assert out.exists()
    head = out.read_bytes()[:5]
    assert head == b"%PDF-"
    assert out.stat().st_size > 3000


def test_report_flags_synthetic_terrain(tmp_path, analyses):
    terr, vs, cov, plan = analyses
    assert terr.is_measured is False
    out = tmp_path / "temsili.csv"
    export_engineering_report_csv(str(out), project_name="x", terrain=terr, camera=CAM,
                                  weather="", viewshed=vs, coverage=cov, perimeter=plan)
    text = out.read_text(encoding="utf-8-sig")
    assert "ölçüm değil" in text.lower() or "bağlayıcı değil" in text.lower()


def test_measured_dem_reported_as_binding(tmp_path, analyses):
    _, vs, cov, plan = analyses
    real = TerrainData(z_grid=np.zeros((40, 40), np.float32), cell_size_m=10.0,
                       is_measured=True, source_note="GeoTIFF: saha.tif")
    out = tmp_path / "olculmus.csv"
    export_engineering_report_csv(str(out), project_name="x", terrain=real, camera=CAM,
                                  weather="", viewshed=vs, coverage=cov, perimeter=plan)
    text = out.read_text(encoding="utf-8-sig")
    assert "ÖLÇÜLMÜŞ DEM" in text


def test_report_works_with_only_viewshed(tmp_path, analyses):
    terr, vs, _, _ = analyses
    out = tmp_path / "sadece-viewshed.pdf"
    export_engineering_report_pdf(str(out), project_name="", terrain=terr, camera=CAM,
                                  weather="", viewshed=vs, coverage=None, perimeter=None)
    assert out.exists() and out.read_bytes()[:5] == b"%PDF-"
