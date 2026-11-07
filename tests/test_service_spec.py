"""Core service: specification compliance paths, templates, overrides, statement."""
from __future__ import annotations

import io
import json
import sys

import pytest

from cctv_simulator import compliance, requirement_library, training_log
from cctv_simulator.database import load_camera_library
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import Server

SPEC = """Madde 1: Çevre çitinde 30 m mesafede teşhis sağlanmalıdır.
Madde 2: IR aydınlatma menzili en az 50 m olmalıdır.
Madde 3: Yüksek kaliteli görüntü sağlanmalıdır."""


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


@pytest.fixture(autouse=True)
def user_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(training_log, "_dir", lambda: tmp_path / "training")
    (tmp_path / "training").mkdir()
    monkeypatch.setattr(requirement_library, "_dir", lambda: tmp_path / "tpl")
    (tmp_path / "tpl").mkdir()


def test_compliance_core_has_no_tk(monkeypatch):
    for name in [m for m in sys.modules if m == "cctv_simulator.compliance"]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "tkinter", None)
    import importlib
    mod = importlib.import_module("cctv_simulator.compliance")
    assert hasattr(mod, "call_gemini") and hasattr(mod, "evaluate_template")


def test_rule_path_is_the_rule_engine_and_is_logged():
    r = ok("spec.analyze", text=SPEC, engine="rule")
    want = compliance.rule_based_compliance(SPEC, load_camera_library())
    assert r["source"] == "rule" and r["fallback_reason"] == ""
    assert json.loads(json.dumps(want, default=str)) == r["result"]
    assert any(q for q in r["result"]["clarification_questions"])          # "yüksek" flagged
    assert training_log.stats()["analyses"] == 1


def test_llm_paths_fall_back_and_say_why(monkeypatch):
    monkeypatch.setattr(compliance, "analyze_with_ollama", lambda *a, **k: None)
    r = ok("spec.analyze", text=SPEC, engine="ollama")
    assert r["source"] == "rule" and "Ollama" in r["fallback_reason"]
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = ok("spec.analyze", text=SPEC, engine="gemini")
    assert r["source"] == "rule" and "anahtar" in r["fallback_reason"]


def test_gemini_result_is_used_when_it_answers(monkeypatch):
    fake = {"profiles": [], "requirements": [], "matrix": [], "camera_scores": [], "recommendation": "x",
            "ambiguities": [], "clarification_questions": []}
    monkeypatch.setattr(compliance, "call_gemini", lambda req, **k: fake)
    r = ok("spec.analyze", text=SPEC, engine="gemini", api_key="k")
    assert r["source"] == "gemini" and r["result"]["recommendation"] == "x"


def test_template_roundtrip_reevaluates_with_the_optic_engine():
    res = ok("spec.analyze", text=SPEC, engine="rule")["result"]
    ok("spec.template_save", name="Şirket A", requirements=res["requirements"])
    assert ok("spec.templates")["templates"] == ["Şirket A"]
    r = ok("spec.template_run", name="Şirket A")["result"]
    dori_rows = [m for m in r["matrix"] if m["evidence_kind"] == "optik motor"]
    assert dori_rows and r["camera_scores"]
    ok("spec.template_delete", name="Şirket A")
    assert ok("spec.templates")["templates"] == []


def test_override_and_statement():
    res = ok("spec.analyze", text=SPEC, engine="rule")["result"]
    row = res["matrix"][0]
    assert ok("spec.override", text=SPEC, row=row, new_status="Uyumsuz", note="saha ölçümü")["logged"]
    assert training_log.stats()["overrides"] == 1
    st = ok("spec.statement", result=res, project_name="Deneme")
    assert "EN 62676-4" in st["markdown"] and "Deneme" in st["markdown"]


def test_read_text_file(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text(SPEC, encoding="utf-8")
    r = ok("spec.read_file", path=str(p))
    assert r["text"] == SPEC and r["pdf"] is False
    assert "bulunamadı" in call("spec.read_file", path=str(tmp_path / "yok.pdf"))["error"]["message"]
