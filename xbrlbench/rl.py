"""xbrlbench.rl — the reinforcement-learning side of the benchmark
(milestone 4): a strict reward function and an exporter that turns the
real-filing train split into prompt datasets for TRL or verl.

    python -m xbrlbench export-rl --in data/filings/v2/questions.jsonl \\
        --out data/rl/train.jsonl [--format trl|verl] [--val-fraction 0.05]
    python -m xbrlbench reward --in results/m3/frontier/responses.jsonl

Reward (docs/PRD.md, scope item 5)
----------------------------------
A completion earns

  1.0   its answer is within the question's tolerance (xbrlbench.grading);
  0.1   a well-formed but wrong answer (the format reward);
  0.0   everything else, including
          - no ANSWER: line, or one that is not the last line;
          - more than one ANSWER: line (hedging);
          - an ANSWER: line holding several numbers, or anything other than
            one bare number;
          - an unclosed <think> block (the completion was cut off);
          - a scale error: the answer is right except for a factor of
            100, 1,000, 10^6 or 10^9 (the "in thousands" trap, or a percent
            given as a fraction). These get no format credit either.

Parsing is deliberately stricter than xbrlbench.grading, which falls back to
the last line or the last number in the text so that old responses can still
be graded. A policy being trained would learn to exploit those fallbacks, so
the reward reads only the final ANSWER: line. A completion that earns reward
here always grades the same way under xbrlbench.grading.

This module is stdlib-only like the rest of the package; the training
script (scripts/train_grpo.py) imports it and needs the `[train]` extra.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass

from . import grading
from .inference import build_messages
from .io_utils import load_jsonl, write_jsonl

CORRECT_REWARD = 1.0
FORMAT_REWARD = 0.1

# Off-by-a-unit factors. 100 covers a percent answered as a fraction (or a
# ratio answered as a percent); the rest are "in thousands/millions/billions"
# slips.
SCALE_FACTORS = (100.0, 1e3, 1e6, 1e9)

# Statuses that earn the format reward. Everything else earns 0 unless correct.
_FORMATTED = {"incorrect", "sign_error"}

# One bare number, as SYSTEM_PROMPT asks for: optional sign or accounting
# parentheses, optional $ and thousands commas, optional decimals, optional %.
_BARE_NUMBER_RE = re.compile(r"^(?:-?\$?|\(\$?)\d[\d,]*(?:\.\d+)?\)?%?$")

DATA_SOURCE = "xbrlbench"
_VAL_SALT = "xbrlbench-rl-val-v1"


@dataclass(frozen=True)
class Reward:
    reward: float
    status: str          # correct | incorrect | sign_error | scale_error | <parse failure>
    value: float | None  # the parsed answer, when there is exactly one


def strip_thinking(text: str) -> str | None:
    """The visible part of a completion: whatever follows the last </think>.
    None when a <think> block is never closed, i.e. generation was cut off
    mid-thought. A completion that starts inside a think block opened by the
    prompt has a </think> but no <think>; that is handled the same way."""
    if "</think>" in text:
        return text.rsplit("</think>", 1)[1]
    if "<think>" in text:
        return None
    return text


def parse_answer(text: str | None) -> tuple[float | None, str]:
    """Strictly parse a completion's final answer. Returns (value, "ok") or
    (None, reason) where reason is one of: empty, unclosed_think, no_answer,
    multiple_answers, answer_not_last, not_a_number, multiple_numbers."""
    if not text or not text.strip():
        return None, "empty"
    body = strip_thinking(text)
    if body is None:
        return None, "unclosed_think"
    lines = [ln.strip() for ln in body.strip().splitlines() if ln.strip()]
    answer_idx = [i for i, ln in enumerate(lines) if ln.upper().startswith("ANSWER:")]
    if not answer_idx:
        return None, "no_answer"
    if len(answer_idx) > 1:
        return None, "multiple_answers"
    if answer_idx[0] != len(lines) - 1:
        return None, "answer_not_last"
    raw = lines[-1].split(":", 1)[1].strip()
    # Count numbers before deduplication: "5 or 5.0" is still a hedge.
    numbers = grading.extract_numbers(raw)
    if len(numbers) > 1:
        return None, "multiple_numbers"
    if not numbers or not _BARE_NUMBER_RE.fullmatch(raw.replace(" ", "")):
        return None, "not_a_number"
    return numbers[0], "ok"


def is_scale_error(value: float, gold: float, unit: str, tolerance: float) -> bool:
    """True when `value` would be correct after multiplying or dividing by
    one of SCALE_FACTORS."""
    if value == 0 or gold == 0:
        return False
    return any(grading.is_correct(value * f, gold, unit, tolerance)
               or grading.is_correct(value / f, gold, unit, tolerance)
               for f in SCALE_FACTORS)


def score(completion: str | None, gold_value: float | None, gold_unit: str | None,
          tolerance: float | None = None) -> Reward:
    """Reward one completion against one question's gold answer."""
    if gold_value is None or gold_unit not in {"USD", "ratio", "percent"}:
        # A data problem: never reward against missing ground truth.
        return Reward(0.0, "bad_gold", None)
    tol = grading.DEFAULT_TOLERANCE if tolerance is None else tolerance
    value, status = parse_answer(completion)
    if value is None:
        return Reward(0.0, status, None)
    if grading.is_correct(value, gold_value, gold_unit, tol):
        return Reward(CORRECT_REWARD, "correct", value)
    if is_scale_error(value, gold_value, gold_unit, tol):
        return Reward(0.0, "scale_error", value)
    if grading.is_correct(-value, gold_value, gold_unit, tol):
        return Reward(FORMAT_REWARD, "sign_error", value)
    return Reward(FORMAT_REWARD, "incorrect", value)


# ---------------------------------------------------------------------------
# Trainer adapters
# ---------------------------------------------------------------------------

def _completion_text(completion) -> str:
    """TRL passes plain strings for text prompts and a list of messages for
    conversational ones."""
    if isinstance(completion, str):
        return completion
    if isinstance(completion, list):
        return "".join(m.get("content") or "" for m in completion if isinstance(m, dict))
    return str(completion or "")


def _batch(completions, gold_value, gold_unit, tolerance) -> list[Reward]:
    tolerance = tolerance or [None] * len(completions)
    return [score(_completion_text(c), g, u, t)
            for c, g, u, t in zip(completions, gold_value, gold_unit, tolerance, strict=True)]


def accuracy_reward(completions, gold_value, gold_unit, tolerance=None, **_) -> list[float]:
    """TRL reward function: 1.0 for a correct answer, else 0.0. Dataset
    columns arrive as keyword arguments, one list entry per completion."""
    return [1.0 if r.status == "correct" else 0.0
            for r in _batch(completions, gold_value, gold_unit, tolerance)]


def format_reward(completions, gold_value, gold_unit, tolerance=None, **_) -> list[float]:
    """TRL reward function: FORMAT_REWARD for a well-formed wrong answer.
    accuracy_reward + format_reward == score(...).reward for every
    completion; TRL logs the two separately."""
    return [FORMAT_REWARD if r.status in _FORMATTED else 0.0
            for r in _batch(completions, gold_value, gold_unit, tolerance)]


def compute_score(data_source, solution_str, ground_truth, extra_info=None) -> float:
    """verl custom reward entry point (custom_reward_function.name=compute_score).
    ground_truth is the JSON written by export_records(fmt="verl")."""
    gt = json.loads(ground_truth) if isinstance(ground_truth, str) else ground_truth
    return score(solution_str, gt.get("value"), gt.get("unit"), gt.get("tolerance")).reward


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _h(*parts) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()


def is_val_company(key: str, fraction: float) -> bool:
    """Deterministic company-level validation split, independent of the
    train/test split's own hash."""
    return fraction > 0 and int(_h(_VAL_SALT, key)[:8], 16) / 0xFFFFFFFF < fraction


def select_rows(rows: list[dict], split: str = "train") -> list[dict]:
    """Rows to train on. Never returns a held-out (test) question."""
    if split == "train":
        picked = [r for r in rows if r.get("split") == "train"]
    elif split == "all":
        if any(r.get("split") == "test" for r in rows):
            raise ValueError("input has held-out (split=test) questions; use --split train")
        picked = list(rows)
    else:
        raise ValueError(f"unknown split {split!r}; training may only use 'train' or 'all'")
    test_companies = {str(r.get("cik")) for r in rows if r.get("split") == "test" and r.get("cik")}
    leaked = {str(r.get("cik")) for r in picked} & test_companies
    if leaked:
        raise ValueError(f"{len(leaked)} companies appear in both splits, e.g. {sorted(leaked)[0]}")
    return picked


def to_record(q: dict, fmt: str = "trl") -> dict:
    messages = build_messages(q["question"], q["context"])
    meta = {
        "id": q["id"],
        "family": q.get("family") or q.get("reasoning_type"),
        "tier": q.get("tier"),
        "company_key": str(q.get("cik") or q.get("ticker")),
        "form": q.get("form"),
    }
    if fmt == "trl":
        # Columns other than "prompt" are passed to the reward functions.
        return {"prompt": messages, "gold_value": q["gold_value"], "gold_unit": q["gold_unit"],
                "tolerance": q.get("tolerance"), **meta}
    if fmt == "verl":
        gt = {"value": q["gold_value"], "unit": q["gold_unit"], "tolerance": q.get("tolerance")}
        return {"data_source": DATA_SOURCE, "prompt": messages, "ability": "financial_reading",
                "reward_model": {"style": "rule", "ground_truth": json.dumps(gt)},
                "extra_info": meta}
    raise ValueError(f"unknown format {fmt!r}")


def export_records(rows: list[dict], fmt: str = "trl", split: str = "train", val_fraction: float = 0.0,
                   families: set[str] | None = None, exclude_families: set[str] | None = None,
                   max_context_chars: int | None = None, limit: int | None = None
                   ) -> tuple[list[dict], list[dict], Counter]:
    """Returns (train_records, val_records, dropped_reasons). Order is a
    deterministic hash shuffle, so --limit takes a fair subset."""
    dropped: Counter = Counter()
    keep = []
    for q in select_rows(rows, split):
        fam = q.get("family") or q.get("reasoning_type")
        if families and fam not in families:
            dropped["family not selected"] += 1
        elif exclude_families and fam in exclude_families:
            dropped["family excluded"] += 1
        elif max_context_chars and len(q.get("context") or "") > max_context_chars:
            dropped["context too long"] += 1
        elif q.get("gold_value") is None or q.get("gold_unit") not in {"USD", "ratio", "percent"}:
            dropped["bad gold"] += 1
        else:
            keep.append(q)
    keep.sort(key=lambda q: _h("order", q["id"]))
    train, val = [], []
    for q in keep:
        key = str(q.get("cik") or q.get("ticker"))
        (val if is_val_company(key, val_fraction) else train).append(to_record(q, fmt))
    if limit is not None:
        train = train[:limit]
    return train, val, dropped


def val_path(out: str) -> str:
    return re.sub(r"(\.jsonl)?$", ".val.jsonl", out, count=1)


def _percentile(xs: list[int], p: float) -> int:
    if not xs:
        return 0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def export(inp: str, out: str, fmt: str = "trl", split: str = "train", val_fraction: float = 0.05,
           families: set[str] | None = None, exclude_families: set[str] | None = None,
           max_context_chars: int | None = None, limit: int | None = None) -> dict:
    train, val, dropped = export_records(load_jsonl(inp), fmt, split, val_fraction, families,
                                         exclude_families, max_context_chars, limit)
    if not train and not val:
        raise SystemExit(f"no questions to export from {inp} (dropped: {dict(dropped) or 'none'}). "
                         "A file without a split field, like the synthetic bank, needs --split all.")
    write_jsonl(out, train)
    if val:
        write_jsonl(val_path(out), val)
    fam_key = "family" if fmt == "trl" else None
    fams = Counter(r[fam_key] if fam_key else r["extra_info"]["family"] for r in train)
    chars = [sum(len(m["content"]) for m in r["prompt"]) for r in train]
    summary = {
        "train": len(train), "val": len(val), "dropped": dict(dropped),
        "companies": len({(r.get("company_key") or r.get("extra_info", {}).get("company_key")) for r in train}),
        "prompt_chars_p50": _percentile(chars, 0.5), "prompt_chars_p95": _percentile(chars, 0.95),
        "prompt_chars_max": max(chars, default=0), "by_family": dict(sorted(fams.items())),
    }
    print(f"Wrote {out} ({len(train)} prompts)" + (f" and {val_path(out)} ({len(val)})" if val else ""))
    print(json.dumps(summary, indent=2))
    return summary


# ---------------------------------------------------------------------------
# Reward check over a saved responses file
# ---------------------------------------------------------------------------

def check_responses(rows: list[dict]) -> dict[str, Counter]:
    """Reward status counts per model for a responses JSONL (the output of
    `run`). Each row's content is scored as a completion would be."""
    out: dict[str, Counter] = {}
    for r in grading.latest_rows(rows):
        res = score(r.get("raw_response"), r.get("gold_value"), r.get("gold_unit"), r.get("tolerance"))
        c = out.setdefault(r.get("model") or "?", Counter())
        c[res.status] += 1
        c["_n"] += 1
        c["_reward"] += res.reward
        if (res.status == "correct") != (grading.grade_row(r, None)["status"] == "correct"):
            c["_disagrees_with_grading"] += 1
    return out


def print_check(stats: dict[str, Counter]) -> None:
    for model, c in sorted(stats.items()):
        n = c["_n"]
        statuses = ", ".join(f"{k} {v}" for k, v in c.most_common() if not k.startswith("_"))
        print(f"{model}: mean reward {c['_reward'] / n:.3f} over {n}; {statuses}; "
              f"correct here but not in grading or vice versa: {c['_disagrees_with_grading']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _set(s: str | None) -> set[str] | None:
    return {x.strip() for x in s.split(",") if x.strip()} if s else None


def build_export_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(description="Export the train split as RL prompts.")
    parser.add_argument("--in", dest="inp", required=True, help="questions.jsonl from `build`")
    parser.add_argument("--out", required=True, help="output JSONL (a .val.jsonl is written beside it)")
    parser.add_argument("--format", dest="fmt", choices=("trl", "verl"), default="trl")
    parser.add_argument("--split", choices=("train", "all"), default="train",
                        help="'all' is for inputs without a split field (e.g. the synthetic bank)")
    parser.add_argument("--val-fraction", type=float, default=0.05,
                        help="share of train companies held back for validation during training")
    parser.add_argument("--families", help="comma-separated task families to keep")
    parser.add_argument("--exclude-families", help="comma-separated task families to drop")
    parser.add_argument("--max-context-chars", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    return parser


def build_check_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(description="Score saved responses with the RL reward.")
    parser.add_argument("--in", dest="inp", required=True, help="responses.jsonl from `run`")
    return parser


def run_export(args: argparse.Namespace) -> dict:
    return export(args.inp, args.out, args.fmt, args.split, args.val_fraction, _set(args.families),
                  _set(args.exclude_families), args.max_context_chars, args.limit)


def run_check(args: argparse.Namespace) -> dict[str, Counter]:
    stats = check_responses(load_jsonl(args.inp))
    print_check(stats)
    return stats
