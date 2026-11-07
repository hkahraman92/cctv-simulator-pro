"""Icon set: Lucide (ISC, bundled in fonts/lucide) rendered for Tk.

The UI used emoji on buttons, tabs and labels: colourful, a different style
per glyph and per Windows version, and a few (🗺, ⛰) came out as tofu with
Segoe UI. Buttons now carry a monochrome Lucide icon in the theme colour
(``StyledButton(..., icon="save")``); every other text drops its emoji and
keeps the design-system status marks (● ▲ ■ ◆).

Glyphs are rendered with PIL from the bundled font (no system install), at
the DPI-scaled size, cached per (name, size, colour). ``master=`` is always
passed to PhotoImage (Kural 5).
"""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from ..design_tokens import fonts_dir, tok

# Lucide 0.460.0 code points (font/info.json) for the icons this app uses.
CODEPOINTS: Dict[str, int] = {
    "map": 0xE114, "satellite": 0xE44C, "mountain": 0xE231, "crosshair": 0xE0B0, "globe": 0xE0EB,
    "search": 0xE155, "zoom-in": 0xE1B6, "zoom-out": 0xE1B7, "eye": 0xE0BE, "folder-open": 0xE247,
    "trash-2": 0xE18E, "save": 0xE151, "check": 0xE070, "file-text": 0xE0D0, "pencil": 0xE1F9,
    "zap": 0xE1B4, "refresh-cw": 0xE149, "key-round": 0xE4A8, "camera": 0xE068, "map-pin": 0xE115,
    "ruler": 0xE14F, "folder": 0xE0DC, "sun": 0xE17C, "chart-column": 0xE2A3,
    "person-standing": 0xE21E, "lock": 0xE10F, "x": 0xE1B2, "lightbulb": 0xE1C2, "shield": 0xE15C,
    "plus": 0xE141, "minus": 0xE120, "hand": 0xE1D7, "contrast": 0xE0A1, "compass": 0xE09F,
    "telescope": 0xE5CA, "settings": 0xE158, "download": 0xE0B6, "gem": 0xE242, "octagon-x": 0xE12C,
    "brain": 0xE3CA, "car": 0xE1D5, "moon": 0xE122, "flame": 0xE0D7, "circle": 0xE07A,
    "circle-check": 0xE226, "upload": 0xE19E, "file-spreadsheet": 0xE327, "image": 0xE0F9,
    "play": 0xE140, "square": 0xE16B, "layers": 0xE104, "radar": 0xE49C, "video": 0xE1A5,
    "cctv": 0xE582, "building-2": 0xE290, "trees": 0xE2F5, "triangle-alert": 0xE193, "info": 0xE0FE,
    "chart-line": 0xE2A5, "route": 0xE543, "fence": 0xE587, "target": 0xE184,
    "sliders-horizontal": 0xE29A, "locate": 0xE1DA,
}

# Emoji the source used -> icon (buttons) / plain mark (everything else).
EMOJI_ICON: Dict[str, str] = {
    "🗺": "map", "🛰": "satellite", "⛰": "mountain", "🎯": "crosshair", "🌐": "globe",
    "🔍": "search", "👁": "eye", "📂": "folder-open", "🗑": "trash-2", "💾": "save", "📄": "file-text",
    "✏": "pencil", "✎": "pencil", "⚡": "zap", "🔄": "refresh-cw", "🔑": "key-round", "📷": "camera",
    "📸": "camera", "📹": "video", "📍": "map-pin", "📐": "ruler", "📁": "folder", "☀": "sun",
    "📊": "chart-column", "🧍": "person-standing", "🔒": "lock", "💡": "lightbulb", "🛡": "shield",
    "➕": "plus", "➖": "minus", "✋": "hand", "🌓": "contrast", "🧭": "compass", "🔭": "telescope",
    "⚙": "settings", "⬇": "download", "💎": "gem", "🛑": "octagon-x", "🧠": "brain", "🚗": "car",
    "🌙": "moon", "🔥": "flame", "🌈": "layers", "⚫": "circle", "✅": "circle-check",
}
# Status emoji become the design-system marks, never a picture.
EMOJI_MARK: Dict[str, str] = {
    "⚠": "▲", "✓": "●", "✅": "●", "✗": "■", "✖": "■", "🟢": "●", "🟡": "▲", "🟠": "▲", "🔴": "■",
}
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿]️?")

_FONT_CACHE: Dict[int, object] = {}
_IMG_CACHE: Dict[Tuple[str, int, str, int], object] = {}


def split_emoji(text: str) -> Tuple[Optional[str], str]:
    """'💾 Projeyi Kaydet' -> ('save', 'Projeyi Kaydet'). Status emoji become
    marks ('⚠ Uyarı' -> (None, '▲ Uyarı')); other emoji are dropped."""
    s = str(text)
    lead = s.lstrip()
    icon = None
    m = _EMOJI_RE.match(lead)
    if m:
        ch = m.group(0)[0]
        icon = EMOJI_ICON.get(ch) if ch not in EMOJI_MARK else None
    return icon, plain(s)


def plain(text: str) -> str:
    """Text with every emoji replaced by its mark or removed; surrounding
    padding kept (LabelFrame titles use " … "), inner double spaces folded."""
    s = str(text)
    if not _EMOJI_RE.search(s):
        return s
    lead = s[:len(s) - len(s.lstrip())]
    trail = s[len(s.rstrip()):]
    core = _EMOJI_RE.sub(lambda m: EMOJI_MARK.get(m.group(0)[0], ""), s.strip())
    core = core.replace("️", "").replace("‍", "")
    core = re.sub(r" {2,}", " ", core).strip()
    return lead + core + trail


def _font(size_px: int):
    f = _FONT_CACHE.get(size_px)
    if f is None:
        from PIL import ImageFont
        f = _FONT_CACHE[size_px] = ImageFont.truetype(str(fonts_dir() / "lucide" / "lucide.ttf"), size_px)
    return f


def icon_image(name: str, master, *, size: Optional[int] = None, color: Optional[str] = None):
    """PhotoImage of a Lucide glyph, or None if unknown / unrenderable."""
    cp = CODEPOINTS.get(name)
    if cp is None:
        return None
    try:
        from PIL import Image, ImageDraw, ImageTk

        from ..theme import dpi_scale
        px = size or max(12, int(round(15 * dpi_scale(master))))
        colour = color or tok("ink-2")
        key = (name, px, colour, id(master.winfo_toplevel()))
        hit = _IMG_CACHE.get(key)
        if hit is not None:
            return hit
        img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        font = _font(px)
        bbox = draw.textbbox((0, 0), chr(cp), font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((px - w) / 2 - bbox[0], (px - h) / 2 - bbox[1]), chr(cp), font=font, fill=colour)
        photo = ImageTk.PhotoImage(img, master=master)    # Kural 5
        _IMG_CACHE[key] = photo
        return photo
    except Exception:
        return None
