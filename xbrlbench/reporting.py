"""xbrlbench.reporting — turn a graded responses file into the breakdowns
someone actually wants to look at: accuracy by model/difficulty/reasoning
type, valid vs. invalid response counts, which questions failed and for
which model, and a couple of specific comparisons (easy vs. hard tier,
single-step vs. multi-step reasoning, strongest/weakest reasoning category).

Run:
  python -m xbrlbench report
    -> results/report_analysis.json, results/report_analysis.csv,
       results/report.md (a Markdown table ready to paste into a README)

This reads the same raw responses file xbrlbench.grading does (not
grading's output) and grades it itself via xbrlbench.grading.summarize, so
`report` never depends on having run `grade` first, and re-running it with a
different --epsilon never repeats paid API calls.

Older responses files (from before reasoning_type was propagated into
responses.jsonl -- see the grading-hardening commit) are missing that field
on every row, which would otherwise make the by-reasoning-type breakdown
useless. This module backfills reasoning_type by joining each response back
to its question in data/questions.jsonl by id, so the breakdown works
whether or not the responses file itself carries the field.
"""

from __future__ import annotations

import argparse
import csv
import json
from typing import Optional

from .grading import summarize
from .io_utils import load_jsonl
from .paths import DEFAULT_QUESTIONS_PATH, DEFAULT_REPORT_PREFIX, DEFAULT_RESPONSES_PATH
from .schema import VALID_REASONING_TYPES

# The reasoning-type-based single-step / multi-step split (distinct from the
# tier-based easy/hard split below): direct_retrieval is "locate one value
# and (if needed) unit-descale it" -- no combining facts. Everything else
# requires combining at least two values, which is what "multi-step" means
# here. See docs/SCHEMA.md's reasoning-type table for the full definitions.
SINGLE_STEP_REASONING_TYPES = {"direct_retrieval"}
MULTI_STEP_REASONING_TYPES = VALID_REASONING_TYPES - SINGLE_STEP_REASONING_TYPES

# The tier-based easy/hard split: T1 is the easiest tier (single lookup),
# T3 is the hardest (distractor / multi-hop) -- see docs/SCHEMA.md.
EASY_TIER = "T1"
HARD_TIER = "T3"


def _bucket(rows: list[dict], key: str) -> dict:
    b: dict = {}
    for r in rows:
        cell = b.setdefault(r.get(key), {"n": 0, "correct": 0, "invalid": 0})
        cell["n"] += 1
        if r["status"] == "correct":
            cell["correct"] += 1
        elif r["status"] == "invalid_response":
            cell["invalid"] += 1
    for cell in b.values():
        cell["accuracy"] = cell["correct"] / cell["n"] if cell["n"] else 0.0
    return b


def _grid(rows: list[dict], key1: str, key2: str) -> dict:
    g: dict = {}
    for r in rows:
        cell = g.setdefault((r.get(key1), r.get(key2)), {"n": 0, "correct": 0, "invalid": 0})
        cell["n"] += 1
        if r["status"] == "correct":
            cell["correct"] += 1
        elif r["status"] == "invalid_response":
            cell["invalid"] += 1
    for cell in g.values():
        cell["accuracy"] = cell["correct"] / cell["n"] if cell["n"] else 0.0
    return g


def _accuracy_of(rows: list[dict]) -> Optional[float]:
    if not rows:
        return None
    correct = sum(1 for r in rows if r["status"] == "correct")
    return correct / len(rows)


def _enrich_reasoning_type(rows: list[dict], questions_path) -> list[dict]:
    try:
        questions_by_id = {q.get("id"): q for q in load_jsonl(questions_path)}
    except (FileNotFoundError, OSError):
        return rows
    enriched = []
    for row in rows:
        if not row.get("reasoning_type") and row.get("id") in questions_by_id:
            row = {**row, "reasoning_type": questions_by_id[row["id"]].get("reasoning_type")}
        enriched.append(row)
    return enriched


def _models_of(graded: list[dict]) -> list[str]:
    return sorted({r.get("model") for r in graded if r.get("model") is not None})


def easy_to_hard_drop(graded: list[dict]) -> dict:
    """Per model (and overall): accuracy on the easiest tier minus accuracy
    on the hardest tier. A large positive drop means the model does fine on
    simple lookups but falls apart as the question gets harder."""
    result = {}
    for model in _models_of(graded) + [None]:
        model_rows = graded if model is None else [r for r in graded if r.get("model") == model]
        easy_acc = _accuracy_of([r for r in model_rows if r.get("tier") == EASY_TIER])
        hard_acc = _accuracy_of([r for r in model_rows if r.get("tier") == HARD_TIER])
        key = "overall" if model is None else model
        result[key] = {
            f"{EASY_TIER}_accuracy": easy_acc,
            f"{HARD_TIER}_accuracy": hard_acc,
            "drop": (easy_acc - hard_acc) if (easy_acc is not None and hard_acc is not None) else None,
        }
    return result


def single_vs_multi_step(graded: list[dict]) -> dict:
    """Per model (and overall): accuracy on direct_retrieval questions vs.
    accuracy on every other (multi-step) reasoning type combined."""
    result = {}
    for model in _models_of(graded) + [None]:
        model_rows = graded if model is None else [r for r in graded if r.get("model") == model]
        single = _accuracy_of([r for r in model_rows if r.get("reasoning_type") in SINGLE_STEP_REASONING_TYPES])
        multi = _accuracy_of([r for r in model_rows if r.get("reasoning_type") in MULTI_STEP_REASONING_TYPES])
        key = "overall" if model is None else model
        result[key] = {
            "single_step_accuracy": single,
            "multi_step_accuracy": multi,
            "drop": (single - multi) if (single is not None and multi is not None) else None,
        }
    return result


def strongest_weakest_reasoning_type(graded: list[dict]) -> dict:
    """Per model (and overall): the reasoning_type with the highest and the
    lowest accuracy, among types this model was actually asked (n > 0)."""
    result = {}
    for model in _models_of(graded) + [None]:
        model_rows = graded if model is None else [r for r in graded if r.get("model") == model]
        by_type = _bucket(model_rows, "reasoning_type")
        by_type = {t: cell for t, cell in by_type.items() if t in VALID_REASONING_TYPES and cell["n"] > 0}
        key = "overall" if model is None else model
        if not by_type:
            result[key] = {"strongest": None, "weakest": None}
            continue
        strongest_type = max(by_type, key=lambda t: (by_type[t]["accuracy"], t))
        weakest_type = min(by_type, key=lambda t: (by_type[t]["accuracy"], t))
        result[key] = {
            "strongest": {"reasoning_type": strongest_type, "accuracy": by_type[strongest_type]["accuracy"]},
            "weakest": {"reasoning_type": weakest_type, "accuracy": by_type[weakest_type]["accuracy"]},
        }
    return result


def build_report(rows: list[dict], questions_path=DEFAULT_QUESTIONS_PATH, epsilon: Optional[float] = None) -> dict:
    rows = _enrich_reasoning_type(rows, questions_path)
    # summarize()'s own tier_table/model_table/grid are ignored here in favor
    # of this module's _bucket/_grid, which add an "accuracy" field to every
    # cell (summarize()'s only does that on the top-level `overall` dict).
    graded, _tier_table, _model_table, _tier_model_grid, overall = summarize(rows, epsilon)

    tier_table = _bucket(graded, "tier")
    model_table = _bucket(graded, "model")
    tier_model_grid = _grid(graded, "tier", "model")
    reasoning_type_table = _bucket(graded, "reasoning_type")
    reasoning_type_model_grid = _grid(graded, "reasoning_type", "model")

    incorrect_ids = sorted({r["id"] for r in graded if r["status"] == "incorrect" and r.get("id")})
    invalid_ids = sorted({r["id"] for r in graded if r["status"] == "invalid_response" and r.get("id")})

    per_question: dict = {}
    for r in graded:
        per_question.setdefault(r.get("id"), {})[r.get("model")] = r["status"]

    try:
        question_bank_size = len(load_jsonl(questions_path))
    except (FileNotFoundError, OSError):
        question_bank_size = None

    return {
        "epsilon": epsilon,
        "question_bank_size": question_bank_size,
        "questions_evaluated": len({r.get("id") for r in graded}),
        "response_count": len(graded),
        "overall": overall,
        "by_model": model_table,
        "by_difficulty": tier_table,
        "by_reasoning_type": reasoning_type_table,
        "by_difficulty_and_model": {f"{t}|{m}": v for (t, m), v in tier_model_grid.items()},
        "by_reasoning_type_and_model": {f"{t}|{m}": v for (t, m), v in reasoning_type_model_grid.items()},
        "incorrect_question_ids": incorrect_ids,
        "invalid_question_ids": invalid_ids,
        "per_question": per_question,
        "easy_to_hard_drop": easy_to_hard_drop(graded),
        "single_vs_multi_step": single_vs_multi_step(graded),
        "strongest_weakest_reasoning_type": strongest_weakest_reasoning_type(graded),
    }


def _fmt_pct(acc) -> str:
    return f"{100 * acc:.1f}%" if acc is not None else "n/a"


def to_markdown(report: dict) -> str:
    lines = ["# XBRLBench results", ""]
    lines.append(
        f"{report['questions_evaluated']} questions, {report['response_count']} graded responses "
        f"(epsilon={report['epsilon']})."
    )
    lines.append("")

    tiers = sorted({k.split("|")[0] for k in report["by_difficulty_and_model"]})
    models = sorted(report["by_model"].keys(), key=lambda m: (m is None, m))

    lines.append("## Accuracy by model x difficulty")
    lines.append("")
    lines.append("| model | " + " | ".join(tiers) + " | overall |")
    lines.append("|" + "---|" * (len(tiers) + 2))
    for model in models:
        cells = [_fmt_pct(report["by_difficulty_and_model"].get(f"{t}|{model}", {}).get("accuracy")) for t in tiers]
        overall_acc = report["by_model"].get(model, {}).get("accuracy")
        lines.append(f"| {model} | " + " | ".join(cells) + f" | {_fmt_pct(overall_acc)} |")
    lines.append("")

    lines.append("## Findings")
    lines.append("")
    drop = report["easy_to_hard_drop"].get("overall", {})
    lines.append(
        f"- Easy ({EASY_TIER}) -> hard ({HARD_TIER}) accuracy drop, all models combined: "
        f"{_fmt_pct(drop.get(f'{EASY_TIER}_accuracy'))} -> {_fmt_pct(drop.get(f'{HARD_TIER}_accuracy'))}."
    )
    step = report["single_vs_multi_step"].get("overall", {})
    lines.append(
        f"- Single-step (direct retrieval) vs. multi-step reasoning, all models combined: "
        f"{_fmt_pct(step.get('single_step_accuracy'))} vs. {_fmt_pct(step.get('multi_step_accuracy'))}."
    )
    for model in models:
        d = report["easy_to_hard_drop"].get(model, {})
        if d.get("drop") is not None and d["drop"] > 0:
            lines.append(
                f"- {model}: {_fmt_pct(d[f'{EASY_TIER}_accuracy'])} on {EASY_TIER} "
                f"vs. {_fmt_pct(d[f'{HARD_TIER}_accuracy'])} on {HARD_TIER}."
            )
    if report["overall"]["invalid"]:
        lines.append(
            f"- {report['overall']['invalid']}/{report['overall']['n']} responses were invalid/unparseable "
            f"(excluded from accuracy)."
        )

    return "\n".join(lines) + "\n"


def write_outputs(report: dict, out_prefix) -> None:
    json_path = f"{out_prefix}_analysis.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    print(f"Wrote {json_path}")

    csv_path = f"{out_prefix}_analysis.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["dimension", "key", "model", "n", "correct", "invalid", "accuracy"])
        for key, cell in sorted(report["by_difficulty_and_model"].items()):
            tier, model = key.split("|", 1)
            w.writerow(["difficulty", tier, model, cell["n"], cell["correct"], cell["invalid"], round(cell["accuracy"], 4)])
        for key, cell in sorted(report["by_reasoning_type_and_model"].items()):
            rtype, model = key.split("|", 1)
            w.writerow(["reasoning_type", rtype, model, cell["n"], cell["correct"], cell["invalid"], round(cell["accuracy"], 4)])
    print(f"Wrote {csv_path}")

    md_path = f"{out_prefix}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(to_markdown(report))
    print(f"Wrote {md_path}")


def report(
    responses_path=DEFAULT_RESPONSES_PATH,
    questions_path=DEFAULT_QUESTIONS_PATH,
    out_prefix=DEFAULT_REPORT_PREFIX,
    epsilon: Optional[float] = None,
) -> dict:
    rows = load_jsonl(responses_path)
    result = build_report(rows, questions_path, epsilon)
    write_outputs(result, out_prefix)
    print(f"\nOverall: {result['overall']['correct']}/{result['overall']['n']} = "
          f"{_fmt_pct(result['overall']['accuracy'])}")
    return result


def build_arg_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(
        description="Generate accuracy/failure breakdowns from a saved responses file.")
    parser.add_argument("--in", dest="inp", default=str(DEFAULT_RESPONSES_PATH))
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS_PATH),
                         help="question bank, used to backfill reasoning_type on older responses files")
    parser.add_argument("--out-prefix", default=str(DEFAULT_REPORT_PREFIX))
    parser.add_argument("--epsilon", type=float, default=None,
                         help="override every row's tolerance (see `xbrlbench grade --help`)")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    report(args.inp, args.questions, args.out_prefix, args.epsilon)


if __name__ == "__main__":
    main()
