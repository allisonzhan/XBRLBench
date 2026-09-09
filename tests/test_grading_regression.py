"""End-to-end smoke test for xbrlbench.grading against the real baseline
responses file.

This intentionally does NOT assert specific accuracy numbers: Commit 3
(grading hardening) changes what "correct" means in a few edge cases
(unit-aware tolerance, stricter ambiguous-answer handling), so the baseline's
numbers are expected to shift. Recomputing and committing the new baseline
numbers is its own dedicated commit (`data: recompute baseline results with
hardened evaluator`) so that empirical result is reviewable independently of
this grading-logic change. This test just proves grade() still runs
end-to-end against real data and produces a well-formed, internally
consistent summary.
"""

from xbrlbench.grading import grade
from xbrlbench.paths import RESULTS_DIR


def test_grading_runs_end_to_end_on_baseline_responses(tmp_path):
    responses_path = RESULTS_DIR / "baseline" / "responses.jsonl"
    out_prefix = tmp_path / "report"

    graded, tier_table, model_table, grid, overall = grade(responses_path, out_prefix)

    assert overall["n"] == 272
    assert overall["correct"] + overall["invalid"] <= overall["n"]
    assert 0.0 <= overall["accuracy"] <= 1.0

    # Every graded row has a well-formed status.
    assert {r["status"] for r in graded} <= {"correct", "incorrect", "invalid_response"}

    # Per-tier and per-model counts sum back up to the overall total.
    assert sum(cell["n"] for cell in tier_table.values()) == overall["n"]
    assert sum(cell["n"] for cell in model_table.values()) == overall["n"]

    for path_suffix in ("_summary.csv", "_summary.json", "_incorrect.jsonl"):
        assert (tmp_path / f"report{path_suffix}").exists()
