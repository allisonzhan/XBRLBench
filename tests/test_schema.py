"""Schema tests for Commit 2: every question in data/questions.jsonl parses
into a Question, every reasoning_type is one of the documented categories,
and the dataclass round-trips through dict conversion."""

from xbrlbench.paths import DEFAULT_QUESTIONS_PATH
from xbrlbench.schema import (
    REQUIRED_FIELDS,
    VALID_GOLD_UNITS,
    VALID_REASONING_TYPES,
    VALID_TIERS,
    Question,
    load_questions,
)


def test_all_questions_load():
    questions = load_questions(DEFAULT_QUESTIONS_PATH)
    assert len(questions) == 68


def test_ids_are_unique():
    questions = load_questions(DEFAULT_QUESTIONS_PATH)
    ids = [q.id for q in questions]
    assert len(ids) == len(set(ids))


def test_every_question_has_a_valid_tier_unit_and_reasoning_type():
    questions = load_questions(DEFAULT_QUESTIONS_PATH)
    for q in questions:
        assert q.tier in VALID_TIERS, q.id
        assert q.gold_unit in VALID_GOLD_UNITS, q.id
        assert q.reasoning_type in VALID_REASONING_TYPES, q.id


def test_every_reasoning_type_is_actually_used():
    """Guards against the taxonomy drifting from the real question bank in
    either direction: every declared category should describe at least one
    real question (see docs/SCHEMA.md's "deliberately exhaustive" note)."""
    questions = load_questions(DEFAULT_QUESTIONS_PATH)
    used = {q.reasoning_type for q in questions}
    assert used == VALID_REASONING_TYPES


def test_question_from_dict_rejects_missing_field():
    row = {f: "x" for f in REQUIRED_FIELDS if f != "tolerance"}
    try:
        Question.from_dict(row)
    except ValueError as e:
        assert "tolerance" in str(e)
    else:
        raise AssertionError("expected ValueError for missing 'tolerance' field")


def test_question_round_trips_through_dict():
    questions = load_questions(DEFAULT_QUESTIONS_PATH)
    original = questions[0]
    rebuilt = Question.from_dict(original.to_dict())
    assert rebuilt == original
