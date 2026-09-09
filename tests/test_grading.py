"""Grading correctness tests (Commit 3). Each test targets one of the
brittleness categories from the audit: percent-vs-decimal confusion, dollars
vs thousands/millions, signs, rounding, commas/currency symbols, answers
embedded in prose, multiple numbers in one answer, floating-point precision,
missing/malformed responses, units, and tolerance thresholds.

The rule under test throughout: grading must never silently guess. Anything
ambiguous comes back as status "invalid_response", not "correct" or a
confident-looking "incorrect".
"""

import pytest

from xbrlbench.grading import (
    extract_numbers,
    extract_single_number,
    get_model_answer,
    grade_row,
    is_correct,
    numeric_tolerance,
)


def row(**overrides):
    base = {
        "id": "TEST-1",
        "tier": "T1",
        "model": "test/model",
        "gold_value": 1000.0,
        "gold_unit": "USD",
        "tolerance": 0.01,
        "extracted_answer": "1000",
        "raw_response": "...\nANSWER: 1000",
        "error": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# extract_numbers / extract_single_number
# ---------------------------------------------------------------------------

def test_commas_and_dollar_sign_stripped():
    assert extract_single_number("$352,755,000,000") == (352755000000.0, "ok")


def test_percent_sign_stripped():
    assert extract_single_number("12.34%") == (12.34, "ok")


def test_accounting_negative_parentheses():
    assert extract_single_number("(7,636,000,000)") == (-7636000000.0, "ok")


def test_explicit_negative_sign():
    assert extract_single_number("-31061000") == (-31061000.0, "ok")


def test_scale_word_billion():
    assert extract_single_number("1.2 billion") == (1_200_000_000.0, "ok")


def test_scale_word_thousand_case_insensitive():
    assert extract_single_number("42 THOUSAND") == (42_000.0, "ok")


def test_number_embedded_in_prose_with_trailing_unit_word():
    # extracted_answer can carry the model's trailing unit word even though
    # the system prompt asks for a bare number -- the grader should still
    # recover the one real number rather than fail to parse.
    assert extract_single_number("352,755,000,000 USD") == (352755000000.0, "ok")


def test_multiple_distinct_numbers_is_ambiguous_not_a_guess():
    val, status = extract_single_number("42 and 17")
    assert status == "ambiguous"
    assert val is None


def test_repeated_identical_number_is_not_ambiguous():
    # "$1,234 and 1,234.00 again" mentions the same value twice in different
    # formatting; that's not genuine ambiguity about which number was meant.
    val, status = extract_single_number("$1,234 and 1,234.00 again")
    assert status == "ok"
    assert val == 1234.0


def test_parenthesized_number_is_a_distinct_negative_value():
    # Accounting-style parentheses mean negative -- "(1234)" is a different
    # value from "1234", not a repeated mention of the same one.
    val, status = extract_single_number("1234 (1234)")
    assert status == "ambiguous"
    assert val is None


def test_empty_text_has_no_numbers():
    assert extract_single_number("") == (None, "empty")
    assert extract_single_number(None) == (None, "empty")


def test_extract_numbers_preserves_order():
    assert extract_numbers("first 1, then 2, then 3") == [1.0, 2.0, 3.0]


# ---------------------------------------------------------------------------
# get_model_answer -- parse source / fallback ladder
# ---------------------------------------------------------------------------

def test_clean_answer_line_is_answer_line_source():
    val, source = get_model_answer(row(extracted_answer="1000", raw_response="reasoning...\nANSWER: 1000"))
    assert (val, source) == (1000.0, "answer_line")


def test_ambiguous_answer_line_does_not_fall_back_to_raw_response():
    # Even though raw_response has a clean trailing number, an ambiguous
    # ANSWER: line must not be silently resolved by guessing from elsewhere.
    val, source = get_model_answer(row(
        extracted_answer="42 and 17",
        raw_response="...\nANSWER: 42 and 17",
    ))
    assert val is None
    assert source == "answer_line_ambiguous"


def test_no_answer_line_falls_back_to_last_line_of_response():
    val, source = get_model_answer(row(
        extracted_answer=None,
        raw_response="Some reasoning mentioning 2023 and 2024.\nFinal figure: 1000",
    ))
    assert (val, source) == (1000.0, "last_line_fallback")


def test_no_answer_line_and_ambiguous_last_line_refuses_to_guess():
    val, source = get_model_answer(row(
        extracted_answer=None,
        raw_response="The value is 1000 dollars.\nSo 1000 or maybe 1001, hard to say.",
    ))
    # The last line itself has two distinct numbers (1000, 1001) -- resolving
    # that by then grabbing an even-less-reliable full-text scan would just
    # be guessing with extra steps, so this stays unresolved rather than
    # picking one.
    assert (val, source) == (None, "last_line_ambiguous")


def test_no_answer_line_and_last_line_has_no_number_widens_to_full_text():
    val, source = get_model_answer(row(
        extracted_answer=None,
        raw_response="The value is 1000 dollars.\nSo that's the figure.",
    ))
    assert (val, source) == (1000.0, "full_text_fallback")


def test_completely_unparseable_response():
    val, source = get_model_answer(row(extracted_answer=None, raw_response="I cannot determine this."))
    assert (val, source) == (None, "unparseable")


def test_missing_raw_response_entirely():
    val, source = get_model_answer(row(extracted_answer=None, raw_response=None))
    assert (val, source) == (None, "unparseable")


# ---------------------------------------------------------------------------
# numeric_tolerance / is_correct -- units, signs, rounding, thresholds
# ---------------------------------------------------------------------------

def test_usd_relative_tolerance():
    tol = numeric_tolerance("USD", 1_000_000.0, 0.01)
    assert tol == pytest.approx(10_000.0)


def test_usd_floor_near_zero_gold():
    # 1% of $0.50 is $0.005 -- too tight to be meaningful; the $1 floor takes over.
    assert numeric_tolerance("USD", 0.5, 0.01) == 1.0


def test_ratio_tolerance_is_absolute_not_relative():
    # A relative reading of 1% * 0.88 would give ~0.0088; the intended
    # absolute reading (matches "2 decimals") gives exactly 0.01.
    assert numeric_tolerance("ratio", 0.8794, 0.01) == 0.01


def test_percent_relative_tolerance_away_from_zero():
    assert numeric_tolerance("percent", 41.8, 0.01) == pytest.approx(0.418)


def test_percent_floor_near_zero_growth():
    # A ~0.05% YoY growth value would otherwise demand near-exact precision
    # purely because the denominator is tiny -- the 0.1pt floor fixes that.
    assert numeric_tolerance("percent", 0.05, 0.01) == 0.1


def test_unsupported_gold_unit_fails_safe_not_silently_correct():
    with pytest.raises(ValueError):
        numeric_tolerance("bushels", 10.0, 0.01)


def test_percent_vs_decimal_confusion_is_graded_incorrect():
    # Gold is 41.82 (a percent, per the system prompt's "12.34 not 0.1234"
    # instruction); a model answering the fractional form must fail, not
    # slip through some accidental relative-tolerance coincidence.
    assert is_correct(0.4182, 41.82, "percent", 0.01) is False


def test_sign_flip_is_graded_incorrect():
    # The documented qwen failure mode: correct magnitude, wrong sign.
    assert is_correct(7_636_000_000.0, -7_636_000_000.0, "USD", 0.01) is False


def test_negative_delta_graded_correct_when_signs_match():
    assert is_correct(-7_636_000_000.0, -7_636_000_000.0, "USD", 0.01) is True


def test_rounding_within_tolerance_is_correct():
    # Gold computed at full precision; model reports a sensibly rounded value.
    assert is_correct(0.88, 0.8794, "ratio", 0.01) is True


def test_floating_point_noise_within_tolerance():
    assert is_correct(41.799999999999997, 41.8, "percent", 0.01) is True


def test_tolerance_boundary_inclusive():
    # Exactly at the tolerance boundary counts as correct (<=, not <).
    assert is_correct(101.0, 100.0, "USD", 0.01) is True  # tol = max(1.0, 1.0) = 1.0


def test_tolerance_boundary_just_outside():
    assert is_correct(101.02, 100.0, "USD", 0.01) is False


def test_missing_model_value_is_never_correct():
    assert is_correct(None, 1000.0, "USD", 0.01) is False


def test_missing_gold_value_is_never_correct():
    assert is_correct(1000.0, None, "USD", 0.01) is False


# ---------------------------------------------------------------------------
# grade_row -- end-to-end status classification
# ---------------------------------------------------------------------------

def test_correct_row():
    graded = grade_row(row(gold_value=1000.0, extracted_answer="1000"), None)
    assert graded["status"] == "correct"
    assert graded["correct"] is True


def test_incorrect_row():
    graded = grade_row(row(gold_value=1000.0, extracted_answer="2000"), None)
    assert graded["status"] == "incorrect"
    assert graded["correct"] is False


def test_ambiguous_answer_is_invalid_not_incorrect():
    graded = grade_row(row(extracted_answer="42 and 17", raw_response="ANSWER: 42 and 17"), None)
    assert graded["status"] == "invalid_response"
    assert graded["parse_source"] == "answer_line_ambiguous"


def test_api_error_row_is_invalid():
    graded = grade_row(row(error="HTTP 500", extracted_answer=None, raw_response=None), None)
    assert graded["status"] == "invalid_response"


def test_missing_gold_value_row_is_invalid_not_incorrect():
    # A benchmark data problem (no ground truth), not a model failure --
    # should not silently count against the model as "incorrect".
    graded = grade_row(row(gold_value=None), None)
    assert graded["status"] == "invalid_response"


def test_missing_gold_unit_row_is_invalid():
    graded = grade_row(row(gold_unit=None), None)
    assert graded["status"] == "invalid_response"


def test_unparseable_response_row_is_invalid():
    graded = grade_row(row(extracted_answer=None, raw_response="no numbers here"), None)
    assert graded["status"] == "invalid_response"


def test_epsilon_override_takes_precedence_over_row_tolerance():
    # Row tolerance of 0.01 would normally make a 5% error "incorrect"; a
    # generous --epsilon override should widen the window.
    r = row(gold_value=1000.0, tolerance=0.01, extracted_answer="1050")
    assert grade_row(r, None)["status"] == "incorrect"
    assert grade_row(r, 0.10)["status"] == "correct"


def test_row_without_tolerance_field_falls_back_to_default():
    # A responses file from before the `tolerance` field existed.
    r = row(gold_value=1000.0, extracted_answer="1005")
    del r["tolerance"]
    graded = grade_row(r, None)
    assert graded["status"] == "correct"  # within the 0.01 (1%) default
