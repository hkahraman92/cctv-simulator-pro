"""API v1: report files — optic tables (CSV / XLSX / PDF) and the terrain engineering report.

Same writers as the classic app and the headless CLI (``exporters``): a report
from the new shell is the same document, not a look-alike.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from . import session as _s
from .params import _camera_from, _levels_from, _require
from .protocol import APP_ERROR, INVALID_PARAMS, Context, RpcError

_FORMATS = {"csv", "xlsx", "pdf"}


def _formats(params: Dict[str, Any], allowed=_FORMATS) -> List[str]:
    f = params.get("formats")
    if not isinstance(f, list) or not f or not set(f) <= allowed:
        raise RpcError(INVALID_PARAMS, f"'formats' şunlardan biri ya da birkaçı olmalı: {', '.join(sorted(allowed))}")
    return list(dict.fromkeys(f))


def _out_dir(params: Dict[str, Any]) -> Path:
    d = Path(_require(params, "out_dir", str))
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RpcError(APP_ERROR, f"Klasör oluşturulamadı: {exc}") from None
    return d


def _stem(params: Dict[str, Any]) -> str:
    import re
    stem = str(params.get("stem") or "cctv-rapor").strip()
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", stem).strip(". ") or "cctv-rapor"
    return stem[:120]


def report_optics(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Optic table report for the project's cameras (the classic app's CSV / XLSX / PDF)."""
    from .. import exporters
    from ..calculations import analyze_dead_zone_coverage, calculate_for_camera
    from ..models import TargetPoint

    cams = params.get("cameras")
    if not isinstance(cams, list) or not cams:
        raise RpcError(INVALID_PARAMS, "'cameras' en az bir kamera içermeli.")
    cameras = [_camera_from(c) for c in cams]
    names = [c.name for c in cameras]
    if len(set(names)) != len(names):
        raise RpcError(INVALID_PARAMS, "Kamera adları benzersiz olmalı (rapor satırları ada göre gruplanır).")
    mode = params.get("mode", "compare")
    if mode not in ("min", "max", "compare"):
        raise RpcError(INVALID_PARAMS, "'mode' min, max ya da compare olmalı.")
    formats = _formats(params)
    out = _out_dir(params)
    stem = _stem(params)
    modes = ["min", "max"] if mode == "compare" else [mode]
    levels = _levels_from(params.get("levels"))
    results = {c.name: [calculate_for_camera(c, m, levels) for m in modes] for c in cameras}
    analyze_dead_zone_coverage(results, cameras)
    target, level_name = TargetPoint(), str(params.get("level_name", ""))
    written = []
    for i, fmt in enumerate(formats):
        ctx.progress(i, len(formats), f"{fmt.upper()} yazılıyor")
        p = out / f"{stem}.{fmt}"
        if fmt == "csv":
            exporters.export_csv(str(p), results)
        elif fmt == "xlsx":
            exporters.export_excel(str(p), cameras, mode, results, target, level_name)
        else:
            exporters.export_pdf(str(p), cameras, mode, results, target, level_name, "")
        written.append(str(p))
    return {"files": written}


def report_engineering(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Terrain engineering report (ASELSAN format): the last single viewshed, combined
    coverage and perimeter plan of a terrain, whichever have been run."""
    from .. import exporters

    entry = _s.SESSION.get(params.get("terrain_id"))
    terrain = entry.terrain(bool(params.get("use_obstacles", True)))
    formats = _formats(params, {"pdf", "csv"})
    out = _out_dir(params)
    stem = _stem(params)
    camera = _camera_from(params.get("camera", {}))
    single = entry.results.get("single")
    multi = entry.results.get("multi")
    plan = entry.results.get("plan", (None,))[0]
    if single is None and multi is None and plan is None:
        raise RpcError(APP_ERROR, "Bu arazide henüz analiz yok: önce görüş alanı, çit planı ya da birleşik kapsama çalıştırın.")
    kw = dict(project_name=str(params.get("project_name") or stem), terrain=terrain, camera=camera,
              weather=str(params.get("weather", "")), viewshed=single, perimeter=plan, multi_viewshed=multi)
    written = []
    for i, fmt in enumerate(formats):
        ctx.progress(i, len(formats), f"{fmt.upper()} yazılıyor")
        p = out / f"{stem}-gorusalani.{fmt}"
        (exporters.export_engineering_report_pdf if fmt == "pdf" else exporters.export_engineering_report_csv)(str(p), **kw)
        written.append(str(p))
    return {"files": written, "sections": {"viewshed": single is not None, "coverage": multi is not None,
                                           "perimeter": plan is not None}, "measured": bool(terrain.is_measured)}


def report_open(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Open a written report with the system's default application (Windows only)."""
    import os

    p = Path(_require(params, "path", str))
    if p.suffix.lower() not in (".pdf", ".csv", ".xlsx", ".md", ".jsonl"):
        raise RpcError(INVALID_PARAMS, "Yalnız rapor dosyaları açılabilir (pdf, csv, xlsx, md, jsonl).")
    if not p.is_file():
        raise RpcError(APP_ERROR, f"Dosya bulunamadı: {p}")
    if not hasattr(os, "startfile"):
        raise RpcError(APP_ERROR, "Bu platformda dosya açma desteklenmiyor.")
    os.startfile(str(p))  # noqa: S606 — a file the core itself just wrote
    return {"opened": str(p)}


METHODS = {
    "report.optics": report_optics,
    "report.engineering": report_engineering,
    "report.open": report_open,
}
