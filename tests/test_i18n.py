"""Interface localisation: Turkish-key catalog with English fallback."""
from __future__ import annotations

import json

import pytest

from cctv_simulator import i18n


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    # isolate the preference file and reset in-memory state each test
    monkeypatch.setattr(i18n, "_pref_path", lambda: tmp_path / "ui_prefs.json")
    i18n._state["lang"] = "tr"
    i18n._catalogs.clear()
    monkeypatch.delenv("CCTV_LANG", raising=False)
    yield
    i18n._state["lang"] = "tr"
    i18n._catalogs.clear()


def test_turkish_is_identity():
    assert i18n.get_language() == "tr"
    assert i18n.t("Kaydet") == "Kaydet"
    assert i18n.t("Rastgele çevrilmemiş metin") == "Rastgele çevrilmemiş metin"


def test_english_translation_and_fallback():
    i18n.set_language("en")
    assert i18n.t("Kaydet") == "Save"
    # a key with no English entry falls back to the Turkish key
    assert i18n.t("Kesinlikle-katalogda-olmayan-anahtar") == "Kesinlikle-katalogda-olmayan-anahtar"


def test_format_arguments():
    i18n.set_language("en")
    out = i18n.t("Mühendislik raporu kaydedildi:\n{path}", path="/tmp/r.pdf")
    assert out == "Engineering report saved:\n/tmp/r.pdf"
    # broken format spec must not raise
    assert i18n.t("{missing}", other=1) == "{missing}"


def test_unknown_language_is_ignored():
    i18n.set_language("de")
    assert i18n.get_language() == "tr"


def test_preference_persists_and_reloads(tmp_path, monkeypatch):
    i18n.set_language("en")
    saved = json.loads((tmp_path / "ui_prefs.json").read_text(encoding="utf-8"))
    assert saved["language"] == "en"
    # a fresh process would call load_preferred_language()
    i18n._state["lang"] = "tr"
    assert i18n.load_preferred_language() == "en"


def test_env_var_overrides_saved_preference(monkeypatch):
    i18n.set_language("en")
    monkeypatch.setenv("CCTV_LANG", "tr")
    assert i18n.load_preferred_language() == "tr"


def test_english_catalog_is_valid_json_and_flat():
    from pathlib import Path
    p = Path(i18n.__file__).parent / "locale" / "en.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in data.items())
    assert data["Kaydet"] == "Save"
