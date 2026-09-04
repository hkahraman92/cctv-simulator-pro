"""Local dataset builder for the spec-review model.

Every analysis run and every human correction in ``spec_assistant`` is appended
here as JSONL. ``build_instruction_dataset`` turns the log into
system/user/assistant pairs — the thing you feed to unsloth / axolotl to
fine-tune a small local model, or use as few-shot examples.

Nothing leaves the machine. Stored under the per-user data dir.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOG_NAME = "compliance.jsonl"


def _dir() -> Optional[Path]:
    try:
        from .config import user_data_dir
        p = user_data_dir() / "training"
        p.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        return None


def _log_path() -> Optional[Path]:
    d = _dir()
    return (d / _LOG_NAME) if d else None


def _sha(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:12]


def _append(record: Dict[str, Any]) -> bool:
    path = _log_path()
    if path is None:
        return False
    record["ts"] = _dt.datetime.now().isoformat(timespec="seconds")
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def log_analysis(spec_text: str, result: Dict[str, Any], source: str) -> bool:
    """One record per analysis: the spec and the model's structured output."""
    if not spec_text or not result:
        return False
    return _append({
        "kind": "analysis",
        "source": source,                         # gemini | ollama | rule | template
        "spec_sha": _sha(spec_text),
        "spec_text": spec_text[:40000],
        "output": {
            "profiles": result.get("profiles", []),
            "requirements": result.get("requirements", []),
            "matrix": [
                {k: m.get(k) for k in ("profile_name", "requirement_id", "requirement",
                                       "camera_model", "status", "evidence", "evidence_kind")}
                for m in result.get("matrix", [])
            ],
            "camera_scores": result.get("camera_scores", []),
            "recommendation": result.get("recommendation", ""),
        },
    })


def log_override(spec_text: str, row: Dict[str, Any], old_status: str,
                 new_status: str, note: str) -> bool:
    """One record per human correction — the training gold."""
    return _append({
        "kind": "override",
        "spec_sha": _sha(spec_text or ""),
        "requirement_id": row.get("requirement_id", ""),
        "requirement": row.get("requirement", ""),
        "camera_model": row.get("camera_model", ""),
        "engine_status": old_status,
        "human_status": new_status,
        "note": note,
        "evidence": row.get("evidence", ""),
    })


def read_all() -> List[Dict[str, Any]]:
    path = _log_path()
    if path is None or not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def stats() -> Dict[str, int]:
    recs = read_all()
    return {
        "records": len(recs),
        "analyses": sum(1 for r in recs if r.get("kind") == "analysis"),
        "overrides": sum(1 for r in recs if r.get("kind") == "override"),
        "unique_specs": len({r.get("spec_sha") for r in recs}),
    }


_SYSTEM = (
    "Sen CCTV teknik şartname analiz uzmanısın. Şartname metninden ölçülebilir "
    "kamera isterlerini çıkarır ve yalnızca geçerli JSON döndürürsün."
)


def build_instruction_dataset(out_path: str | Path) -> int:
    """Write system/user/assistant JSONL for fine-tuning. Human overrides are
    folded into the assistant target so the model learns the corrections.
    Returns the number of examples written."""
    recs = read_all()
    analyses = [r for r in recs if r.get("kind") == "analysis"]
    overrides: Dict[str, List[Dict[str, Any]]] = {}
    for r in recs:
        if r.get("kind") == "override":
            overrides.setdefault(r.get("spec_sha", ""), []).append(r)

    seen: set = set()
    n = 0
    with Path(out_path).open("w", encoding="utf-8") as f:
        for r in analyses:
            sha = r.get("spec_sha", "")
            if sha in seen:
                continue
            seen.add(sha)
            output = json.loads(json.dumps(r.get("output", {})))  # deep copy
            corr = overrides.get(sha, [])
            applied = 0
            if corr:
                by_id = {(o["requirement_id"], o["camera_model"]): o for o in corr}
                for m in output.get("matrix", []):
                    o = by_id.get((m.get("requirement_id"), m.get("camera_model")))
                    if o:
                        m["status"] = o["human_status"]
                        if o.get("note"):
                            m["evidence"] = (m.get("evidence", "") + f" [insan: {o['note']}]").strip()
                        applied += 1
            f.write(json.dumps({
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": (r.get("spec_text", "") or "").strip()},
                    {"role": "assistant", "content": json.dumps(output, ensure_ascii=False)},
                ],
                "meta": {"spec_sha": sha, "source": r.get("source"), "corrections_applied": applied},
            }, ensure_ascii=False) + "\n")
            n += 1
    return n
