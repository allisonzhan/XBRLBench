"""Tests for xbrlbench.grading's results aggregation (Commit 9):
summarize()'s bucketing/grid construction and write_outputs()'s file
contents. grade_row() itself (the per-row correctness logic) is covered
exhaustively in tests/test_grading.py -- this file is about what happens
once many graded rows get bucketed and written out.
"""

import csv
import json

from xbrlbench.grading import summarize, write_outputs


def row(**overrides):
    base = {
        "id": "Q1", "tier": "T1", "model": "model/a", "gold_value": 1000.0,
        "gold_unit": "USD", "tolerance": 0.01, "extracted_answer": "1000",
        "raw_response": "ANSWER: 1000", "error": None,
    }
    base.update(overrides)
    return base


def fixture_rows():
    return [
        # model/a: 1 correct, 1 incorrect (T1); 1 correct (T2)
        row(id="Q1", model="model/a", tier="T1", extracted_answer="1000"),
        row(id="Q2", model="model/a", tier="T1", gold_value=2000.0, extracted_answer="9999"),
        row(id="Q3", model="model/a", tier="T2", gold_value=500.0, extracted_answer="500"),
        # model/b: 1 correct (T1), 1 invalid/unparseable (T2)
        row(id="Q1", model="model/b", tier="T1", extracted_answer="1000"),
        row(id="Q3", model="model/b", tier="T2", gold_value=500.0, extracted_answer=None, raw_response="no numbers"),
    ]


def test_overall_counts():
    _graded, _tier, _model, _grid, overall = summarize(fixture_rows())
    assert overall["n"] == 5
    assert overall["correct"] == 3
    assert overall["invalid"] == 1
    assert overall["accuracy"] == 3 / 5


def test_tier_table_bucketing():
    _graded, tier_table, _model, _grid, _overall = summarize(fixture_rows())
    assert tier_table["T1"] == {"n": 3, "correct": 2, "invalid": 0}
    assert tier_table["T2"] == {"n": 2, "correct": 1, "invalid": 1}


def test_model_table_bucketing():
    _graded, _tier, model_table, _grid, _overall = summarize(fixture_rows())
    assert model_table["model/a"] == {"n": 3, "correct": 2, "invalid": 0}
    assert model_table["model/b"] == {"n": 2, "correct": 1, "invalid": 1}


def test_tier_model_grid():
    _graded, _tier, _model, grid, _overall = summarize(fixture_rows())
    assert grid[("T1", "model/a")] == {"n": 2, "correct": 1, "invalid": 0}
    assert grid[("T2", "model/a")] == {"n": 1, "correct": 1, "invalid": 0}
    assert grid[("T1", "model/b")] == {"n": 1, "correct": 1, "invalid": 0}
    assert grid[("T2", "model/b")] == {"n": 1, "correct": 0, "invalid": 1}


def test_empty_input_produces_zeroed_overall():
    _graded, tier_table, model_table, grid, overall = summarize([])
    assert overall == {"n": 0, "correct": 0, "invalid": 0, "accuracy": 0.0}
    assert tier_table == {}
    assert model_table == {}
    assert grid == {}


def test_epsilon_override_applied_uniformly_across_all_rows():
    # Q2 is off by 4999/2000 = 250% -- way outside any sane tolerance, so a
    # huge epsilon override should still leave it graded correct.
    _graded, _tier, _model, _grid, overall = summarize(fixture_rows(), epsilon=5.0)
    assert overall["correct"] == 4  # everything except the genuinely unparseable Q3/model_b row


# ---------------------------------------------------------------------------
# write_outputs -- CSV/JSON file contents
# ---------------------------------------------------------------------------

def test_write_outputs_json_structure(tmp_path):
    graded, tier_table, model_table, grid, overall = summarize(fixture_rows())
    out_prefix = tmp_path / "report"
    write_outputs(graded, tier_table, model_table, grid, overall, out_prefix, epsilon=None)

    data = json.loads((tmp_path / "report_summary.json").read_text(encoding="utf-8"))
    assert data["epsilon"] is None
    assert data["overall"]["n"] == 5
    assert data["by_tier"]["T1"]["correct"] == 2
    assert data["by_model"]["model/a"]["n"] == 3
    assert data["by_tier_and_model"]["T1|model/a"]["correct"] == 1


def test_write_outputs_csv_has_expected_rows_and_totals_row(tmp_path):
    graded, tier_table, model_table, grid, overall = summarize(fixture_rows())
    out_prefix = tmp_path / "report"
    write_outputs(graded, tier_table, model_table, grid, overall, out_prefix, epsilon=None)

    with open(tmp_path / "report_summary.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert {"tier", "model", "n", "correct", "invalid", "accuracy"} == set(rows[0].keys())
    grand_total = next(r for r in rows if r["tier"] == "ALL_TIERS" and r["model"] == "ALL_MODELS")
    assert grand_total["n"] == "5"
    assert grand_total["correct"] == "3"


def test_write_outputs_incorrect_file_includes_incorrect_and_invalid_not_correct(tmp_path):
    graded, tier_table, model_table, grid, overall = summarize(fixture_rows())
    out_prefix = tmp_path / "report"
    write_outputs(graded, tier_table, model_table, grid, overall, out_prefix, epsilon=None)

    with open(tmp_path / "report_incorrect.jsonl", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]

    assert len(rows) == 2  # Q2/model_a (incorrect) + Q3/model_b (invalid)
    statuses = {r["status"] for r in rows}
    assert statuses == {"incorrect", "invalid_response"}
    ids = {(r["id"], r["model"]) for r in rows}
    assert ids == {("Q2", "model/a"), ("Q3", "model/b")}
