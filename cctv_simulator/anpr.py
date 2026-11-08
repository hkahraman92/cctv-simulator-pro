"""ANPR / plate-reading geometry on a straight road, with vehicle speed.

Pixel density alone does not make a readable plate. An ANPR camera also needs

* the plate seen at a shallow angle -- most engines want <= 30 deg between the
  plate normal and the line of sight, horizontally and vertically;
* the plate inside the frame for enough video frames while the car crosses
  the zone (``min_frames`` at ``fps``) -- a fast car gives few;
* a short enough exposure: the plate's motion across the image during one
  exposure (motion blur) must stay below about a character stroke.

Frame (metres): the road runs along +y, ``y = 0`` level with the pole.
Lanes are numbered from the road's right edge (``x = 0``) outwards, lane ``i``
centred at ``x = (i + 0.5) * lane_width``. The pole stands at ``x = pole_x_m``
(negative = on the shoulder, inside the road = gantry), lens at ``height_m``.
Vehicles travel along the road (front plates read as they approach, rear
plates as they leave -- same geometry); the plate is a vertical rectangle at
``plate_height_m`` facing along the road.

Optics come only from ``calculations.camera_optics`` (Kural 1). Off-axis
pixel density is taken as the on-axis value at the same slant distance; the
plate's horizontal foreshortening is applied (``ppm * cos(horizontal angle)``).
Neighbouring-lane vehicles hiding a plate are not modelled.

No Tk; numpy only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

import numpy as np

from .calculations import camera_optics
from .models import CameraConfig

PLATE_WIDTH_M = 0.52           # EU / TR plate (520 x 110 mm)
DEFAULT_PPM = 143.0            # models.DEFAULT_LEVELS "ANPR_Alg" (TR plate)
SAMPLE_M = 0.25


def _tr(v: float, digits: int = 1) -> str:
    """Turkish decimal comma for messages (the shell shows numbers the same way)."""
    return f"{v:.{digits}f}".replace(".", ",")


@dataclass
class AnprSetup:
    height_m: float = 6.0
    pole_x_m: float = -1.5          # lateral position of the pole (lane edge = 0)
    lanes: int = 2
    lane_width_m: float = 3.5
    aim_y_m: float = 20.0           # aim point along the road
    aim_x_m: Optional[float] = None  # aim point across (None = middle of the lanes)
    plate_height_m: float = 0.5
    target_ppm: float = DEFAULT_PPM
    max_h_deg: float = 30.0
    max_v_deg: float = 30.0
    speed_kmh: float = 90.0
    fps: float = 25.0
    min_frames: int = 3
    exposure_s: float = 1.0 / 1000.0
    blur_px: float = 2.0
    max_y_m: float = 200.0
    night_ir: bool = False          # night: the plate must also be inside the camera's IR range


@dataclass
class LaneResult:
    lane: int                      # 1 = nearest the road's right edge
    x_m: float
    zone_y0_m: Optional[float]     # longest stretch where the plate is readable
    zone_y1_m: Optional[float]
    zone_len_m: float
    frames: float                  # frames inside the zone at the set speed
    max_speed_kmh: float           # speed that still gives min_frames
    ppm_min: float                 # effective (foreshortened) px/m over the zone
    ppm_max: float
    plate_px_min: float            # pixels across the plate width
    h_angle_max_deg: float
    v_angle_max_deg: float
    blur_px_max: float             # at the set speed and exposure
    max_exposure_s: float          # longest exposure keeping blur <= blur_px at the set speed
    ok: bool
    reasons: List[str] = field(default_factory=list)


@dataclass
class AnprResult:
    focal_mm: float
    pan_deg: float                 # horizontal aim, 0 = straight along the road, + towards larger x
    tilt_deg: float                # down from horizontal
    hfov_deg: float
    vfov_deg: float
    lanes: List[LaneResult]
    lanes_ok: int
    setup: AnprSetup


def _aim(setup: AnprSetup):
    ax = setup.aim_x_m if setup.aim_x_m is not None else setup.lanes * setup.lane_width_m / 2.0
    dx, dy = ax - setup.pole_x_m, setup.aim_y_m
    dz = setup.plate_height_m - setup.height_m
    pan = math.degrees(math.atan2(dx, dy))
    tilt = -math.degrees(math.atan2(dz, math.hypot(dx, dy)))
    return pan, tilt


def _longest(mask: np.ndarray):
    """(start, end) indices of the longest run of True, or None."""
    if not mask.any():
        return None
    m = np.concatenate([[False], mask, [False]]).astype(np.int8)
    d = np.diff(m)
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    k = int(np.argmax(ends - starts))
    return int(starts[k]), int(ends[k]) - 1


def analyse(camera: CameraConfig, focal_mm: float, setup: AnprSetup, *,
            pan_deg: Optional[float] = None, tilt_deg: Optional[float] = None) -> AnprResult:
    """Readable-plate zone per lane for one camera setting."""
    optics = camera_optics(camera, focal_mm)
    a_pan, a_tilt = _aim(setup)
    pan = a_pan if pan_deg is None else pan_deg
    tilt = a_tilt if tilt_deg is None else tilt_deg
    p, t = math.radians(pan), math.radians(tilt)
    fwd = np.array([math.sin(p) * math.cos(t), math.cos(p) * math.cos(t), -math.sin(t)])
    right = np.array([math.cos(p), -math.sin(p), 0.0])
    up = np.cross(right, fwd)
    half_h, half_v = math.radians(optics.hfov_deg / 2.0), math.radians(optics.vfov_deg / 2.0)
    ys = np.arange(SAMPLE_M, setup.max_y_m + 1e-9, SAMPLE_M)
    v_ms = setup.speed_kmh / 3.6
    lanes: List[LaneResult] = []
    for i in range(setup.lanes):
        x = (i + 0.5) * setup.lane_width_m
        d = np.stack([np.full_like(ys, x - setup.pole_x_m), ys,
                      np.full_like(ys, setup.plate_height_m - setup.height_m)], axis=1)
        s = np.linalg.norm(d, axis=1)
        df, dr, du = d @ fwd, d @ right, d @ up
        in_frame = (df > 0) & (np.abs(np.arctan2(dr, df)) <= half_h) & (np.abs(np.arctan2(du, df)) <= half_v)
        h_ang = np.degrees(np.arctan2(abs(x - setup.pole_x_m), ys))
        v_ang = np.degrees(np.arctan2(setup.height_m - setup.plate_height_m, np.hypot(x - setup.pole_x_m, ys)))
        ppm = optics.ppm_at(s) * np.cos(np.radians(h_ang))
        ok_angle = (h_ang <= setup.max_h_deg) & (v_ang <= setup.max_v_deg)
        readable = in_frame & ok_angle & (ppm >= setup.target_ppm)
        lit = np.ones_like(readable)
        if setup.night_ir:
            lit = s <= max(float(camera.ir_range_m), 0.0)
            readable &= lit
        # motion blur: the part of the car's velocity across the line of sight
        u = d / s[:, None]
        vel = np.array([0.0, -v_ms, 0.0])
        v_perp = np.linalg.norm(vel[None, :] - (u @ vel)[:, None] * u, axis=1)
        blur = v_perp * setup.exposure_s * optics.ppm_at(s)
        reasons: List[str] = []
        run = _longest(readable)
        if run is None:
            if not in_frame.any():
                reasons.append("Şerit kadrajda değil.")
            elif not (in_frame & ok_angle).any():
                reasons.append(f"Kadrajda plaka açısı > {setup.max_h_deg:.0f}° (yatay) / {setup.max_v_deg:.0f}° (dikey).")
            elif setup.night_ir and not (in_frame & ok_angle & lit & (ppm >= setup.target_ppm)).any()                     and (in_frame & ok_angle & (ppm >= setup.target_ppm)).any():
                reasons.append(f"Okunabilir bölge IR menzilinin ({camera.ir_range_m:.0f} m) dışında.")
            else:
                reasons.append(f"Kadrajda plaka {setup.target_ppm:.0f} px/m'ye ulaşmıyor "
                               f"(en çok {float(np.max(ppm[in_frame & ok_angle])):.0f}).")
            lanes.append(LaneResult(i + 1, x, None, None, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                                    False, reasons))
            continue
        a, b = run
        zl = float(ys[b] - ys[a] + SAMPLE_M)
        frames = zl / v_ms * setup.fps if v_ms > 0 else math.inf
        max_speed = zl * setup.fps / max(setup.min_frames, 1) * 3.6
        sl = slice(a, b + 1)
        blur_max = float(np.max(blur[sl]))
        worst = float(np.max(v_perp[sl] * optics.ppm_at(s[sl])))
        max_exp = setup.blur_px / worst if worst > 0 else math.inf
        if frames < setup.min_frames:
            reasons.append(f"{setup.speed_kmh:.0f} km/sa hızda okuma bölgesinde {_tr(frames)} kare "
                           f"(en az {setup.min_frames}); en çok {max_speed:.0f} km/sa.")
        if blur_max > setup.blur_px:
            reasons.append(f"Hareket bulanıklığı {_tr(blur_max)} px > {_tr(setup.blur_px)} px; "
                           f"enstantane ≤ 1/{math.ceil(1 / max_exp):.0f} sn olmalı.")
        lanes.append(LaneResult(
            lane=i + 1, x_m=x, zone_y0_m=float(ys[a]), zone_y1_m=float(ys[b]), zone_len_m=zl, frames=frames,
            max_speed_kmh=max_speed, ppm_min=float(np.min(ppm[sl])), ppm_max=float(np.max(ppm[sl])),
            plate_px_min=float(np.min(ppm[sl])) * PLATE_WIDTH_M, h_angle_max_deg=float(np.max(h_ang[sl])),
            v_angle_max_deg=float(np.max(v_ang[sl])), blur_px_max=blur_max, max_exposure_s=max_exp,
            ok=not reasons, reasons=reasons))
    return AnprResult(focal_mm=focal_mm, pan_deg=pan, tilt_deg=tilt, hfov_deg=optics.hfov_deg,
                      vfov_deg=optics.vfov_deg, lanes=lanes, lanes_ok=sum(1 for ln in lanes if ln.ok), setup=setup)


def _score(r: AnprResult) -> tuple:
    """Lanes passing first; then the weakest lane's frame margin, capped at 2x
    (past that, a longer zone buys nothing); then the weakest lane's pixels
    across the plate -- reading accuracy, and a nearer, less occluded aim."""
    need = max(r.setup.min_frames, 1)
    margin = min((min(ln.frames / need, 2.0) for ln in r.lanes), default=0.0)
    plate = min((ln.plate_px_min for ln in r.lanes), default=0.0)
    return (r.lanes_ok, sum(1 for ln in r.lanes if ln.zone_len_m > 0), round(margin, 2), round(plate, 1))


def optimize(camera: CameraConfig, setup: AnprSetup, *, focals: Optional[Sequence[float]] = None,
             aim_ys: Optional[Sequence[float]] = None,
             progress: Optional[Callable[[int, int], None]] = None) -> AnprResult:
    """Best focal and aim point for a fixed pole: every lane readable at the set
    speed if possible, then frame margin (capped at 2x), then plate pixels."""
    from dataclasses import replace

    lo, hi = float(camera.focal_min_mm), float(max(camera.focal_max_mm, camera.focal_min_mm))
    if focals is None:
        focals = [lo] if hi - lo < 0.05 else list(np.geomspace(lo, hi, 24))
    if aim_ys is None:
        aim_ys = list(np.arange(4.0, 81.0, 2.0))
    road_w = setup.lanes * setup.lane_width_m
    aim_xs = sorted({road_w / 2.0, *[(i + 0.5) * setup.lane_width_m for i in range(setup.lanes)]})
    best: Optional[AnprResult] = None
    total = len(focals)
    for k, f in enumerate(focals):
        for ay in aim_ys:
            for ax in aim_xs:
                r = analyse(camera, float(f), replace(setup, aim_y_m=float(ay), aim_x_m=float(ax)))
                if best is None or _score(r) > _score(best):
                    best = r
        if progress:
            progress(k + 1, total)
    return best
