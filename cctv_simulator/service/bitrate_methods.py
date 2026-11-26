"""API v1: per-camera bitrate recommendation (`bitrate.estimate`). Estimates, not measurements."""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List

from .params import _camera_from, _require
from .protocol import APP_ERROR, INVALID_PARAMS, Context, RpcError

_OPTION_KEYS = ("fps", "codec", "scene", "gop_s", "mode", "motion_share")
_NUMERIC = ("fps", "gop_s", "motion_share")


def _options(raw: Any, where: str) -> Dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise RpcError(INVALID_PARAMS, f"'{where}' bir nesne olmalı.")
    out: Dict[str, Any] = {}
    for k in _OPTION_KEYS:
        v = raw.get(k)
        if v is None:
            continue
        if k in _NUMERIC and (isinstance(v, bool) or not isinstance(v, (int, float))):
            raise RpcError(INVALID_PARAMS, f"'{where}.{k}' sayı olmalı.")
        if k not in _NUMERIC and not isinstance(v, str):
            raise RpcError(INVALID_PARAMS, f"'{where}.{k}' metin olmalı.")
        out[k] = v
    return out


def _number(params: Dict[str, Any], key: str, default: float) -> float:
    v = params.get(key, default)
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise RpcError(INVALID_PARAMS, f"'{key}' sayı olmalı.")
    return float(v)


def bitrate_estimate(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Per-camera bitrate / storage estimate (codec, fps, scene, GOP, recording mode); estimates, not measurements."""
    from ..bitrate import estimate
    from ..database import load_camera_library

    cameras = _require(params, "cameras", list)
    if not cameras:
        raise RpcError(INVALID_PARAMS, "'cameras' boş olamaz.")
    defaults = _options(params.get("defaults"), "defaults")
    retention = _number(params, "retention_days", 30.0)
    raid = _number(params, "raid_overhead", 0.15)
    night_factor = _number(params, "night_factor", 2.0)
    night_share = _number(params, "night_share", 0.5)
    library = load_camera_library()

    rows: List[Dict[str, Any]] = []
    for i, item in enumerate(cameras):
        if not isinstance(item, dict):
            raise RpcError(INVALID_PARAMS, f"'cameras[{i}]' bir nesne olmalı.")
        opts = {**defaults, **_options(item, f"cameras[{i}]")}
        model = item.get("model")
        if isinstance(model, str) and model:
            record = library.get(model)
            if not isinstance(record, dict) or not record:
                raise RpcError(APP_ERROR, f"Kütüphanede model yok: {model}")
            base = item.get("camera")
            cam = _camera_from(_library_camera({"name": model, **({"base": base} if base else {})}, ctx))
        elif "camera" in item:
            cam = _camera_from(item["camera"])
            rec = library.get(cam.model_name) if cam.model_name else None
            record = rec if isinstance(rec, dict) and rec else None
        else:
            raise RpcError(INVALID_PARAMS, f"'cameras[{i}]' için 'model' ya da 'camera' gerekli.")
        try:
            est = estimate(cam, night_factor=night_factor, night_share=night_share, retention_days=retention,
                           raid_overhead=raid, model_record=record, **opts)
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None
        label = str(item.get("label") or (cam.name if "camera" in item else model) or f"Kamera {i + 1}")
        rows.append({"label": label, "model": cam.model_name or (model or ""), **dataclasses.asdict(est)})
    totals = {
        "avg_record_mbps": sum(r["avg_record_mbps"] for r in rows),
        "peak_mbps": sum(r["peak_mbps"] for r in rows),
        "storage_tb": sum(r["storage_tb"] for r in rows),
        "retention_days": retention,
    }
    assumptions = [
        "Bit hızları kodek, sahne ve GOP için sektörde tipik piksel başına bit değerlerinden tahmin edilir; ölçüm değildir.",
        "Gerçek akış kodlayıcıya, gürültüye, ışığa ve kameranın hız denetimi ayarlarına göre değişir.",
        f"Gece çarpanı ×{night_factor:g} (günün %{night_share * 100:.0f}), termal kameralarda uygulanmaz; "
        "NVR girişi gece tepe değeridir.",
        f"Depolama {retention:g} gün, RAID payı %{raid * 100:.0f}; ondalık TB.",
        "Depolama VBR ortalamasıyladır; kamera sabit bit hızına (CBR) ayarlanırsa kayıt her zaman "
        "önerilen CBR değerinde olur ve depolama buna göre artar.",
    ]
    return {"rows": rows, "totals": totals, "assumptions": assumptions}


def _library_camera(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    from .methods import library_camera

    return library_camera(params, ctx)["camera"]


METHODS = {"bitrate.estimate": bitrate_estimate}
