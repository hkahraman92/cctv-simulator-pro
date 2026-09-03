"""Camera-eye frame renderer for the 3D view.

Renders the scene into a PIL image at the camera's true field of view — a person
at 60 m is genuinely ~15 px tall and unresolvable, no faking needed. Then:

* a subtle Gaussian blur for the measured lens MTF (``k`` =
  ``CameraConfig.effective_px_ratio`` from cctv_iq);
* a palette pass (day / IR night with gain-falloff + noise / thermal LUT);
* an optional digital-zoom inset that crops to the target and upsamples, so the
  sensor's pixel budget at range (``ppm``) becomes visible — this is what an
  operator sees when they zoom in.

No Tk. Pure function ``render_camera_frame`` -> RGB ``PIL.Image``. The window
blits it and paints the crisp HUD / grid / distance labels on top.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .perspective_3d import Perspective3DEngine, Point3D

_SKY_TOP = (104, 158, 206)
_SKY_HORIZON = (198, 216, 230)
_GROUND_NEAR = (146, 152, 144)
_GROUND_FAR = (190, 196, 196)

_TARGET_H = {"human": 1.8, "vehicle": 1.55, "chart": 1.0}

# Front-view human silhouette: (dx, dy) as fractions of pixel HEIGHT,
# dy = 0 at head-top, 1 at the feet. Closed polygon, arms at the sides.
_HUMAN = [
    (-0.05, 0.13), (-0.13, 0.15), (-0.15, 0.17), (-0.15, 0.42), (-0.09, 0.44),
    (-0.09, 0.52), (-0.085, 1.00), (-0.02, 1.00), (-0.015, 0.55),
    (0.015, 0.55), (0.02, 1.00), (0.085, 1.00), (0.09, 0.52), (0.09, 0.44),
    (0.15, 0.42), (0.15, 0.17), (0.13, 0.15), (0.05, 0.13),
]


def _vgrad(w: int, h: int, top, bottom) -> Image.Image:
    t = np.linspace(0.0, 1.0, max(h, 1))[:, None]
    arr = np.empty((h, w, 3), np.uint8)
    for i in range(3):
        arr[:, :, i] = np.clip(top[i] + (bottom[i] - top[i]) * t, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def _sky_ground(w: int, h: int, horizon_y: float) -> Image.Image:
    hy = int(max(0, min(h, horizon_y)))
    img = Image.new("RGB", (w, h), _GROUND_FAR)
    if hy > 0:
        img.paste(_vgrad(w, hy, _SKY_TOP, _SKY_HORIZON), (0, 0))
    if hy < h:
        img.paste(_vgrad(w, h - hy, _SKY_HORIZON, _GROUND_NEAR), (0, hy))
    if 0 < hy < h:
        ImageDraw.Draw(img, "RGBA").rectangle([0, hy - 2, w, hy + 2], fill=(255, 255, 255, 60))
    return img


def _p(engine, x, y, z):
    return engine.project_point(Point3D(x, y, z))


def _draw_human(base, heat, engine, x_m, y_m, rgb, alpha=255):
    feet = _p(engine, x_m, y_m, 0.0)
    head = _p(engine, x_m, y_m, 1.8)
    if not feet.visible or feet.depth <= 0.05:
        return
    top = min(feet.v, head.v)
    hgt = abs(feet.v - head.v)
    if hgt < 1.5:
        return
    cx = feet.u
    poly = [(cx + dx * hgt, top + fy * hgt) for dx, fy in _HUMAN]
    hr = hgt * 0.05
    hbox = [cx - hr * 1.05, top - hr * 0.15, cx + hr * 1.05, top + hr * 2.1]

    d = ImageDraw.Draw(base, "RGBA")
    d.polygon(poly, fill=(*rgb, alpha), outline=(15, 15, 20, min(alpha, 200)))
    d.ellipse(hbox, fill=(*rgb, alpha), outline=(15, 15, 20, min(alpha, 200)))
    if hgt > 60 and alpha > 200:  # only bother with a face when it could be seen
        d.ellipse([cx - hr * 0.7, top + hr * 0.3, cx + hr * 0.7, top + hr * 1.7],
                  fill=(min(rgb[0] + 60, 255), min(rgb[1] + 45, 255), min(rgb[2] + 30, 255), alpha))

    h = ImageDraw.Draw(heat, "L")
    h.polygon(poly, fill=230)
    h.ellipse(hbox, fill=255)


def _draw_vehicle(base, heat, engine, x_m, y_m, ppm, alpha=255):
    fl = _p(engine, x_m - 1.1, y_m, 0.0)
    fr = _p(engine, x_m + 1.1, y_m, 0.0)
    hood_l = _p(engine, x_m - 1.1, y_m, 0.75)
    hood_r = _p(engine, x_m + 1.1, y_m, 0.75)
    roof_l = _p(engine, x_m - 0.8, y_m, 1.5)
    roof_r = _p(engine, x_m + 0.8, y_m, 1.5)
    plate = _p(engine, x_m, y_m, 0.42)
    if not fl.visible or fl.depth <= 0.05:
        return

    d = ImageDraw.Draw(base, "RGBA")
    body = [(fl.u, fl.v), (hood_l.u, hood_l.v), (roof_l.u, roof_l.v),
            (roof_r.u, roof_r.v), (hood_r.u, hood_r.v), (fr.u, fr.v)]
    d.polygon(body, fill=(150, 42, 42, alpha), outline=(12, 12, 12, min(alpha, 220)))
    d.polygon([(roof_l.u, roof_l.v), (roof_r.u, roof_r.v), (hood_r.u, hood_r.v), (hood_l.u, hood_l.v)],
              fill=(28, 38, 48, alpha))
    wr = max(abs(fr.u - fl.u) * 0.085, 1.5)
    for wx in (fl.u + wr * 1.4, fr.u - wr * 1.4):
        d.ellipse([wx - wr, fl.v - wr, wx + wr, fl.v + wr], fill=(8, 8, 8, alpha))
    pw = max(abs(fr.u - fl.u) * 0.28, 5.0)
    ph = max(pw * 0.24, 2.5)
    if alpha > 200:
        d.rectangle([plate.u - pw / 2, plate.v - ph / 2, plate.u + pw / 2, plate.v + ph / 2],
                    fill=(240, 240, 240, 255), outline=(15, 15, 15, 255))
        d.rectangle([plate.u - pw / 2, plate.v - ph / 2, plate.u - pw / 2 + pw * 0.15, plate.v + ph / 2],
                    fill=(20, 85, 185, 255))
        if ppm >= 145.0 and pw >= 34:
            d.text((plate.u - pw * 0.28, plate.v - ph * 0.45), "34 CE 26", fill=(10, 10, 10, 255))

    hh = ImageDraw.Draw(heat, "L")
    hh.polygon(body, fill=110)
    ex, ey = (fl.u + fr.u) / 2.0, (hood_l.v + fl.v) / 2.0
    hh.ellipse([ex - pw * 0.7, ey - ph, ex + pw * 0.7, ey + ph], fill=255)
    for wx in (fl.u + wr * 1.4, fr.u - wr * 1.4):
        hh.ellipse([wx - wr, fl.v - wr, wx + wr, fl.v + wr], fill=205)


def _draw_chart(base, heat, engine, x_m, y_m):
    c = _p(engine, x_m, y_m, 1.0)
    if not c.visible or c.depth <= 0.05:
        return
    size = max((engine.viewport_h / c.depth) * 0.8, 6.0)
    d = ImageDraw.Draw(base, "RGBA")
    box = [c.u - size / 2, c.v - size / 2, c.u + size / 2, c.v + size / 2]
    d.rectangle(box, fill=(244, 244, 244, 255), outline=(12, 12, 12, 255))
    for rf in (0.82, 0.6, 0.38, 0.18):
        r = size * 0.5 * rf
        d.ellipse([c.u - r, c.v - r, c.u + r, c.v + r], outline=(22, 22, 22, 255), width=max(int(size * 0.02), 1))
    d.polygon([(c.u, c.v - size / 2), (c.u + size / 2, c.v - size / 2), (c.u, c.v)], fill=(12, 12, 12, 255))
    ImageDraw.Draw(heat, "L").rectangle(box, fill=95)


def _draw_target(base, heat, engine, ttype, x_m, y_m, ppm, alpha=255):
    if ttype == "vehicle":
        _draw_vehicle(base, heat, engine, x_m, y_m, ppm, alpha)
    elif ttype == "chart":
        _draw_chart(base, heat, engine, x_m, y_m)
    else:
        _draw_human(base, heat, engine, x_m, y_m, (44, 82, 150), alpha)


def _paint_dori(base, dori_polys):
    d = ImageDraw.Draw(base, "RGBA")
    for poly in dori_polys:
        pts = poly.get("points", [])
        if len(pts) != 4 or any((not p.visible or p.depth <= 0.05) for p in pts):
            continue
        col = poly.get("color", "#888888").lstrip("#")
        try:
            rgb = tuple(int(col[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            rgb = (136, 136, 136)
        xy = [(p.u, p.v) for p in pts]
        d.polygon(xy, fill=(*rgb, 90), outline=(*rgb, 200))


def _degrade(img: Image.Image, screen_px: float, true_px: float) -> Image.Image:
    """Downsample so ``screen_px`` of target height carry only ``true_px`` of
    real sensor detail. BOX down is a true low-pass; NEAREST up keeps the pixel
    grid visible; a block-sized blur then softens it the way real low-res
    footage looks (and keeps it from aliasing into noise)."""
    w, h = img.size
    if screen_px < 2 or true_px >= screen_px:
        return img
    s = max(true_px / screen_px, 0.01)
    sw, sh = max(int(w * s), 2), max(int(h * s), 2)
    block = h / sh
    out = img.resize((sw, sh), Image.Resampling.BOX).resize((w, h), Image.Resampling.NEAREST)
    return out.filter(ImageFilter.GaussianBlur(radius=min(max(block * 0.42, 0.4), 6.0)))


_VIGNETTE_CACHE: dict = {}


def _vignette_mask(w: int, h: int, strength: float) -> np.ndarray:
    key = (w, h, round(strength, 2))
    m = _VIGNETTE_CACHE.get(key)
    if m is None:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / math.sqrt(2)
        m = np.clip(1.0 - strength * r ** 2.2, 0.25, 1.0)[:, :, None]
        if len(_VIGNETTE_CACHE) > 24:
            _VIGNETTE_CACHE.clear()
        _VIGNETTE_CACHE[key] = m
    return m


def _vignette(img, strength):
    if strength <= 0.01:
        return img
    w, h = img.size
    arr = np.asarray(img, np.float32) * _vignette_mask(w, h, strength)
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGB")


_IRONBOW_STOPS = np.array(
    [(0, 0, 8), (30, 0, 70), (85, 0, 120), (150, 22, 110),
     (210, 62, 60), (245, 130, 22), (255, 200, 45), (255, 255, 225)], np.float32)
# 256-entry lookup so the palette is a table read, not per-pixel interpolation.
_IRONBOW_LUT = np.stack([
    np.interp(np.linspace(0, len(_IRONBOW_STOPS) - 1, 256),
              np.arange(len(_IRONBOW_STOPS)), _IRONBOW_STOPS[:, c])
    for c in range(3)
], axis=-1).astype(np.uint8)


_NOISE_CACHE: dict = {}


def _noise(w: int, h: int) -> np.ndarray:
    m = _NOISE_CACHE.get((w, h))
    if m is None:
        m = np.random.default_rng(7).normal(0, 9, (h, w)).astype(np.float32)
        if len(_NOISE_CACHE) > 8:
            _NOISE_CACHE.clear()
        _NOISE_CACHE[(w, h)] = m
    return m


def _apply_palette(base, heat, palette, target_dist, ir_range_m, vig=True):
    if palette == "day":
        return _vignette(base, 0.32) if vig else base

    if palette == "ir":
        luma = np.asarray(base.convert("L"), np.float32)
        gain = 1.05
        if ir_range_m > 1.0:
            gain = float(np.clip(1.1 - 0.42 * (target_dist / ir_range_m), 0.22, 1.1))
        g = np.clip(luma * gain + _noise(luma.shape[1], luma.shape[0]), 0, 255)
        out = np.stack([g * 0.10, g, g * 0.16], axis=-1)
        out[::3] *= 0.85
        im = Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGB")
        return _vignette(im, 0.45) if vig else im

    hf = np.asarray(Image.fromarray(np.asarray(heat, np.uint8)).filter(ImageFilter.GaussianBlur(1.5)), np.float32)
    grad = np.linspace(66, 26, hf.shape[0])[:, None]
    hf = np.maximum(hf, grad).clip(0, 255).astype(np.uint8)

    if palette == "thermal_black":
        return Image.fromarray(np.repeat((255 - hf)[:, :, None], 3, 2), "RGB")
    if palette == "thermal_ironbow":
        return Image.fromarray(_IRONBOW_LUT[hf], "RGB")
    return Image.fromarray(np.repeat(hf[:, :, None], 3, 2), "RGB")


def _zoom_inset(scene, heat, engine, x_m, y_m, th, ppm, k, palette, target_dist, ir_range_m,
                iw, ih):
    feet = _p(engine, x_m, y_m, 0.0)
    head = _p(engine, x_m, y_m, th)
    if not (feet.visible and head.visible) or feet.depth <= 0.05:
        return None, 1.0
    native = max(abs(feet.v - head.v), 1.0)
    W, H = scene.size
    zoom = float(np.clip(0.72 * ih / native, 1.5, 30.0))
    cw, ch = W / zoom, H / zoom
    cx = min(max((feet.u + head.u) / 2 - cw / 2, 0), max(W - cw, 0))
    cy = min(max((feet.v + head.v) / 2 - ch / 2, 0), max(H - ch, 0))
    box = (cx, cy, cx + cw, cy + ch)
    ins = scene.resize((iw, ih), Image.Resampling.BICUBIC, box=box)
    ins_heat = heat.resize((iw, ih), Image.Resampling.BICUBIC, box=box)
    ins = _degrade(ins, min(native * zoom, ih), ppm * th * max(k, 0.05))
    ins_heat = _degrade(ins_heat.convert("RGB"), min(native * zoom, ih), ppm * th * max(k, 0.05)).convert("L")
    return _apply_palette(ins, ins_heat, palette, target_dist, ir_range_m), zoom


def render_camera_frame(
    engine: Perspective3DEngine,
    *,
    w: int,
    h: int,
    target_type: str = "human",
    target_dist: float = 15.0,
    lateral_offset: float = 0.0,
    ppm: float = 100.0,
    palette: str = "day",
    k: float = 1.0,
    show_dori: bool = True,
    dori_polys: Optional[Sequence[dict]] = None,
    ir_range_m: float = 0.0,
    zoom_inset: bool = True,
    fast: bool = False,
) -> Tuple[Image.Image, float]:
    """Returns ``(frame, inset_zoom)``. ``inset_zoom`` is 1.0 when no inset was drawn.

    The frame is rendered at reduced resolution (it ends up soft / pixelated
    anyway) and scaled up, then the engine viewport is restored to (w, h) so the
    caller's crisp overlays line up. ``fast=True`` (during a drag) drops the
    working resolution, the vignette and the zoom inset.
    """
    out_w, out_h = max(int(w), 16), max(int(h), 16)
    scale = min(1.0, (430.0 if fast else 720.0) / out_w)
    w, h = max(round(out_w * scale), 16), max(round(out_h * scale), 16)
    if fast:
        zoom_inset = False
    engine.set_viewport_size(w, h)
    th = _TARGET_H.get(target_type, 1.8)

    base = _sky_ground(w, h, engine.get_horizon_y())
    heat = Image.new("L", (w, h), 0)
    if show_dori and dori_polys:
        _paint_dori(base, dori_polys)
    _draw_target(base, heat, engine, target_type, lateral_offset, target_dist, ppm, alpha=255)

    main = base
    if k < 0.985:
        main = main.filter(ImageFilter.GaussianBlur(radius=min((1.0 / max(k, 0.05) - 1.0) * 0.7, 3.0)))
    frame = _apply_palette(main, heat, palette, target_dist, ir_range_m, vig=not fast)

    inset_zoom = 1.0
    if zoom_inset:
        iw, ih = int(w * 0.36), int(h * 0.36)
        ins, inset_zoom = _zoom_inset(base, heat, engine, lateral_offset, target_dist, th,
                                      ppm, k, palette, target_dist, ir_range_m, iw, ih)
        if ins is not None and inset_zoom > 1.05:
            fr = frame.copy()
            px, py = w - iw - 8, h - ih - 8
            d = ImageDraw.Draw(fr)
            d.rectangle([px - 2, py - 2, px + iw + 1, py + ih + 1], outline=(255, 214, 0), width=2)
            fr.paste(ins, (px, py))
            d = ImageDraw.Draw(fr)
            d.rectangle([px, py, px + 88, py + 14], fill=(18, 21, 26))
            d.text((px + 4, py + 3), f"ZOOM x{inset_zoom:.0f}", fill=(255, 214, 0))
            frame = fr

    if (w, h) != (out_w, out_h):
        frame = frame.resize((out_w, out_h), Image.Resampling.BILINEAR)
    engine.set_viewport_size(out_w, out_h)
    return frame, inset_zoom
