#!/usr/bin/env python3
"""Evaluate a spec-review model against a gold JSONL.

Gold file: one JSON object per line, each ``{"spec_text": "...", "gold": {<result>}}``
(or a plain instruction record from ``training_log.build_instruction_dataset`` —
the assistant message is taken as gold).

    # rule engine (offline baseline)
    py -3.13 scripts/eval_compliance.py gold.jsonl --model rule

    # a local Ollama model
    py -3.13 scripts/eval_compliance.py gold.jsonl --model qwen2.5:7b

    # a candidate against the baseline, side by side
    py -3.13 scripts/eval_compliance.py gold.jsonl --model cctv-uygunluk --baseline rule

No GPU. `--model rule` needs nothing; an Ollama model needs `ollama serve`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cctv_simulator.compliance_eval import evaluate_predictions  # noqa: E402


def _load_gold(path: Path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if "gold" in rec and "spec_text" in rec:
            rows.append((rec["spec_text"], rec["gold"]))
            continue
        if "messages" in rec:  # training_log instruction format
            msgs = {m["role"]: m["content"] for m in rec["messages"]}
            spec = msgs.get("user", "")
            try:
                gold = json.loads(msgs.get("assistant", "{}"))
            except ValueError:
                continue
            rows.append((spec, gold))
    return rows


def _runner(name: str):
    if name == "rule":
        from cctv_simulator.compliance import rule_based_compliance

        def run(spec: str):
            try:
                return rule_based_compliance(spec, {})
            except Exception:
                return None
        return run

    from cctv_simulator.compliance import analyze_with_ollama

    def run(spec: str):
        return analyze_with_ollama(spec, {}, model=name)
    return run


def _report(label: str, agg: dict) -> None:
    if agg.get("n", 0) == 0:
        print(f"[{label}] boş küme"); return
    print(f"\n=== {label} (n={agg['n']}) ===")
    print(f"  JSON parse oranı        : {agg['json_parse_rate']:.1%}")
    print(f"  DORI ister precision    : {agg['dori_precision']:.3f}")
    print(f"  DORI ister recall       : {agg['dori_recall']:.3f}")
    print(f"  DORI ister F1           : {agg['dori_f1']:.3f}")
    print(f"  Matris durum doğruluğu  : {agg['status_accuracy_weighted']:.1%} "
          f"({agg['status_rows_compared']} satır)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("gold", type=Path)
    ap.add_argument("--model", default="rule", help='"rule" veya bir Ollama model adı')
    ap.add_argument("--baseline", default="", help="karşılaştırma için ikinci model")
    ap.add_argument("--limit", type=int, default=0, help="ilk N örnek")
    args = ap.parse_args(argv)

    if not args.gold.is_file():
        ap.error(f"gold dosyası yok: {args.gold}")
    rows = _load_gold(args.gold)
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        ap.error("gold dosyasından örnek çıkarılamadı")
    print(f"{len(rows)} değerlendirme örneği", file=sys.stderr)

    for label in filter(None, [args.model, args.baseline]):
        run = _runner(label)
        preds = [(run(spec), gold) for spec, gold in rows]
        _report(label, evaluate_predictions(preds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
