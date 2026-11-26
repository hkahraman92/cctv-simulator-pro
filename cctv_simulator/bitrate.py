"""Per-camera recording bitrate estimate (codec / fps / scene / GOP / recording mode).

``perimeter_planner._bitrate_mbps`` stays the plan's flat guess; this module is
the separate, option-aware estimator behind the desktop "Bit Hızı" tab. No Tk.

The bits-per-pixel constants below are industry-typical approximations, NOT
measurements: real streams depend on the encoder, noise, lighting and the
camera's own rate-control settings. Every result is an estimate
(``is_estimate``) and a brochure maximum, when the model states one, caps it.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Set

from .models import CameraConfig

BPP_H264_MEDIUM = 0.08            # bits per pixel per frame, H.264, medium scene, 2 s GOP
H265_FACTOR = 0.55                # H.265 relative to H.264
SMART_FACTOR_CALM = 0.35          # Smart H.265+ on low / medium scenes
SMART_FACTOR_BUSY = 0.50          # smart codecs gain little on busy scenes
MJPEG_BPP = 1.0                   # intra-only: fixed, no scene / GOP effect
SCENE_FACTORS = {"low": 0.5, "medium": 1.0, "high": 1.8}
GOP_REFERENCE_S = 2.0
SUB_STREAM_SHARE = 1.0 / 8.0      # "mixed": continuous sub-stream relative to the main stream
DEFAULT_FPS = 25.0
STANDARD_MBPS = (0.5, 1, 1.5, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 32)
CODECS = ("h264", "h265", "smart_h265", "mjpeg")
MODES = ("continuous", "motion", "mixed")

_BITRATE_RE = re.compile(r"(\d[\d.,]*)\s*([km])\s*[bp]", re.IGNORECASE)


@dataclass
class BitrateEstimate:
    pixels: int
    fps_used: float
    fps_clamped: bool
    codec: str
    scene: str
    mode: str
    day_mbps: float
    night_mbps: float
    peak_mbps: float
    avg_record_mbps: float
    storage_tb_per_30d: float
    retention_days: float
    storage_tb: float
    recommended_cbr_mbps: float
    recommended_vbr_max_mbps: float
    thermal: bool = False
    supported_codecs: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    is_estimate: bool = True


def round_up_standard(mbps: float) -> float:
    """Smallest common encoder setting that is >= ``mbps`` (multiples of 8 above 32)."""
    for v in STANDARD_MBPS:
        if mbps <= v + 1e-9:
            return float(v)
    return float(math.ceil(mbps / 8.0) * 8)


def parse_bitrate_cap_mbps(text: Any) -> Optional[float]:
    """Largest value with a unit in free text such as "32 Kbps ~ 16 Mbps" (Turkish decimals
    accepted; the typo "kpbs" too). None when no value carries a unit."""
    from .database import parse_tr_number

    best: Optional[float] = None
    for num, unit in _BITRATE_RE.findall(str(text or "")):
        try:
            v = parse_tr_number(num.rstrip(".,"))
        except ValueError:
            continue
        mbps = v / 1000.0 if unit.lower() == "k" else v
        if best is None or mbps > best:
            best = mbps
    return best


def supported_codecs(record: Optional[dict]) -> Set[str]:
    """Codec ids a library record's `codec` / `smart_codec` text declares (empty = unknown)."""
    if not record:
        return set()
    codec = re.sub(r"[\s.\-_]", "", str(record.get("codec") or "").lower())
    smart = re.sub(r"[\s.\-_]", "", str(record.get("smart_codec") or "").lower())
    out: Set[str] = set()
    if "264" in codec:
        out.add("h264")
    if "265" in codec or "hevc" in codec:
        out.add("h265")
    if "mjpeg" in codec or "mjpg" in codec or "jpeg" in codec:
        out.add("mjpeg")
    # only an H.265 smart codec counts: "H.264+" / "Smart H.264+" is not Smart H.265+
    if "265+" in codec or ("265" in smart and smart not in ("yok", "no", "none", "hayir", "hayır")):
        out.add("smart_h265")
    return out


def bits_per_pixel(codec: str, scene: str, gop_s: float) -> float:
    if codec == "mjpeg":
        return MJPEG_BPP
    gop = min(max(gop_s, 0.5), 10.0)
    gop_factor = 0.85 + 0.3 / gop                      # 1.0 at the 2 s reference
    if codec == "h264":
        codec_factor = 1.0
    elif codec == "h265":
        codec_factor = H265_FACTOR
    else:
        codec_factor = SMART_FACTOR_BUSY if scene == "high" else SMART_FACTOR_CALM
    return BPP_H264_MEDIUM * codec_factor * SCENE_FACTORS[scene] * gop_factor


def _num(value: Any) -> Optional[float]:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
    return float(value) if ok else None


def estimate(camera: CameraConfig, *, codec: str = "h265", fps: Optional[float] = None,
             scene: str = "medium", gop_s: float = 2.0, mode: str = "continuous",
             motion_share: float = 0.3, night_factor: float = 2.0, night_share: float = 0.5,
             retention_days: float = 30.0, raid_overhead: float = 0.15,
             model_record: Optional[dict] = None) -> BitrateEstimate:
    from .calculations import _sensor_and_resolution
    from .network_plan import storage_tb
    from .viewshed_3d import is_thermal_camera

    if codec not in CODECS:
        raise ValueError(f"Bilinmeyen codec: {codec}")
    if scene not in SCENE_FACTORS:
        raise ValueError(f"Bilinmeyen sahne: {scene}")
    if mode not in MODES:
        raise ValueError(f"Bilinmeyen kayıt düzeni: {mode}")
    if fps is not None and not fps > 0:
        raise ValueError("fps pozitif olmalı.")
    if not 0.0 <= motion_share <= 1.0 or not 0.0 <= night_share <= 1.0:
        raise ValueError("Hareket / gece payı 0–1 aralığında olmalı.")
    if night_factor < 1.0 or retention_days <= 0 or raid_overhead < 0:
        raise ValueError("Gece çarpanı ≥ 1, kayıt günü > 0, RAID payı ≥ 0 olmalı.")

    notes: List[str] = []
    warnings: List[str] = []
    _, _, res_w, res_h, _ = _sensor_and_resolution(camera, False)    # nominal pixels, not k-scaled
    pixels = int(round(res_w * res_h))

    fps_used = float(fps) if fps is not None else DEFAULT_FPS
    fps_clamped = False
    cap_fps = _num((model_record or {}).get("max_fps"))
    if cap_fps is not None and fps_used > cap_fps:
        fps_used, fps_clamped = cap_fps, True
        notes.append(f"fps modelin en çok {cap_fps:g} değerine indirildi.")

    supported = supported_codecs(model_record)
    names = {"h264": "H.264", "h265": "H.265", "smart_h265": "Smart H.265+", "mjpeg": "MJPEG"}
    if supported and codec not in supported:
        warnings.append(f"Model {names[codec]} desteklediğini bildirmiyor "
                        f"(bildirilen: {', '.join(names[c] for c in CODECS if c in supported)}); yine de hesaplandı.")

    thermal = is_thermal_camera(camera)
    day = pixels * fps_used * bits_per_pixel(codec, scene, gop_s) / 1e6
    night = day if thermal else day * night_factor
    if thermal:
        notes.append("Termal kamera: gece IR gürültüsü çarpanı uygulanmadı.")
    peak = night

    raw_cap = (model_record or {}).get("video_bitrate")
    if raw_cap not in (None, ""):
        cap = parse_bitrate_cap_mbps(raw_cap)
        if cap is None:
            notes.append(f"Broşür bit hızı okunamadı ({raw_cap}); sınır uygulanmadı.")
        elif cap > 0:
            if max(day, night) > cap:
                notes.append(f"Broşür üst sınırı ({cap:g} Mbps) uygulandı.")
            day, night, peak = min(day, cap), min(night, cap), min(peak, cap)

    avg_stream = day * (1.0 - night_share) + night * night_share
    if mode == "continuous":
        avg_record = avg_stream
    elif mode == "motion":
        avg_record = avg_stream * motion_share
    else:
        avg_record = avg_stream * min(1.0, SUB_STREAM_SHARE + motion_share)
    return BitrateEstimate(
        pixels=pixels, fps_used=fps_used, fps_clamped=fps_clamped, codec=codec, scene=scene, mode=mode,
        day_mbps=day, night_mbps=night, peak_mbps=peak, avg_record_mbps=avg_record,
        storage_tb_per_30d=storage_tb(avg_record, 30.0, raid_overhead),
        retention_days=float(retention_days),
        storage_tb=storage_tb(avg_record, retention_days, raid_overhead),
        recommended_cbr_mbps=round_up_standard(peak), recommended_vbr_max_mbps=round_up_standard(peak * 1.25),
        thermal=thermal, supported_codecs=[c for c in CODECS if c in supported],
        notes=notes, warnings=warnings)
