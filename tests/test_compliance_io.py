"""Spec-review IO: requirement templates, compliance statement, PDF, ollama guard."""
from __future__ import annotations

import pytest

from cctv_simulator import requirement_library as RL
from cctv_simulator import spec_pdf
from cctv_simulator.compliance import analyze_with_ollama, ollama_available
from cctv_simulator.compliance_report import build_statement

_RESULT = {
    "profiles": [{"id": "P1", "name": "Dış Ortam", "description": ""}],
    "requirements": [
        {"id": "P1-D1", "profile_id": "P1", "profile_name": "Dış Ortam", "category": "dori",
         "requirement": "30 m'de teşhis (≥250 px/m)", "required_ppm": 250, "distance_m": 30,
         "weight": 5, "confidence": 0.9, "spec_quote": "30 m mesafede teşhis",
         "standard_clause": "EN 62676-4 §6.2"},
        {"id": "P1-R1", "profile_id": "P1", "profile_name": "Dış Ortam", "category": "ir",
         "requirement": "IR ≥ 40 m", "weight": 3, "confidence": 0.85, "standard_clause": "EN 62676-2 §5.3"},
    ],
    "matrix": [
        {"profile_id": "P1", "profile_name": "Dış Ortam", "requirement_id": "P1-D1",
         "requirement": "30 m'de teşhis (≥250 px/m)", "camera_model": "Uzun Lens",
         "status": "Uyumlu", "evidence": "8-60 mm: 120 m'de 250 px/m", "evidence_kind": "optik motor",
         "standard_clause": "EN 62676-4 §6.2", "confidence": 0.9, "spec_quote": "30 m mesafede teşhis"},
        {"profile_id": "P1", "profile_name": "Dış Ortam", "requirement_id": "P1-R1",
         "requirement": "IR ≥ 40 m", "camera_model": "Uzun Lens", "status": "Uyumlu",
         "evidence": "IR 50 m", "evidence_kind": "broşür", "standard_clause": "EN 62676-2 §5.3", "confidence": 0.85},
    ],
    "camera_scores": [{"profile_name": "Dış Ortam", "camera_model": "Uzun Lens",
                       "score": 92, "verdict": "Uyumlu", "notes": "8/8 ağırlık"}],
    "recommendation": "Dış Ortam: Uzun Lens (92/100)",
    "ambiguities": [{"term": "yeterli", "quote": "yeterli gece görüşü",
                     "clarification": "“yeterli” nicel değil..."}],
    "clarification_questions": ["“yeterli” nicel değil..."],
}


def test_build_statement_contains_requirements_clauses_and_rfi():
    md = build_statement(_RESULT, project_name="Test Sahası", bidder="ACME")
    assert "EN 62676-4 UYGUNLUK BEYANI" in md
    assert "Test Sahası" in md and "ACME" in md
    assert "P1-D1" in md and "EN 62676-4 §6.2" in md
    assert "optik" in md.lower()
    assert "AÇIKLAMA TALEPLERİ" in md or "RFI" in md
    assert "İmza" in md


def test_requirement_template_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(RL, "_dir", lambda: tmp_path)
    assert RL.list_templates() == []
    assert RL.save_template("Standart Dış Ortam", _RESULT["requirements"], {"k": "v"})
    assert RL.list_templates() == ["standart-d-ortam"]
    loaded = RL.load_template("Standart Dış Ortam")
    assert loaded and len(loaded["requirements"]) == 2
    assert loaded["requirements"][0]["required_ppm"] == 250
    assert RL.delete_template("Standart Dış Ortam")
    assert RL.list_templates() == []


def test_template_save_needs_a_name(tmp_path, monkeypatch):
    monkeypatch.setattr(RL, "_dir", lambda: tmp_path)
    assert RL.save_template("", _RESULT["requirements"]) is False


def test_ollama_guards_when_unreachable():
    # nothing listening on 11434 in CI -> both must fail soft, not raise
    assert ollama_available(timeout=0.3) in (True, False)
    assert analyze_with_ollama("30 m teşhis", {}, timeout=0.3) is None


def test_spec_pdf_returns_none_without_lib_or_file(tmp_path):
    if not spec_pdf.PDF_AVAILABLE:
        assert spec_pdf.extract_text(tmp_path / "nope.pdf") is None
    else:
        assert spec_pdf.extract_text(tmp_path / "does-not-exist.pdf") is None
