"""RL reward and export (milestone 4). The reward must be impossible to
game by hedging, must give nothing for scale errors, and must never pay out
on an answer that xbrlbench.grading would mark wrong."""

import json

import pytest

from xbrlbench import grading, rl
from xbrlbench.inference import SYSTEM_PROMPT


def s(text, gold=1_234_000.0, unit="USD", tol=0.01):
    return rl.score(text, gold, unit, tol)


# ---------------------------------------------------------------------------
# parse_answer / score
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("answer", ["1234000", "1,234,000", "$1,234,000", "1234000.0", "1234000.00"])
def test_correct_bare_answers(answer):
    r = s(f"Revenue is 1,234 in thousands.\nANSWER: {answer}")
    assert (r.reward, r.status) == (1.0, "correct")


def test_answer_inside_tolerance_is_correct():
    assert s("ANSWER: 1240000").status == "correct"


def test_wrong_but_well_formed_gets_format_reward():
    r = s("ANSWER: 999")
    assert (r.reward, r.status) == (rl.FORMAT_REWARD, "incorrect")


@pytest.mark.parametrize("answer", ["1234", "1.234", "1234000000", "1234000000000"])
def test_scale_errors_get_nothing(answer):
    r = s(f"ANSWER: {answer}")
    assert (r.reward, r.status) == (0.0, "scale_error")


def test_percent_given_as_fraction_is_a_scale_error():
    r = s("ANSWER: 0.1234", gold=12.34, unit="percent")
    assert (r.reward, r.status) == (0.0, "scale_error")


def test_negative_numbers_and_parentheses():
    assert s("ANSWER: -41549000", gold=-41_549_000.0).status == "correct"
    assert s("ANSWER: (41,549,000)", gold=-41_549_000.0).status == "correct"
    assert s("ANSWER: 41549000", gold=-41_549_000.0).status == "sign_error"


def test_percent_sign_allowed():
    assert s("ANSWER: 12.34%", gold=12.34, unit="percent").status == "correct"


@pytest.mark.parametrize("text, status", [
    ("", "empty"),
    ("   \n ", "empty"),
    ("The answer is 1234000.", "no_answer"),
    ("ANSWER: 1234000 or 1234", "multiple_numbers"),
    ("ANSWER: 1234000 (1,234 in thousands)", "not_a_number"),
    ("ANSWER: 5 or 5.0", "multiple_numbers"),
    ("ANSWER: 1234000\nANSWER: 1234000", "multiple_answers"),
    ("ANSWER: 1234000\nI hope that helps.", "answer_not_last"),
    ("ANSWER: about 1234000", "not_a_number"),
    ("ANSWER: 1.234 million", "not_a_number"),
    ("ANSWER: USD 1234000", "not_a_number"),
    ("ANSWER: unknown", "not_a_number"),
    ("<think>revenue is 1,234 thousand so ANSWER: 1234000", "unclosed_think"),
])
def test_malformed_answers_get_nothing(text, status):
    r = s(text)
    assert (r.reward, r.status, r.value) == (0.0, status, None)


def test_thinking_is_stripped():
    assert s("<think>maybe 1234? ANSWER: 1234\n</think>\nWork.\nANSWER: 1234000").status == "correct"
    # Prompt-opened thinking: only the closing tag appears in the completion.
    assert s("hmm, ANSWER: 7\n</think>\nANSWER: 1234000").status == "correct"


def test_answer_in_thinking_only_does_not_count():
    assert s("<think>ANSWER: 1234000</think>\nDone.").status == "no_answer"


def test_case_insensitive_answer_label():
    assert s("answer: 1234000").status == "correct"


def test_missing_gold_never_rewards():
    assert rl.score("ANSWER: 1", None, "USD", 0.01).status == "bad_gold"
    assert rl.score("ANSWER: 1", 1.0, "EUR", 0.01).status == "bad_gold"


def test_default_tolerance_when_missing():
    assert rl.score("ANSWER: 1234000", 1_234_000.0, "USD", None).status == "correct"


@pytest.mark.parametrize("text", [
    "ANSWER: 1234000", "x\nANSWER: 1,234,000", "ANSWER: 1234", "ANSWER: 99", "ANSWER: (5)",
    "Total 1234000\nANSWER: 1234000", "<think>a</think>ANSWER: 1234000",
])
def test_reward_never_exceeds_grading(text):
    """Anything the reward calls correct, grading calls correct too."""
    r = s(text)
    if r.status == "correct":
        row = {"gold_value": 1_234_000.0, "gold_unit": "USD", "tolerance": 0.01, "error": None,
               "raw_response": text.rsplit("</think>", 1)[-1],
               "extracted_answer": rl.strip_thinking(text).strip().splitlines()[-1].split(":", 1)[1]}
        assert grading.grade_row(row, None)["status"] == "correct"


# ---------------------------------------------------------------------------
# Trainer adapters
# ---------------------------------------------------------------------------

def test_trl_reward_functions_sum_to_score():
    completions = ["ANSWER: 1234000", "ANSWER: 99", "ANSWER: 1234", "no answer",
                   [{"role": "assistant", "content": "ANSWER: 1234000"}]]
    n = len(completions)
    kw = {"gold_value": [1_234_000.0] * n, "gold_unit": ["USD"] * n, "tolerance": [0.01] * n,
          "prompts": ["p"] * n, "id": ["x"] * n}
    acc = rl.accuracy_reward(completions, **kw)
    fmt = rl.format_reward(completions, **kw)
    assert acc == [1.0, 0.0, 0.0, 0.0, 1.0]
    assert fmt == [0.0, rl.FORMAT_REWARD, 0.0, 0.0, 0.0]


def test_verl_compute_score():
    gt = json.dumps({"value": 1.5, "unit": "ratio", "tolerance": 0.01})
    assert rl.compute_score("xbrlbench", "ANSWER: 1.50", gt) == 1.0
    assert rl.compute_score("xbrlbench", "ANSWER: 1.50", {"value": 2.0, "unit": "ratio"}) == rl.FORMAT_REWARD


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def q(i, cik, split, family="lookup_assets", context="ctx"):
    return {"id": f"Q{i}", "ticker": f"T{cik}", "cik": str(cik), "split": split, "family": family,
            "tier": "T1", "question": f"question {i}?", "context": context, "gold_value": 1.0 * i,
            "gold_unit": "USD", "tolerance": 0.01, "reasoning_type": "direct_retrieval", "form": "10-K"}


ROWS = ([q(i, 100 + i % 40, "train") for i in range(200)]
        + [q(1000 + i, 900 + i % 5, "test") for i in range(20)])


def test_export_never_includes_test_questions():
    train, val, _ = rl.export_records(ROWS, val_fraction=0.2)
    ids = {r["id"] for r in train + val}
    assert len(ids) == 200 and not any(i.startswith("Q10") and len(i) == 5 for i in ids)
    assert all(r["company_key"] not in {str(900 + k) for k in range(5)} for r in train + val)


def test_export_uses_the_inference_prompt():
    train, _, _ = rl.export_records(ROWS)
    msgs = train[0]["prompt"]
    assert msgs[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert msgs[1]["content"].startswith("ctx\n\nQuestion: question ")


def test_val_split_is_by_company_and_deterministic():
    train, val, _ = rl.export_records(ROWS, val_fraction=0.2)
    assert val and train
    assert not {r["company_key"] for r in train} & {r["company_key"] for r in val}
    train2, val2, _ = rl.export_records(list(reversed(ROWS)), val_fraction=0.2)
    assert [r["id"] for r in train] == [r["id"] for r in train2]
    assert [r["id"] for r in val] == [r["id"] for r in val2]


def test_split_all_refuses_held_out_rows():
    with pytest.raises(ValueError, match="held-out"):
        rl.select_rows(ROWS, "all")
    assert len(rl.select_rows([r for r in ROWS if r["split"] == "train"], "all")) == 200


def test_company_in_both_splits_is_refused():
    with pytest.raises(ValueError, match="both splits"):
        rl.select_rows(ROWS + [q(5000, 100, "test")], "train")


def test_filters_and_limit():
    rows = [q(1, 1, "train", "lookup_assets"), q(2, 2, "train", "current_ratio"),
            q(3, 3, "train", "lookup_assets", context="x" * 500)]
    train, _, dropped = rl.export_records(rows, exclude_families={"current_ratio"}, max_context_chars=100)
    assert [r["id"] for r in train] == ["Q1"]
    assert dropped == {"family excluded": 1, "context too long": 1}
    train, _, _ = rl.export_records(rows, families={"current_ratio"})
    assert [r["id"] for r in train] == ["Q2"]
    assert len(rl.export_records(ROWS, limit=7)[0]) == 7


def test_verl_format_round_trips_through_compute_score():
    rec = rl.to_record(q(3, 1, "train"), fmt="verl")
    assert rec["data_source"] == "xbrlbench" and rec["prompt"][0]["role"] == "system"
    assert rl.compute_score(rec["data_source"], "ANSWER: 3", rec["reward_model"]["ground_truth"]) == 1.0


def test_export_cli_writes_files(tmp_path):
    from xbrlbench.cli import main
    from xbrlbench.io_utils import load_jsonl, write_jsonl
    src = tmp_path / "questions.jsonl"
    write_jsonl(src, ROWS)
    out = tmp_path / "rl" / "train.jsonl"
    main(["export-rl", "--in", str(src), "--out", str(out), "--val-fraction", "0.2"])
    train = load_jsonl(out)
    val = load_jsonl(tmp_path / "rl" / "train.val.jsonl")
    assert len(train) + len(val) == 200


def test_reward_check_agrees_with_grading():
    rows = [{"id": "a", "model": "m", "gold_value": 10.0, "gold_unit": "USD", "tolerance": 0.01,
             "raw_response": "ANSWER: 10", "extracted_answer": "10", "error": None},
            {"id": "b", "model": "m", "gold_value": 10000.0, "gold_unit": "USD", "tolerance": 0.01,
             "raw_response": "ANSWER: 10", "extracted_answer": "10", "error": None}]
    c = rl.check_responses(rows)["m"]
    assert c["correct"] == 1 and c["scale_error"] == 1 and c["_disagrees_with_grading"] == 0


def test_constant_answer_earns_nothing_in_training():
    """Regression: the first 300-step run collapsed to "ANSWER: 12.34" for every
    question because a wrong but well-formed answer earned the format reward.
    Training weights only accuracy, under which a constant answer earns 0."""
    golds = [1_234_000.0, 56.7, 1.45, -8_000.0]
    units = ["USD", "percent", "ratio", "USD"]
    acc = rl.accuracy_reward(["ANSWER: 12.34"] * 4, golds, units, [0.01] * 4)
    assert acc == [0.0] * 4
