"""API v1: camera database editing (admin password, same JSON overlay as the classic app).

Writes are stateless: every write carries the admin password and is checked
against the classic app's ``admin_settings.json`` (default "admin").
"""
from __future__ import annotations

import hmac
from typing import Any, Dict

from .params import _require
from .protocol import APP_ERROR, INVALID_PARAMS, Context, RpcError

# Core optic fields shown first in the form (the rest come from the extended specs).
_CORE_FIELDS = (
    ("model_name", "Model adı"), ("stock_code", "Stok kodu"), ("kamera_tipi", "Kamera tipi"),
    ("sensor_name", "Sensör"), ("resolution_name", "Çözünürlük"),
    ("focal_min_mm", "Odak min (mm)"), ("focal_max_mm", "Odak max (mm)"),
    ("ir_range_m", "IR menzili (m)"), ("min_lux", "En düşük ışık (lux)"),
    ("effective_px_ratio", "Etkin piksel oranı k"), ("source_sheet", "Kaynak"),
)
_MULTILINE = frozenset({"overview", "highlights", "image_enhancements", "network_protocols", "cyber_security",
                        "basic_analytics", "ai_analytics"})


def _check(params: Dict[str, Any]) -> None:
    from ..config import get_admin_password

    given = str(params.get("password", ""))
    if not hmac.compare_digest(given.encode("utf-8"), get_admin_password().encode("utf-8")):
        raise RpcError(APP_ERROR, "Yönetici şifresi hatalı.", {"auth": False})


def library_fields(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Form layout of a camera record: core optic fields, then the brochure fields."""
    from ..config import RESOLUTIONS, SENSOR_DIMS_MM
    from ..database import CAMERA_DB_NUMERIC_KEYS, camera_db_extended_field_specs

    fields = [{"key": k, "label": lbl, "group": "optik"} for k, lbl in _CORE_FIELDS]
    fields += [{"key": k, "label": lbl, "group": "broşür"} for k, lbl in camera_db_extended_field_specs()]
    for f in fields:
        f["numeric"] = f["key"] in CAMERA_DB_NUMERIC_KEYS
        f["multiline"] = f["key"] in _MULTILINE
    return {"fields": fields, "sensors": list(SENSOR_DIMS_MM), "resolutions": list(RESOLUTIONS)}


def library_record(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """One record with what is missing (critical / completable) and whether it is user-editable."""
    from ..config import DEFAULT_CAMERA_LIBRARY
    from ..database import camera_db_missing_fields, load_camera_library, read_camera_library_json

    name = _require(params, "name", str)
    model = load_camera_library().get(name)
    if not isinstance(model, dict) or not model:
        raise RpcError(APP_ERROR, f"Kütüphanede model yok: {name}")
    req, imp = camera_db_missing_fields(model, fallback_name=name)
    overlay = read_camera_library_json()
    return {"name": name, "record": model, "missing_required": req, "missing_important": imp,
            "builtin": name in DEFAULT_CAMERA_LIBRARY, "user_entry": isinstance(overlay.get(name), dict)}


def library_auth(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Check the admin password (the form unlocks; every write checks again)."""
    _check(params)
    return {"ok": True}


def library_save(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Create / update / rename a model in the user overlay (numbers in Turkish notation accepted)."""
    from ..database import CAMERA_DB_NUMERIC_KEYS, load_camera_library, parse_tr_number, save_library_model

    _check(params)
    record = _require(params, "record", dict)
    name = str(record.get("model_name", "")).strip()
    if not name:
        raise RpcError(INVALID_PARAMS, "Model adı boş olamaz.")
    old = params.get("old_name") or None
    library = load_camera_library()
    if old != name and name in library and not params.get("overwrite"):
        raise RpcError(APP_ERROR, f"{name} zaten var.", {"exists": True})
    base = dict(library.get(old or name, {}) or {})
    for key, value in record.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            base.pop(key, None)
            continue
        if key in CAMERA_DB_NUMERIC_KEYS and not isinstance(value, (int, float)):
            try:
                value = parse_tr_number(str(value))
            except ValueError:
                raise RpcError(INVALID_PARAMS, f"{key} sayısal olmalı.", {"field": key}) from None
            value = int(value) if float(value).is_integer() else value
        base[key] = value.strip() if isinstance(value, str) else value
    base["model_name"] = name
    save_library_model(name, base, old)
    return {"name": name, "renamed_from": old if old and old != name else None}


def library_delete(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Delete a user-added model (built-in models cannot be deleted)."""
    from ..database import delete_library_model

    _check(params)
    name = _require(params, "name", str)
    if not delete_library_model(name):
        raise RpcError(APP_ERROR, "Bu hazır şablon modeldir ve kullanıcı veritabanında saklanmaz; silinemez.")
    return {"deleted": name}


def library_set_password(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Change the admin password (current password required)."""
    from ..config import set_admin_password

    _check(params)
    new = str(params.get("new_password", "")).strip()
    if not new:
        raise RpcError(INVALID_PARAMS, "Yeni şifre boş olamaz.")
    set_admin_password(new)
    return {"ok": True}


METHODS = {
    "library.fields": library_fields,
    "library.record": library_record,
    "library.auth": library_auth,
    "library.save": library_save,
    "library.delete": library_delete,
    "library.set_password": library_set_password,
}
