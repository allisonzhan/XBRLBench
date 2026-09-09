"""xbrlbench.validation — integrity checks for the benchmark question bank,
meant to run before an evaluation so a broken or hand-edited question bank
is caught early instead of silently producing misleading accuracy numbers.

Run:
  python -m xbrlbench validate

Every finding is either an ERROR (a genuine schema/integrity violation --
the run exits non-zero if any are found) or a WARNING (a heuristic that
flags something worth a human look, e.g. wording that suggests one unit
while gold_unit says another; not necessarily wrong).

This module is deliberately tolerant of malformed input: a check that needs
a field a row doesn't have just skips that finding for that row rather than
raising, so one broken row can't hide problems in the rest of the file.
"""

from __future__ import annotations

import argparse
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Optional

from .io_utils import load_jsonl
from .paths import DEFAULT_QUESTIONS_PATH
from .schema import VALID_GOLD_UNITS, VALID_REASONING_TYPES, VALID_TIERS

ERROR = "error"
WARNING = "warning"

# Tolerance sanity bounds (tolerance's meaning depends on gold_unit -- see
# docs/SCHEMA.md). These catch obvious mistakes (a stray "1" meant to be
# "0.01", a negative/zero value), not a judgment call about the "right"
# tolerance for a specific question.
_MAX_SANE_TOLERANCE = 1.0    # >= 100% relative / >= 1.0 absolute is nonsensical -- error
_LOOSE_TOLERANCE_WARN = 0.1  # >= 10% relative / >= 0.1 absolute is unusually loose -- warning

_MIN_LEAK_DIGITS = 3  # don't flag leakage for a gold value whose formatted string is shorter than this (too many false positives, e.g. a bare "1")

# Wording that suggests a specific unit, checked against gold_unit. Kept
# deliberately small and literal (a heuristic, not NLP) to keep the false
# positive rate low.
_UNIT_WORDING_HINTS = {
    "percent": ("percentage", "%"),
    "ratio": ("ratio",),
}


@dataclass(frozen=True)
class ValidationIssue:
    severity: str  # "error" | "warning"
    code: str
    message: str
    question_id: Optional[str] = None

    def __str__(self) -> str:
        prefix = "ERROR" if self.severity == ERROR else "WARN "
        loc = f" [{self.question_id}]" if self.question_id else ""
        return f"{prefix}{loc}: {self.message}"


def _is_real_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_nonempty_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _format_variants(value: float) -> list[str]:
    """A few plausible renderings of `value`, used to detect the exact gold
    answer having leaked verbatim into the question text."""
    variants = {f"{value:.2f}", f"{value:,.2f}"}
    if value == int(value):
        variants.add(str(int(value)))
        variants.add(f"{int(value):,}")
    return [v for v in variants if len(v.lstrip("-")) >= _MIN_LEAK_DIGITS]


def _check_required_fields(row: dict) -> list[ValidationIssue]:
    qid = row.get("id")
    issues: list[ValidationIssue] = []

    if not _is_nonempty_str(row.get("id")):
        issues.append(ValidationIssue(ERROR, "missing_id", "question has no id", qid))
    if not _is_nonempty_str(row.get("question")):
        issues.append(ValidationIssue(ERROR, "missing_question_text", "question text is empty", qid))
    if not _is_nonempty_str(row.get("context")):
        issues.append(ValidationIssue(ERROR, "missing_context", "context is empty", qid))
    if not _is_nonempty_str(row.get("source_concept")):
        issues.append(ValidationIssue(ERROR, "missing_source_concept", "missing source_concept (provenance)", qid))

    tier = row.get("tier")
    if tier not in VALID_TIERS:
        issues.append(ValidationIssue(ERROR, "invalid_tier", f"tier {tier!r} is not one of {sorted(VALID_TIERS)}", qid))

    reasoning_type = row.get("reasoning_type")
    if reasoning_type not in VALID_REASONING_TYPES:
        issues.append(ValidationIssue(
            ERROR, "invalid_reasoning_type",
            f"reasoning_type {reasoning_type!r} is not one of {sorted(VALID_REASONING_TYPES)}", qid))

    gold_unit = row.get("gold_unit")
    if gold_unit not in VALID_GOLD_UNITS:
        issues.append(ValidationIssue(
            ERROR, "invalid_gold_unit", f"gold_unit {gold_unit!r} is not one of {sorted(VALID_GOLD_UNITS)}", qid))

    gold_value = row.get("gold_value")
    if gold_value is None:
        issues.append(ValidationIssue(ERROR, "missing_gold_value", "gold_value is missing", qid))
    elif not _is_real_number(gold_value):
        issues.append(ValidationIssue(ERROR, "malformed_gold_value", f"gold_value {gold_value!r} is not a finite number", qid))

    tolerance = row.get("tolerance")
    if tolerance is None:
        issues.append(ValidationIssue(ERROR, "missing_tolerance", "tolerance is missing", qid))
    elif not _is_real_number(tolerance) or tolerance <= 0:
        issues.append(ValidationIssue(ERROR, "invalid_tolerance", f"tolerance {tolerance!r} must be a positive finite number", qid))
    elif tolerance >= _MAX_SANE_TOLERANCE:
        issues.append(ValidationIssue(
            ERROR, "invalid_tolerance",
            f"tolerance {tolerance!r} is >= {_MAX_SANE_TOLERANCE} (effectively disables the correctness check)", qid))
    elif tolerance >= _LOOSE_TOLERANCE_WARN:
        issues.append(ValidationIssue(WARNING, "loose_tolerance", f"tolerance {tolerance!r} is unusually loose", qid))

    return issues


def _check_unit_wording(row: dict) -> list[ValidationIssue]:
    qid = row.get("id")
    question = (row.get("question") or "").lower()
    gold_unit = row.get("gold_unit")
    issues = []
    for unit, hints in _UNIT_WORDING_HINTS.items():
        if gold_unit != unit and any(hint in question for hint in hints):
            issues.append(ValidationIssue(
                WARNING, "unit_wording_mismatch",
                f"question wording suggests '{unit}' but gold_unit is {gold_unit!r}", qid))
    return issues


def _check_answer_leakage(row: dict) -> list[ValidationIssue]:
    qid = row.get("id")
    gold_value = row.get("gold_value")
    question = row.get("question") or ""
    if not _is_real_number(gold_value):
        return []
    for variant in _format_variants(float(gold_value)):
        if variant in question:
            return [ValidationIssue(
                ERROR, "answer_leaked_in_question",
                f"gold_value appears verbatim ({variant!r}) in the question text", qid)]
    return []


def _check_duplicates(rows: list[dict]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    id_counts = Counter(r.get("id") for r in rows if _is_nonempty_str(r.get("id")))
    for qid, count in id_counts.items():
        if count > 1:
            issues.append(ValidationIssue(ERROR, "duplicate_id", f"id appears {count} times", qid))

    # Same underlying fact (company, year, tier, source concept) asked more
    # than once under different ids -- almost always a generation bug.
    fact_ids: dict[tuple, list[str]] = defaultdict(list)
    for r in rows:
        key = (r.get("ticker"), r.get("fiscal_year"), r.get("tier"), r.get("source_concept"))
        fact_ids[key].append(r.get("id"))
    for key, ids in fact_ids.items():
        if len(ids) > 1:
            for qid in ids:
                issues.append(ValidationIssue(
                    ERROR, "duplicate_fact",
                    f"same (ticker, fiscal_year, tier, source_concept)={key} tested by {len(ids)} questions: {ids}", qid))

    # Identical question wording across different ids -- plausible by
    # coincidence, so a warning rather than an error.
    text_ids: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        text = (r.get("question") or "").strip().lower()
        if text:
            text_ids[text].append(r.get("id"))
    for text, ids in text_ids.items():
        if len(ids) > 1:
            for qid in ids:
                issues.append(ValidationIssue(
                    WARNING, "duplicate_question_text",
                    f"identical question text used by {len(ids)} questions: {ids}", qid))

    return issues


def validate_questions(rows: list[dict]) -> list[ValidationIssue]:
    """Run every integrity check over a list of raw question dicts (as
    loaded from questions.jsonl)."""
    issues: list[ValidationIssue] = []
    for row in rows:
        issues += _check_required_fields(row)
        issues += _check_unit_wording(row)
        issues += _check_answer_leakage(row)
    issues += _check_duplicates(rows)
    return issues


def print_report(issues: list[ValidationIssue], n_questions: int) -> None:
    errors = [i for i in issues if i.severity == ERROR]
    warnings = [i for i in issues if i.severity == WARNING]

    print(f"Validated {n_questions} questions: {len(errors)} error(s), {len(warnings)} warning(s)")
    if issues:
        print()
        for issue in errors + warnings:
            print(issue)
    else:
        print("No issues found.")


def validate_and_report(path=DEFAULT_QUESTIONS_PATH) -> list[ValidationIssue]:
    rows = load_jsonl(path)
    issues = validate_questions(rows)
    print_report(issues, len(rows))
    return issues


def build_arg_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(
        description="Validate benchmark question bank integrity.")
    parser.add_argument("--in", dest="inp", default=str(DEFAULT_QUESTIONS_PATH))
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    issues = validate_and_report(args.inp)
    if any(i.severity == ERROR for i in issues):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
