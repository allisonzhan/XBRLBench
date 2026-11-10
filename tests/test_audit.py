"""Tests for xbrlbench.audit on questions generated from the realistic fixture."""

import copy
import json
from pathlib import Path

import pytest

from xbrlbench import audit, filings, ixbrl

FIXTURE = Path(__file__).parent / "fixtures" / "realistic_10k.htm"


@pytest.fixture(scope="module")
def questions():
    doc = ixbrl.parse_ixbrl(FIXTURE.read_bytes())
    qs, _ = filings.build_filing_questions(doc, filings.FilingMeta(cik="0009999999", accession="a"))
    return qs


def test_every_generated_question_passes(questions):
    assert len(questions) == 11
    assert {q["id"]: audit.audit_question(q) for q in questions} == {q["id"]: [] for q in questions}


def test_inputs_are_recorded_in_formula_order(questions):
    q = next(q for q in questions if q["family"] == "current_ratio")
    assert [i["label"] for i in q["inputs"]] == ["Total current assets", "Total current liabilities"]
    assert [i["displayed"] for i in q["inputs"]] == ["534,000", "180,000"]


def test_scale_error_is_caught(questions):
    q = copy.deepcopy(next(q for q in questions if q["family"] == "lookup_assets"))
    q["context"] = q["context"].replace("(In thousands", "(In millions")
    assert any("x 1e6" in p for p in audit.audit_question(q))


def test_wrong_gold_is_caught(questions):
    q = copy.deepcopy(next(q for q in questions if q["family"] == "free_cash_flow"))
    q["gold_value"] += 1_000_000
    assert any("formula gives" in p for p in audit.audit_question(q))


def test_value_missing_from_row_is_caught(questions):
    q = copy.deepcopy(next(q for q in questions if q["family"] == "lookup_revenue"))
    q["context"] = q["context"].replace("$1,284,550", "$1,284,551")
    assert any("not shown in row" in p for p in audit.audit_question(q))


def test_audit_file_and_sample(tmp_path, questions):
    path = tmp_path / "q.jsonl"
    path.write_text("".join(json.dumps(q) + "\n" for q in questions), encoding="utf-8")
    total, failures = audit.audit(str(path), n=5, seed=1)
    assert total == 5 and failures == []
