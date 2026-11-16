"""scripts/train_grpo.py: argument checks that run before any heavy import
(torch/trl are not installed in CI)."""

import importlib.util
from pathlib import Path

import pytest

from xbrlbench import rl
from xbrlbench.io_utils import write_jsonl

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "train_grpo.py"
_spec = importlib.util.spec_from_file_location("train_grpo", _PATH)
train_grpo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(train_grpo)


def args(*extra):
    return train_grpo.build_arg_parser().parse_args(["--train", "t.jsonl", *extra])


def test_defaults_fit_a_t4_debug_run():
    a = args()
    assert a.model == "Qwen/Qwen3-1.7B" and not a.thinking
    train_grpo.check_args(a)  # does not raise


def test_batch_must_hold_whole_groups():
    with pytest.raises(SystemExit, match="multiple of"):
        train_grpo.check_args(args("--per-device-batch", "3", "--grad-accum", "1"))
    with pytest.raises(SystemExit, match="whole groups"):
        train_grpo.check_args(args("--steps-per-generation", "1"))
    with pytest.raises(SystemExit, match="at least 2"):
        train_grpo.check_args(args("--num-generations", "1"))


def test_load_prompts_rejects_raw_questions(tmp_path):
    raw = tmp_path / "questions.jsonl"
    write_jsonl(raw, [{"id": "x", "question": "q", "context": "c", "gold_value": 1.0}])
    with pytest.raises(SystemExit, match="export-rl"):
        train_grpo.load_prompts(str(raw))


def test_load_prompts_and_default_val(tmp_path):
    rows = [rl.to_record({"id": f"Q{i}", "question": "q", "context": "c", "gold_value": 1.0,
                          "gold_unit": "USD", "tolerance": 0.01}) for i in range(5)]
    train = tmp_path / "train.jsonl"
    write_jsonl(train, rows)
    assert len(train_grpo.load_prompts(str(train), limit=3)) == 3
    assert train_grpo.default_val_path(str(train)) is None
    write_jsonl(tmp_path / "train.val.jsonl", rows[:1])
    assert train_grpo.default_val_path(str(train)) == str(tmp_path / "train.val.jsonl")


_SFT_PATH = _PATH.parent / "train_sft.py"
_sft_spec = importlib.util.spec_from_file_location("train_sft", _SFT_PATH)
train_sft = importlib.util.module_from_spec(_sft_spec)
_sft_spec.loader.exec_module(train_sft)


def test_sft_loader_wants_worked_answers(tmp_path):
    prompts = tmp_path / "train.jsonl"
    write_jsonl(prompts, [rl.to_record({"id": "Q1", "question": "q", "context": "c", "gold_value": 1.0,
                                        "gold_unit": "USD", "tolerance": 0.01})])
    with pytest.raises(SystemExit, match="--format sft"):
        train_sft.load_examples(str(prompts))
    sft = tmp_path / "warmup.jsonl"
    write_jsonl(sft, [{"prompt": [], "completion": [{"role": "assistant", "content": "ANSWER: 1"}]}] * 3)
    assert len(train_sft.load_examples(str(sft), limit=2)) == 2
    assert train_sft.build_arg_parser().parse_args(["--train", "x"]).model == "Qwen/Qwen3-1.7B"
