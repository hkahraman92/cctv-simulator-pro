"""API v1 methods. Each takes ``(params, ctx)`` and returns JSON-able data.

This is the contract the desktop shell is generated against: a method's name,
its parameters and the shape of its result are versioned by ``API_VERSION``.
Adding a method or an optional field is a minor change; renaming or removing
one is a major change. ``system.describe`` lists everything here.

Physics is never re-derived: results come straight from ``calculations``
(Rule 1); this module only converts between JSON and the engine's types.
"""
from __future__ import annotations

import dataclasses
import platform
import sys
from typing import Any, Dict, List

from . import API_VERSION
from .params import _camera_from, _levels_from, _require
from .protocol import INVALID_PARAMS, APP_ERROR, Context, RpcError


def _result_json(result) -> Dict[str, Any]:
    data = dataclasses.asdict(result)
    data["camera"] = dataclasses.asdict(result.camera)
    return data


# ── system ───────────────────────────────────────────────────────────────
def system_info(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """API version and runtime of this service."""
    return {
        "api_version": API_VERSION,
        "python": platform.python_version(),
        "platform": sys.platform,
        "frozen": bool(getattr(sys, "frozen", False)),
    }


def system_ping(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Liveness check."""
    return {"pong": True}


def system_describe(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Every method of this API version with a one-line summary."""
    return {"api_version": API_VERSION,
            "methods": [{"name": name, "summary": (fn.__doc__ or "").strip().splitlines()[0]
                         if fn.__doc__ else ""} for name, fn in sorted(METHODS.items())]}


# ── catalog / library ────────────────────────────────────────────────────
def catalog_get(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Sensor and resolution names the engine accepts, and the default DORI levels."""
    from ..config import RESOLUTIONS, SENSOR_DIMS_MM
    from ..models import DEFAULT_LEVELS

    return {
        "sensors": [{"name": n, "width_mm": w, "height_mm": h} for n, (w, h) in SENSOR_DIMS_MM.items()],
        "resolutions": [{"name": n, "width_px": w, "height_px": h} for n, (w, h) in RESOLUTIONS.items()],
        "levels": [dataclasses.asdict(lv) for lv in DEFAULT_LEVELS],
    }


def _model_summary(name: str, model: Dict[str, Any]) -> Dict[str, Any]:
    from ..database import is_ptz_camera

    return {
        "name": name,
        "sensor_name": model.get("sensor_name", ""),
        "resolution_name": model.get("resolution_name", ""),
        "focal_min_mm": model.get("focal_min_mm"),
        "focal_max_mm": model.get("focal_max_mm"),
        "ir_range_m": model.get("ir_range_m"),
        "camera_type": model.get("camera_type") or model.get("kamera_tipi", ""),
        "is_ptz": is_ptz_camera(model),
    }


def library_list(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Camera library (built-in + user edits): one summary row per model."""
    from ..database import load_camera_library

    lib = load_camera_library()
    rows: List[Dict[str, Any]] = [_model_summary(n, m) for n, m in lib.items()
                                  if isinstance(m, dict) and m]
    return {"count": len(rows), "models": rows}


def library_get(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Full record of one library model."""
    from ..database import load_camera_library

    name = _require(params, "name", str)
    model = load_camera_library().get(name)
    if not isinstance(model, dict) or not model:
        raise RpcError(APP_ERROR, f"Kütüphanede model yok: {name}")
    return {"summary": _model_summary(name, model), "record": model}


_MODEL_FLOATS = ("focal_min_mm", "focal_max_mm", "ir_range_m", "min_lux", "pole_height_m",
                 "tilt_deg", "target_height_m")


def library_camera(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Camera config for a library model, applied onto ``base`` (or the defaults).

    Same field rule as the classic app's ``apply_camera_model``; mounting
    fields (mast, tilt, target height) are taken from the model only when
    ``with_mounting`` is true, since they describe a site, not the camera.
    """
    from ..database import is_ptz_camera, load_camera_library
    from ..models import CameraConfig

    name = _require(params, "name", str)
    model = load_camera_library().get(name)
    if not isinstance(model, dict) or not model:
        raise RpcError(APP_ERROR, f"Kütüphanede model yok: {name}")
    base = _camera_from(params["base"]) if "base" in params else CameraConfig()
    changes: Dict[str, Any] = {"model_name": name}
    for key in ("sensor_name", "resolution_name"):
        if isinstance(model.get(key), str):
            changes[key] = model[key]
    mounting = ("pole_height_m", "tilt_deg", "target_height_m")
    for key in _MODEL_FLOATS:
        if key in mounting and not params.get("with_mounting"):
            continue
        if isinstance(model.get(key), (int, float)) and not isinstance(model.get(key), bool):
            changes[key] = float(model[key])
    changes["effective_px_ratio"] = float(model.get("effective_px_ratio", 1.0) or 1.0)
    changes["camera_type"] = str(model.get("camera_type") or model.get("kamera_tipi") or "")
    cam = dataclasses.replace(base, **changes)
    _camera_from(dataclasses.asdict(cam))           # same validation as every other entry point
    return {"camera": dataclasses.asdict(cam), "is_ptz": is_ptz_camera(model)}


# ── optics ───────────────────────────────────────────────────────────────
def optics_calculate(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Optic engine for one camera: FOV, dead zone, DORI distances.

    ``mode`` "min"/"max" picks a lens end; ``focal_mm`` evaluates any focal
    length inside the lens range instead (the inspector's slider).
    """
    from ..calculations import calculate_for_camera, camera_optics

    cam = _camera_from(params.get("camera", {}))
    levels = _levels_from(params.get("levels"))
    mode = params.get("mode", "min")
    if mode not in ("min", "max"):
        raise RpcError(INVALID_PARAMS, "'mode' 'min' veya 'max' olmalı.")
    focal = params.get("focal_mm")
    if focal is not None:
        if isinstance(focal, bool) or not isinstance(focal, (int, float)):
            raise RpcError(INVALID_PARAMS, "'focal_mm' sayı olmalı.")
        if not (cam.focal_min_mm <= focal <= cam.focal_max_mm):
            raise RpcError(INVALID_PARAMS, "'focal_mm' lens aralığının dışında.")
        cam = dataclasses.replace(cam, focal_min_mm=float(focal), focal_max_mm=float(focal))
        mode = "min"
    result = calculate_for_camera(cam, mode, levels)
    optics = camera_optics(cam, result.focal_mm, strict=True)
    out = _result_json(result)
    out["optics"] = dataclasses.asdict(optics)
    return out


# ── project ──────────────────────────────────────────────────────────────
def project_load(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Read a .json project file (same schema as the classic application)."""
    from .. import project_io

    path = _require(params, "path", str)
    try:
        project = project_io.load_project(path)
    except FileNotFoundError:
        raise RpcError(APP_ERROR, f"Proje dosyası bulunamadı: {path}") from None
    except ValueError as exc:
        raise RpcError(APP_ERROR, f"Proje dosyası okunamadı: {exc}") from None
    return {"path": path, "project": dataclasses.asdict(project)}


def project_save(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Write a project back in the classic application's schema."""
    from .. import project_io
    from ..models import PPMLevel, TargetPoint

    path = _require(params, "path", str)
    raw = _require(params, "project", dict)
    try:
        data = dict(raw)
        data["cameras"] = [_camera_from(c) for c in data.get("cameras", [])]
        if not data["cameras"]:
            raise RpcError(INVALID_PARAMS, "Projede en az bir kamera olmalı.")
        if "target_point" in data:
            data["target_point"] = TargetPoint(**data["target_point"])
        if "ppm_levels" in data:
            data["ppm_levels"] = [PPMLevel(**lv) for lv in data["ppm_levels"]]
        fields = {f.name for f in dataclasses.fields(project_io.ProjectData)}
        unknown = sorted(set(data) - fields)
        if unknown:
            raise RpcError(INVALID_PARAMS, "Bilinmeyen proje alanı: " + ", ".join(unknown))
        project = project_io.ProjectData(**data)
    except TypeError as exc:
        raise RpcError(INVALID_PARAMS, f"Proje yapısı geçersiz: {exc}") from None
    project_io.save_project(path, project)
    return {"path": path}


METHODS = {
    "system.info": system_info,
    "system.ping": system_ping,
    "system.describe": system_describe,
    "catalog.get": catalog_get,
    "library.list": library_list,
    "library.get": library_get,
    "library.camera": library_camera,
    "optics.calculate": optics_calculate,
    "project.load": project_load,
    "project.save": project_save,
}

# Terrain / viewshed / perimeter methods live in their own module.
from .terrain_methods import METHODS as _TERRAIN_METHODS  # noqa: E402

METHODS.update(_TERRAIN_METHODS)
from .bench_methods import METHODS as _BENCH_METHODS  # noqa: E402

METHODS.update(_BENCH_METHODS)
from .spec_methods import METHODS as _SPEC_METHODS  # noqa: E402

METHODS.update(_SPEC_METHODS)
from .report_methods import METHODS as _REPORT_METHODS  # noqa: E402

METHODS.update(_REPORT_METHODS)
from .ptz_methods import METHODS as _PTZ_METHODS  # noqa: E402

METHODS.update(_PTZ_METHODS)
from .library_methods import METHODS as _LIBRARY_METHODS  # noqa: E402

METHODS.update(_LIBRARY_METHODS)
from .planning_methods import METHODS as _PLANNING_METHODS  # noqa: E402

METHODS.update(_PLANNING_METHODS)
