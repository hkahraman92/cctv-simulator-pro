"""Design tokens: both themes complete, legible, and preferences merge."""
import json

import pytest

from cctv_simulator import design_tokens as dt
from cctv_simulator import i18n


def _lum(hex_color):
    h = hex_color.lstrip("#")
    out = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = out
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_both_themes_define_the_same_tokens():
    assert set(dt.TOKENS["dark"]) == set(dt.TOKENS["light"])
    for theme in dt.THEMES:
        for role in dt._CANVAS_ROLES:
            assert dt.tok(role, theme).startswith("#")


# (text token, grounds its usage note promises, floor)
TEXT_PAIRS = [
    ("ink", ("bg-0", "bg-1", "bg-2", "bg-3"), 4.5),
    ("ink-2", ("bg-0", "bg-1", "bg-2", "bg-3"), 4.5),
    ("ink-3", ("bg-0", "bg-1", "bg-2"), 4.5),
    ("steel", ("bg-0", "bg-1", "bg-2"), 4.5),
    ("accent", ("bg-0", "bg-1", "bg-2"), 4.5),
    ("on-accent", ("accent", "accent-hi"), 4.5),
    ("on-brand", ("brand", "brand-hi"), 4.5),
    ("warn", ("bg-1", "bg-2", "warn-bg"), 4.5),
    ("danger", ("bg-1", "bg-2", "danger-bg"), 4.5),
    ("info", ("bg-1", "bg-2", "info-bg"), 4.5),
    ("ok", ("bg-1", "bg-2", "ok-bg"), 4.5),
    ("line-strong", ("bg-1", "bg-2"), 3.0),   # control borders
]


@pytest.mark.parametrize("theme", dt.THEMES)
@pytest.mark.parametrize("fg,grounds,floor", TEXT_PAIRS)
def test_text_is_legible_on_its_grounds(theme, fg, grounds, floor):
    for bg in grounds:
        ratio = contrast(dt.tok(fg, theme), dt.tok(bg, theme))
        assert ratio >= floor, f"{theme}: {fg} on {bg} = {ratio:.2f}"


def test_theme_preference_does_not_clobber_language(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("CCTV_THEME", raising=False)
    state = dict(dt._state)
    try:
        i18n._save_preference("en")
        dt.set_theme("light")
        i18n._save_preference("tr")
        data = json.loads(dt._pref_path().read_text(encoding="utf-8"))
        assert data == {"language": "tr", "theme": "light"}
        dt._state["theme"] = "dark"
        assert dt.load_preferred_theme() == "light"
        monkeypatch.setenv("CCTV_THEME", "dark")
        assert dt.load_preferred_theme() == "dark"
    finally:
        dt._state.update(state)


def test_unknown_theme_is_rejected():
    with pytest.raises(ValueError):
        dt.set_theme("neon", persist=False)


def test_palette_follows_the_active_theme():
    state = dict(dt._state)
    try:
        dt.set_theme("dark", persist=False)
        assert dt.PALETTE["surface"] == dt.TOKENS["dark"]["bg-1"]
        dt.set_theme("light", persist=False)
        assert dt.PALETTE["surface"] == dt.TOKENS["light"]["bg-1"]
        assert dt.PALETTE.on_accent == dt.TOKENS["light"]["on-accent"]
        assert dt.rgba("danger", 128) == (0xB3, 0x26, 0x1E, 128)
    finally:
        dt._state.update(state)


def test_bundled_fonts_ship_with_their_licenses():
    base = dt.fonts_dir()
    for rel in dt.FONT_FILES:
        assert (base / rel).is_file(), rel
    for family in ("barlow", "barlowsemicondensed", "ibmplexmono"):
        assert (base / family / "OFL.txt").is_file()
