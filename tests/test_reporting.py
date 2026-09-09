"""Reporting tests (Commit 5)."""

import json

from xbrlbench.paths import DEFAULT_QUESTIONS_PATH, RESULTS_DIR
from xbrlbench.reporting import build_report, report


def response_row(**overrides):
    base = {
        "id": "AAPL-2023-T1-0",
        "ticker": "AAPL",
        "fiscal_year": 2023,
        "tier": "T1",
        "model": "model/a",
        "question": "q",
        "gold_value": 1000.0,
        "gold_unit": "USD",
        "reasoning_type": "direct_retrieval",
        "tolerance": 0.01,
        "extracted_answer": "1000",
        "raw_response": "ANSWER: 1000",
        "error": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Synthetic data -- exercise every breakdown deterministically
# ---------------------------------------------------------------------------

def synthetic_rows():
    rows = []
    # model/a: perfect on direct_retrieval (T1), perfect on ratio (T2), 0/1 on
    # multi_period_comparison (T3) -- an easy-to-hard drop and a single-vs-
    # multi-step drop, both attributable to the one T3 miss.
    rows.append(response_row(id="Q1", model="model/a", tier="T1", reasoning_type="direct_retrieval",
                              gold_value=1000.0, extracted_answer="1000"))
    rows.append(response_row(id="Q2", model="model/a", tier="T2", reasoning_type="ratio",
                              gold_value=0.5, gold_unit="ratio", extracted_answer="0.5"))
    rows.append(response_row(id="Q3", model="model/a", tier="T3", reasoning_type="multi_period_comparison",
                              gold_value=500.0, extracted_answer="999"))  # wrong

    # model/b: perfect everywhere.
    rows.append(response_row(id="Q1", model="model/b", tier="T1", reasoning_type="direct_retrieval",
                              gold_value=1000.0, extracted_answer="1000"))
    rows.append(response_row(id="Q2", model="model/b", tier="T2", reasoning_type="ratio",
                              gold_value=0.5, gold_unit="ratio", extracted_answer="0.5"))
    rows.append(response_row(id="Q3", model="model/b", tier="T3", reasoning_type="multi_period_comparison",
                              gold_value=500.0, extracted_answer="500"))

    # An invalid response (API error) -- must not count as incorrect.
    rows.append(response_row(id="Q4", model="model/a", tier="T1", reasoning_type="direct_retrieval",
                              gold_value=42.0, extracted_answer=None, raw_response=None, error="HTTP 500"))
    return rows


def test_overall_and_by_model():
    rep = build_report(synthetic_rows())
    assert rep["response_count"] == 7
    assert rep["overall"]["invalid"] == 1
    assert rep["by_model"]["model/a"]["n"] == 4
    assert rep["by_model"]["model/a"]["correct"] == 2  # Q1, Q2 correct; Q3 wrong; Q4 invalid
    assert rep["by_model"]["model/b"]["correct"] == 3


def test_by_difficulty_and_reasoning_type():
    rep = build_report(synthetic_rows())
    assert rep["by_difficulty"]["T3"]["n"] == 2
    assert rep["by_difficulty"]["T3"]["correct"] == 1
    assert rep["by_reasoning_type"]["multi_period_comparison"]["correct"] == 1


def test_incorrect_and_invalid_ids():
    rep = build_report(synthetic_rows())
    assert rep["incorrect_question_ids"] == ["Q3"]
    assert rep["invalid_question_ids"] == ["Q4"]


def test_per_question_grid():
    rep = build_report(synthetic_rows())
    assert rep["per_question"]["Q3"] == {"model/a": "incorrect", "model/b": "correct"}


def test_easy_to_hard_drop():
    rep = build_report(synthetic_rows())
    drop_a = rep["easy_to_hard_drop"]["model/a"]
    # T1 for model/a is Q1 (correct) and Q4 (invalid) -- an invalid response
    # counts against accuracy just like an incorrect one (same convention as
    # xbrlbench.grading's overall accuracy: correct / n, n includes invalid).
    assert drop_a["T1_accuracy"] == 0.5
    assert drop_a["T3_accuracy"] == 0.0
    assert drop_a["drop"] == 0.5

    drop_b = rep["easy_to_hard_drop"]["model/b"]
    assert drop_b["drop"] == 0.0


def test_single_vs_multi_step():
    rep = build_report(synthetic_rows())
    step_a = rep["single_vs_multi_step"]["model/a"]
    # direct_retrieval (single-step) for model/a is Q1 (correct) and Q4
    # (invalid) -> 1/2, same accuracy convention as above.
    assert step_a["single_step_accuracy"] == 0.5
    assert step_a["multi_step_accuracy"] == 0.5    # Q2 correct, Q3 wrong


def test_strongest_weakest_reasoning_type():
    rep = build_report(synthetic_rows())
    sw_a = rep["strongest_weakest_reasoning_type"]["model/a"]
    assert sw_a["weakest"]["reasoning_type"] == "multi_period_comparison"
    assert sw_a["weakest"]["accuracy"] == 0.0

    sw_b = rep["strongest_weakest_reasoning_type"]["model/b"]
    # model/b is perfect everywhere -- strongest and weakest both at 1.0.
    assert sw_b["strongest"]["accuracy"] == 1.0
    assert sw_b["weakest"]["accuracy"] == 1.0


def test_question_bank_size_and_evaluated_count():
    rep = build_report(synthetic_rows())
    assert rep["questions_evaluated"] == 4  # Q1..Q4
    assert rep["question_bank_size"] == 68  # the real data/questions.jsonl


# ---------------------------------------------------------------------------
# reasoning_type backfill for responses predating the field
# ---------------------------------------------------------------------------

def test_reasoning_type_backfilled_from_question_bank_when_missing():
    # A response row shaped like the pre-hardening baseline file: no
    # reasoning_type of its own.
    row = response_row(id="AAPL-2023-T1-0", model="model/a", tier="T1",
                        gold_value=352755000000.0, extracted_answer="352755000000")
    del row["reasoning_type"]
    rep = build_report([row], questions_path=DEFAULT_QUESTIONS_PATH)
    # AAPL-2023-T1-0 is the "total assets" question -> direct_retrieval in the real bank.
    assert rep["by_reasoning_type"].get("direct_retrieval", {}).get("n") == 1
    assert None not in rep["by_reasoning_type"]


# ---------------------------------------------------------------------------
# End-to-end against the real baseline file
# ---------------------------------------------------------------------------

def test_report_runs_end_to_end_on_baseline_responses(tmp_path):
    responses_path = RESULTS_DIR / "baseline" / "responses.jsonl"
    out_prefix = tmp_path / "report"

    result = report(responses_path, DEFAULT_QUESTIONS_PATH, out_prefix)

    assert result["response_count"] == 272
    assert result["questions_evaluated"] == 68
    # The reasoning-type breakdown must be meaningful (backfilled), not one
    # big None bucket, even though the baseline file predates the field.
    assert None not in result["by_reasoning_type"]
    assert set(result["by_reasoning_type"].keys()) == {
        "direct_retrieval", "ratio", "percentage_change", "multi_period_comparison",
    }

    for suffix in ("_analysis.json", "_analysis.csv", ".md"):
        path = tmp_path / f"report{suffix}"
        assert path.exists()

    written = json.loads((tmp_path / "report_analysis.json").read_text(encoding="utf-8"))
    assert written["overall"]["n"] == 272
