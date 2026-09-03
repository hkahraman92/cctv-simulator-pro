"""Reusable requirement sets / firm spec templates for the compliance review.

A firm re-uses the same boilerplate ("our standard outdoor fixed profile").
Instead of re-extracting from scratch every time, save the extracted (and
hand-corrected) requirement list as a named template and reload it.

Stored as JSON under the per-user data dir, one file per template.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


def _dir() -> Optional[Path]:
    try:
        from .config import user_data_dir
        p = user_data_dir() / "spec-templates"
        p.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        return None


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-")
    return s or "sablon"


def list_templates() -> List[str]:
    d = _dir()
    if d is None:
        return []
    return sorted(p.stem for p in d.glob("*.json"))


def save_template(name: str, requirements: List[Dict[str, Any]],
                  meta: Optional[Dict[str, Any]] = None) -> bool:
    d = _dir()
    if d is None or not name.strip():
        return False
    payload = {
        "name": name.strip(),
        "meta": meta or {},
        "requirements": [_clean_req(r) for r in requirements],
    }
    try:
        (d / f"{_slug(name)}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def load_template(name: str) -> Optional[Dict[str, Any]]:
    d = _dir()
    if d is None:
        return None
    path = d / f"{_slug(name)}.json"
    if not path.is_file():
        # also accept an exact stem
        path = d / f"{name}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    data.setdefault("requirements", [])
    data.setdefault("name", name)
    return data


def delete_template(name: str) -> bool:
    d = _dir()
    if d is None:
        return False
    for cand in (d / f"{_slug(name)}.json", d / f"{name}.json"):
        if cand.is_file():
            try:
                cand.unlink()
                return True
            except OSError:
                return False
    return False


_KEEP = ("id", "profile_id", "profile_name", "category", "requirement", "weight",
         "value", "ranges", "mode", "task", "required_ppm", "distance_m",
         "confidence", "spec_quote", "standard_clause", "standard_desc",
         "user_note", "user_status")


def _clean_req(r: Dict[str, Any]) -> Dict[str, Any]:
    return {k: r[k] for k in _KEEP if k in r}
