"""Bitrate estimator: anchors, monotonicity, caps, modes (estimates, not measurements)."""
from __future__ import annotations

import pytest

from cctv_simulator import bitrate
from cctv_simulator.bitrate import estimate, parse_bitrate_cap_mbps, round_up_standard
from cctv_simulator.config import RESOLUTIONS
from cctv_simulator.models import CameraConfig


RES_4MP = "4 MP (2K - 2688x1520)"
CAM = CameraConfig(resolution_name=RES_4MP)


def test_anchor_4mp_h265_25fps_medium_is_about_4_mbps():
    e = estimate(CAM, codec="h265", fps=25, scene="medium")
    assert e.pixels == RESOLUTIONS[RES_4MP][0] * RESOLUTIONS[RES_4MP][1]
    assert e.day_mbps == pytest.approx(4.0, rel=0.15)
    assert e.night_mbps == pytest.approx(2 * e.day_mbps)
    assert e.is_estimate


def test_codec_order_and_scene_monotone():
    h264, h265, smart = (estimate(CAM, codec=c).day_mbps for c in ("h264", "h265", "smart_h265"))
    assert h264 > h265 > smart
    lo, mid, hi = (estimate(CAM, scene=s).day_mbps for s in ("low", "medium", "high"))
    assert lo < mid < hi
    # smart gains less on a busy scene
    assert (estimate(CAM, codec="smart_h265", scene="high").day_mbps / estimate(CAM, codec="h264", scene="high").day_mbps
            > estimate(CAM, codec="smart_h265", scene="low").day_mbps / estimate(CAM, codec="h264", scene="low").day_mbps)
    assert estimate(CAM, gop_s=1.0).day_mbps > estimate(CAM, gop_s=4.0).day_mbps
    assert estimate(CAM, codec="mjpeg").day_mbps > h264


def test_fps_is_clamped_to_model_max_fps():
    e = estimate(CAM, fps=30, model_record={"max_fps": 15})
    assert e.fps_used == 15 and e.fps_clamped and any("fps" in n for n in e.notes)
    assert not estimate(CAM, fps=10, model_record={"max_fps": 15}).fps_clamped


def test_thermal_has_no_night_factor():
    thermal = CameraConfig(resolution_name=CAM.resolution_name, model_name="Termal Test")
    e = estimate(thermal)
    assert e.thermal and e.night_mbps == e.day_mbps


@pytest.mark.parametrize("text, want", [
    ("32 Kbps ~ 16 Mbps", 16.0), ("16Mbps", 16.0), ("8192 kbps", 8.192), ("512 - 16384 kpbs", 16.384),
    ("1,5 Mbps", 1.5), ("yüksek", None), ("", None),
])
def test_brochure_cap_parsing(text, want):
    got = parse_bitrate_cap_mbps(text)
    assert got == want if want is None else got == pytest.approx(want)


def test_brochure_cap_applies_and_unparseable_is_noted():
    e = estimate(CAM, codec="h264", scene="high", model_record={"video_bitrate": "8192 kbps"})
    assert e.peak_mbps == pytest.approx(8.192) and e.day_mbps <= 8.192
    assert any("broşür üst sınırı" in n.lower() for n in e.notes)
    e2 = estimate(CAM, model_record={"video_bitrate": "değişken"})
    assert any("okunamadı" in n for n in e2.notes)


def test_unsupported_codec_warns_but_computes():
    rec = {"codec": "H.264, H.265", "smart_codec": "yok"}
    e = estimate(CAM, codec="smart_h265", model_record=rec)
    assert e.warnings and e.day_mbps > 0
    assert not estimate(CAM, codec="h265", model_record=rec).warnings
    assert not estimate(CAM, codec="smart_h265", model_record={"codec": "H.265+, H.265"}).warnings
    assert not estimate(CAM, codec="mjpeg", model_record={}).warnings        # empty field: nothing to check


def test_motion_and_mixed_record_less_than_continuous():
    cont = estimate(CAM, mode="continuous")
    motion = estimate(CAM, mode="motion", motion_share=0.3)
    mixed = estimate(CAM, mode="mixed", motion_share=0.3)
    assert motion.avg_record_mbps < mixed.avg_record_mbps < cont.avg_record_mbps
    assert motion.peak_mbps == cont.peak_mbps == mixed.peak_mbps          # NVR input is the live main stream
    assert cont.avg_record_mbps == pytest.approx(cont.day_mbps * 1.5)     # 50 % night at x2


def test_recommendations_round_up_to_standard_settings():
    assert round_up_standard(4.05) == 5 and round_up_standard(4.0) == 4 and round_up_standard(0.1) == 0.5
    assert round_up_standard(33) == 40
    e = estimate(CAM)
    assert e.recommended_cbr_mbps in bitrate.STANDARD_MBPS and e.recommended_cbr_mbps >= e.peak_mbps
    assert e.recommended_vbr_max_mbps >= e.peak_mbps * 1.25 - 1e-9


def test_storage_uses_the_network_plan_formula():
    from cctv_simulator.network_plan import storage_tb
    e = estimate(CAM, retention_days=14, raid_overhead=0.2)
    assert e.storage_tb == pytest.approx(storage_tb(e.avg_record_mbps, 14, 0.2))
    assert e.storage_tb_per_30d == pytest.approx(storage_tb(e.avg_record_mbps, 30, 0.2))


@pytest.mark.parametrize("kw", [{"codec": "x"}, {"scene": "x"}, {"mode": "x"}, {"fps": 0}, {"motion_share": 2}])
def test_bad_options_raise(kw):
    with pytest.raises(ValueError):
        estimate(CAM, **kw)


def test_smart_h264_is_not_smart_h265():
    from cctv_simulator.bitrate import supported_codecs

    assert "smart_h265" not in supported_codecs({"codec": "H.264+, H.264", "smart_codec": "Smart H.264+"})
    assert "smart_h265" in supported_codecs({"codec": "H.265", "smart_codec": "Smart H.265+"})
    assert "smart_h265" in supported_codecs({"codec": "H.265+, H.264"})
