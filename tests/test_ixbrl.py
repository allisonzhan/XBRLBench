"""Tests for xbrlbench.ixbrl against a small hand-written iXBRL 10-K
(tests/fixtures/sample_10k.htm) that mirrors the structure of real EDGAR
filings. No network."""

from pathlib import Path

import pytest

from xbrlbench.ixbrl import parse_ixbrl, parse_number, scale_exponent, statement_tables

FIXTURE = Path(__file__).parent / "fixtures" / "sample_10k.htm"


@pytest.fixture(scope="module")
def doc():
    return parse_ixbrl(FIXTURE.read_bytes())


# --- parse_number -----------------------------------------------------------

def test_scale_applied():
    assert parse_number("391,035", "ixt:num-dot-decimal", "6") == 391_035_000_000


def test_sign_attribute_makes_value_negative():
    assert parse_number("269", None, "6", "-") == -269_000_000


def test_fixed_zero_dash_is_zero():
    assert parse_number("—", "ixt:fixed-zero", "6") == 0


def test_comma_decimal_format():
    assert parse_number("1.234,5", "ixt:num-comma-decimal") == 1234.5


def test_unparseable_text_raises():
    with pytest.raises(ValueError):
        parse_number("n/a")


# --- contexts and facts -----------------------------------------------------

def test_contexts_parsed(doc):
    assert doc.contexts["i-2024"].is_instant
    assert doc.contexts["i-2024"].end == "2024-09-28"
    assert doc.contexts["c-fy24"].days == 363
    assert doc.contexts["c-fy24-products"].dimensions == (("srt:ProductOrServiceAxis", "us-gaap:ProductMember"),)


def test_dei_cover_facts(doc):
    assert doc.dei["DocumentFiscalYearFocus"] == "2024"
    assert doc.dei["DocumentType"] == "10-K"


def test_find_instant_fact_by_period(doc):
    [fact] = doc.find("Assets", end="2024-09-28", instant=True)
    assert fact.value == 364_980_000_000
    assert fact.concept == "us-gaap:Assets"


def test_find_excludes_dimensioned_contexts_by_default(doc):
    totals = doc.find("RevenueFromContractWithCustomerExcludingAssessedTax", end="2024-09-28")
    assert [f.value for f in totals] == [391_035_000_000]
    with_segments = doc.find("RevenueFromContractWithCustomerExcludingAssessedTax", end="2024-09-28",
                             dimensionless=False)
    assert len(with_segments) == 2


def test_find_duration_filters_out_quarter(doc):
    annual = doc.find("NetIncomeLoss", end="2024-09-28", min_days=350, max_days=380)
    assert [f.value for f in annual] == [93_736_000_000]


def test_negative_fact(doc):
    [fact] = doc.find("NonoperatingIncomeExpense", end="2024-09-28")
    assert fact.value == -269_000_000


def test_nil_fact_has_no_value(doc):
    [fact] = doc.find("Goodwill")
    assert fact.value is None


def test_malformed_document_raises_value_error():
    with pytest.raises(ValueError):
        parse_ixbrl("<html><body><p>unclosed</body></html>")


# --- statement tables -------------------------------------------------------

def test_only_tables_with_tagged_numbers_are_returned(doc):
    tables = statement_tables(doc)
    assert [t.kind for t in tables] == ["income_statement", "balance_sheet"]


def test_scale_label_detected(doc):
    income, balance = statement_tables(doc)
    assert income.scale_label == "millions"
    assert scale_exponent(income.scale_label) == 6


def test_rendered_table_has_no_markup_and_folds_currency_cells(doc):
    income, _ = statement_tables(doc)
    text = income.render()
    assert "ix:" not in text and "<" not in text
    assert "Net sales | $391,035 | $383,285" in text
    assert "Other income/(expense), net | (269) | (565)" in text


def test_table_facts_are_those_inside_the_table(doc):
    _, balance = statement_tables(doc)
    concepts = {f.concept for f in balance.facts}
    assert concepts == {"us-gaap:LiabilitiesCurrent", "us-gaap:Assets", "us-gaap:Goodwill"}
