"""Local training-data pipeline for the spec-review model."""
from __future__ import annotations

import json

import pytest

from cctv_simulator import training_log as TL
from cctv_simulator.compliance import OLLAMA_MODELS, build_compliance_prompt

_RESULT = {
    "profiles": [{"id": "P1", "name": "Dış Ortam"}],
    "requirements": [{"id": "P1-D1", "category": "dori", "requirement": "30 m teşhis",
                      "required_ppm": 250, "distance_m": 30, "weight": 5}],
    "matrix": [{"profile_name": "Dış Ortam", "requirement_id": "P1-D1",
                "requirement": "30 m teşhis", "camera_model": "A", "status": "Uyumsuz",
                "evidence": "yalnız 16 m", "evidence_kind": "optik motor"}],
    "camera_scores": [{"profile_name": "Dış Ortam", "camera_model": "A", "score": 40, "verdict": "Uyumsuz"}],
    "recommendation": "uzun lens gerekir",
}


@pytest.fixture
def logdir(tmp_path, monkeypatch):
    monkeypatch.setattr(TL, "_dir", lambda: tmp_path)
    return tmp_path


def test_log_and_stats(logdir):
    assert TL.log_analysis("30 m teşhis şartı", _RESULT, "rule")
    assert TL.log_override("30 m teşhis şartı", _RESULT["matrix"][0], "Uyumsuz", "Uyumlu", "teyit")
    st = TL.stats()
    assert st == {"records": 2, "analyses": 1, "overrides": 1, "unique_specs": 1}


def test_empty_inputs_do_not_log(logdir):
    assert TL.log_analysis("", _RESULT, "rule") is False
    assert TL.log_analysis("x", {}, "rule") is False
    assert TL.stats()["records"] == 0


def test_dataset_folds_override_into_target(logdir, tmp_path):
    spec = "Kamera 30 m mesafede teşhis yapmalı"
    TL.log_analysis(spec, _RESULT, "gemini")
    TL.log_override(spec, _RESULT["matrix"][0], "Uyumsuz", "Uyumlu", "tedarikçi yazılı teyit")
    out = tmp_path / "train.jsonl"
    n = TL.build_instruction_dataset(out)
    assert n == 1
    rec = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert [m["role"] for m in rec["messages"]] == ["system", "user", "assistant"]
    assert rec["meta"]["corrections_applied"] == 1
    target = json.loads(rec["messages"][2]["content"])
    row = target["matrix"][0]
    assert row["status"] == "Uyumlu"                    # override applied
    assert "insan" in row["evidence"] or "teyit" in row["evidence"]


def test_dataset_dedupes_by_spec(logdir, tmp_path):
    TL.log_analysis("aynı şartname metni", _RESULT, "rule")
    TL.log_analysis("aynı şartname metni", _RESULT, "gemini")
    out = tmp_path / "d.jsonl"
    assert TL.build_instruction_dataset(out) == 1


def test_prompt_has_fewshot_and_dori_guidance():
    p = build_compliance_prompt("30 m teşhis", {"A": {"model_name": "A"}})
    assert "ÖRNEK" in p
    assert "dori" in p and "required_ppm" in p and "spec_quote" in p
    assert OLLAMA_MODELS[0].startswith("qwen")
