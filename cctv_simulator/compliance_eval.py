"""Evaluation metrics for the spec-review model (local or Gemini).

Compares a model's structured output against a gold answer for one spec:

- ``json_parse_ok``       — did the reply parse as the expected JSON object,
- ``dori_requirement_f1`` — did it find the same measurable DORI/range
  requirements the rule engine / a human found (precision / recall / F1 on the
  ``(task, distance_m, required_ppm)`` set),
- ``matrix_status_accuracy`` — of the compliance-matrix verdicts, how many match
  the gold ``(requirement_id, camera_model) -> status`` map.

No GPU, no network. Feed it dicts; ``scripts/eval_compliance.py`` wires it to a
gold JSONL and a model runner.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

_PR = Tuple[float, float, float]   # precision, recall, f1


def _req_key(r: Dict[str, Any]) -> Tuple[str, float, float]:
    return (
        str(r.get("task") or r.get("requirement", "")).strip().lower(),
        round(float(r.get("distance_m", 0) or 0), 1),
        round(float(r.get("required_ppm", 0) or 0), 1),
    )


def dori_requirement_prf(pred: Dict[str, Any], gold: Dict[str, Any]) -> _PR:
    p = {_req_key(r) for r in pred.get("requirements", []) if r.get("category") == "dori"}
    g = {_req_key(r) for r in gold.get("requirements", []) if r.get("category") == "dori"}
    if not g and not p:
        return (1.0, 1.0, 1.0)
    tp = len(p & g)
    precision = tp / len(p) if p else 0.0
    recall = tp / len(g) if g else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return (precision, recall, f1)


def _status_map(result: Dict[str, Any]) -> Dict[Tuple[str, str], str]:
    out: Dict[Tuple[str, str], str] = {}
    for m in result.get("matrix", []):
        key = (str(m.get("requirement_id", "")), str(m.get("camera_model", "")))
        out[key] = str(m.get("user_status") or m.get("status", "")).strip()
    return out


def matrix_status_accuracy(pred: Dict[str, Any], gold: Dict[str, Any]) -> Tuple[float, int]:
    """(accuracy, n_compared) over rows present in the gold matrix."""
    g = _status_map(gold)
    if not g:
        return (1.0, 0)
    p = _status_map(pred)
    hits = sum(1 for k, v in g.items() if p.get(k, "").casefold() == v.casefold())
    return (hits / len(g), len(g))


def evaluate_one(pred: Optional[Dict[str, Any]], gold: Dict[str, Any]) -> Dict[str, Any]:
    if not pred:
        return {"json_parse_ok": False, "dori_precision": 0.0, "dori_recall": 0.0,
                "dori_f1": 0.0, "status_accuracy": 0.0, "status_n": 0}
    pr, rc, f1 = dori_requirement_prf(pred, gold)
    acc, n = matrix_status_accuracy(pred, gold)
    return {"json_parse_ok": True, "dori_precision": pr, "dori_recall": rc,
            "dori_f1": f1, "status_accuracy": acc, "status_n": n}


def aggregate(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)
    n = len(rows)
    if not n:
        return {"n": 0}
    parsed = [r for r in rows if r.get("json_parse_ok")]
    status_rows = [r for r in rows if r.get("status_n", 0) > 0]

    def _mean(key: str, src: List[Dict[str, Any]]) -> float:
        return sum(r.get(key, 0.0) for r in src) / len(src) if src else 0.0

    total_status = sum(r.get("status_n", 0) for r in status_rows)
    weighted_acc = (
        sum(r["status_accuracy"] * r["status_n"] for r in status_rows) / total_status
        if total_status else 0.0
    )
    return {
        "n": n,
        "json_parse_rate": len(parsed) / n,
        "dori_precision": _mean("dori_precision", parsed),
        "dori_recall": _mean("dori_recall", parsed),
        "dori_f1": _mean("dori_f1", parsed),
        "status_accuracy_weighted": weighted_acc,
        "status_rows_compared": total_status,
    }


def evaluate_predictions(pairs: Iterable[Tuple[Optional[Dict[str, Any]], Dict[str, Any]]]) -> Dict[str, Any]:
    return aggregate(evaluate_one(pred, gold) for pred, gold in pairs)
