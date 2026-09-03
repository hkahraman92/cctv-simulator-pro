"""cctv_iq — headless image-quality measurement core.

Slanted-edge MTF (ISO 12233 style): given a photo of a near-vertical or
near-horizontal high-contrast edge, recover the system MTF and reduce it to a
single number the optic engine can consume.

    k          = MTF50 frequency / Nyquist (0.5 cy/px)
    eff. lines = nominal horizontal pixels x k
    eff. MP    = nominal MP x k**2

Feed ``k`` back as ``CameraConfig.effective_px_ratio`` so
``calculations.calculate_for_camera`` reports the resolution the system really
delivers, not the sensor's label.

No Tk. numpy required; Pillow only for file loading.

CLI:
    py -3.13 -m cctv_simulator.cctv_iq edge.png --json
    py -3.13 -m cctv_simulator.cctv_iq edge.png --roi 120,80,220,300
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

OVERSAMPLE = 4
NYQUIST_CY_PX = 0.5


@dataclass
class MTFResult:
    edge_angle_deg: float
    mtf50_cy_px: float          # frequency where MTF drops to 0.50
    mtf_at_nyquist: float       # MTF value at 0.5 cy/px
    k: float                    # mtf50 / Nyquist, clamped to (0, 1]
    samples: int                # rows (or cols) used to build the ESF
    orientation: str            # "vertical" | "horizontal"

    def effective_lines(self, nominal_px: float) -> float:
        return nominal_px * self.k

    def effective_mp(self, nominal_mp: float) -> float:
        return nominal_mp * self.k * self.k


def _to_gray(img: np.ndarray) -> np.ndarray:
    a = np.asarray(img, dtype=np.float64)
    if a.ndim == 3:
        a = a[..., :3] @ np.array([0.2126, 0.7152, 0.0722])
    return a


def _edge_positions(roi: np.ndarray) -> np.ndarray:
    """Sub-pixel edge column for every row, via derivative centroid."""
    d = np.abs(np.diff(roi, axis=1))
    x = np.arange(d.shape[1], dtype=np.float64)
    w = d.sum(axis=1)
    w[w == 0] = 1.0
    return (d * x).sum(axis=1) / w + 0.5


def slanted_edge_mtf(gray: np.ndarray) -> MTFResult:
    """Core measurement. ``gray`` is a 2D ROI containing ONE straight edge."""
    gray = np.asarray(gray, dtype=np.float64)
    if gray.ndim != 2 or min(gray.shape) < 8:
        raise ValueError("ROI must be a 2D array at least 8x8.")

    # Orient so the edge runs (roughly) top-to-bottom.
    orientation = "vertical"
    if np.mean(np.abs(np.diff(gray, axis=0))) > np.mean(np.abs(np.diff(gray, axis=1))):
        gray = gray.T
        orientation = "horizontal"

    rows = np.arange(gray.shape[0], dtype=np.float64)
    edge_x = _edge_positions(gray)

    # Robust line fit: edge_x ~= slope * row + intercept.
    slope, intercept = np.polyfit(rows, edge_x, 1)
    angle = np.arctan(slope)
    if abs(np.degrees(angle)) < 0.20 or abs(np.degrees(angle)) > 15.0:
        # Too straight -> aliasing; too steep -> not a slanted edge.
        raise ValueError(
            f"Edge tilt {np.degrees(angle):.2f}° outside 0.2–15°. "
            "Reshoot with a ~5° slant."
        )

    # Project each pixel onto the edge normal, in pixel units.
    xs = np.arange(gray.shape[1], dtype=np.float64)[None, :]
    dist = (xs - (slope * rows[:, None] + intercept)) * np.cos(angle)

    # Oversampled edge spread function: bin projected distance at 1/OVERSAMPLE px.
    bin_px = 1.0 / OVERSAMPLE
    order = np.argsort(dist, axis=None)
    d_sorted = dist.flatten()[order]
    v_sorted = gray.flatten()[order]
    lo = np.ceil(d_sorted[0] / bin_px) * bin_px
    hi = np.floor(d_sorted[-1] / bin_px) * bin_px
    centers = np.arange(lo, hi + bin_px, bin_px)
    idx = np.clip(((d_sorted - centers[0]) / bin_px).round().astype(int), 0, len(centers) - 1)
    esf = np.zeros(len(centers))
    cnt = np.zeros(len(centers))
    np.add.at(esf, idx, v_sorted)
    np.add.at(cnt, idx, 1.0)
    good = cnt > 0
    esf = esf[good] / cnt[good]
    if esf.size < 16:
        raise ValueError("Not enough edge samples; use a larger ROI.")

    # LSF = d(ESF), windowed to suppress ringing.
    lsf = np.gradient(esf)
    lsf -= lsf.mean()
    lsf *= np.hamming(lsf.size)

    # MTF = |FFT(LSF)|, normalised at DC. Frequency axis in cycles/pixel.
    mtf = np.abs(np.fft.rfft(lsf))
    mtf /= mtf[0] if mtf[0] != 0 else 1.0
    freq = np.fft.rfftfreq(lsf.size, d=bin_px)  # cycles per pixel

    mtf50 = _first_crossing(freq, mtf, 0.5)
    mtf_nyq = float(np.interp(NYQUIST_CY_PX, freq, mtf))
    k = float(np.clip(mtf50 / NYQUIST_CY_PX, 1e-3, 1.0))

    return MTFResult(
        edge_angle_deg=round(float(np.degrees(angle)), 3),
        mtf50_cy_px=round(float(mtf50), 5),
        mtf_at_nyquist=round(mtf_nyq, 5),
        k=round(k, 5),
        samples=int(gray.shape[0]),
        orientation=orientation,
    )


def _first_crossing(freq: np.ndarray, mtf: np.ndarray, level: float) -> float:
    below = np.where(mtf < level)[0]
    if below.size == 0:
        return float(freq[-1])
    i = below[0]
    if i == 0:
        return float(freq[0])
    f0, f1 = freq[i - 1], freq[i]
    m0, m1 = mtf[i - 1], mtf[i]
    if m0 == m1:
        return float(f1)
    return float(f0 + (level - m0) * (f1 - f0) / (m1 - m0))


def measure_file(path: str | Path, roi: Optional[Tuple[int, int, int, int]] = None) -> dict:
    from PIL import Image

    with Image.open(path) as im:
        gray = _to_gray(np.asarray(im))
    if roi is not None:
        x0, y0, x1, y1 = roi
        gray = gray[y0:y1, x0:x1]
    res = slanted_edge_mtf(gray)
    return {
        "file": str(path),
        "roi": list(roi) if roi else None,
        "image_size": [int(gray.shape[1]), int(gray.shape[0])],
        **asdict(res),
    }


def synthetic_edge(size: int = 128, angle_deg: float = 5.0, blur_px: float = 1.0,
                   contrast: Tuple[float, float] = (20.0, 235.0)) -> np.ndarray:
    """A clean Gaussian-blurred slanted edge for tests / calibration.

    Larger ``blur_px`` -> softer edge -> lower ``k``. Pure numpy: the edge
    profile is an analytic erf, so no convolution / scipy needed.
    """
    import math

    _erf = np.vectorize(math.erf)
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float64)
    slope = np.tan(np.radians(angle_deg))
    # signed perpendicular distance to the edge line, in pixels
    dist = (xs - (size / 2 + slope * (ys - size / 2))) * math.cos(math.radians(angle_deg))
    sigma = max(blur_px, 1e-3)
    step = 0.5 * (1.0 + _erf(dist / (math.sqrt(2.0) * sigma)))
    return contrast[0] + step * (contrast[1] - contrast[0])


def _cli(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cctv_simulator.cctv_iq", description=__doc__.splitlines()[0])
    p.add_argument("image", type=Path)
    p.add_argument("--roi", help="x0,y0,x1,y1 piksel", default=None)
    p.add_argument("--nominal-lines", type=float, default=None, help="etiket yatay piksel (etkin çizgi için)")
    p.add_argument("--nominal-mp", type=float, default=None, help="etiket MP (etkin MP için)")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    roi = tuple(int(v) for v in a.roi.split(",")) if a.roi else None
    if roi is not None and len(roi) != 4:
        p.error("--roi 4 sayı olmalı: x0,y0,x1,y1")

    try:
        out = measure_file(a.image, roi)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"ölçüm başarısız: {exc}", file=sys.stderr)
        return 2

    if a.nominal_lines:
        out["effective_lines"] = round(a.nominal_lines * out["k"], 1)
    if a.nominal_mp:
        out["effective_mp"] = round(a.nominal_mp * out["k"] ** 2, 2)

    if a.json:
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        for key, val in out.items():
            print(f"{key:18} {val}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
