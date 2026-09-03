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
