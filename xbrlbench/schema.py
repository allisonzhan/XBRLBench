"""Typed representation of a benchmark question record, plus the reasoning-
type taxonomy and the field constraints used by xbrlbench.validation.

See docs/SCHEMA.md for full field-by-field documentation and the policy for
when xbrlbench.BENCHMARK_VERSION must bump.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from .io_utils import load_jsonl
from .paths import DEFAULT_QUESTIONS_PATH

VALID_TIERS = {"T1", "T2", "T3"}

VALID_GOLD_UNITS = {"USD", "ratio", "percent"}

# Reasoning categories that actually describe the current question templates
# (xbrlbench.generation.make_questions) -- see docs/SCHEMA.md for one worked
# example of each. Only extend this set when a new question template is
# added that genuinely isn't covered by an existing category.
VALID_REASONING_TYPES = {
    "direct_retrieval",        # one value, located in the snippet and (if
                                # needed) unit-descaled -- no combination of
                                # multiple facts.
    "ratio",                   # two values from the SAME period combined by
                                # division into a financial ratio.
    "percentage_change",       # (new - old) / old across two periods,
                                # expressed as a percentage.
    "multi_period_comparison", # a value compared/subtracted across two
                                # fiscal years without being expressed as a
                                # percentage.
}

# The field set a question record must have. Kept in one place so the
# dataclass, the JSONL loader, and validation all agree.
REQUIRED_FIELDS = (
    "id", "ticker", "fiscal_year", "tier", "question", "gold_value",
    "gold_unit", "source_concept", "context", "reasoning_type", "tolerance",
)


@dataclass(frozen=True)
class Question:
    id: str
    ticker: str
    fiscal_year: int
    tier: str
    question: str
    gold_value: float
    gold_unit: str
    source_concept: str
    context: str
    reasoning_type: str
    tolerance: float

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Question":
        known = {f.name for f in fields(cls)}
        missing = known - d.keys()
        if missing:
            raise ValueError(f"question {d.get('id', '?')} is missing field(s): {sorted(missing)}")
        return cls(**{k: d[k] for k in known})

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def load_questions(path=DEFAULT_QUESTIONS_PATH) -> list[Question]:
    """Load and parse every record in a questions.jsonl into Question
    objects. Raises ValueError (via Question.from_dict) on the first record
    missing a required field -- for a tolerant, whole-file integrity report
    use xbrlbench.validation instead."""
    return [Question.from_dict(row) for row in load_jsonl(path)]
