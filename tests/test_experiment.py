"""Run-metadata tests (Commit 7): experiment.py's helpers, and
inference.run()'s directory/overwrite/resume behavior with the network call
mocked out (these tests make no real HTTP requests)."""

import json
import re

import pytest

from xbrlbench import experiment, inference
from xbrlbench.io_utils import load_jsonl
from xbrlbench.paths import DEFAULT_QUESTIONS_PATH


def fake_result(**overrides):
    base = {"raw_response": "reasoning...\nANSWER: 42", "latency_s": 0.01,
            "prompt_tokens": 10, "completion_tokens": 5, "error": None}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# experiment.py helpers
# ---------------------------------------------------------------------------

def test_generate_run_id_format():
    run_id = experiment.generate_run_id()
    assert re.fullmatch(r"\d{8}T\d{6}Z", run_id)


def test_git_commit_hash_is_a_full_hash_or_none():
    result = experiment.git_commit_hash()
    assert result is None or re.fullmatch(r"[0-9a-f]{40}", result)


def test_git_commit_hash_never_raises(monkeypatch):
    monkeypatch.setattr(experiment, "REPO_ROOT", "/definitely/not/a/real/path/xyz")
    assert experiment.git_commit_hash() is None


def test_build_metadata_shape():
    meta = experiment.build_metadata(
        run_id="20260101T000000Z", started_at="2026-01-01T00:00:00+00:00",
        models=["a/b"], temperature=0, max_tokens=None, prompt_version="v1",
        question_count=5, resume=False,
    )
    assert meta["run_id"] == "20260101T000000Z"
    assert meta["models"] == ["a/b"]
    assert meta["completed_at"] is None
    assert meta["response_count"] is None
    assert meta["resume"] is False


# ---------------------------------------------------------------------------
# inference.run() -- directory, metadata, overwrite/resume behavior
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fake_api_key_and_no_sleep(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    monkeypatch.setattr(inference.time, "sleep", lambda *_: None)


def test_run_creates_out_dir_with_responses_and_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(inference, "call_with_retry", lambda *a, **k: fake_result())
    out_dir = tmp_path / "run1"

    inference.run(DEFAULT_QUESTIONS_PATH, str(out_dir), models=["test/model"], limit=2)

    responses = load_jsonl(out_dir / "responses.jsonl")
    assert len(responses) == 2
    assert all(r["extracted_answer"] == "42" for r in responses)

    metadata = json.loads((out_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["models"] == ["test/model"]
    assert metadata["question_count"] == 2
    assert metadata["response_count"] == 2
    assert metadata["completed_at"] is not None
    assert metadata["resume"] is False
    assert metadata["prompt_version"] == inference.PROMPT_VERSION
    assert metadata["temperature"] == inference.TEMPERATURE


def test_run_without_out_dir_auto_generates_a_timestamped_run(monkeypatch, tmp_path):
    monkeypatch.setattr(inference, "call_with_retry", lambda *a, **k: fake_result())
    monkeypatch.setattr(inference, "RUNS_DIR", tmp_path / "runs")

    inference.run(DEFAULT_QUESTIONS_PATH, None, models=["test/model"], limit=1)

    created = list((tmp_path / "runs").iterdir())
    assert len(created) == 1
    assert re.fullmatch(r"\d{8}T\d{6}Z", created[0].name)
    assert (created[0] / "responses.jsonl").exists()
    assert (created[0] / "metadata.json").exists()


def test_run_refuses_to_overwrite_existing_run_without_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(inference, "call_with_retry", lambda *a, **k: fake_result())
    out_dir = tmp_path / "run1"
    inference.run(DEFAULT_QUESTIONS_PATH, str(out_dir), models=["test/model"], limit=1)

    with pytest.raises(SystemExit, match="already exists"):
        inference.run(DEFAULT_QUESTIONS_PATH, str(out_dir), models=["test/model"], limit=1)


def test_resume_without_out_dir_is_rejected(monkeypatch):
    monkeypatch.setattr(inference, "call_with_retry", lambda *a, **k: fake_result())
    with pytest.raises(SystemExit, match="--resume requires --out-dir"):
        inference.run(DEFAULT_QUESTIONS_PATH, None, models=["test/model"], limit=1, resume=True)


def test_resume_against_nonexistent_out_dir_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(inference, "call_with_retry", lambda *a, **k: fake_result())
    with pytest.raises(SystemExit, match="no existing responses file"):
        inference.run(DEFAULT_QUESTIONS_PATH, str(tmp_path / "nope"), models=["test/model"], limit=1, resume=True)


def test_resume_skips_already_done_pairs_and_extends_response_count(tmp_path, monkeypatch):
    monkeypatch.setattr(inference, "call_with_retry", lambda *a, **k: fake_result())
    out_dir = tmp_path / "run1"

    inference.run(DEFAULT_QUESTIONS_PATH, str(out_dir), models=["test/model"], limit=2)
    first_responses = load_jsonl(out_dir / "responses.jsonl")
    assert len(first_responses) == 2

    # Resume with a larger limit -- the first 2 (id, model) pairs should be
    # skipped, only the 3rd question's response newly appended.
    inference.run(DEFAULT_QUESTIONS_PATH, str(out_dir), models=["test/model"], limit=3, resume=True)

    responses = load_jsonl(out_dir / "responses.jsonl")
    assert len(responses) == 3
    assert len({r["id"] for r in responses}) == 3  # no duplicates re-appended

    metadata = json.loads((out_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["resume"] is True
    assert metadata["response_count"] == 3


def test_api_error_is_recorded_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(inference, "call_with_retry",
                         lambda *a, **k: fake_result(raw_response=None, error="HTTP 500: boom"))
    out_dir = tmp_path / "run1"

    inference.run(DEFAULT_QUESTIONS_PATH, str(out_dir), models=["test/model"], limit=1)

    responses = load_jsonl(out_dir / "responses.jsonl")
    assert responses[0]["error"] == "HTTP 500: boom"
    assert responses[0]["extracted_answer"] is None


def test_missing_api_key_raises_before_any_directory_is_created(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # Point load_dotenv at a nonexistent file so it can't pick up a real key
    # from a developer's .env during a local test run.
    monkeypatch.setattr(inference, "load_dotenv", lambda *a, **k: None)
    out_dir = tmp_path / "run1"

    with pytest.raises(SystemExit, match="OPENROUTER_API_KEY"):
        inference.run(DEFAULT_QUESTIONS_PATH, str(out_dir), models=["test/model"], limit=1)

    assert not out_dir.exists()
