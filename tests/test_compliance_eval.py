"""Spec-review evaluation metrics (no GPU, no network)."""
from __future__ import annotations

from cctv_simulator.compliance_eval import (
    aggregate,
    dori_requirement_prf,
    evaluate_one,
    evaluate_predictions,
    matrix_status_accuracy,
)
from cctv_simulator.training_log import split_dataset

_GOLD = {
    "requirements": [
        {"id": "P1-D1", "category": "dori", "task": "identify", "distance_m": 30, "required_ppm": 250},
        {"id": "P1-D2", "category": "dori", "task": "plaka", "distance_m": 25, "required_ppm": 143},
        {"id": "P1-R1", "category": "resolution", "requirement": "8 MP"},
    ],
    "matrix": [
        {"requirement_id": "P1-D1", "camera_model": "A", "status": "Uyumlu"},
        {"requirement_id": "P1-D2", "camera_model": "A", "status": "Uyumsuz"},
    ],
}


def test_perfect_prediction_scores_one():
    r = evaluate_one(_GOLD, _GOLD)
    assert r["json_parse_ok"] and r["dori_f1"] == 1.0 and r["status_accuracy"] == 1.0


def test_missing_one_requirement_halves_recall():
    pred = {"requirements": _GOLD["requirements"][:1], "matrix": _GOLD["matrix"]}
    pr, rc, f1 = dori_requirement_prf(pred, _GOLD)
    assert pr == 1.0 and rc == 0.5
    assert 0.66 < f1 < 0.67


def test_wrong_status_drops_accuracy():
    pred = {
        "requirements": _GOLD["requirements"],
        "matrix": [
            {"requirement_id": "P1-D1", "camera_model": "A", "status": "Uyumlu"},
            {"requirement_id": "P1-D2", "camera_model": "A", "status": "Uyumlu"},  # wrong
        ],
    }
    acc, n = matrix_status_accuracy(pred, _GOLD)
    assert n == 2 and acc == 0.5


def test_user_override_status_is_preferred_as_prediction():
    pred = {
        "requirements": _GOLD["requirements"],
        "matrix": [
            {"requirement_id": "P1-D1", "camera_model": "A", "status": "Uyumsuz", "user_status": "Uyumlu"},
            {"requirement_id": "P1-D2", "camera_model": "A", "status": "Uyumsuz"},
        ],
    }
    acc, _ = matrix_status_accuracy(pred, _GOLD)
    assert acc == 1.0


def test_none_prediction_is_a_parse_failure():
    r = evaluate_one(None, _GOLD)
    assert r["json_parse_ok"] is False and r["dori_f1"] == 0.0


def test_aggregate_weights_status_accuracy_by_row_count():
    agg = evaluate_predictions([
        (_GOLD, _GOLD),                       # 2 rows, all correct
        (None, _GOLD),                        # parse fail
    ])
    assert agg["n"] == 2
    assert agg["json_parse_rate"] == 0.5
    assert agg["status_accuracy_weighted"] == 1.0     # only the parsed one contributes
    assert agg["dori_f1"] == 1.0                      # mean over PARSED rows only


def test_split_dataset_keeps_specs_whole():
    recs = [{"meta": {"spec_sha": f"s{i}"}, "messages": []} for i in range(20)]
    train, ev = split_dataset(recs, eval_ratio=0.25, seed=1)
    assert len(train) + len(ev) == 20
    tr_shas = {r["meta"]["spec_sha"] for r in train}
    ev_shas = {r["meta"]["spec_sha"] for r in ev}
    assert tr_shas.isdisjoint(ev_shas)
    assert 3 <= len(ev) <= 7
