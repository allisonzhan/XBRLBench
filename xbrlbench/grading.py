"""xbrlbench.grading — grade xbrlbench.inference's raw model responses
against gold answers and report accuracy by tier and by model.

Reads a responses JSONL (xbrlbench.inference's output), which already
carries gold_value / gold_unit / tier / model alongside each raw_response.

Run:
  python -m xbrlbench grade
    -> results/report_summary.csv, results/report_summary.json,
       results/report_incorrect.jsonl (+ the tier x model table on stdout)

Flags:
  --epsilon 0.01   override every row's tolerance (see "Tolerance" below).
                    Default: use each row's own `tolerance` field (falls
                    back to 0.01 if the row predates that field).

Note: this module never calls a model — it only re-reads a saved responses
file, so re-grading (a different --epsilon, or after this file changes) never
re-spends API budget.

Every graded row gets an explicit status -- "correct", "incorrect", or
"invalid_response" -- instead of a bare boolean. "invalid_response" covers
everything that isn't a genuine right-or-wrong numeric judgment: an API
error, a missing/malformed gold value, or a model answer this module could
not safely resolve to one number. The rule throughout this file is: never
silently guess. An ambiguous model answer (e.g. two different numbers on the
ANSWER: line) is graded invalid, not correct-by-luck or incorrect-by-luck.

Tolerance
---------
Each question's `tolerance` field (see docs/SCHEMA.md) is combined with its
`gold_unit` to get an absolute tolerance window, because a single relative
formula applied uniformly to USD/ratio/percent values has two real failure
modes: it demands near-exact precision on ratios that were only ever asked
for to 2 decimals, and it collapses toward zero tolerance for a
near-zero-percent value (e.g. ~0% YoY growth), which is exactly a
division-by-near-zero trap.

  USD:     relative -- max(tolerance * |gold|, $1 floor)
  ratio:   absolute -- `tolerance` itself (matches the "2 decimals" the
           question already asks for; e.g. 0.01 == off by at most 1 in the
           last requested digit)
  percent: relative -- max(tolerance * |gold|, 0.1 percentage-point floor)

This is the same historical value (0.01) used everywhere no per-question
tolerance was set, so grading against a pre-schema responses file (no
`tolerance` field) behaves the same as before for USD questions and only
gets the near-zero-percent and ratio-precision fixes described above.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from typing import Optional

from .io_utils import load_jsonl
from .paths import DEFAULT_REPORT_PREFIX, DEFAULT_RESPONSES_PATH

# A number, optionally wrapped in accounting-style parentheses for a negative
# value, e.g. "(1,234.56)" == -1234.56.
_NUMBER_TOKEN = r"\(?-?\d[\d,]*\.?\d*\)?"
NUMBER_RE = re.compile(_NUMBER_TOKEN)

SCALE_WORDS = {
    "thousand": 1e3,
    "million": 1e6,
    "billion": 1e9,
    "trillion": 1e12,
}
_SCALE_WORD_RE = re.compile(
    r"(" + _NUMBER_TOKEN + r")\s*(" + "|".join(SCALE_WORDS) + r")s?\b",
    re.IGNORECASE,
)

DEFAULT_TOLERANCE = 0.01  # used when a row/question predates the `tolerance` field
_USD_FLOOR = 1.0          # $1 -- keeps relative tolerance from vanishing near $0
_PERCENT_FLOOR = 0.1      # 0.1 percentage points -- same, for near-0% values


def _parse_single_token(token: str) -> Optional[float]:
    """Parse one already-isolated number token: strips $/,/% and recognizes
    accounting-style "(1,234)" as a negative. Returns None (never raises) on
    anything that isn't cleanly numeric -- callers treat that as "no number
    here", not as a crash."""
    token = token.strip()
    negative = token.startswith("(") and token.endswith(")")
    if negative:
        token = token[1:-1]
    token = token.replace(",", "").replace("$", "").rstrip("%").strip()
    if not token or token in {"-", "."}:
        return None
    try:
        val = float(token)
    except ValueError:
        return None
    return -val if negative else val


def extract_numbers(text: Optional[str]) -> list[float]:
    """Every numeric value mentioned in `text`, in order of appearance.
    Recognizes accounting-style negative parentheses and a number
    immediately followed by a scale word (thousand/million/billion/
    trillion), e.g. "1.2 billion" -> 1_200_000_000.0."""
    if not text:
        return []
    consumed_spans = set()
    values: list[float] = []
    for m in _SCALE_WORD_RE.finditer(text):
        val = _parse_single_token(m.group(1))
        if val is None:
            continue
        values.append(val * SCALE_WORDS[m.group(2).lower()])
        consumed_spans.add((m.start(1), m.end(1)))
    for m in NUMBER_RE.finditer(text):
        if (m.start(), m.end()) in consumed_spans:
            continue
        val = _parse_single_token(m.group())
        if val is not None:
            values.append(val)
    return values


def _dedupe(values: list[float]) -> list[float]:
    """Collapse values that are the same number up to floating-point noise
    (e.g. two different renderings of the same figure), so formatting
    repetition doesn't look like multiple distinct answers."""
    out: list[float] = []
    for v in values:
        if not any(abs(v - o) <= 1e-6 * max(abs(v), abs(o), 1.0) for o in out):
            out.append(v)
    return out


def extract_single_number(text: Optional[str]) -> tuple[Optional[float], str]:
    """Returns (value, status):
      "ok"        -- exactly one distinct number in `text` -> value is it
      "empty"     -- no numbers found -> value is None
      "ambiguous" -- more than one distinct number found -> value is None
                     (never guess which one the model meant)
    """
    values = _dedupe(extract_numbers(text))
    if not values:
        return None, "empty"
    if len(values) > 1:
        return None, "ambiguous"
    return values[0], "ok"


def get_model_answer(row: dict) -> tuple[Optional[float], str]:
    """Resolve a graded row's model answer to a single float, in decreasing
    order of confidence. Returns (value_or_None, parse_source):

      "answer_line"           -- a clean, single number on the model's
                                  ANSWER: line (xbrlbench.inference's
                                  extracted_answer field).
      "answer_line_ambiguous" -- the ANSWER: line had >1 distinct number;
                                  refused to guess which one was meant.
      "last_line_fallback"    -- no ANSWER: line, but the response's last
                                  non-empty line has exactly one number.
      "last_line_ambiguous"   -- that last line had >1 distinct number.
      "full_text_fallback"    -- no usable last line; last-resort scan takes
                                  the last number anywhere in the response.
                                  Lowest confidence of the four "found
                                  something" outcomes -- kept for backward
                                  compatibility with older prompt formats,
                                  but never silently promoted above the
                                  others.
      "unparseable"           -- no number found anywhere.
    """
    extracted = row.get("extracted_answer")
    if extracted:
        val, status = extract_single_number(extracted)
        if status == "ok":
            return val, "answer_line"
        if status == "ambiguous":
            return None, "answer_line_ambiguous"
        # status == "empty": the ANSWER: line had no numeric content -- fall through.

    raw = row.get("raw_response")
    if raw:
        lines = [ln for ln in raw.strip().splitlines() if ln.strip()]
        if lines:
            val, status = extract_single_number(lines[-1])
            if status == "ok":
                return val, "last_line_fallback"
            if status == "ambiguous":
                return None, "last_line_ambiguous"
        values = extract_numbers(raw)
        if values:
            return values[-1], "full_text_fallback"

    return None, "unparseable"


def numeric_tolerance(gold_unit: str, gold_val: float, tolerance: float) -> float:
    """The absolute +/- window a model answer must fall within. See the
    "Tolerance" section of this module's docstring for the reasoning behind
    each unit's formula."""
    gold_val = abs(gold_val)
    if gold_unit == "USD":
        return max(tolerance * gold_val, _USD_FLOOR)
    if gold_unit == "ratio":
        return tolerance
    if gold_unit == "percent":
        return max(tolerance * gold_val, _PERCENT_FLOOR)
    # Fail safe: an unrecognized unit must never silently grade correct.
    raise ValueError(f"unsupported gold_unit: {gold_unit!r}")


def is_correct(model_val: Optional[float], gold_val: Optional[float],
                gold_unit: Optional[str], tolerance: float) -> bool:
    if model_val is None or gold_val is None or gold_unit is None:
        return False
    return abs(model_val - gold_val) <= numeric_tolerance(gold_unit, gold_val, tolerance)


def grade_row(row: dict, epsilon_override: Optional[float]) -> dict:
    """Grade one response row. Never raises on malformed input -- anything
    it can't confidently judge comes back as status "invalid_response"."""
    model_val, source = get_model_answer(row)
    gold_val = row.get("gold_value")
    gold_unit = row.get("gold_unit")
    tolerance = epsilon_override if epsilon_override is not None else row.get("tolerance")
    if tolerance is None:
        # Either epsilon_override wasn't given and the row has no `tolerance`
        # field (older responses file), or it's explicitly null -- both fall
        # back to the historical default rather than crashing.
        tolerance = DEFAULT_TOLERANCE

    if row.get("error") is not None:
        status = "invalid_response"          # the model call itself failed
    elif gold_val is None or gold_unit is None:
        status = "invalid_response"          # malformed/missing ground truth -- a data problem, not a model failure
    elif model_val is None:
        status = "invalid_response"          # unparseable or ambiguous model answer
    elif is_correct(model_val, gold_val, gold_unit, tolerance):
        status = "correct"
    else:
        status = "incorrect"

    return {
        **row,
        "parsed_model_answer": model_val,
        "parse_source": source,
        "status": status,
        "correct": status == "correct",  # kept for simple truthiness checks / backward compatibility
    }


def summarize(rows: list[dict], epsilon: Optional[float] = None):
    """Returns (graded, tier_table, model_table, grid, overall). Each bucket
    cell is {"n", "correct", "invalid"}; "incorrect" is implicit
    (n - correct - invalid)."""
    graded = [grade_row(row, epsilon) for row in rows]

    def bucket(rows_, key):
        b: dict = {}
        for r in rows_:
            cell = b.setdefault(r.get(key), {"n": 0, "correct": 0, "invalid": 0})
            cell["n"] += 1
            if r["status"] == "correct":
                cell["correct"] += 1
            elif r["status"] == "invalid_response":
                cell["invalid"] += 1
        return b

    tier_table = bucket(graded, "tier")
    model_table = bucket(graded, "model")

    grid: dict = {}
    for r in graded:
        cell = grid.setdefault((r.get("tier"), r.get("model")), {"n": 0, "correct": 0, "invalid": 0})
        cell["n"] += 1
        if r["status"] == "correct":
            cell["correct"] += 1
        elif r["status"] == "invalid_response":
            cell["invalid"] += 1

    n_total = len(graded)
    n_correct = sum(1 for r in graded if r["status"] == "correct")
    n_invalid = sum(1 for r in graded if r["status"] == "invalid_response")
    overall = {
        "n": n_total, "correct": n_correct, "invalid": n_invalid,
        "accuracy": (n_correct / n_total) if n_total else 0.0,
    }

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
            cell = grid.get((t, m), {"n": 0, "correct": 0, "invalid": 0})
            m_n += cell["n"]
            m_c += cell["correct"]
            cells.append(pct(cell["correct"], cell["n"]))
        cells.append(pct(m_c, m_n))
        print(f"{m:<{col_w}} " + " ".join(f"{c:>10}" for c in cells))

    print(f"\n{'ALL MODELS':<{col_w}} " +
          " ".join(f"{pct(tier_table[t]['correct'], tier_table[t]['n']):>10}" for t in tiers) +
          f" {pct(overall['correct'], overall['n']):>10}")

    print(f"\nOverall: {overall['correct']}/{overall['n']} = {pct(overall['correct'], overall['n'])}")
    if overall["invalid"]:
        print(f"Invalid/unparseable responses: {overall['invalid']}/{overall['n']} "
              f"(excluded from accuracy, counted as not-correct)")


def write_outputs(graded, tier_table, model_table, grid, overall, out_prefix, epsilon):
    # CSV: one row per tier x model cell, plus overall rows.
    csv_path = f"{out_prefix}_summary.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["tier", "model", "n", "correct", "invalid", "accuracy"])

        def row_out(tier, model, cell):
            acc = cell["correct"] / cell["n"] if cell["n"] else 0.0
            w.writerow([tier, model, cell["n"], cell["correct"], cell["invalid"], round(acc, 4)])

        for (tier, model), cell in sorted(grid.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
            row_out(tier, model, cell)
        for tier, cell in sorted(tier_table.items(), key=lambda kv: str(kv[0])):
            row_out(tier, "ALL_MODELS", cell)
        for model, cell in sorted(model_table.items(), key=lambda kv: str(kv[0])):
            row_out("ALL_TIERS", model, cell)
        row_out("ALL_TIERS", "ALL_MODELS", overall)
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
    n_written = 0
    with open(incorrect_path, "w", encoding="utf-8") as f:
        for r in graded:
            if r["status"] != "correct":
                f.write(json.dumps({
                    "id": r.get("id"),
                    "tier": r.get("tier"),
                    "model": r.get("model"),
                    "ticker": r.get("ticker"),
                    "fiscal_year": r.get("fiscal_year"),
                    "question": r.get("question"),
                    "gold_value": r.get("gold_value"),
                    "gold_unit": r.get("gold_unit"),
                    "status": r.get("status"),
                    "parsed_model_answer": r.get("parsed_model_answer"),
                    "parse_source": r.get("parse_source"),
                    "error": r.get("error"),
                    "raw_response": r.get("raw_response"),
                }) + "\n")
                n_written += 1
    print(f"Wrote {incorrect_path} ({n_written} non-correct responses for failure-mode analysis)")


def grade(responses_path=DEFAULT_RESPONSES_PATH, out_prefix=DEFAULT_REPORT_PREFIX, epsilon: Optional[float] = None):
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
    parser.add_argument("--epsilon", type=float, default=None,
                         help="override every row's tolerance (default: use each "
                              "question's own `tolerance` field, or 0.01 if absent)")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    grade(args.inp, args.out_prefix, args.epsilon)


if __name__ == "__main__":
    main()
