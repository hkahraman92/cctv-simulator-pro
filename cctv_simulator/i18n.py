"""Lightweight interface localisation.

The Turkish source string *is* the key — wrapping a call site never breaks the
default UI:

    from .i18n import t, set_language
    btn.config(text=t("Kaydet"))                 # "Save" when lang == "en"
    lbl.config(text=t("{n} kamera yüklendi", n=5))

English (and any future) translations live in ``locale/<lang>.json`` as a flat
``{turkish: translated}`` map; a missing entry falls back to the Turkish key.
Turkish stays the code default, so ``locale/tr.json`` is optional/empty.

Language resolution order: ``CCTV_LANG`` env var → saved UI preference
(``user_data_dir()/ui_prefs.json``) → "tr". Changing the language persists the
choice; already-built Tk widgets are not re-rendered, so the UI applies it on
the next launch.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Tuple

_LOCALE_DIR = Path(__file__).resolve().parent / "locale"
_DEFAULT = "tr"
SUPPORTED: Tuple[str, ...] = ("tr", "en")
LANGUAGE_NAMES = {"tr": "Türkçe", "en": "English"}

_state = {"lang": _DEFAULT}
_catalogs: Dict[str, Dict[str, str]] = {}


def _catalog(lang: str) -> Dict[str, str]:
    if lang not in _catalogs:
        cat: Dict[str, str] = {}
        path = _LOCALE_DIR / f"{lang}.json"
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    cat = {str(k): str(v) for k, v in loaded.items()}
            except (OSError, ValueError):
                cat = {}
        _catalogs[lang] = cat
    return _catalogs[lang]


def available_languages() -> Tuple[str, ...]:
    return SUPPORTED


def get_language() -> str:
    return _state["lang"]


def set_language(lang: str, *, persist: bool = True) -> None:
    lang = (lang or "").lower()
    if lang not in SUPPORTED:
        return
    _state["lang"] = lang
    if persist:
        _save_preference(lang)


def t(key: str, /, **fmt) -> str:
    """Translate ``key`` into the active language, formatting with ``fmt``."""
    lang = _state["lang"]
    text = key if lang == _DEFAULT else _catalog(lang).get(key, key)
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def _pref_path() -> Path:
    from .config import user_data_dir
    return user_data_dir() / "ui_prefs.json"


def _save_preference(lang: str) -> None:
    try:
        p = _pref_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"language": lang}), encoding="utf-8")
    except OSError:
        pass


def load_preferred_language() -> str:
    """Resolve and activate the startup language. Call once at launch."""
    env = os.environ.get("CCTV_LANG", "").strip().lower()
    if env in SUPPORTED:
        _state["lang"] = env
        return env
    try:
        p = _pref_path()
        if p.is_file():
            saved = json.loads(p.read_text(encoding="utf-8")).get("language", _DEFAULT)
            if saved in SUPPORTED:
                _state["lang"] = saved
    except (OSError, ValueError):
        pass
    return _state["lang"]
