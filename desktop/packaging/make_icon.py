"""Render the 1024 px app icon (Lucide "cctv" on the console surface + a DORI
ramp arc), then `npx tauri icon` derives every size from it.

    py -3.13 desktop/packaging/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "app-icon.png"
FONT = ROOT / "cctv_simulator" / "fonts" / "lucide" / "lucide.ttf"


def main() -> None:
    from cctv_simulator.ui.icons import CODEPOINTS

    n = 1024
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((40, 40, n - 40, n - 40), radius=200, fill="#0f131c", outline="#262a33", width=12)
    # DORI coverage fan: brightest (identify) nearest the lens
    glyph = chr(CODEPOINTS["cctv"])
    font = ImageFont.truetype(str(FONT), 500)
    box = d.textbbox((0, 0), glyph, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    ox, oy = (n - w) / 2 - box[0] + 150, (n - h) / 2 - box[1] - 90
    # Lucide "cctv": the lens is the body's upper-left end; the fan opens from there.
    cx, cy = ox + box[0] + w * 0.16, oy + box[1] + h * 0.30
    for r, colour in ((620, "#1f5f86"), (470, "#2b8fb8"), (320, "#4cd7f6"), (180, "#acedff")):
        d.pieslice((cx - r, cy - r, cx + r, cy + r), 112, 158, fill=colour)
    d.rounded_rectangle((40, 40, n - 40, n - 40), radius=200, outline="#262a33", width=12)
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle((40, 40, n - 40, n - 40), radius=200, fill=255)
    img.putalpha(mask)
    d = ImageDraw.Draw(img)
    d.text((ox, oy), glyph, font=font, fill="#4edea3", stroke_width=10, stroke_fill="#0f131c")
    img.save(OUT)
    print(OUT)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT))
    main()
