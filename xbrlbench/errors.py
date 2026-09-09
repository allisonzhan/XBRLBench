"""xbrlbench.errors — deterministic, human-inspectable detail on every
non-correct response, meant to surface patterns a single accuracy number
can't: e.g. "this model succeeds on direct retrieval but fails once a
denominator has to be derived first."

Run:
  python -m xbrlbench errors
    -> results/errors.jsonl (full detail, one record per non-correct
       response), results/errors.csv (the same, minus raw_response, for
       spreadsheet scanning)

Like grade and report, this reads the raw responses file directly and
grades it itself, so it never depends on having run grade/report first and
never repeats paid API calls.

No LLM is used to classify failures here, deliberately: every field is
either copied straight from the graded row or computed with plain
arithmetic (absolute/relative error). The "pattern" a reader is meant to
find is whatever's visible when the records are grouped by reasoning_type,
tier, or parse_source -- see print_summary().
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from typing import Optional

from .grading import summarize
from .io_utils import load_jsonl
from .paths import DEFAULT_QUESTIONS_PATH, DEFAULT_RESPONSES_PATH, RESULTS_DIR
from .reporting import enrich_reasoning_type

DEFAULT_ERRORS_PREFIX = RESULTS_DIR / "errors"


def _error_magnitudes(model_val: Optional[float], gold_val: Optional[float]) -> tuple:
    if model_val is None or gold_val is None:
        return None, None
    abs_err = abs(model_val - gold_val)
    rel_err = (abs_err / abs(gold_val)) if gold_val != 0 else None
    return abs_err, rel_err


def build_error_records(rows: list[dict], questions_path=DEFAULT_QUESTIONS_PATH, epsilon: Optional[float] = None) -> list[dict]:
    rows = enrich_reasoning_type(rows, questions_path)
    graded, *_ = summarize(rows, epsilon)

    questions_by_id = {}
    try:
        questions_by_id = {q.get("id"): q for q in load_jsonl(questions_path)}
    except (FileNotFoundError, OSError):
        pass

    records = []
    for r in graded:
        if r["status"] == "correct":
            continue
        q = questions_by_id.get(r.get("id"), {})
        abs_err, rel_err = _error_magnitudes(r.get("parsed_model_answer"), r.get("gold_value"))
        records.append({
            "id": r.get("id"),
            "model": r.get("model"),
            "status": r.get("status"),
            "tier": r.get("tier"),
            "reasoning_type": r.get("reasoning_type") or q.get("reasoning_type"),
            "question": r.get("question"),
            "gold_value": r.get("gold_value"),
            "gold_unit": r.get("gold_unit"),
            "source_concept": r.get("source_concept") or q.get("source_concept"),
            "parsed_model_answer": r.get("parsed_model_answer"),
            "parse_source": r.get("parse_source"),
            "absolute_error": abs_err,
            "relative_error": rel_err,
            "api_error": r.get("error"),
            "raw_response": r.get("raw_response"),
        })

    records.sort(key=lambda r: (
        r["reasoning_type"] or "",
        r["tier"] or "",
        r["id"] or "",
        r["model"] or "",
    ))
    return records


def print_summary(records: list[dict]) -> None:
    print(f"{len(records)} non-correct response(s)\n")
    if not records:
        return

    def _print_counts(title: str, counts: Counter) -> None:
        print(f"{title}:")
        for key, n in sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0]))):
            print(f"  {key}: {n}")
        print()

    _print_counts("By status", Counter(r["status"] for r in records))
    _print_counts("By reasoning type", Counter(r["reasoning_type"] for r in records))
    _print_counts("By tier", Counter(r["tier"] for r in records))
    _print_counts("By parse source", Counter(r["parse_source"] for r in records))
    _print_counts("By model", Counter(r["model"] for r in records))


def write_outputs(records: list[dict], out_prefix) -> None:
    jsonl_path = f"{out_prefix}.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    print(f"Wrote {jsonl_path}")

    csv_path = f"{out_prefix}.csv"
    fieldnames = [k for k in records[0].keys() if k != "raw_response"] if records else [
        "id", "model", "status", "tier", "reasoning_type", "question", "gold_value", "gold_unit",
        "source_concept", "parsed_model_answer", "parse_source", "absolute_error", "relative_error", "api_error",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in records:
            w.writerow({k: v for k, v in r.items() if k != "raw_response"})
    print(f"Wrote {csv_path} ({len(records)} rows, raw_response omitted -- see the .jsonl for full transcripts)")


def errors(
    responses_path=DEFAULT_RESPONSES_PATH,
    questions_path=DEFAULT_QUESTIONS_PATH,
    out_prefix=DEFAULT_ERRORS_PREFIX,
    epsilon: Optional[float] = None,
) -> list[dict]:
    rows = load_jsonl(responses_path)
    records = build_error_records(rows, questions_path, epsilon)
    print_summary(records)
    write_outputs(records, out_prefix)
    return records


def build_arg_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(
        description="Deterministic per-response detail on every non-correct answer.")
    parser.add_argument("--in", dest="inp", default=str(DEFAULT_RESPONSES_PATH))
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS_PATH),
                         help="question bank, used to backfill reasoning_type/source_concept if missing")
    parser.add_argument("--out-prefix", default=str(DEFAULT_ERRORS_PREFIX))
    parser.add_argument("--epsilon", type=float, default=None,
                         help="override every row's tolerance (see `xbrlbench grade --help`)")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    errors(args.inp, args.questions, args.out_prefix, args.epsilon)


if __name__ == "__main__":
    main()
