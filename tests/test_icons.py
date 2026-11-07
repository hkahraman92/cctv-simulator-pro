"""Icon set: every icon renders from the bundled font; no emoji back in the UI."""
import pathlib
import re

import pytest
from PIL import Image, ImageDraw, ImageFont

from cctv_simulator.design_tokens import fonts_dir
from cctv_simulator.ui.icons import CODEPOINTS, EMOJI_ICON, EMOJI_MARK, plain, split_emoji

FONT = fonts_dir() / "lucide" / "lucide.ttf"
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿]")


def test_font_and_license_are_bundled():
    assert FONT.is_file()
    assert "ISC" in (FONT.parent / "LICENSE.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(CODEPOINTS))
def test_every_icon_has_a_glyph(name):
    font = ImageFont.truetype(str(FONT), 32)
    img = Image.new("L", (40, 40), 0)
    ImageDraw.Draw(img).text((4, 4), chr(CODEPOINTS[name]), font=font, fill=255)
    assert img.getbbox() is not None, name          # not a blank / missing glyph


def test_emoji_map_only_points_at_known_icons():
    assert set(EMOJI_ICON.values()) <= set(CODEPOINTS)


def test_split_and_plain():
    assert split_emoji("💾 Projeyi Kaydet") == ("save", "Projeyi Kaydet")
    assert split_emoji("⚠ Uyarı") == (None, "▲ Uyarı")
    assert split_emoji("Kaydet") == (None, "Kaydet")
    assert plain(" 🔄 PTZ — tur ") == " PTZ — tur "             # LabelFrame padding kept
    assert plain("Kör nokta ✓") == "Kör nokta ●"
    assert set(EMOJI_MARK.values()) <= set("▲●■")


def test_no_emoji_back_in_the_interface_source():
    root = pathlib.Path(__file__).resolve().parents[1] / "cctv_simulator"
    offenders = []
    for f in list(root.rglob("*.py")) + [root / "locale" / "en.json"]:
        if f.name == "icons.py":
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if EMOJI.search(line):
                offenders.append(f"{f.relative_to(root)}:{i}")
    assert not offenders, "emoji in UI source (use icon= / ● ▲ ■ ◆): " + ", ".join(offenders[:10])
