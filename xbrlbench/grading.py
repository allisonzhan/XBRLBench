"""xbrlbench.grading — grade xbrlbench.inference's raw model responses
against gold answers and report accuracy by tier and by model.

Reads a responses JSONL (xbrlbench.inference's output), which already
carries gold_value / gold_unit / tier / model alongside each raw_response.
Grading is numeric with a relative-tolerance epsilon (default 1%) to absorb
rounding and "in thousands" scale mismatches.

Run:
  python -m xbrlbench grade
    -> results/report_summary.csv, results/report_summary.json,
       results/report_incorrect.jsonl (+ the tier x model table on stdout)

Flags:
  --epsilon 0.01      relative tolerance for numeric correctness (default 1%)

Note: this module never calls a model — it only re-reads a saved responses
file, so re-grading with a different --epsilon (or after a grading-logic fix)
never re-spends API budget.

This is Commit 1's port of the original grade.py: the algorithm below is
intentionally unchanged from the flat-script version so this refactor is
behavior-preserving. Grading correctness itself is hardened in a later,
dedicated commit.
"""

from __future__ import annotations

import argparse
import csv
import json
import re

from .io_utils import load_jsonl
from .paths import DEFAULT_REPORT_PREFIX, DEFAULT_RESPONSES_PATH

NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def parse_number(s):
    if s is None:
        return None
    s = s.strip().replace(",", "").replace("$", "").rstrip("%").strip()
    try:
        return float(s)
    except ValueError:
        return None


def last_number_in_text(text):
    """Fallback heuristic when there's no clean ANSWER: line: take the last
    number that appears anywhere in the response."""
    if not text:
        return None
    matches = NUMBER_RE.findall(text)
    if not matches:
        return None
    return parse_number(matches[-1])


def get_model_answer(row):
    """extracted_answer (from inference's ANSWER: parse) first; otherwise
    fall back to scanning raw_response for a trailing number. Returns
    (value_or_None, source_tag)."""
    val = parse_number(row.get("extracted_answer"))
    if val is not None:
        return val, "answer_line"
    val = last_number_in_text(row.get("raw_response"))
    if val is not None:
        return val, "last_number_fallback"
    return None, "unparseable"


def is_correct(model_val, gold_val, epsilon):
    if model_val is None or gold_val is None:
        return False
    if gold_val == 0:
        return abs(model_val) <= epsilon  # avoid div-by-zero; treat epsilon as absolute here
    return abs(model_val - gold_val) / abs(gold_val) <= epsilon


def summarize(rows, epsilon):
    """Returns (graded, tier_table, model_table, grid, overall)."""
    graded = []
    for row in rows:
        model_val, source = get_model_answer(row)
        gold_val = row.get("gold_value")
        correct = is_correct(model_val, gold_val, epsilon) if row.get("error") is None else False
        graded.append({**row, "parsed_model_answer": model_val,
                        "parse_source": source, "correct": correct})

    def bucket(rows_, key):
        b = {}
        for r in rows_:
            k = r.get(key)
            b.setdefault(k, {"n": 0, "correct": 0})
            b[k]["n"] += 1
            if r["correct"]:
                b[k]["correct"] += 1
        return b

    tier_table = bucket(graded, "tier")
    model_table = bucket(graded, "model")

    # tier x model grid for the headline printout
    grid = {}
    for r in graded:
        key = (r.get("tier"), r.get("model"))
        grid.setdefault(key, {"n": 0, "correct": 0})
        grid[key]["n"] += 1
        if r["correct"]:
            grid[key]["correct"] += 1

    n_total = len(graded)
    n_correct = sum(1 for r in graded if r["correct"])
    overall = {"n": n_total, "correct": n_correct,
               "accuracy": (n_correct / n_total) if n_total else 0.0}

    return graded, tier_table, model_table, grid, overall


def pct(correct, n):
    return f"{100 * correct / n:.1f}%" if n else "n/a"


def print_report(tier_table, model_table, grid, overall, epsilon):
    tiers = sorted(tier_table.keys(), key=lambda t: (t is None, t))
    models = sorted(model_table.keys(), key=lambda m: (m is None, m))

    print(f"\n=== Accuracy by tier x model (epsilon={epsilon}) ===\n")
    col_w = max(len(m) for m in models) if models else 10
    col_w = max(col_w, 24)
    print(f"{'model':<{col_w}} " + " ".join(f"{t:>10}" for t in tiers) + f" {'overall':>10}")
    for m in models:
        cells = []
        m_n = m_c = 0
        for t in tiers:
            cell = grid.get((t, m), {"n": 0, "correct": 0})
            m_n += cell["n"]
            m_c += cell["correct"]
            cells.append(pct(cell["correct"], cell["n"]))
        cells.append(pct(m_c, m_n))
        print(f"{m:<{col_w}} " + " ".join(f"{c:>10}" for c in cells))

    print(f"\n{'ALL MODELS':<{col_w}} " +
          " ".join(f"{pct(tier_table[t]['correct'], tier_table[t]['n']):>10}" for t in tiers) +
          f" {pct(overall['correct'], overall['n']):>10}")

    print(f"\nOverall: {overall['correct']}/{overall['n']} = {pct(overall['correct'], overall['n'])}")


def write_outputs(graded, tier_table, model_table, grid, overall, out_prefix, epsilon):
    # CSV: one row per tier x model cell, plus overall rows.
    csv_path = f"{out_prefix}_summary.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["tier", "model", "n", "correct", "accuracy"])
        for (tier, model), cell in sorted(grid.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
            acc = cell["correct"] / cell["n"] if cell["n"] else 0.0
            w.writerow([tier, model, cell["n"], cell["correct"], round(acc, 4)])
        for tier, cell in sorted(tier_table.items(), key=lambda kv: str(kv[0])):
            acc = cell["correct"] / cell["n"] if cell["n"] else 0.0
            w.writerow([tier, "ALL_MODELS", cell["n"], cell["correct"], round(acc, 4)])
        for model, cell in sorted(model_table.items(), key=lambda kv: str(kv[0])):
            acc = cell["correct"] / cell["n"] if cell["n"] else 0.0
            w.writerow(["ALL_TIERS", model, cell["n"], cell["correct"], round(acc, 4)])
        w.writerow(["ALL_TIERS", "ALL_MODELS", overall["n"], overall["correct"], round(overall["accuracy"], 4)])
    print(f"\nWrote {csv_path}")

    json_path = f"{out_prefix}_summary.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "epsilon": epsilon,
            "overall": overall,
            "by_tier": tier_table,
            "by_model": model_table,
            "by_tier_and_model": {f"{t}|{m}": v for (t, m), v in grid.items()},
        }, f, indent=2)
    print(f"Wrote {json_path}")

    incorrect_path = f"{out_prefix}_incorrect.jsonl"
    n_incorrect = 0
    with open(incorrect_path, "w", encoding="utf-8") as f:
        for r in graded:
            if not r["correct"]:
                f.write(json.dumps({
                    "id": r.get("id"),
                    "tier": r.get("tier"),
                    "model": r.get("model"),
                    "ticker": r.get("ticker"),
                    "fiscal_year": r.get("fiscal_year"),
                    "question": r.get("question"),
                    "gold_value": r.get("gold_value"),
                    "gold_unit": r.get("gold_unit"),
                    "parsed_model_answer": r.get("parsed_model_answer"),
                    "parse_source": r.get("parse_source"),
                    "error": r.get("error"),
                    "raw_response": r.get("raw_response"),
                }) + "\n")
                n_incorrect += 1
    print(f"Wrote {incorrect_path} ({n_incorrect} incorrect responses for failure-mode analysis)")


def grade(responses_path=DEFAULT_RESPONSES_PATH, out_prefix=DEFAULT_REPORT_PREFIX, epsilon: float = 0.01):
    rows = load_jsonl(responses_path)
    graded, tier_table, model_table, grid, overall = summarize(rows, epsilon)
    print_report(tier_table, model_table, grid, overall, epsilon)
    write_outputs(graded, tier_table, model_table, grid, overall, out_prefix, epsilon)
    return graded, tier_table, model_table, grid, overall


def build_arg_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(
        description="Grade saved model responses against gold answers.")
    parser.add_argument("--in", dest="inp", default=str(DEFAULT_RESPONSES_PATH))
    parser.add_argument("--out-prefix", default=str(DEFAULT_REPORT_PREFIX))
    parser.add_argument("--epsilon", type=float, default=0.01,
                         help="relative tolerance for numeric correctness (default 0.01 = 1%%)")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    grade(args.inp, args.out_prefix, args.epsilon)


if __name__ == "__main__":
    main()
