"""Error-analysis tests (Commit 6)."""

from xbrlbench.errors import build_error_records, errors
from xbrlbench.paths import DEFAULT_QUESTIONS_PATH, RESULTS_DIR


def response_row(**overrides):
    base = {
        "id": "AAPL-2023-T1-0",
        "ticker": "AAPL",
        "fiscal_year": 2023,
        "tier": "T1",
        "model": "model/a",
        "question": "What were AAPL's total assets for fiscal year 2023? Answer in USD.",
        "gold_value": 1000.0,
        "gold_unit": "USD",
        "source_concept": "Assets",
        "reasoning_type": "direct_retrieval",
        "tolerance": 0.01,
        "extracted_answer": "1000",
        "raw_response": "ANSWER: 1000",
        "error": None,
    }
    base.update(overrides)
    return base


def test_correct_responses_are_excluded():
    rows = [response_row(id="Q1", extracted_answer="1000")]  # correct
    records = build_error_records(rows)
    assert records == []


def test_incorrect_response_record_shape():
    rows = [response_row(id="Q1", gold_value=1000.0, extracted_answer="1100")]
    records = build_error_records(rows)
    assert len(records) == 1
    r = records[0]
    assert r["id"] == "Q1"
    assert r["status"] == "incorrect"
    assert r["parsed_model_answer"] == 1100.0
    assert r["gold_value"] == 1000.0
    assert r["reasoning_type"] == "direct_retrieval"
    assert r["source_concept"] == "Assets"


def test_absolute_and_relative_error_computed():
    rows = [response_row(id="Q1", gold_value=1000.0, extracted_answer="1100")]
    r = build_error_records(rows)[0]
    assert r["absolute_error"] == 100.0
    assert r["relative_error"] == 0.1


def test_error_magnitudes_none_when_unparseable():
    rows = [response_row(id="Q1", extracted_answer=None, raw_response="I cannot determine this.")]
    r = build_error_records(rows)[0]
    assert r["status"] == "invalid_response"
    assert r["parsed_model_answer"] is None
    assert r["absolute_error"] is None
    assert r["relative_error"] is None


def test_relative_error_none_when_gold_is_zero():
    rows = [response_row(id="Q1", gold_value=0.0, extracted_answer="5", tolerance=0.01)]
    r = build_error_records(rows)[0]
    assert r["absolute_error"] == 5.0
    assert r["relative_error"] is None


def test_api_error_row_included_with_no_numeric_answer():
    rows = [response_row(id="Q1", error="HTTP 500", extracted_answer=None, raw_response=None)]
    r = build_error_records(rows)[0]
    assert r["status"] == "invalid_response"
    assert r["api_error"] == "HTTP 500"


def test_records_sorted_by_reasoning_type_then_tier_then_id():
    rows = [
        response_row(id="B", tier="T3", reasoning_type="multi_period_comparison", extracted_answer="2000"),
        response_row(id="A", tier="T1", reasoning_type="direct_retrieval", extracted_answer="2000"),
    ]
    records = build_error_records(rows)
    assert [r["id"] for r in records] == ["A", "B"]  # direct_retrieval sorts before multi_period_comparison


def test_reasoning_type_and_source_concept_backfilled_from_question_bank():
    row = response_row(id="AAPL-2023-T1-0", gold_value=352755000000.0, extracted_answer="999")
    del row["reasoning_type"]
    del row["source_concept"]
    records = build_error_records([row], questions_path=DEFAULT_QUESTIONS_PATH)
    r = records[0]
    assert r["reasoning_type"] == "direct_retrieval"
    assert r["source_concept"] == "Assets"


def test_errors_end_to_end_on_baseline_responses(tmp_path):
    responses_path = RESULTS_DIR / "baseline" / "responses.jsonl"
    out_prefix = tmp_path / "errors"

    records = errors(responses_path, DEFAULT_QUESTIONS_PATH, out_prefix)

    # Matches report_summary.json: 272 - 261 correct = 11 non-correct.
    assert len(records) == 11
    assert all(r["status"] != "correct" for r in records)
    assert all(r["reasoning_type"] is not None for r in records)  # backfilled, not left None

    for suffix in (".jsonl", ".csv"):
        path = tmp_path / f"errors{suffix}"
        assert path.exists()

    csv_text = (tmp_path / "errors.csv").read_text(encoding="utf-8")
    assert "raw_response" not in csv_text.splitlines()[0]  # omitted from the CSV header
