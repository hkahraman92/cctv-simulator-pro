"""Draft technical-requirements spec writer: section content + .docx output."""
from __future__ import annotations

import pytest

from cctv_simulator.models import CameraConfig, DEFAULT_LEVELS
from cctv_simulator.calculations import calculate_for_camera
from cctv_simulator.spec_writer import build_requirement_sections, write_requirement_docx

docx = pytest.importorskip("docx")


@pytest.fixture
def cam():
    return CameraConfig(
        name="Kamera 1",
        model_name="Test Model",
        sensor_name='1/2.8"',
        resolution_name="2 MP (1080p - 1920x1080)",
        focal_min_mm=3.2,
        focal_max_mm=10.5,
        pole_height_m=4.0,
        tilt_deg=-12.0,
        ir_range_m=30.0,
        min_lux=0.01,
    )


@pytest.fixture
def results(cam):
    return [calculate_for_camera(cam, "min", DEFAULT_LEVELS), calculate_for_camera(cam, "max", DEFAULT_LEVELS)]


@pytest.fixture
def model():
    return {
        "overview": "Kompakt IR mini bullet kamera.",
        "lens_type": "Motorize varifocal",
        "wdr": "120dB",
        "onvif": "Profile S/G/T",
        "ip_rating": "IP67",
        "certificates": "CE, FCC, RoHS",
    }


def test_sections_skip_empty_groups(cam, model, results):
    secs = build_requirement_sections(cam, model, results, lang="tr")
    titles = [t for t, _ in secs]
    # populated groups show up
    assert "1. SENSÖR, LENS VE GENEL DONANIM" in titles
    assert "2. OPTİK PERFORMANS (EN 62676-4 DORI)" in titles
    assert "7. SERTİFİKASYON" in titles
    # nothing in the model dict touches section 4 -> omitted entirely
    assert "4. GÖRÜNTÜ İŞLEME VE ANALİTİK" not in titles


def test_dori_section_only_lists_achievable_tiers(cam, model, results):
    secs = dict(build_requirement_sections(cam, model, results, lang="tr"))
    dori_items = secs["2. OPTİK PERFORMANS (EN 62676-4 DORI)"]
    assert dori_items  # this setup achieves at least some tiers
    for item in dori_items:
        assert "en az" in item and "px/m" in item
    # every achievable (status == "Aktif") DORI row is represented once
    achievable = sum(
        1 for res in results for row in res.rows
        if row.status == "Aktif" and row.ppm in {12.5, 25.0, 62.5, 125.0, 250.0}
    )
    assert len(dori_items) == achievable


def test_lens_range_sentence_present(cam, model, results):
    secs = dict(build_requirement_sections(cam, model, results, lang="en"))
    sensor_items = secs["1. SENSOR, LENS AND GENERAL HARDWARE"]
    assert any("3.2" in item and "10.5" in item for item in sensor_items)


def test_thermal_camera_skips_na_ir_and_lux_fields(results):
    # BUGFIX: the camera library uses ir_range_m=0 / min_lux=0 as a "not
    # applicable" sentinel for passive thermal sensors (e.g. ASELSAN UMA
    # T10) -- these must not surface as "en az 0 m / 0 lux" requirements.
    # The raw library dict (passed as `model` here, as main_window does via
    # database.load_camera_library()) still carries those same-named 0.0
    # keys -- a naive model.get() fallback would resurrect them even after
    # the CameraConfig-side value is correctly skipped.
    thermal_cam = CameraConfig(
        name="Termal", model_name="ASELSAN UMA T10", sensor_name="LWIR 1280x1024 (12µm)",
        resolution_name="HD Termal (1280x1024)", focal_min_mm=35.0, focal_max_mm=350.0,
        ir_range_m=0.0, min_lux=0.0,
    )
    thermal_model = {"ir_range_m": 0.0, "min_lux": 0.0}
    thermal_results = [calculate_for_camera(thermal_cam, "min", DEFAULT_LEVELS)]
    secs = dict(build_requirement_sections(thermal_cam, thermal_model, thermal_results, lang="tr"))
    # both fields are N/A and nothing else feeds this section -> omitted entirely
    assert "3. GECE GÖRÜŞ VE AYDINLATMA" not in secs


def test_write_requirement_docx_tr_and_en(tmp_path, cam, model, results):
    path_tr = tmp_path / "spec-tr.docx"
    path_en = tmp_path / "spec-en.docx"
    write_requirement_docx(str(path_tr), cam, model, results, lang="tr", project_name="Test Proje")
    write_requirement_docx(str(path_en), cam, model, results, lang="en", project_name="Test Project")
    assert path_tr.exists() and path_en.exists()

    doc_tr = docx.Document(str(path_tr))
    text_tr = "\n".join(p.text for p in doc_tr.paragraphs)
    assert "TASLAK TEKNİK ŞARTNAME" in text_tr
    assert "Test Model" in text_tr

    doc_en = docx.Document(str(path_en))
    text_en = "\n".join(p.text for p in doc_en.paragraphs)
    assert "DRAFT TECHNICAL SPECIFICATION" in text_en


def test_missing_python_docx_raises_friendly_error(monkeypatch, cam, model, results, tmp_path):
    import cctv_simulator.spec_writer as sw
    monkeypatch.setattr(sw, "_DOCX_AVAILABLE", False)
    with pytest.raises(RuntimeError, match="python-docx"):
        sw.write_requirement_docx(str(tmp_path / "x.docx"), cam, model, results, lang="tr")
