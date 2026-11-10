"""GRPO + LoRA training on XBRLBench real-filing questions (milestone 4).

Defaults target the debug run: Qwen3-1.7B on a free Kaggle T4 (16 GB, fp16
only), non-thinking mode, short completions. The main Qwen3-4B run uses the
same script with --model Qwen/Qwen3-4B and a bigger GPU.

Input is the output of `python -m xbrlbench export-rl` (TRL format). The
reward is xbrlbench.rl: 1 for a correct answer, 0.1 for a well-formed wrong
one, 0 for scale errors, hedges and anything unparseable. TRL logs the two
parts as rewards/accuracy_reward and rewards/format_reward.

    pip install -e ".[train]"
    # one GPU: vLLM shares it with training
    python scripts/train_grpo.py --train data/rl/train.jsonl --out runs/grpo-1.7b --vllm colocate
    # two GPUs (Kaggle T4 x2): vLLM server on GPU 1, training on GPU 0
    CUDA_VISIBLE_DEVICES=1 trl vllm-serve --model Qwen/Qwen3-1.7B --dtype half \\
        --max_model_len 4096 --gpu_memory_utilization 0.9 &
    CUDA_VISIBLE_DEVICES=0 python scripts/train_grpo.py --train data/rl/train.jsonl --vllm server

Written against trl 1.17 and peft 0.21 (see the `train` extra).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xbrlbench import rl  # noqa: E402  (stdlib-only; safe to import before the heavy deps)
from xbrlbench.io_utils import load_jsonl  # noqa: E402


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--train", required=True, help="TRL-format JSONL from `xbrlbench export-rl`")
    p.add_argument("--val", default=None, help="validation JSONL (default: the .val.jsonl beside --train, if any)")
    p.add_argument("--out", default="runs/grpo", help="output directory (checkpoints, adapter, merged model)")
    p.add_argument("--model", default="Qwen/Qwen3-1.7B")
    p.add_argument("--thinking", action="store_true",
                   help="train with Qwen3 thinking on (needs far longer completions; off by default)")
    # Sequence lengths. Prompts longer than --max-prompt-tokens are dropped, not truncated:
    # a truncated statement table would make the gold answer unreachable.
    p.add_argument("--max-prompt-tokens", type=int, default=3072)
    p.add_argument("--max-completion-tokens", type=int, default=768)
    # GRPO
    p.add_argument("--num-generations", type=int, default=8, help="completions per prompt (the GRPO group)")
    p.add_argument("--per-device-batch", type=int, default=4, help="completions per forward/backward pass")
    p.add_argument("--grad-accum", type=int, default=8)
    p.add_argument("--max-steps", type=int, default=300)
    p.add_argument("--lr", type=float, default=2e-5)
    p.add_argument("--beta", type=float, default=0.0, help="KL penalty; 0 skips the reference model")
    p.add_argument("--temperature", type=float, default=0.8)
    # LoRA
    p.add_argument("--lora-r", type=int, default=32)
    p.add_argument("--lora-alpha", type=int, default=64)
    # Generation backend
    p.add_argument("--vllm", choices=("off", "colocate", "server"), default="colocate")
    p.add_argument("--vllm-gpu-memory", type=float, default=0.35, help="colocate mode: share of GPU memory for vLLM")
    # Bookkeeping
    p.add_argument("--eval-steps", type=int, default=50, help="0 disables in-training evaluation")
    p.add_argument("--eval-limit", type=int, default=64, help="validation prompts per evaluation")
    p.add_argument("--save-steps", type=int, default=50)
    p.add_argument("--limit", type=int, default=None, help="use only the first N training prompts")
    p.add_argument("--resume", action="store_true", help="continue from the last checkpoint in --out")
    p.add_argument("--merge", action="store_true", help="also save a merged fp16 model for vLLM evaluation")
    p.add_argument("--report-to", default="none", help="e.g. wandb")
    p.add_argument("--seed", type=int, default=0)
    return p


def check_args(args: argparse.Namespace, n_processes: int = 1) -> None:
    """Fail fast on settings TRL would only reject after loading the model."""
    effective = args.per_device_batch * args.grad_accum * n_processes
    if effective % args.num_generations:
        raise SystemExit(f"per-device batch x grad accum x processes ({effective}) must be a multiple of "
                         f"--num-generations ({args.num_generations})")
    if args.num_generations < 2:
        raise SystemExit("--num-generations must be at least 2: GRPO compares completions within a group")


def default_val_path(train: str) -> str | None:
    path = rl.val_path(train)
    return path if os.path.exists(path) else None


def load_prompts(path: str, limit: int | None = None) -> list[dict]:
    rows = load_jsonl(path)
    if not rows or "prompt" not in rows[0] or "gold_value" not in rows[0]:
        raise SystemExit(f"{path} is not TRL-format export-rl output (run `xbrlbench export-rl --format trl`)")
    return rows[:limit] if limit else rows


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    check_args(args, int(os.environ.get("WORLD_SIZE", "1")))

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import GRPOConfig, GRPOTrainer

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    template_kwargs = {"enable_thinking": args.thinking}
    tokenizer = AutoTokenizer.from_pretrained(args.model)

    def fits(row: dict) -> bool:
        text = tokenizer.apply_chat_template(row["prompt"], tokenize=False, add_generation_prompt=True,
                                             **template_kwargs)
        return len(tokenizer(text, add_special_tokens=False)["input_ids"]) <= args.max_prompt_tokens

    def dataset(path: str | None, limit: int | None):
        if not path:
            return None, 0
        rows = load_prompts(path, limit)
        kept = [r for r in rows if fits(r)]
        print(f"{path}: {len(kept)}/{len(rows)} prompts fit in {args.max_prompt_tokens} tokens")
        return Dataset.from_list(kept), len(rows) - len(kept)

    train_ds, train_dropped = dataset(args.train, args.limit)
    val_path = args.val or default_val_path(args.train)
    eval_ds, _ = dataset(val_path, args.eval_limit) if args.eval_steps else (None, 0)

    # T4 (compute capability 7.5) has no native bfloat16. torch's
    # is_bf16_supported() can say yes via emulation, so check the hardware.
    bf16 = torch.cuda.is_available() and torch.cuda.get_device_capability()[0] >= 8
    dtype = torch.bfloat16 if bf16 else torch.float16
    model_path = args.model
    if not bf16 and args.vllm == "colocate":
        # Colocated vLLM loads the model from its path with the dtype in its
        # config (bfloat16 for Qwen3), which a T4 refuses. Save an fp16 copy
        # once and train from that, so both sides agree.
        model_path = str(out / "base_fp16")
        if not (Path(model_path) / "config.json").exists():
            AutoModelForCausalLM.from_pretrained(args.model, dtype=dtype).save_pretrained(model_path)
            tokenizer.save_pretrained(model_path)
    model = AutoModelForCausalLM.from_pretrained(model_path, dtype=dtype)

    config = GRPOConfig(
        output_dir=str(out),
        seed=args.seed,
        learning_rate=args.lr,
        lr_scheduler_type="constant_with_warmup",
        warmup_steps=10,
        max_steps=args.max_steps,
        per_device_train_batch_size=args.per_device_batch,
        gradient_accumulation_steps=args.grad_accum,
        gradient_checkpointing=True,
        bf16=bf16,
        fp16=not bf16,
        num_generations=args.num_generations,
        max_completion_length=args.max_completion_tokens,
        temperature=args.temperature,
        chat_template_kwargs=template_kwargs,
        beta=args.beta,
        # Cut-off completions earn 0 anyway; leaving them out of the loss
        # keeps them from being pushed down as if they were wrong (DAPO).
        mask_truncated_completions=True,
        use_vllm=args.vllm != "off",
        vllm_mode="server" if args.vllm == "server" else "colocate",
        vllm_gpu_memory_utilization=args.vllm_gpu_memory,
        vllm_max_model_length=args.max_prompt_tokens + args.max_completion_tokens,
        vllm_enable_sleep_mode=args.vllm == "colocate",
        logging_steps=5,
        log_completions=True,
        num_completions_to_print=2,
        save_steps=args.save_steps,
        save_total_limit=3,
        eval_strategy="steps" if eval_ds is not None else "no",
        eval_steps=args.eval_steps or None,
        # 2 samples per validation prompt is enough to track accuracy cheaply.
        num_generations_eval=2,
        per_device_eval_batch_size=4,
        report_to=args.report_to,
    )
    peft_config = LoraConfig(r=args.lora_r, lora_alpha=args.lora_alpha, lora_dropout=0.0,
                             target_modules="all-linear", task_type="CAUSAL_LM")

    trainer = GRPOTrainer(
        model=model,
        reward_funcs=[rl.accuracy_reward, rl.format_reward],
        args=config,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        processing_class=tokenizer,
        peft_config=peft_config,
    )
    (out / "run_config.json").write_text(json.dumps({
        **vars(args), "train_prompts": len(train_ds), "train_dropped_too_long": train_dropped,
        "eval_prompts": len(eval_ds) if eval_ds is not None else 0, "dtype": str(dtype),
        "reward": {"correct": rl.CORRECT_REWARD, "format": rl.FORMAT_REWARD, "scale_factors": rl.SCALE_FACTORS},
    }, indent=2))
    trainer.train(resume_from_checkpoint=args.resume or None)

    adapter_dir = out / "adapter"
    trainer.save_model(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))
    print(f"Saved LoRA adapter to {adapter_dir}")

    if args.merge:
        merged = trainer.model.merge_and_unload()
        merged_dir = out / "merged"
        merged.to(torch.float16).save_pretrained(str(merged_dir))
        tokenizer.save_pretrained(str(merged_dir))
        print(f"Saved merged model to {merged_dir} (serve it with vLLM to evaluate)")


if __name__ == "__main__":
    main()
