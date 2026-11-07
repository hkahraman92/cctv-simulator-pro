"""Parameter validation shared by every API module (Turkish, field-named errors)."""
from __future__ import annotations

import dataclasses
from typing import Any, Dict

from .protocol import INVALID_PARAMS, RpcError


def _require(params: Dict[str, Any], key: str, kind: type | tuple) -> Any:
    if key not in params:
        raise RpcError(INVALID_PARAMS, f"'{key}' parametresi gerekli.")
    value = params[key]
    if not isinstance(value, kind) or isinstance(value, bool) and kind is not bool:
        raise RpcError(INVALID_PARAMS, f"'{key}' parametresinin türü yanlış.")
    return value


def _camera_from(raw: Any):
    from ..config import RESOLUTIONS, SENSOR_DIMS_MM
    from ..models import CameraConfig

    if not isinstance(raw, dict):
        raise RpcError(INVALID_PARAMS, "'camera' bir nesne olmalı.")
    known = {f.name: f for f in dataclasses.fields(CameraConfig)}
    unknown = sorted(set(raw) - set(known))
    if unknown:
        raise RpcError(INVALID_PARAMS, "Bilinmeyen kamera alanı: " + ", ".join(unknown),
                       {"unknown": unknown})
    for name, value in raw.items():
        want = known[name].type
        numeric = want in (float, "float")
        if numeric and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise RpcError(INVALID_PARAMS, f"'camera.{name}' sayı olmalı.")
        if want in (str, "str") and not isinstance(value, str):
            raise RpcError(INVALID_PARAMS, f"'camera.{name}' metin olmalı.")
    cam = CameraConfig(**{k: float(v) if known[k].type in (float, "float") else v for k, v in raw.items()})
    if cam.sensor_name not in SENSOR_DIMS_MM:
        raise RpcError(INVALID_PARAMS, f"Bilinmeyen sensör: {cam.sensor_name}")
    if cam.resolution_name not in RESOLUTIONS:
        raise RpcError(INVALID_PARAMS, f"Bilinmeyen çözünürlük: {cam.resolution_name}")
    if not (0 < cam.focal_min_mm <= cam.focal_max_mm):
        raise RpcError(INVALID_PARAMS, "Odak aralığı geçersiz (0 < min ≤ max).")
    return cam


def _levels_from(raw: Any):
    from ..models import DEFAULT_LEVELS, PPMLevel

    if raw is None:
        return list(DEFAULT_LEVELS)
    if not isinstance(raw, list) or not raw:
        raise RpcError(INVALID_PARAMS, "'levels' boş olmayan bir liste olmalı.")
    out = []
    for item in raw:
        try:
            out.append(PPMLevel(key=str(item["key"]), name=str(item["name"]), ppm=float(item["ppm"]),
                                color=str(item.get("color", "")), level_type=str(item.get("level_type", ""))))
        except (KeyError, TypeError, ValueError):
            raise RpcError(INVALID_PARAMS, "Her seviye 'key', 'name' ve 'ppm' içermeli.") from None
        if out[-1].ppm <= 0:
            raise RpcError(INVALID_PARAMS, f"Seviye px/m pozitif olmalı: {out[-1].key}")
    return out
