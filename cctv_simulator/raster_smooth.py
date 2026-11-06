"""Display-only smoothing of class overlays (DORI zones, layer bands).

The analyses are cell grids; blitting them with nearest-neighbour scaling
turns every cell into a hard square, so a DORI fan looks like a staircase
and terrain shadows like Lego. This keeps the cell-level answer and only
draws its boundaries better:

* every colour of the painted overlay is one class (overlays use a handful);
* each class indicator is upsampled bilinearly (``supersample`` x), so a
  boundary becomes the 0.5 contour of a smooth surface instead of a cell
  edge -- diagonal staircases turn into straight-ish lines, corners round;
* a pixel takes the strongest coloured class; its alpha fades with the
  coloured share near the edge to background (anti-aliasing), while
  between two coloured classes the colour switches without a gap.

Where the 0.5 contour falls is exactly where nearest-neighbour put the
boundary, so areas read the same; only the stairs go. Analysis numbers
(areas, percentages) never come from this image.

More than ``max_classes`` colours (a continuous ramp) -> plain nearest
resize, nothing is invented. No Tk.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
from PIL import Image


def _smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def smooth_overlay(ov: np.ndarray, out_size: Tuple[int, int], *, supersample: int = 6,
                   max_side: int = 1600, max_classes: int = 12) -> Image.Image:
    """RGBA class overlay (rows, cols, 4 uint8, image orientation: row 0 =
    top) -> smoothed RGBA PIL image of ``out_size`` (width, height)."""
    ov = np.ascontiguousarray(ov, dtype=np.uint8)
    h, w = ov.shape[:2]
    out_w, out_h = max(int(out_size[0]), 1), max(int(out_size[1]), 1)
    if h == 0 or w == 0:
        return Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    keys = ov.view(np.uint32).reshape(h, w)
    alpha0 = ov[..., 3] == 0
    keys = np.where(alpha0, np.uint32(0), keys)              # every transparent pixel = background
    uniq = np.unique(keys)
    colored = uniq[uniq != 0]
    if colored.size == 0:
        return Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    if colored.size > max_classes:
        return Image.fromarray(ov, "RGBA").resize((out_w, out_h), Image.Resampling.NEAREST)

    # Only the part that has content: a camera fan covers a sliver of the
    # map, and smoothing the whole map-sized image for it cost ~70 ms.
    rows = np.flatnonzero((keys != 0).any(axis=1))
    cols = np.flatnonzero((keys != 0).any(axis=0))
    r0, r1 = max(int(rows[0]) - 2, 0), min(int(rows[-1]) + 3, h)
    c0, c1 = max(int(cols[0]) - 2, 0), min(int(cols[-1]) + 3, w)
    if (r1 - r0) * (c1 - c0) < 0.6 * h * w:
        fx, fy = out_w / w, out_h / h
        x0, y0 = int(round(c0 * fx)), int(round(r0 * fy))
        x1, y1 = max(int(round(c1 * fx)), x0 + 1), max(int(round(r1 * fy)), y0 + 1)
        part = smooth_overlay(ov[r0:r1, c0:c1], (x1 - x0, y1 - y0), supersample=supersample,
                              max_side=max_side, max_classes=max_classes)
        full = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
        full.paste(part, (x0, y0))
        return full

    # Work at supersample x the grid, but never beyond what the output needs
    # (and a size cap): beyond the output resolution smoothing adds nothing.
    s = max(1.0, min(float(supersample), max_side / max(h, w), max(out_w / w, out_h / h)))
    sw, sh = max(1, int(round(w * s))), max(1, int(round(h * s)))

    def up(mask: np.ndarray) -> np.ndarray:
        img = Image.fromarray(mask.astype(np.float32), mode="F")
        return np.asarray(img.resize((sw, sh), Image.Resampling.BILINEAR), dtype=np.float32)

    best_p = np.zeros((sh, sw), np.float32)
    best_k = np.zeros((sh, sw), np.int32)
    fg = np.zeros((sh, sw), np.float32)
    for i, key in enumerate(colored):
        p = up(keys == key)
        fg += p
        take = p > best_p
        best_p = np.where(take, p, best_p)
        best_k = np.where(take, i, best_k)
    colors = np.frombuffer(colored.astype(np.uint32).tobytes(), np.uint8).reshape(-1, 4)
    out = colors[best_k].copy()
    out[..., 3] = (out[..., 3].astype(np.float32) * _smoothstep(0.3, 0.7, np.clip(fg, 0.0, 1.0))
                   ).astype(np.uint8)
    img = Image.fromarray(out, "RGBA")
    if (sw, sh) != (out_w, out_h):
        img = img.resize((out_w, out_h), Image.Resampling.BILINEAR)
    return img


def clip_to_wedge(img: Image.Image, apex: Tuple[float, float], px_per_m: Tuple[float, float],
                  pan_deg: float, hfov_deg: float, reach_m: float) -> Image.Image:
    """Cut an RGBA overlay to the camera's horizontal field of view: a wedge
    from ``apex`` (image px) along ``pan_deg`` (clockwise from north, image
    up = north), ``hfov_deg`` wide, ``reach_m`` long. A cell straddling the
    cone edge is part in, part out; the grid shows it whole, the wedge draws
    the edge where the optics put it. Display only, like smooth_overlay."""
    if hfov_deg >= 359.0 or reach_m <= 0:
        return img
    from PIL import ImageChops, ImageDraw
    import math
    ax, ay = apex
    sx, sy = px_per_m
    a0, a1 = pan_deg - hfov_deg / 2.0, pan_deg + hfov_deg / 2.0
    n = max(8, int(abs(a1 - a0) * 2))
    pts = [(ax, ay)]
    for i in range(n + 1):
        a = math.radians(a0 + (a1 - a0) * i / n)
        pts.append((ax + reach_m * sx * math.sin(a), ay - reach_m * sy * math.cos(a)))
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).polygon(pts, fill=255)
    out = img.convert("RGBA")
    r, g, b, a = out.split()
    return Image.merge("RGBA", (r, g, b, ImageChops.multiply(a, mask)))
