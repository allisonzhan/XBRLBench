"""Validation tests (Commit 4). Each fixture introduces exactly one defect
so a test can assert the specific issue code fires -- and, for the real
question bank, that nothing spurious fires."""

from xbrlbench.io_utils import load_jsonl
from xbrlbench.paths import DEFAULT_QUESTIONS_PATH
from xbrlbench.validation import ERROR, WARNING, validate_questions


def good_question(**overrides):
    base = {
        "id": "AAPL-2023-T1-0",
        "ticker": "AAPL",
        "fiscal_year": 2023,
        "tier": "T1",
        "question": "What were AAPL's total assets for fiscal year 2023? Answer in USD.",
        "gold_value": 352755000000.0,
        "gold_unit": "USD",
        "source_concept": "Assets",
        "context": "AAPL -- Consolidated Financial Statements (excerpt), FY2023\n...",
        "reasoning_type": "direct_retrieval",
        "tolerance": 0.01,
    }
    base.update(overrides)
    return base


def codes(issues, severity=None):
    return {i.code for i in issues if severity is None or i.severity == severity}


def ids_with_code(issues, code):
    return {i.question_id for i in issues if i.code == code}


# ---------------------------------------------------------------------------
# The real question bank
# ---------------------------------------------------------------------------

def test_real_question_bank_has_no_errors():
    rows = load_jsonl(DEFAULT_QUESTIONS_PATH)
    issues = validate_questions(rows)
    errors = [i for i in issues if i.severity == ERROR]
    assert errors == [], f"unexpected errors in the real question bank: {errors}"


def test_real_question_bank_has_no_warnings():
    rows = load_jsonl(DEFAULT_QUESTIONS_PATH)
    issues = validate_questions(rows)
    warnings = [i for i in issues if i.severity == WARNING]
    assert warnings == [], f"unexpected warnings in the real question bank: {warnings}"


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------

def test_duplicate_id_detected():
    rows = [good_question(id="X-1"), good_question(id="X-1", question="a different question entirely?")]
    issues = validate_questions(rows)
    assert "duplicate_id" in codes(issues)


def test_duplicate_fact_detected():
    rows = [
        good_question(id="A", question="What were AAPL's total assets for FY2023, phrased one way?"),
        good_question(id="B", question="What were AAPL's total assets for FY2023, phrased another way?"),
    ]
    issues = validate_questions(rows)
    assert "duplicate_fact" in codes(issues)
    assert ids_with_code(issues, "duplicate_fact") == {"A", "B"}


def test_duplicate_question_text_is_a_warning_not_an_error():
    rows = [
        good_question(id="A", ticker="AAPL", source_concept="Assets"),
        good_question(id="B", ticker="MSFT", source_concept="NetIncomeLoss",
                       question=good_question()["question"]),  # same wording, different fact
    ]
    issues = validate_questions(rows)
    assert "duplicate_question_text" in codes(issues, WARNING)
    assert "duplicate_question_text" not in codes(issues, ERROR)


def test_distinct_questions_produce_no_duplicate_findings():
    rows = [
        good_question(id="A", ticker="AAPL", question="Question A"),
        good_question(id="B", ticker="MSFT", question="Question B", source_concept="NetIncomeLoss"),
    ]
    issues = validate_questions(rows)
    assert "duplicate_id" not in codes(issues)
    assert "duplicate_fact" not in codes(issues)
    assert "duplicate_question_text" not in codes(issues)


# ---------------------------------------------------------------------------
# Missing / malformed values
# ---------------------------------------------------------------------------

def test_missing_gold_value():
    issues = validate_questions([good_question(gold_value=None)])
    assert "missing_gold_value" in codes(issues, ERROR)


def test_malformed_gold_value_nan():
    issues = validate_questions([good_question(gold_value=float("nan"))])
    assert "malformed_gold_value" in codes(issues, ERROR)


def test_malformed_gold_value_wrong_type():
    issues = validate_questions([good_question(gold_value="352755000000")])
    assert "malformed_gold_value" in codes(issues, ERROR)


def test_invalid_gold_unit():
    issues = validate_questions([good_question(gold_unit="dollars")])
    assert "invalid_gold_unit" in codes(issues, ERROR)


def test_invalid_tier():
    issues = validate_questions([good_question(tier="T9")])
    assert "invalid_tier" in codes(issues, ERROR)


def test_invalid_reasoning_type():
    issues = validate_questions([good_question(reasoning_type="vibes")])
    assert "invalid_reasoning_type" in codes(issues, ERROR)


def test_missing_source_concept():
    issues = validate_questions([good_question(source_concept="")])
    assert "missing_source_concept" in codes(issues, ERROR)


def test_missing_question_text():
    issues = validate_questions([good_question(question="")])
    assert "missing_question_text" in codes(issues, ERROR)


def test_missing_context():
    issues = validate_questions([good_question(context=None)])
    assert "missing_context" in codes(issues, ERROR)


# ---------------------------------------------------------------------------
# Tolerance
# ---------------------------------------------------------------------------

def test_missing_tolerance():
    issues = validate_questions([good_question(tolerance=None)])
    assert "missing_tolerance" in codes(issues, ERROR)


def test_negative_tolerance():
    issues = validate_questions([good_question(tolerance=-0.01)])
    assert "invalid_tolerance" in codes(issues, ERROR)


def test_zero_tolerance():
    issues = validate_questions([good_question(tolerance=0.0)])
    assert "invalid_tolerance" in codes(issues, ERROR)


def test_impossibly_large_tolerance_is_an_error():
    issues = validate_questions([good_question(tolerance=1.5)])
    assert "invalid_tolerance" in codes(issues, ERROR)


def test_unusually_loose_tolerance_is_a_warning():
    issues = validate_questions([good_question(tolerance=0.25)])
    assert "loose_tolerance" in codes(issues, WARNING)
    assert "invalid_tolerance" not in codes(issues, ERROR)


def test_normal_tolerance_is_clean():
    issues = validate_questions([good_question(tolerance=0.01)])
    assert codes(issues) == set()


# ---------------------------------------------------------------------------
# Unit wording heuristic
# ---------------------------------------------------------------------------

def test_percentage_wording_with_non_percent_unit_warns():
    issues = validate_questions([good_question(
        question="What was AAPL's growth as a percentage?", gold_unit="USD")])
    assert "unit_wording_mismatch" in codes(issues, WARNING)


def test_percentage_wording_with_percent_unit_is_clean():
    issues = validate_questions([good_question(
        question="What was AAPL's growth as a percentage?", gold_unit="percent")])
    assert "unit_wording_mismatch" not in codes(issues)


# ---------------------------------------------------------------------------
# Answer leakage
# ---------------------------------------------------------------------------

def test_answer_leaked_verbatim_in_question():
    issues = validate_questions([good_question(
        question="What were AAPL's total assets for FY2023? (Hint: it's 352,755,000,000.)",
    )])
    assert "answer_leaked_in_question" in codes(issues, ERROR)


def test_answer_only_in_context_is_not_leakage():
    # The gold value legitimately appears in `context` (it's the source
    # data) -- only `question` is checked for leakage.
    issues = validate_questions([good_question(
        context="... Total assets .......... 352,755,000,000 ...",
    )])
    assert "answer_leaked_in_question" not in codes(issues)


def test_small_gold_value_does_not_false_positive_on_leakage():
    # A ratio gold value like 1.0 shouldn't flag every question that
    # happens to contain the digit "1" somewhere.
    issues = validate_questions([good_question(
        gold_value=1.0, gold_unit="ratio",
        question="What was AAPL's current ratio for fiscal year 2023?",
    )])
    assert "answer_leaked_in_question" not in codes(issues)
