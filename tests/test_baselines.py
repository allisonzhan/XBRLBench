"""Milestone 3 tooling: held-out sampling, run options (local servers,
parallel calls, spend cap, retry of failed calls), and the report's
confidence intervals and real-filing breakdowns. No network calls."""

import json

import pytest

from xbrlbench import heldout, inference
from xbrlbench.grading import latest_rows, summarize
from xbrlbench.io_utils import load_jsonl, write_jsonl
from xbrlbench.reporting import (
    bootstrap_ci,
    build_report,
    paired_difference,
    sic_division,
    size_bucket,
    to_markdown,
)


def corpus_row(i, cik, family, split="test", **extra):
    row = {"id": f"C{cik}-{i}-{family}", "ticker": f"T{cik}", "cik": str(cik), "family": family,
           "split": split, "form": "10-Q", "sic": "3571", "tier": "T1", "reasoning_type": "direct_retrieval",
           "question": "q?", "context": "ctx", "gold_value": 1000.0, "gold_unit": "USD", "tolerance": 0.01}
    row.update(extra)
    return row


def corpus():
    rows = []
    for cik in range(1, 11):
        for i in range(3):
            rows.append(corpus_row(i, cik, "lookup_assets", gold_value=float(cik) * 1e9, period_end=f"2026-0{i + 1}-30"))
            rows.append(corpus_row(i, cik, "current_ratio", tier="T2", reasoning_type="ratio", gold_unit="ratio"))
    rows.append(corpus_row(0, 99, "lookup_assets", split="train"))
    return rows


# ---------------------------------------------------------------------------
# heldout.sample
# ---------------------------------------------------------------------------

def test_quotas_are_proportional_and_sum_to_n():
    q = heldout.quotas({"a": 50, "b": 30, "c": 20}, 10)
    assert q == {"a": 5, "b": 3, "c": 2}
    assert sum(heldout.quotas({"a": 7, "b": 7, "c": 7}, 10).values()) == 10
    assert heldout.quotas({"a": 2, "b": 1}, 50) == {"a": 2, "b": 1}


def test_sample_is_deterministic_test_only_and_spread_over_companies():
    rows = corpus()
    s1, s2 = heldout.sample(rows, 20), heldout.sample(list(reversed(rows)), 20)
    assert [r["id"] for r in s1] == [r["id"] for r in s2]
    assert len(s1) == 20
    assert all(r["split"] == "test" for r in s1)
    by_family = {f: [r for r in s1 if r["family"] == f] for f in ("lookup_assets", "current_ratio")}
    assert len(by_family["lookup_assets"]) == len(by_family["current_ratio"]) == 10
    # Round-robin over 10 companies: one question each before any company repeats.
    assert len({r["cik"] for r in by_family["lookup_assets"]}) == 10


def test_sample_cli_writes_questions_and_ids(tmp_path):
    src, out = tmp_path / "q.jsonl", tmp_path / "held.jsonl"
    write_jsonl(src, corpus())
    heldout.main(["--in", str(src), "--out", str(out), "--n", "6"])
    rows = load_jsonl(out)
    assert len(rows) == 6
    assert (tmp_path / "held.jsonl.ids.txt").read_text().split() == [r["id"] for r in rows]


# ---------------------------------------------------------------------------
# inference.run options (network mocked)
# ---------------------------------------------------------------------------

@pytest.fixture
def questions_file(tmp_path):
    path = tmp_path / "held.jsonl"
    write_jsonl(path, corpus())
    return path


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    monkeypatch.setattr(inference, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(inference.time, "sleep", lambda *_: None)


def fake(cost=0.01, **over):
    base = {"raw_response": "work\nANSWER: 1000", "latency_s": 0.1, "prompt_tokens": 10,
            "completion_tokens": 5, "cost": cost, "error": None}
    base.update(over)
    return base


def test_payload_reasoning_options():
    p = inference.build_payload("m", "q", "c", reasoning="off")
    assert p["reasoning"] == {"enabled": False} and p["usage"] == {"include": True}
    assert inference.build_payload("m", "q", "c", reasoning="high")["reasoning"] == {"effort": "high"}
    assert "reasoning" not in inference.build_payload("m", "q", "c")
    local = inference.build_payload("m", "q", "c", api_base="http://localhost:8000/v1",
                                    max_tokens=2048, reasoning="off")
    assert "usage" not in local and "reasoning" not in local
    assert local["chat_template_kwargs"] == {"enable_thinking": False}
    assert local["max_tokens"] == 2048


def test_split_filter_workers_and_carried_fields(tmp_path, monkeypatch, questions_file):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(inference, "call_with_retry", lambda *a, **k: fake())
    out = tmp_path / "run"
    inference.run(questions_file, str(out), models=["a/m", "b/m"], split="test", workers=4)
    rows = load_jsonl(out / "responses.jsonl")
    assert len(rows) == 60 * 2
    assert {r["family"] for r in rows} == {"lookup_assets", "current_ratio"}
    assert all(r["cik"] and r["form"] == "10-Q" and r["cost"] == 0.01 for r in rows)
    meta = json.loads((out / "metadata.json").read_text())
    assert meta["split"] == "test" and meta["prompt_version"] == inference.PROMPT_VERSION
    assert meta["cost_usd_this_session"] == pytest.approx(1.2)


def test_local_server_needs_no_openrouter_key(tmp_path, monkeypatch, questions_file):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    seen = {}

    def call(api_key, model, q, c, **opts):
        seen.update(opts, key=api_key)
        return fake(cost=None)

    monkeypatch.setattr(inference, "call_with_retry", call)
    inference.run(questions_file, str(tmp_path / "r"), models=["Qwen/Qwen3-4B"], limit=1,
                  api_base="http://localhost:8000/v1", max_tokens=4096)
    assert seen["api_base"] == "http://localhost:8000/v1" and seen["max_tokens"] == 4096
    assert seen["key"] is None


def test_max_cost_stops_new_calls(tmp_path, monkeypatch, questions_file):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(inference, "call_with_retry", lambda *a, **k: fake(cost=1.0))
    out = tmp_path / "run"
    inference.run(questions_file, str(out), models=["a/m"], split="test", max_cost=3.0)
    assert len(load_jsonl(out / "responses.jsonl")) == 3
    assert json.loads((out / "metadata.json").read_text())["completed_at"] is None


def test_resume_retries_failed_calls_and_grading_keeps_the_retry(tmp_path, monkeypatch, questions_file):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    out = tmp_path / "run"
    monkeypatch.setattr(inference, "call_with_retry", lambda *a, **k: fake(raw_response=None, error="HTTP 500"))
    inference.run(questions_file, str(out), models=["a/m"], limit=2)
    monkeypatch.setattr(inference, "call_with_retry", lambda *a, **k: fake())
    inference.run(questions_file, str(out), models=["a/m"], limit=2, resume=True)
    rows = load_jsonl(out / "responses.jsonl")
    assert len(rows) == 4
    graded, *_, overall = summarize(rows)
    assert overall["n"] == 2 and overall["invalid"] == 0


def test_call_with_retry_handles_empty_content(monkeypatch):
    monkeypatch.setattr(inference, "call_openrouter", lambda *a, **k: {
        "choices": [{"message": {"content": None}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 9, "cost": 0.002,
                  "completion_tokens_details": {"reasoning_tokens": 9}}})
    r = inference.call_with_retry("k", "m", "q", "c")
    assert r["raw_response"] == "" and r["error"] is None
    assert r["finish_reason"] == "length" and r["reasoning_tokens"] == 9 and r["cost"] == 0.002


def test_call_with_retry_retries_200_error_bodies(monkeypatch):
    calls = iter([{"error": {"message": "upstream down"}},
                  {"choices": [{"message": {"content": "ANSWER: 1"}}], "usage": {}}])
    monkeypatch.setattr(inference, "call_openrouter", lambda *a, **k: next(calls))
    assert inference.call_with_retry("k", "m", "q", "c")["raw_response"] == "ANSWER: 1"


def test_latest_rows_keeps_last_per_id_model_and_all_idless_rows():
    rows = [{"id": "a", "model": "m", "v": 1}, {"id": "a", "model": "m", "v": 2},
            {"id": None, "model": "m"}, {"id": None, "model": "m"}]
    out = latest_rows(rows)
    assert len(out) == 3 and out[0]["v"] == 2


# ---------------------------------------------------------------------------
# Report: CIs and breakdowns
# ---------------------------------------------------------------------------

def graded(cik, correct, model="m", qid=None):
    return {"id": qid or f"{cik}-{correct}", "cik": str(cik), "model": model,
            "status": "correct" if correct else "incorrect"}


def test_bootstrap_ci_brackets_accuracy_and_is_deterministic():
    rows = [graded(c, i % 2 == 0, qid=f"{c}-{i}") for c in range(20) for i in range(5)]
    ci = bootstrap_ci(rows)
    assert ci == bootstrap_ci(rows)
    acc = sum(r["status"] == "correct" for r in rows) / len(rows)
    assert ci[0] <= acc <= ci[1]
    assert bootstrap_ci([graded(1, True)] * 3) == (1.0, 1.0)


def test_paired_difference_on_shared_questions():
    a = [graded(c, True, "a", f"q{c}") for c in range(10)]
    b = [graded(c, c < 5, "b", f"q{c}") for c in range(10)] + [graded(99, True, "b", "extra")]
    d = paired_difference(b, a)
    assert d["n"] == 10 and d["difference"] == pytest.approx(-0.5)
    assert d["ci95"][0] <= -0.5 <= d["ci95"][1]


def test_sector_and_size_labels():
    assert sic_division("3571") == "Manufacturing"
    assert sic_division("6022") == "Finance"
    assert sic_division("") == "unknown"
    assert size_bucket(5e8).startswith("small") and size_bucket(5e9).startswith("mid")
    assert size_bucket(5e10).startswith("large") and size_bucket(None) == "unknown"


def test_report_has_ci_table_and_breakdowns(tmp_path, questions_file):
    qs = load_jsonl(questions_file)
    rows = []
    for q in qs:
        if q["split"] != "test":
            continue
        for model, answer in (("big/m", "1000"), ("small/m", "1000" if q["family"] == "lookup_assets" else "7")):
            gold = q["gold_value"]
            rows.append({"id": q["id"], "model": model, "tier": q["tier"], "gold_value": gold,
                         "gold_unit": q["gold_unit"], "tolerance": 0.01, "error": None,
                         "extracted_answer": str(gold) if answer == "1000" else answer,
                         "raw_response": "x", "cost": 0.5})
    rep = build_report(rows, questions_file)
    assert rep["models"]["big/m"]["accuracy"] == 1.0
    assert rep["models"]["small/m"]["accuracy"] == 0.5
    assert rep["models"]["small/m"]["vs_best"]["difference"] == pytest.approx(-0.5)
    assert rep["models"]["big/m"]["cost_usd"] == 30.0
    assert rep["breakdowns"]["family"]["current_ratio|small/m"]["accuracy"] == 0.0
    assert set(k.split("|")[0] for k in rep["breakdowns"]["sector"]) == {"Manufacturing"}
    sizes = {k.split("|")[0] for k in rep["breakdowns"]["size"]}
    assert sizes == {"mid ($1-10B)", "large (>$10B)"}
    md = to_markdown(rep)
    assert "95% confidence intervals" in md and "## Accuracy by task family" in md
