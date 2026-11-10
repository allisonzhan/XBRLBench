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
import re
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


# Matches the header line render_snippet writes for each fiscal-year block,
# e.g. "AAPL — Consolidated Financial Statements (excerpt), FY2023".
_STATEMENT_HEADER = re.compile(r"^(?P<ticker>\S+) \S+ Consolidated Financial Statements \(excerpt\), FY(?P<year>\d{4})$")
_LINE_ITEM = re.compile(r"^\s+(?P<label>.+?) \.{3,} (?P<value>\S+)$")
_MISSING_VALUE = "\u2014"  # em dash -- render_snippet's placeholder for a missing fact

# Line items that should essentially never repeat to the dollar across two
# consecutive fiscal years. Two or more repeating means the same period was
# rendered under two fiscal-year labels (the companyfacts `fy` pitfall
# fixed in xbrlbench.generation.annual_value).
_REPEAT_THRESHOLD = 2

# source_concept "X (distractor: Y)" -> the rendered label of Y that must be
# present for the question to actually contain its distractor.
_DISTRACTOR_LABELS = {
    "Liabilities": "Total liabilities",
}


def _statement_blocks(context: str) -> dict[tuple[str, int], dict[str, str]]:
    """Parse a rendered context into {(ticker, year): {label: value}}."""
    blocks: dict[tuple[str, int], dict[str, str]] = {}
    current = None
    for line in context.splitlines():
        header = _STATEMENT_HEADER.match(line.strip())
        if header:
            current = (header["ticker"], int(header["year"]))
            blocks[current] = {}
            continue
        item = _LINE_ITEM.match(line)
        if current is not None and item:
            blocks[current][item["label"].strip()] = item["value"]
    return blocks


def _check_cross_year_consistency(rows: list[dict]) -> list[ValidationIssue]:
    """Catch fiscal-period mix-ups that per-row checks can't see: the same
    (ticker, year) rendered differently in two contexts, or two consecutive
    years of one company rendering the same figures."""
    issues: list[ValidationIssue] = []
    snapshots: dict[tuple[str, int], dict[str, str]] = {}
    seen_in: dict[tuple[str, int], str] = {}
    for r in rows:
        for key, items in _statement_blocks(r.get("context") or "").items():
            if key not in snapshots:
                snapshots[key] = items
                seen_in[key] = r.get("id")
            elif snapshots[key] != items:
                issues.append(ValidationIssue(
                    ERROR, "inconsistent_year_snapshot",
                    f"{key[0]} FY{key[1]} renders differently here than in {seen_in[key]}", r.get("id")))

    for (ticker, year), items in sorted(snapshots.items()):
        prev = snapshots.get((ticker, year - 1))
        if not prev:
            continue
        repeated = sorted(
            label for label, value in items.items()
            if value != _MISSING_VALUE and prev.get(label) == value)
        if len(repeated) >= _REPEAT_THRESHOLD:
            issues.append(ValidationIssue(
                ERROR, "repeated_values_across_years",
                f"{ticker} FY{year - 1} and FY{year} show identical values for {repeated} -- "
                f"likely the same period rendered under two fiscal-year labels"))
    return issues


def _check_degenerate_gold(row: dict) -> list[ValidationIssue]:
    """A change/comparison question whose gold answer is exactly 0 almost
    always means both periods resolved to the same data."""
    if row.get("reasoning_type") in ("percentage_change", "multi_period_comparison") \
            and row.get("gold_value") == 0:
        return [ValidationIssue(
            WARNING, "zero_change_gold",
            "change/comparison question has a gold_value of exactly 0", row.get("id"))]
    return []


def _check_distractor_present(row: dict) -> list[ValidationIssue]:
    """A T3 distractor question is only a distractor question if the
    distractor line actually has a value in the context."""
    match = re.search(r"\(distractor: (\w+)\)", row.get("source_concept") or "")
    if not match:
        return []
    label = _DISTRACTOR_LABELS.get(match.group(1))
    if label is None:
        return []
    for items in _statement_blocks(row.get("context") or "").values():
        if items.get(label, _MISSING_VALUE) != _MISSING_VALUE:
            return []
    return [ValidationIssue(
        WARNING, "missing_distractor",
        f"distractor line {label!r} has no value in the context, so this is a plain lookup",
        row.get("id"))]


def validate_questions(rows: list[dict]) -> list[ValidationIssue]:
    """Run every integrity check over a list of raw question dicts (as
    loaded from questions.jsonl)."""
    issues: list[ValidationIssue] = []
    for row in rows:
        issues += _check_required_fields(row)
        issues += _check_unit_wording(row)
        issues += _check_answer_leakage(row)
        issues += _check_degenerate_gold(row)
        issues += _check_distractor_present(row)
    issues += _check_duplicates(rows)
    issues += _check_cross_year_consistency(rows)
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
