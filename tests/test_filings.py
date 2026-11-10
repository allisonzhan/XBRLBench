"""Tests for real-filing question generation (xbrlbench.filings) and the
parser behaviour it relies on, against tests/fixtures/realistic_10k.htm: a
fictional company in the markup style real EDGAR 10-Ks use (titles and the
"(In thousands...)" line above each table, split "$"/"("/")" cells, a
balance sheet split over a page break, arbitrary unit ids). No network."""

import json
from pathlib import Path

import pytest

from xbrlbench import edgar, filings, ixbrl
from xbrlbench.validation import ERROR, validate_questions

FIXTURE = Path(__file__).parent / "fixtures" / "realistic_10k.htm"
META = filings.FilingMeta(cik="0009999999", ticker="NWIN", accession="0009999999-25-000001",
                          filing_date="2025-08-20", sic="3829")


@pytest.fixture(scope="module")
def raw():
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def doc(raw):
    return ixbrl.parse_ixbrl(raw)


@pytest.fixture(scope="module")
def built(doc):
    return filings.build_filing_questions(doc, META)


def by_family(questions):
    return {q["family"]: q for q in questions}


# --- parser on realistic markup ---------------------------------------------

def test_units_resolved_by_measure_not_id(doc):
    [eps] = doc.find("EarningsPerShareBasic", end="2025-06-28")
    assert eps.unit == "USD/shares" and eps.value == 3.12
    [assets] = doc.find("Assets", end="2025-06-28")
    assert assets.unit == "USD" and assets.scale == 3


def test_heading_above_table_gives_title_and_scale(doc):
    income = ixbrl.statement_tables(doc)[0]
    assert income.heading.startswith("CONSOLIDATED STATEMENTS OF OPERATIONS")
    assert income.scale_label == "thousands"


def test_page_footer_not_in_next_heading(doc):
    tables = ixbrl.statement_tables(doc)
    assert all("Form 10-K |" not in t.heading for t in tables)


def test_split_balance_sheet_is_merged(doc):
    stmts = ixbrl.primary_statements(doc)
    assert set(stmts) == {"income_statement", "balance_sheet", "cash_flow"}
    text = stmts["balance_sheet"].render()
    assert "Total assets | $1,000,000 | $935,000" in text
    assert "Total current liabilities | 180,000 | 168,800" in text


def test_segment_note_table_is_not_a_statement(doc):
    tables = ixbrl.statement_tables(doc)
    assert tables[-1].heading.endswith("Revenue by segment (in thousands):")
    assert tables[-1].kind is None


def test_open_paren_cell_folds_into_number(doc):
    cash = ixbrl.primary_statements(doc)["cash_flow"].render()
    assert "Purchases of property and equipment | (56,200) | (50,100) | (47,900)" in cash


def test_row_labels(doc):
    income = ixbrl.primary_statements(doc)["income_statement"]
    [ni] = [f for f in income.facts if f.concept == "us-gaap:NetIncomeLoss" and f.context_id == "c-1"]
    assert income.label_of(ni) == "Net income"


# --- question generation ------------------------------------------------------

def test_report_ok(built):
    _, report = built
    assert report.status == "ok"
    assert report.statements == ["balance_sheet", "cash_flow", "income_statement"]
    assert report.dropped == []


@pytest.mark.parametrize("family,gold,unit", [
    ("lookup_revenue", 1_284_550_000, "USD"),
    ("lookup_net_income", 150_210_000, "USD"),
    ("lookup_assets", 1_000_000_000, "USD"),
    ("lookup_liabilities_current", 180_000_000, "USD"),
    ("lookup_operating_cash_flow", 205_300_000, "USD"),
    ("current_ratio", 2.9667, "ratio"),            # 534,000 / 180,000
    ("gross_margin", 42.228, "percent"),           # 542,440 / 1,284,550
    ("operating_margin", 15.7627, "percent"),      # 202,480 / 1,284,550
    ("revenue_growth", 7.1959, "percent"),         # 1,284,550 / 1,198,320 - 1
    ("net_income_change", 18_707_000, "USD"),      # 150,210 - 131,503
    ("free_cash_flow", 149_100_000, "USD"),        # 205,300 - 56,200
])
def test_gold_values_hand_checked(built, family, gold, unit):
    q = by_family(built[0])[family]
    assert q["gold_value"] == pytest.approx(gold, abs=1e-4)
    assert q["gold_unit"] == unit


def test_questions_quote_the_filings_row_labels(built):
    q = by_family(built[0])
    assert '"Net revenue"' in q["lookup_revenue"]["question"]
    assert '"Total current assets" divided by "Total current liabilities"' in q["current_ratio"]["question"]


def test_change_questions_state_the_sign_convention(built):
    q = by_family(built[0])
    assert "negative if it decreased" in q["revenue_growth"]["question"]
    assert "negative if it decreased" in q["net_income_change"]["question"]


def test_context_has_only_the_needed_statement_and_no_markup(built):
    q = by_family(built[0])
    ctx = q["current_ratio"]["context"]
    assert "CONSOLIDATED BALANCE SHEETS" in ctx and "STATEMENTS OF OPERATIONS" not in ctx
    assert "(In thousands" in ctx
    assert "<" not in ctx and "ix:" not in ctx
    assert ctx.startswith("Northwind Instruments, Inc. — Form 10-K for the fiscal year ended June 28, 2025")


def test_metadata_fields(built):
    q = built[0][0]
    assert q["ticker"] == "NWIN" and q["fiscal_year"] == 2025 and q["period_end"] == "2025-06-28"
    assert q["accession"] == META.accession and q["cik"] == META.cik
    assert q["id"] == "NWIN-2025-lookup_revenue"


def test_generated_questions_pass_validation(built):
    issues = validate_questions(built[0])
    assert [i for i in issues if i.severity == ERROR] == []


def test_scale_mismatch_drops_statement(raw):
    # The heading says thousands but the tags say millions: never guess.
    bad = raw.replace("(In thousands, except per share amounts)", "(In millions, except per share amounts)")
    qs, report = filings.build_filing_questions(ixbrl.parse_ixbrl(bad), META)
    assert "income_statement" not in report.statements
    assert any("income_statement: stated millions but tagged scale 3" in d for d in report.dropped)
    assert not any(q["family"] in ("lookup_revenue", "gross_margin") for q in qs)


def test_duplicate_row_label_is_skipped(raw):
    # Two rows labelled "Net revenue" -> the label no longer identifies one line.
    bad = raw.replace("<span>Cost of revenue</span>", "<span>Net revenue</span>")
    qs, _ = filings.build_filing_questions(ixbrl.parse_ixbrl(bad), META)
    assert "lookup_revenue" not in by_family(qs)


def test_no_statements(built):
    doc = ixbrl.parse_ixbrl(
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>nothing</p></body></html>')
    qs, report = filings.build_filing_questions(doc, META)
    assert qs == [] and report.status == "no_statements"


# --- splits and sampling --------------------------------------------------------

def test_company_group_is_stable_and_roughly_twenty_percent():
    groups = [filings.company_group(c) for c in range(1, 5001)]
    assert groups == [filings.company_group(c) for c in range(1, 5001)]
    assert 0.17 < groups.count("test") / len(groups) < 0.23


def test_split_respects_cutoff_and_company_group():
    test_cik = next(c for c in range(1, 1000) if filings.company_group(c) == "test")
    train_cik = next(c for c in range(1, 1000) if filings.company_group(c) == "train")
    assert filings.assign_split(test_cik, "2026-08-01") == "test"
    assert filings.assign_split(test_cik, "2026-02-01") is None
    assert filings.assign_split(train_cik, "2026-02-01") == "train"
    assert filings.assign_split(train_cik, "2026-08-01") is None


def test_interleaved_order_spreads_any_prefix():
    order = filings.interleaved_order(list(range(100)))
    assert sorted(order) == list(range(100))
    assert order[:4] == [0, 50, 25, 75]


def test_parse_company_tickers_dedupes_share_classes():
    data = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "2": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."},
            "1": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."}}
    assert edgar.parse_company_tickers(data) == [
        {"cik": "0000320193", "ticker": "AAPL", "title": "Apple Inc."},
        {"cik": "0001652044", "ticker": "GOOGL", "title": "Alphabet Inc."},
    ]


# --- corpus build with the network mocked ------------------------------------------

def test_build_corpus_end_to_end(monkeypatch, tmp_path, raw):
    train_cik = next(c for c in range(1, 1000) if filings.company_group(c) == "train")
    cik = f"{train_cik:010d}"
    subs = {"name": "Northwind Instruments, Inc.", "sic": "3829", "filings": {"recent": {
        "form": ["10-K", "10-K"], "accessionNumber": ["a-25", "a-24"],
        "primaryDocument": ["k25.htm", "k24.htm"], "filingDate": ["2025-08-20", "2024-08-21"],
        "reportDate": ["2025-06-28", "2024-06-29"]}}}
    monkeypatch.setattr(edgar, "company_list", lambda email: [{"cik": cik, "ticker": "NWIN", "title": "x"}])
    monkeypatch.setattr(edgar, "get_submissions", lambda c, email, since="": subs)
    fetched = []

    def fake_fetch(url, email):
        fetched.append(url)
        if url.endswith("k24.htm"):
            return b"<html><body>not xml"
        return raw.encode("utf-8")

    monkeypatch.setattr(edgar, "fetch", fake_fetch)
    out = str(tmp_path / "q.jsonl")
    stats = filings.build_corpus("x@example.com", out, n_companies=1, per_company=2)
    assert stats["ok"] == 1 and stats["not_ixbrl"] == 1 and stats["companies_ok"] == 1
    rows = [json.loads(line) for line in open(out, encoding="utf-8")]
    assert len(rows) == 11 and {r["split"] for r in rows} == {"train"}
    log = [json.loads(line) for line in open(out + ".log.jsonl", encoding="utf-8")]
    assert [r["status"] for r in log] == ["ok", "not_ixbrl"]

    # Resume: nothing is fetched again, and the company already counts.
    fetched.clear()
    filings.build_corpus("x@example.com", out, n_companies=1, per_company=2)
    assert fetched == []
    assert filings.summarize(out)["questions"] == 11


def test_build_corpus_trial_tickers_take_newest_filing_any_split(monkeypatch, tmp_path, raw):
    test_cik = next(c for c in range(1, 1000) if filings.company_group(c) == "test")
    subs = {"name": "N", "sic": "3829", "filings": {"recent": {
        "form": ["10-K"], "accessionNumber": ["a-25"], "primaryDocument": ["k25.htm"],
        "filingDate": ["2025-08-20"], "reportDate": ["2025-06-28"]}}}
    monkeypatch.setattr(edgar, "company_list", lambda email: [
        {"cik": f"{test_cik:010d}", "ticker": "NWIN", "title": "N"},
        {"cik": "0000000002", "ticker": "OTHER", "title": "O"}])
    monkeypatch.setattr(edgar, "get_submissions", lambda c, email, since="": subs)
    monkeypatch.setattr(edgar, "fetch", lambda url, email: raw.encode("utf-8"))
    out = str(tmp_path / "trial.jsonl")
    stats = filings.build_corpus("x@example.com", out, n_companies=5, per_company=1, tickers=["nwin"])
    assert stats["ok"] == 1 and stats["companies_ok"] == 1
    rows = [json.loads(line) for line in open(out, encoding="utf-8")]
    assert {r["split"] for r in rows} == {None}  # held-out company, filed before the cutoff


# --- regressions from the first real-filing trial (AAPL, AMZN, WMT, JPM, JNJ, MSFT) ---

def test_par_value_inside_label_does_not_count_as_a_scale(doc, built):
    # "Common stock ..., $0.001 par value" tags a scale-0 USD fact inside the
    # label. It used to make the whole balance sheet "mixed USD scales [0, 3]".
    bs = ixbrl.primary_statements(doc)["balance_sheet"]
    par = [w for f, w in zip(bs.facts, bs.in_text, strict=True)
           if f.concept == "us-gaap:CommonStockParOrStatedValuePerShare"]
    assert par == [True]
    assert filings.table_scale_problem(bs) is None
    assert "balance_sheet" in built[1].statements


def test_small_caps_title_is_one_line(doc):
    cash = ixbrl.primary_statements(doc)["cash_flow"]
    assert cash.heading.startswith("CONSOLIDATED STATEMENTS OF CASH FLOWS\n(In thousands)")


def test_date_split_over_two_divs_keeps_a_space(doc):
    income = ixbrl.primary_statements(doc)["income_statement"]
    assert "June 28, 2025 | June 29, 2024 | July 1, 2023" in income.render()


def test_company_without_filings_is_logged(monkeypatch, tmp_path):
    subs = {"name": "X", "sic": "2911", "filings": {"recent": {
        "form": ["10-K"], "accessionNumber": ["a"], "primaryDocument": ["k.txt"],
        "filingDate": ["2025-02-01"], "reportDate": ["2024-12-31"]}}}
    monkeypatch.setattr(edgar, "company_list", lambda email: [{"cik": "0000034088", "ticker": "XOM", "title": "X"}])
    monkeypatch.setattr(edgar, "get_submissions", lambda c, email, since="": subs)
    out = str(tmp_path / "q.jsonl")
    filings.build_corpus("x@example.com", out, n_companies=1, tickers=["XOM"])
    [rec] = [json.loads(line) for line in open(out + ".log.jsonl", encoding="utf-8")]
    assert rec["status"] == "no_filings" and "k.txt" in rec["note"]
