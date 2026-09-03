"""Physics-backed spec review: DORI extraction, optics check, ambiguity, clauses."""
from __future__ import annotations

import pytest

from cctv_simulator.compliance import rule_based_compliance
from cctv_simulator.compliance_optics import (
    camera_from_model,
    evaluate_dori_requirement,
    extract_dori_requirements,
)
from cctv_simulator.compliance_standards import (
    clause_for,
    dori_ppm_for_task,
    find_ambiguities,
)

_LONG = {"model_name": "Uzun Lens", "sensor_name": '1/2.8"',
         "resolution_name": "4 MP (2K - 2688x1520)", "focal_min_mm": 8, "focal_max_mm": 60}
_SHORT = {"model_name": "Geniş Açı", "sensor_name": '1/2.8"',
          "resolution_name": "2 MP (1080p - 1920x1080)", "focal_min_mm": 2.8, "focal_max_mm": 4}


def test_dori_extraction_reads_task_and_distance():
    reqs = extract_dori_requirements("Kamera 30 m mesafede teşhis yapmalı; 25 metrede plaka okunmalı.")
    tasks = {(r["task"], r["distance_m"]) for r in reqs}
    assert ("identify", 30.0) in tasks
    assert any(t[0] in ("plaka", "anpr", "lpr") and t[1] == 25.0 for t in tasks)
    for r in reqs:
        assert r["required_ppm"] > 0 and r["spec_quote"]


def test_explicit_ppm_at_distance():
    reqs = extract_dori_requirements("Hedefte 125 PPM @ 40 m sağlanmalı.")
    assert reqs and reqs[0]["required_ppm"] == 125.0 and reqs[0]["distance_m"] == 40.0


def test_optics_check_passes_long_lens_fails_short_lens_for_identification():
    req = {"required_ppm": 250.0, "distance_m": 30.0, "requirement": "teşhis @ 30 m"}
    long_status, long_ev = evaluate_dori_requirement("Uzun", _LONG, req)
    short_status, _ = evaluate_dori_requirement("Kısa", _SHORT, req)
    assert long_status == "Uyumlu"
    assert short_status == "Uyumsuz"
    assert "px/m" in long_ev


def test_optics_check_needs_optics_data():
    status, ev = evaluate_dori_requirement("Yok", {"model_name": "x"},
                                           {"required_ppm": 125, "distance_m": 20})
    assert status == "Bulunamadı"


def test_camera_from_model_maps_fuzzy_keys():
    cam = camera_from_model({"model_name": "m", "sensor_name": "1/2.8 inch",
                             "resolution_name": "4MP", "focal_min_mm": 4, "focal_max_mm": 12})
    assert cam is not None and cam.focal_max_mm == 12
    assert camera_from_model({"model_name": "m"}) is None


def test_ambiguity_flags_unquantified_clauses_only():
    amb = find_ambiguities(
        "Çözünürlük en az 8 MP olmalı.\n- Yüksek dinamik aralık desteği bulunmalı.\n- WDR en az 120 dB."
    )
    terms = [a["term"] for a in amb]
    assert "yüksek" in terms          # the clause with no number
    assert all("8 MP" not in a["quote"] for a in amb)   # quantified clause not flagged


def test_task_and_clause_lookup():
    assert dori_ppm_for_task("teşhis yapılmalı") == ("identify", 250.0)
    assert dori_ppm_for_task("plaka okuma")[1] == 143.0
    assert dori_ppm_for_task("hiçbir şey") is None
    assert clause_for("dori")[0].startswith("EN 62676-4")


def test_rule_based_compliance_emits_optics_rows_and_clarifications():
    spec = ("Sabit kamera. 30 m mesafede yüz teşhisi. En az 8 MP çözünürlük. "
            "IR en az 40 m. Yeterli gece görüş performansı olmalı.")
    lib = {"Uzun Lens": _LONG, "Geniş Açı": _SHORT}
    res = rule_based_compliance(spec, lib)
    kinds = {m.get("evidence_kind") for m in res["matrix"]}
    assert "optik motor" in kinds
    assert res["clarification_questions"]     # "yeterli gece görüş" is vague
    for m in res["matrix"]:
        assert "confidence" in m and "standard_clause" in m
