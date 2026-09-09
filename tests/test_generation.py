"""Tests for xbrlbench.generation (Commit 9): the ground-truth computation
chain (annual_value's XBRL fact filtering, snippet rendering, and
make_questions's tier/reasoning-type construction) had no direct test
coverage before this -- only indirect exercise via the fact that the real
generated data/questions.jsonl exists and passes validate.

fetch_companyfacts (the actual SEC network call) is intentionally not
tested here -- it's a one-line urllib wrapper, and CI must not make network
calls.
"""

from xbrlbench.generation import (
    _days,
    annual_value,
    make_questions,
    render_snippet,
    render_two_year_snippet,
)


# ---------------------------------------------------------------------------
# _days
# ---------------------------------------------------------------------------

def test_days_between_dates():
    assert _days("2023-01-01", "2023-12-31") == 364


def test_days_full_year_span():
    assert _days("2023-01-01", "2024-01-01") == 365


# ---------------------------------------------------------------------------
# annual_value -- the FY / 10-K / duration filtering logic
# ---------------------------------------------------------------------------

def usd_row(**overrides):
    base = {"fy": 2023, "fp": "FY", "form": "10-K", "val": 1000, "start": "2023-01-01", "end": "2023-12-31"}
    base.update(overrides)
    return base


def facts_with(concept: str, rows: list[dict]) -> dict:
    return {"facts": {"us-gaap": {concept: {"units": {"USD": rows}}}}}


def test_picks_matching_fy_and_form():
    facts = facts_with("Assets", [usd_row(val=500)])
    val, concept = annual_value(facts, ["Assets"], 2023)
    assert (val, concept) == (500, "Assets")


def test_rejects_wrong_fiscal_year():
    facts = facts_with("Assets", [usd_row(fy=2022, val=500)])
    assert annual_value(facts, ["Assets"], 2023) == (None, None)


def test_rejects_non_fy_period():
    facts = facts_with("Assets", [usd_row(fp="Q1", val=500)])
    assert annual_value(facts, ["Assets"], 2023) == (None, None)


def test_rejects_non_10k_form():
    facts = facts_with("Assets", [usd_row(form="10-Q", val=500)])
    assert annual_value(facts, ["Assets"], 2023) == (None, None)


def test_accepts_10k_a_amendment_form():
    # form values like "10-K/A" should still count -- the check is a prefix match.
    facts = facts_with("Assets", [usd_row(form="10-K/A", val=500)])
    val, _ = annual_value(facts, ["Assets"], 2023)
    assert val == 500


def test_drops_partial_period_under_300_days():
    facts = facts_with("Revenues", [usd_row(val=100, start="2023-10-01", end="2023-12-31")])  # ~91 days
    assert annual_value(facts, ["Revenues"], 2023) == (None, None)


def test_keeps_full_year_period_at_or_above_300_days():
    facts = facts_with("Revenues", [usd_row(val=100, start="2023-01-01", end="2023-12-31")])  # 364 days
    val, _ = annual_value(facts, ["Revenues"], 2023)
    assert val == 100


def test_instant_values_without_start_end_are_kept():
    # Balance-sheet concepts like Assets are "instant" facts -- no start/end.
    row = usd_row(val=999)
    del row["start"]
    del row["end"]
    facts = facts_with("Assets", [row])
    val, _ = annual_value(facts, ["Assets"], 2023)
    assert val == 999


def test_prefers_tightest_matching_span_when_multiple_rows_qualify():
    # Two qualifying rows for the same FY (e.g. a restated figure filed
    # twice) -- the shorter (tighter) span wins.
    facts = facts_with("Revenues", [
        usd_row(val=100, start="2022-06-01", end="2023-12-31"),   # ~578 days, too long a span
        usd_row(val=200, start="2023-01-01", end="2023-12-31"),   # 364 days, the real annual figure
    ])
    val, _ = annual_value(facts, ["Revenues"], 2023)
    assert val == 200


def test_tries_concept_candidates_in_order():
    facts = facts_with("Revenues", [usd_row(val=42)])  # only the fallback concept is present
    val, concept = annual_value(facts, ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"], 2023)
    assert (val, concept) == (42, "Revenues")


def test_missing_concept_entirely():
    facts = {"facts": {"us-gaap": {}}}
    assert annual_value(facts, ["Assets"], 2023) == (None, None)


def test_missing_units_usd_entirely():
    facts = {"facts": {"us-gaap": {"Assets": {"units": {}}}}}
    assert annual_value(facts, ["Assets"], 2023) == (None, None)


# ---------------------------------------------------------------------------
# render_snippet / render_two_year_snippet
# ---------------------------------------------------------------------------

def test_render_snippet_scales_to_thousands():
    snippet = render_snippet({"assets": 352_755_000_000}, "AAPL", 2023)
    assert "352,755,000" in snippet
    assert "352,755,000,000" not in snippet  # must be scaled down, not the raw value


def test_render_snippet_missing_value_renders_as_dash():
    snippet = render_snippet({}, "AAPL", 2023)
    assert "—" in snippet


def test_render_snippet_includes_ticker_and_year():
    snippet = render_snippet({}, "AAPL", 2023)
    assert "AAPL" in snippet
    assert "FY2023" in snippet


def test_render_two_year_snippet_stacks_both_years():
    snippet = render_two_year_snippet({"assets": 2000}, {"assets": 1000}, "AAPL", 2024)
    assert "FY2023" in snippet
    assert "FY2024" in snippet
    # FY-1 (prior year) rendered first, current year second.
    assert snippet.index("FY2023") < snippet.index("FY2024")


# ---------------------------------------------------------------------------
# make_questions -- tier construction, gold values, reasoning-type mapping
# (reasoning_type isn't a make_questions output field yet at this layer --
# see xbrlbench.schema; this just locks down source_concept/tier/gold_value,
# which the schema migration's reasoning_type mapping was keyed on)
# ---------------------------------------------------------------------------

FULL_VALS = {
    "revenue": 1000.0, "net_income": 100.0, "assets": 5000.0, "assets_current": 2000.0,
    "liabilities": 3000.0, "liabilities_current": 1000.0, "equity": 2000.0,
    "gross_profit": 400.0, "cogs": 600.0,
}
PREV_VALS = {**FULL_VALS, "revenue": 800.0, "assets": 4000.0}


def by_concept(questions):
    return {q["source_concept"]: q for q in questions}


def test_full_data_produces_all_seven_question_templates():
    qs = make_questions("AAPL", 2023, FULL_VALS, PREV_VALS)
    concepts = {q["source_concept"] for q in qs}
    assert concepts == {
        "Assets", "NetIncomeLoss", "AssetsCurrent/LiabilitiesCurrent", "GrossProfit/Revenue",
        "Revenue YoY", "LiabilitiesCurrent (distractor: Liabilities)", "Assets delta",
    }


def test_ids_are_sequential_and_scoped_to_ticker_fy():
    qs = make_questions("AAPL", 2023, FULL_VALS, PREV_VALS)
    ids = [q["id"] for q in qs]
    assert ids == [f"AAPL-2023-{q['tier']}-{i}" for i, q in enumerate(qs)]
    assert len(ids) == len(set(ids))


def test_t1_gold_values_are_direct_lookups_in_usd():
    qs = by_concept(make_questions("AAPL", 2023, FULL_VALS, None))
    assert qs["Assets"]["gold_value"] == 5000.0
    assert qs["Assets"]["gold_unit"] == "USD"
    assert qs["Assets"]["tier"] == "T1"


def test_t2_current_ratio_computed_correctly():
    qs = by_concept(make_questions("AAPL", 2023, FULL_VALS, None))
    q = qs["AssetsCurrent/LiabilitiesCurrent"]
    assert q["gold_value"] == 2.0  # 2000 / 1000
    assert q["gold_unit"] == "ratio"
    assert q["tier"] == "T2"


def test_t2_gross_margin_computed_as_percentage():
    qs = by_concept(make_questions("AAPL", 2023, FULL_VALS, None))
    q = qs["GrossProfit/Revenue"]
    assert q["gold_value"] == 40.0  # 100 * 400/1000
    assert q["gold_unit"] == "percent"


def test_t2_yoy_growth_requires_prev_vals_and_uses_two_year_context():
    without_prev = by_concept(make_questions("AAPL", 2023, FULL_VALS, None))
    assert "Revenue YoY" not in without_prev

    with_prev = by_concept(make_questions("AAPL", 2023, FULL_VALS, PREV_VALS))
    q = with_prev["Revenue YoY"]
    assert q["gold_value"] == 25.0  # 100 * (1000-800)/800
    assert q["gold_unit"] == "percent"
    assert "FY2022" in q["context"] and "FY2023" in q["context"]  # two-year context, not the single-year snippet


def test_t3_distractor_liabilities_gold_is_current_not_total():
    qs = by_concept(make_questions("AAPL", 2023, FULL_VALS, None))
    q = qs["LiabilitiesCurrent (distractor: Liabilities)"]
    assert q["gold_value"] == 1000.0  # liabilities_current, not liabilities (3000)
    assert q["tier"] == "T3"


def test_t3_assets_delta_can_be_negative():
    vals = {**FULL_VALS, "assets": 3000.0}  # decreased from prev 4000
    qs = by_concept(make_questions("AAPL", 2023, vals, PREV_VALS))
    q = qs["Assets delta"]
    assert q["gold_value"] == -1000.0


def test_missing_source_value_skips_that_question_not_crashes():
    vals = {**FULL_VALS, "assets": None}
    qs = by_concept(make_questions("AAPL", 2023, vals, None))
    assert "Assets" not in qs
    assert "NetIncomeLoss" in qs  # unrelated questions still generated


def test_no_prev_vals_skips_only_the_two_year_questions():
    qs = by_concept(make_questions("AAPL", 2023, FULL_VALS, None))
    assert "Revenue YoY" not in qs
    assert "Assets delta" not in qs
    assert "Assets" in qs and "AssetsCurrent/LiabilitiesCurrent" in qs


def test_gold_value_rounded_to_four_decimals():
    vals = {**FULL_VALS, "assets_current": 1.0, "liabilities_current": 3.0}
    qs = by_concept(make_questions("AAPL", 2023, vals, None))
    assert qs["AssetsCurrent/LiabilitiesCurrent"]["gold_value"] == 0.3333
