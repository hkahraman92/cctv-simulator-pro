"""Headless CLI: load a project, compute, export — no Tk, no display."""
from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from cctv_simulator.__main__ import main
from cctv_simulator.models import DEFAULT_LEVELS, CameraConfig, PPMLevel, TargetPoint
from cctv_simulator.project_io import ProjectData, load_project, save_project


@pytest.fixture
def project_file(tmp_path):
    path = tmp_path / "plan.json"
    data = {
        "version": "2.0",
        "cameras": [
            asdict(CameraConfig(name="Giris", focal_min_mm=4, focal_max_mm=16, pole_height_m=6)),
            asdict(CameraConfig(name="Otopark", focal_min_mm=2.8, focal_max_mm=12)),
        ],
        "target_point": asdict(TargetPoint(active=True, name="Kapi", x_m=18.0)),
        "ppm_levels": [asdict(x) for x in DEFAULT_LEVELS],
        "lens_mode": "compare",
        "design_level": "Optik: TR Plaka (143 PPM)",
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_project_roundtrip(tmp_path):
    proj = ProjectData(cameras=[CameraConfig(name="A"), CameraConfig(name="B")])
    p = tmp_path / "rt.json"
    save_project(p, proj)
    back = load_project(p)
    assert [c.name for c in back.cameras] == ["A", "B"]
    assert len(back.ppm_levels) == len(DEFAULT_LEVELS)


def test_load_project_drops_unknown_keys(tmp_path):
    p = tmp_path / "future.json"
    p.write_text(json.dumps({"cameras": [{"name": "X", "some_future_field": 1}]}), encoding="utf-8")
    proj = load_project(p)
    assert proj.cameras[0].name == "X"


def test_load_project_rejects_empty(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"cameras": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_project(p)


def test_cli_json_output(project_file, capsys):
    rc = main(["--project", str(project_file), "--mode", "compare", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out["cameras"]) == {"Giris", "Otopark"}
    assert len(out["cameras"]["Giris"]) == 2  # compare -> min + max
    assert out["cameras"]["Giris"][0]["rows"]


def test_cli_exports_all_formats(project_file, tmp_path):
    outdir = tmp_path / "rapor"
    rc = main(["--project", str(project_file), "--export", "csv,xlsx,pdf", "--out", str(outdir)])
    assert rc == 0
    assert (outdir / "plan.csv").stat().st_size > 0
    assert (outdir / "plan.xlsx").stat().st_size > 0
    assert (outdir / "plan.pdf").stat().st_size > 0


def test_cli_rejects_unknown_format(project_file):
    with pytest.raises(SystemExit):
        main(["--project", str(project_file), "--export", "docx"])


@pytest.fixture
def viewshed_project_file(tmp_path):
    path = tmp_path / "saha.json"
    data = {
        "version": "2.0",
        "project_name": "Sınır KGYS",
        "cameras": [
            asdict(CameraConfig(name="Kule-1", focal_min_mm=8, focal_max_mm=48, pole_height_m=12)),
            asdict(CameraConfig(name="Kule-2", focal_min_mm=8, focal_max_mm=48, pole_height_m=12)),
        ],
        "ppm_levels": [asdict(x) for x in DEFAULT_LEVELS],
        "terrain": {
            "source": "procedural", "preset": "rolling_hills", "width_m": 2000.0,
            "weather": "Hafif pus", "viewshed_range_m": 800.0,
            "placements": [
                {"camera": "Kule-1", "x_m": 700, "y_m": 700, "mast_m": 12, "pan_deg": 45, "tilt_deg": -3},
                {"camera": "Kule-2", "x_m": 1300, "y_m": 1300, "mast_m": 12, "pan_deg": 225, "tilt_deg": -3},
            ],
        },
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_cli_viewshed_json(viewshed_project_file, capsys):
    rc = main(["--project", str(viewshed_project_file), "--viewshed", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    vs = out["viewshed"]
    assert vs["terrain_measured"] is False
    assert 0.0 <= vs["combined_coverage_pct"] <= 100.0
    assert {c["label"] for c in vs["cameras"]} == {"Kule-1", "Kule-2"}
    assert vs["pct_by_zone"]["detect"] >= vs["pct_by_zone"]["ident"] - 1e-9


def test_cli_viewshed_report_export(viewshed_project_file, tmp_path):
    outdir = tmp_path / "rapor"
    rc = main(["--project", str(viewshed_project_file), "--viewshed",
               "--export", "pdf,csv", "--out", str(outdir)])
    assert rc == 0
    assert (outdir / "saha-gorusalani.pdf").read_bytes()[:5] == b"%PDF-"
    csv_text = (outdir / "saha-gorusalani.csv").read_text(encoding="utf-8-sig")
    assert "ÇOKLU KAMERA BİRLEŞİK GÖRÜŞ ALANI" in csv_text
    assert "TEMSİLİ" in csv_text


def test_cli_viewshed_needs_placements(project_file):
    # project_file has no terrain.placements
    with pytest.raises(ValueError):
        main(["--project", str(project_file), "--viewshed", "--json"])


@pytest.fixture
def ptz_project_file(tmp_path):
    path = tmp_path / "ptz.json"
    data = {
        "version": "2.0", "project_name": "PTZ Gözetleme",
        "cameras": [asdict(CameraConfig(name="PTZ-1", focal_min_mm=6, focal_max_mm=180, pole_height_m=15))],
        "ppm_levels": [asdict(x) for x in DEFAULT_LEVELS],
        "terrain": {
            "source": "procedural", "preset": "rolling_hills", "width_m": 2000.0,
            "ptz": {
                "camera": "PTZ-1", "x_m": 1000, "y_m": 1000, "mast_m": 15,
                "range_m": 700, "slew_speed_deg_s": 120.0,
                "presets": [
                    {"name": "Kuzey kapı", "pan_deg": 0, "tilt_deg": -3, "lens_mode": "min", "dwell_s": 8},
                    {"name": "Doğu çit", "pan_deg": 90, "tilt_deg": -4, "lens_mode": "max", "dwell_s": 6},
                    {"name": "Güney yol", "pan_deg": 180, "tilt_deg": -3, "lens_mode": "min", "dwell_s": 10},
                ],
            },
        },
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_cli_ptz_json_and_report(ptz_project_file, tmp_path, capsys):
    outdir = tmp_path / "r"
    rc = main(["--project", str(ptz_project_file), "--ptz", "--json",
               "--export", "csv", "--out", str(outdir)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    pt = out["ptz_tour"]
    assert pt["presets"] == ["Kuzey kapı", "Doğu çit", "Güney yol"]
    assert pt["tour_period_s"] > 24.0                    # 24 s dwell + slew
    assert pt["worst_revisit_s"] >= pt["mean_revisit_s"]
    csv_text = (outdir / "ptz-gorusalani.csv").read_text(encoding="utf-8-sig")
    assert "PTZ PRESET TURU" in csv_text
    assert "revizit" in csv_text.lower()


def test_cli_ptz_needs_presets(project_file):
    with pytest.raises(ValueError):
        main(["--project", str(project_file), "--ptz", "--json"])
