"""Behavior-preservation check for Commit 1: xbrlbench.grading is a straight
port of the old grade.py with no algorithm changes, so re-grading the
existing baseline responses must reproduce the committed
results/baseline/report_summary.json exactly.
"""

import json

from xbrlbench.grading import grade
from xbrlbench.paths import RESULTS_DIR


def test_regrading_baseline_reproduces_committed_summary(tmp_path):
    baseline_dir = RESULTS_DIR / "baseline"
    responses_path = baseline_dir / "responses.jsonl"
    committed_summary_path = baseline_dir / "report_summary.json"

    out_prefix = tmp_path / "report"
    grade(responses_path, out_prefix, epsilon=0.01)

    regenerated = json.loads((tmp_path / "report_summary.json").read_text(encoding="utf-8"))
    committed = json.loads(committed_summary_path.read_text(encoding="utf-8"))

    assert regenerated == committed
