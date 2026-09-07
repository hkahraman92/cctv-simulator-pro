#!/usr/bin/env python3
"""LoRA fine-tune a small local model on the spec-review dataset.

Pipeline:
  1. build the instruction dataset from the local training log
     (analysis runs + human overrides -> system/user/assistant JSONL),
  2. hold out ~15 % of specs for evaluation,
  3. QLoRA SFT with unsloth,
  4. export a q4_k_m GGUF + an Ollama Modelfile.

    pip install "unsloth[cu121] @ git+https://github.com/unslothai/unsloth.git" trl datasets
    py -3.13 scripts/finetune_compliance.py --out out/cctv-uygunluk

    # just prepare + split the data, no training (no GPU needed):
    py -3.13 scripts/finetune_compliance.py --prepare-only --dataset out/train.jsonl

Needs one 16-24 GB GPU (RTX 3090/4090) or a rented A100; a few hours, ~$5-20.
Evaluate the result with scripts/eval_compliance.py against the eval split.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cctv_simulator import training_log as TL  # noqa: E402

_SYSTEM = ("Sen CCTV teknik şartname analiz uzmanısın. Şartname metninden ölçülebilir "
           "kamera isterlerini çıkarır ve yalnızca geçerli JSON döndürürsün.")

_MODELFILE = """FROM ./{gguf}
PARAMETER temperature 0.1
PARAMETER num_ctx 16384
PARAMETER num_predict 8192
SYSTEM \"\"\"{system}\"\"\"
"""


def _prepare(out_dir: Path, eval_ratio: float):
    out_dir.mkdir(parents=True, exist_ok=True)
    full = out_dir / "dataset_full.jsonl"
    n = TL.build_instruction_dataset(full)
    if n == 0:
        raise SystemExit(
            "Eğitim kaydı boş. Önce şartname tezgâhında birkaç analiz + override yap "
            "(training_log %APPDATA%\\<app>\\training\\compliance.jsonl).")
    recs = [json.loads(l) for l in full.read_text(encoding="utf-8").splitlines() if l.strip()]
    train, ev = TL.split_dataset(recs, eval_ratio=eval_ratio)
    (out_dir / "train.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in train), encoding="utf-8")
    (out_dir / "eval.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in ev), encoding="utf-8")
    print(f"{n} örnek → train {len(train)} / eval {len(ev)}  ({out_dir})")
    return out_dir / "train.jsonl"


def _train(train_jsonl: Path, out_dir: Path, base: str, epochs: int, seq_len: int):
    try:
        from unsloth import FastLanguageModel
        from datasets import Dataset
        from trl import SFTConfig, SFTTrainer
    except ImportError as exc:
        raise SystemExit(
            f"Eğitim bağımlılıkları yok ({exc}).\n"
            '  pip install "unsloth @ git+https://github.com/unslothai/unsloth.git" trl datasets')

    model, tok = FastLanguageModel.from_pretrained(base, max_seq_length=seq_len, load_in_4bit=True)
    model = FastLanguageModel.get_peft_model(
        model, r=32, lora_alpha=32,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])

    rows = [json.loads(l) for l in train_jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]
    ds = Dataset.from_list(
        [{"text": tok.apply_chat_template(r["messages"], tokenize=False)} for r in rows])

    SFTTrainer(
        model=model, tokenizer=tok, train_dataset=ds,
        args=SFTConfig(per_device_train_batch_size=1, gradient_accumulation_steps=8,
                       num_train_epochs=epochs, learning_rate=2e-4, warmup_ratio=0.05,
                       logging_steps=5, output_dir=str(out_dir / "checkpoints"),
                       optim="adamw_8bit", seed=0),
    ).train()

    name = out_dir.name
    model.save_pretrained_gguf(str(out_dir / name), tok, quantization_method="q4_k_m")
    gguf = f"{name}.Q4_K_M.gguf"
    (out_dir / "Modelfile").write_text(
        _MODELFILE.format(gguf=gguf, system=_SYSTEM), encoding="utf-8")
    print(f"\nGGUF + Modelfile: {out_dir}\n"
          f"  ollama create {name} -f {out_dir / 'Modelfile'}\n"
          f"  py -3.13 scripts/eval_compliance.py {out_dir / 'eval.jsonl'} --model {name} --baseline qwen2.5:7b")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("out/cctv-uygunluk"))
    ap.add_argument("--dataset", type=Path, default=None,
                    help="hazır train.jsonl (verilmezse training_log'dan üretilir)")
    ap.add_argument("--base", default="unsloth/Qwen2.5-7B-Instruct-bnb-4bit")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--seq-len", type=int, default=16384)
    ap.add_argument("--eval-ratio", type=float, default=0.15)
    ap.add_argument("--prepare-only", action="store_true", help="veriyi hazırla, eğitme")
    args = ap.parse_args(argv)

    train_jsonl = args.dataset or _prepare(args.out, args.eval_ratio)
    if args.prepare_only:
        return 0
    _train(Path(train_jsonl), args.out, args.base, args.epochs, args.seq_len)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
