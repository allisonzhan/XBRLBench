"""Tests for xbrlbench.io_utils's shared JSONL/.env helpers (Commit 9)."""

import os

from xbrlbench.io_utils import append_jsonl, load_dotenv, load_jsonl, write_jsonl


def test_write_then_load_round_trips(tmp_path):
    path = tmp_path / "rows.jsonl"
    rows = [{"id": "A", "n": 1}, {"id": "B", "n": 2}]
    write_jsonl(path, rows)
    assert load_jsonl(path) == rows


def test_write_jsonl_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "rows.jsonl"
    write_jsonl(path, [{"id": "A"}])
    assert path.exists()


def test_load_jsonl_skips_blank_lines(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text('{"id": "A"}\n\n   \n{"id": "B"}\n', encoding="utf-8")
    assert load_jsonl(path) == [{"id": "A"}, {"id": "B"}]


def test_load_jsonl_preserves_unicode(tmp_path):
    path = tmp_path / "rows.jsonl"
    write_jsonl(path, [{"text": "AAPL — total assets — café"}])
    rows = load_jsonl(path)
    assert rows[0]["text"] == "AAPL — total assets — café"


def test_append_jsonl_adds_to_existing_file(tmp_path):
    path = tmp_path / "rows.jsonl"
    write_jsonl(path, [{"id": "A"}])
    append_jsonl(path, {"id": "B"})
    assert load_jsonl(path) == [{"id": "A"}, {"id": "B"}]


def test_append_jsonl_creates_file_and_parents_if_missing(tmp_path):
    path = tmp_path / "nested" / "rows.jsonl"
    append_jsonl(path, {"id": "A"})
    assert load_jsonl(path) == [{"id": "A"}]


def test_write_jsonl_overwrites_existing_content(tmp_path):
    path = tmp_path / "rows.jsonl"
    write_jsonl(path, [{"id": "A"}, {"id": "B"}])
    write_jsonl(path, [{"id": "C"}])
    assert load_jsonl(path) == [{"id": "C"}]


# ---------------------------------------------------------------------------
# load_dotenv
# ---------------------------------------------------------------------------

def test_load_dotenv_sets_unset_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("XBRLBENCH_TEST_VAR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("XBRLBENCH_TEST_VAR=hello\n", encoding="utf-8")

    load_dotenv(env_file)

    assert os.environ["XBRLBENCH_TEST_VAR"] == "hello"
    del os.environ["XBRLBENCH_TEST_VAR"]


def test_load_dotenv_never_overrides_an_existing_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRLBENCH_TEST_VAR", "from_shell")
    env_file = tmp_path / ".env"
    env_file.write_text("XBRLBENCH_TEST_VAR=from_file\n", encoding="utf-8")

    load_dotenv(env_file)

    assert os.environ["XBRLBENCH_TEST_VAR"] == "from_shell"


def test_load_dotenv_missing_file_is_a_silent_noop(tmp_path):
    load_dotenv(tmp_path / "does_not_exist.env")  # must not raise


def test_load_dotenv_skips_comments_and_blank_lines(tmp_path, monkeypatch):
    monkeypatch.delenv("XBRLBENCH_TEST_VAR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("# a comment\n\nXBRLBENCH_TEST_VAR=hello\n", encoding="utf-8")

    load_dotenv(env_file)

    assert os.environ["XBRLBENCH_TEST_VAR"] == "hello"
    del os.environ["XBRLBENCH_TEST_VAR"]


def test_load_dotenv_strips_quotes(tmp_path, monkeypatch):
    monkeypatch.delenv("XBRLBENCH_TEST_VAR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('XBRLBENCH_TEST_VAR="quoted value"\n', encoding="utf-8")

    load_dotenv(env_file)

    assert os.environ["XBRLBENCH_TEST_VAR"] == "quoted value"
    del os.environ["XBRLBENCH_TEST_VAR"]
