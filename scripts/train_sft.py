"""Supervised warm-up before GRPO (milestone 4).

The 300-step GRPO run left dollar questions untouched: the base model keeps
every figure in the table's thousands or millions, so all completions in a
group score 0 and GRPO has nothing to compare. This script fine-tunes a LoRA
adapter on a few hundred worked answers that convert the scale explicitly
(`python -m xbrlbench export-rl --format sft`), merges it, and saves a
model that scripts/train_grpo.py can start from (--model <out>/merged).

    python scripts/train_sft.py --train data/rl/warmup.jsonl --out runs/sft
    python scripts/train_grpo.py --train data/rl/train.jsonl --model runs/sft/merged

The worked answers are generated from the questions' own tagged inputs, so
the warm-up costs nothing and uses no other model.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from xbrlbench.io_utils import load_jsonl  # noqa: E402


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--train", required=True, help="SFT-format JSONL from `xbrlbench export-rl --format sft`")
    p.add_argument("--out", default="runs/sft")
    p.add_argument("--model", default="Qwen/Qwen3-1.7B")
    p.add_argument("--limit", type=int, default=None, help="use only the first N examples")
    p.add_argument("--epochs", type=float, default=1.0)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--per-device-batch", type=int, default=2)
    p.add_argument("--grad-accum", type=int, default=8)
    p.add_argument("--max-length", type=int, default=4096)
    p.add_argument("--lora-r", type=int, default=32)
    p.add_argument("--lora-alpha", type=int, default=64)
    p.add_argument("--seed", type=int, default=0)
    return p


def load_examples(path: str, limit: int | None = None) -> list[dict]:
    rows = load_jsonl(path)
    if not rows or "completion" not in rows[0]:
        raise SystemExit(f"{path} is not SFT-format export-rl output (run `xbrlbench export-rl --format sft`)")
    return rows[:limit] if limit else rows


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from train_grpo import ignore_old_torchao
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    ignore_old_torchao()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tokenizer = AutoTokenizer.from_pretrained(args.model)

    # Render the prompt exactly as GRPO and evaluation will (non-thinking chat
    # template, generation prompt included), so the warm-up teaches the same
    # turn the model is later sampled from. TRL appends the EOS token to each
    # completion and puts the loss on the completion only.
    rows = load_examples(args.train, args.limit)
    data = [{
        "prompt": tokenizer.apply_chat_template(r["prompt"], tokenize=False, add_generation_prompt=True,
                                                enable_thinking=False),
        "completion": r["completion"][0]["content"],
    } for r in rows]
    # SFT truncates from the end, which would cut off the worked answer: drop
    # examples that don't fit instead.
    fits = [len(tokenizer(d["prompt"] + d["completion"])["input_ids"]) < args.max_length for d in data]
    data = [d for d, ok in zip(data, fits, strict=True) if ok]
    print(f"{len(data)} warm-up examples ({fits.count(False)} longer than {args.max_length} tokens dropped)",
          flush=True)
    if not data:
        raise SystemExit("no warm-up example fits --max-length")

    bf16 = torch.cuda.is_available() and torch.cuda.get_device_capability()[0] >= 8
    dtype = torch.bfloat16 if bf16 else torch.float16
    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=dtype)

    config = SFTConfig(
        output_dir=str(out),
        seed=args.seed,
        num_train_epochs=args.epochs,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_steps=5,
        per_device_train_batch_size=args.per_device_batch,
        gradient_accumulation_steps=args.grad_accum,
        gradient_checkpointing=True,
        bf16=bf16,
        fp16=not bf16,
        max_length=args.max_length,
        completion_only_loss=True,
        logging_steps=5,
        disable_tqdm=True,
        save_strategy="no",
        report_to="none",
    )
    trainer = SFTTrainer(
        model=model,
        args=config,
        train_dataset=Dataset.from_list(data),
        processing_class=tokenizer,
        peft_config=LoraConfig(r=args.lora_r, lora_alpha=args.lora_alpha, lora_dropout=0.0,
                               target_modules="all-linear", task_type="CAUSAL_LM"),
    )
    trainer.train()
    (out / "log_history.json").write_text(json.dumps(trainer.state.log_history, indent=1))
    (out / "run_config.json").write_text(json.dumps({**vars(args), "examples": len(data), "dtype": str(dtype)},
                                                    indent=2))

    merged = trainer.model.merge_and_unload()
    merged_dir = out / "merged"
    merged.to(torch.float16).save_pretrained(str(merged_dir))
    tokenizer.save_pretrained(str(merged_dir))
    print(f"Saved the warmed-up model to {merged_dir}", flush=True)


if __name__ == "__main__":
    main()
