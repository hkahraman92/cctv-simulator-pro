"""Design tokens: the one source of every UI colour and font.

Mirrors the "CCTV Simülatör Tasarım Sistemi" (tokens.json). Two themes share
the token names: ``dark`` ("Konsol", the default) and ``light`` ("Ofis").
Tk-free: modules read colours through :func:`tok` / :data:`PALETTE`, never
hex literals, so a theme switch changes every window at once.

Theme order: ``CCTV_THEME`` env -> saved preference (``ui_prefs.json``
``theme``) -> ``dark``. Like the language, a change takes effect on restart.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict

THEMES = ("dark", "light")
THEME_NAMES = {"dark": "Konsol (koyu)", "light": "Ofis (açık)"}
_DEFAULT = "dark"

TOKENS: Dict[str, Dict[str, str]] = {
    "dark": {
        "bg-0": "#0B1220", "bg-1": "#111A2B", "bg-2": "#18233A", "bg-3": "#22314F",
        "line": "#2A3954", "line-strong": "#5A6E93", "steel": "#8995A9",
        "ink": "#E6EAF0", "ink-2": "#AEB8C7", "ink-3": "#8692A6",
        "brand": "#002D62", "brand-hi": "#0B3F80", "on-brand": "#E6EAF0",
        "accent": "#4CB782", "accent-hi": "#63C795", "on-accent": "#06170E",
        "ok": "#4CB782", "ok-bg": "#0F2A1D",
        "warn": "#E0A23A", "warn-bg": "#33270F",
        "danger": "#F0644D", "danger-bg": "#3A1915",
        "info": "#7FB0E0", "info-bg": "#14263D",
        "dori-monitor": "#2C4A6E", "dori-detect": "#2F6E9E", "dori-observe": "#3A93C2",
        "dori-recognize": "#5DB5D9", "dori-identify": "#9AD6EC",
    },
    "light": {
        "bg-0": "#E9EDF2", "bg-1": "#FFFFFF", "bg-2": "#F4F6F9", "bg-3": "#DCE4EE",
        "line": "#D0D8E2", "line-strong": "#828FA0", "steel": "#5A6677",
        "ink": "#0E1726", "ink-2": "#384559", "ink-3": "#566276",
        "brand": "#002D62", "brand-hi": "#0B3F80", "on-brand": "#FFFFFF",
        "accent": "#1C7A51", "accent-hi": "#15633F", "on-accent": "#FFFFFF",
        "ok": "#1C7A51", "ok-bg": "#DDF1E6",
        "warn": "#8A5A00", "warn-bg": "#FBEFD3",
        "danger": "#B3261E", "danger-bg": "#FBE3E0",
        "info": "#1F5E98", "info-bg": "#E1ECF8",
        "dori-monitor": "#C9DDF0", "dori-detect": "#8DB9E0", "dori-observe": "#4F8FC7",
        "dori-recognize": "#2467A6", "dori-identify": "#0F3F73",
    },
}

# Drawing-surface roles (plan, profile, charts) derived from the tokens so
# canvas code names what it draws, not a colour.
_CANVAS_ROLES = {
    "surface": "bg-1", "surface-alt": "bg-2", "grid": "line", "grid-minor": "bg-2",
    "axis": "line-strong", "label": "ink-3", "text": "ink", "text-2": "ink-2",
    "ground": "steel", "structure": "ink-2", "camera": "accent", "selected": "ink",
    "target": "warn", "ok": "ok", "danger": "danger", "info": "info",
    "ray": "info", "ray-axis": "steel", "lane-a": "info", "lane-b": "dori-recognize",
}

FONT_FILES = (
    "barlow/Barlow-Regular.ttf", "barlow/Barlow-Medium.ttf", "barlow/Barlow-SemiBold.ttf",
    "barlowsemicondensed/BarlowSemiCondensed-Medium.ttf",
    "barlowsemicondensed/BarlowSemiCondensed-SemiBold.ttf",
    "ibmplexmono/IBMPlexMono-Regular.ttf", "ibmplexmono/IBMPlexMono-Medium.ttf",
    "ibmplexmono/IBMPlexMono-SemiBold.ttf",
)
FONT_UI_PREFERRED = "Barlow"
FONT_DISPLAY_PREFERRED = "Barlow Semi Condensed SemiBold"
FONT_MONO_PREFERRED = "IBM Plex Mono"
FONT_UI_FALLBACK = "Segoe UI"
FONT_MONO_FALLBACK = "Consolas"

_state = {"theme": _DEFAULT, "fonts": None}


def _pref_path() -> Path:
    from .config import user_data_dir
    return user_data_dir() / "ui_prefs.json"


def read_prefs(path: Path | None = None) -> dict:
    try:
        p = path or _pref_path()
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def write_pref(key: str, value, path: Path | None = None) -> None:
    """Merge one key into ui_prefs.json (other keys, e.g. language, survive)."""
    try:
        p = path or _pref_path()
        data = read_prefs(p)
        data[key] = value
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def load_preferred_theme() -> str:
    env = os.environ.get("CCTV_THEME", "").strip().lower()
    if env in THEMES:
        _state["theme"] = env
        return env
    saved = read_prefs().get("theme")
    if saved in THEMES:
        _state["theme"] = saved
    return _state["theme"]


def get_theme() -> str:
    return _state["theme"]


def set_theme(theme: str, *, persist: bool = True) -> None:
    if theme not in THEMES:
        raise ValueError(f"bilinmeyen tema: {theme}")
    _state["theme"] = theme
    if persist:
        write_pref("theme", theme)


def is_dark() -> bool:
    return _state["theme"] == "dark"


def tok(name: str, theme: str | None = None) -> str:
    """Colour token value (``"bg-1"``) or canvas role (``"grid"``) for a theme."""
    table = TOKENS[theme or _state["theme"]]
    if name in table:
        return table[name]
    return table[_CANVAS_ROLES[name]]


class _Palette:
    """``PALETTE["grid"]`` / ``PALETTE.ink`` read the active theme lazily."""

    def __getitem__(self, name: str) -> str:
        return tok(name)

    def __getattr__(self, name: str) -> str:
        try:
            return tok(name.replace("_", "-"))
        except KeyError as exc:
            raise AttributeError(name) from exc


PALETTE = _Palette()


def rgba(name: str, alpha: int, theme: str | None = None) -> tuple:
    """Token as an (r, g, b, a) tuple for PIL overlays."""
    h = tok(name, theme).lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def fonts_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "cctv_simulator" / "fonts"
    return Path(__file__).resolve().parent / "fonts"


def register_fonts() -> bool:
    """Load the bundled faces for this process only (no install). Windows."""
    if _state["fonts"] is not None:
        return _state["fonts"]
    ok = False
    if os.name == "nt":
        try:
            import ctypes
            add = ctypes.windll.gdi32.AddFontResourceExW
            fr_private = 0x10
            loaded = 0
            for rel in FONT_FILES:
                path = fonts_dir() / rel
                if path.is_file() and add(str(path), fr_private, 0):
                    loaded += 1
            ok = loaded == len(FONT_FILES)
        except (OSError, AttributeError):
            ok = False
    _state["fonts"] = ok
    return ok


def font_families() -> Dict[str, str]:
    """Resolved family names; fall back when the bundled faces did not load."""
    if register_fonts():
        return {"ui": FONT_UI_PREFERRED, "display": FONT_DISPLAY_PREFERRED,
                "mono": FONT_MONO_PREFERRED}
    return {"ui": FONT_UI_FALLBACK, "display": FONT_UI_FALLBACK, "mono": FONT_MONO_FALLBACK}
