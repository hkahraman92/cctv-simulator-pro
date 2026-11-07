"""API v1: specification compliance (rule engine + optic engine, local / Gemini LLM),
requirement templates, human overrides (training log), EN 62676-4 statement."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from .params import _require
from .protocol import APP_ERROR, INVALID_PARAMS, Cancelled, Context, RpcError

_MAX_SPEC_CHARS = 400_000


def _text(params: Dict[str, Any]) -> str:
    text = _require(params, "text", str).strip()
    if not text:
        raise RpcError(INVALID_PARAMS, "Şartname metni boş.")
    if len(text) > _MAX_SPEC_CHARS:
        raise RpcError(INVALID_PARAMS, "Şartname metni çok uzun (400 bin karakter sınırı).")
    return text


def spec_read_file(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Text of a specification file (.pdf via pypdf, .txt / .md as UTF-8)."""
    path = Path(_require(params, "path", str))
    if not path.is_file():
        raise RpcError(APP_ERROR, f"Dosya bulunamadı: {path}")
    if path.suffix.lower() == ".pdf":
        from ..spec_pdf import extract_text
        text = extract_text(path)
        if text is None:
            raise RpcError(APP_ERROR, "PDF'ten metin çıkarılamadı (pypdf kurulu değil ya da taranmış görüntü). "
                                      "Gemini yolu PDF'i doğrudan okuyabilir.", {"pdf_only": True})
        return {"path": str(path), "text": text, "pdf": True, "chars": len(text)}
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp1254")          # Turkish Windows text files
    return {"path": str(path), "text": text, "pdf": False, "chars": len(text)}


def spec_engines(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Which analysis paths are available right now."""
    from ..compliance import OLLAMA_MODELS, ollama_available

    return {"ollama": ollama_available(), "ollama_models": OLLAMA_MODELS,
            "gemini_key_in_env": bool(os.environ.get("GEMINI_API_KEY", "").strip()),
            "gemini_models": ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash-lite"]}


def spec_analyze(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Compliance matrix. ``engine``: rule (offline) | ollama | gemini. An LLM path
    that fails falls back to the rule engine and says why (``fallback_reason``)."""
    from .. import training_log
    from ..compliance import analyze_with_ollama, call_gemini, gemini_request, rule_based_compliance
    from ..database import load_camera_library

    text = _text(params)
    engine = params.get("engine", "rule")
    if engine not in ("rule", "ollama", "gemini"):
        raise RpcError(INVALID_PARAMS, "'engine' rule, ollama ya da gemini olmalı.")
    library = load_camera_library()
    result, source, fallback = None, engine, ""
    if engine == "ollama":
        ctx.progress(0, 1, "Yerel model analiz ediyor (ilk çağrı model yüklenirken uzun sürebilir)")
        result = analyze_with_ollama(text, library, model=str(params.get("model") or "qwen2.5:7b"))
        if result is None:
            fallback = "Ollama yanıt vermedi ya da geçerli JSON üretmedi."
    elif engine == "gemini":
        key = str(params.get("api_key") or os.environ.get("GEMINI_API_KEY", "")).strip()
        if not key:
            fallback = "Gemini API anahtarı yok."
        else:
            pdf = None
            if params.get("pdf_path"):
                try:
                    pdf = Path(str(params["pdf_path"])).read_bytes()
                except OSError:
                    pdf = None
            req = gemini_request(text, library, key, str(params.get("model") or "gemini-2.0-flash"), pdf)
            ctx.progress(0, 1, "Gemini'ye gönderildi")
            try:
                result = call_gemini(req, on_status=lambda m: ctx.progress(0, 1, m),
                                     is_cancelled=lambda: ctx.token.cancelled)
            except Cancelled:
                raise
            except Exception as exc:
                if ctx.token.cancelled:
                    raise Cancelled() from None
                fallback = f"Gemini yanıtı alınamadı: {exc}"
    if result is None:
        ctx.progress(1, 1, "Kurallı + fizik analizi")
        result, source = rule_based_compliance(text, library), "rule"
    try:
        training_log.log_analysis(text, result, source)
    except Exception:
        pass
    return {"result": result, "source": source, "fallback_reason": fallback}


def spec_override(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Record a human correction of one matrix row (the training gold)."""
    from .. import training_log

    text = _text(params)
    row = _require(params, "row", dict)
    new_status = _require(params, "new_status", str)
    if new_status not in ("Uyumlu", "Kısmi", "Uyumsuz", "Bulunamadı"):
        raise RpcError(INVALID_PARAMS, "Geçersiz durum.")
    logged = training_log.log_override(text, row, str(row.get("status", "")), new_status,
                                       str(params.get("note", "")))
    return {"logged": bool(logged)}


def spec_templates(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Saved requirement templates (names)."""
    from .. import requirement_library as RL

    return {"templates": RL.list_templates()}


def spec_template_save(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Save a result's requirements as a named template."""
    from .. import requirement_library as RL

    name = _require(params, "name", str).strip()
    reqs = _require(params, "requirements", list)
    if not name or not reqs:
        raise RpcError(INVALID_PARAMS, "Şablon adı ve en az bir ister gerekli.")
    if not RL.save_template(name, reqs):
        raise RpcError(APP_ERROR, "Şablon kaydedilemedi (kullanıcı klasörü yazılamıyor).")
    return {"name": name}


def spec_template_run(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Re-evaluate a saved template against the current camera library."""
    from .. import requirement_library as RL
    from ..compliance import evaluate_template
    from ..database import load_camera_library

    name = _require(params, "name", str)
    tpl = RL.load_template(name)
    if not tpl or not tpl.get("requirements"):
        raise RpcError(APP_ERROR, f"Şablon bulunamadı ya da boş: {name}")
    tpl.setdefault("name", name)
    return {"result": evaluate_template(tpl, load_camera_library()), "source": "template", "fallback_reason": ""}


def spec_template_delete(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Delete a saved template."""
    from .. import requirement_library as RL

    name = _require(params, "name", str)
    if not RL.delete_template(name):
        raise RpcError(APP_ERROR, f"Şablon silinemedi: {name}")
    return {"deleted": name}


def spec_statement(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """EN 62676-4 compliance statement: markdown, and a PDF when ``pdf_path`` is given."""
    from ..compliance_report import build_statement, write_statement_pdf

    result = _require(params, "result", dict)
    kw = {"project_name": str(params.get("project_name", "")), "bidder": str(params.get("bidder", ""))}
    chosen = params.get("chosen_by_profile")
    if isinstance(chosen, dict):
        kw["chosen_by_profile"] = {str(k): str(v) for k, v in chosen.items()}
    md = build_statement(result, **kw)
    out: Dict[str, Any] = {"markdown": md}
    if params.get("pdf_path"):
        write_statement_pdf(str(params["pdf_path"]), result, **kw)
        out["pdf_path"] = str(params["pdf_path"])
    return out


def training_export(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Write the instruction-tuning JSONL from analyses + overrides."""
    from .. import training_log as TL

    st = TL.stats()
    if params.get("path"):
        if not st.get("records"):
            raise RpcError(APP_ERROR, "Henüz kayıt yok. Analiz çalıştırın ve kararları düzeltin.")
        n = TL.build_instruction_dataset(str(params["path"]))
        return {**st, "written": n, "path": str(params["path"])}
    return st


METHODS = {
    "spec.read_file": spec_read_file,
    "spec.engines": spec_engines,
    "spec.analyze": spec_analyze,
    "spec.override": spec_override,
    "spec.templates": spec_templates,
    "spec.template_save": spec_template_save,
    "spec.template_run": spec_template_run,
    "spec.template_delete": spec_template_delete,
    "spec.statement": spec_statement,
    "training.export": training_export,
}
