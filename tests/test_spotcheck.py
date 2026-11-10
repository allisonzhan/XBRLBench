"""Tests for xbrlbench.edgar filing selection and xbrlbench.spotcheck, with
network calls replaced by the iXBRL fixture."""

from pathlib import Path

import pytest

from xbrlbench import edgar, spotcheck
from xbrlbench.ixbrl import parse_ixbrl

FIXTURE = Path(__file__).parent / "fixtures" / "sample_10k.htm"

SUBMISSIONS = {"filings": {"recent": {
    "form":            ["10-Q",       "10-K",        "8-K",         "10-K",        "10-K/A"],
    "accessionNumber": ["0001-24-9",  "0001-24-5",   "0001-24-4",   "0001-23-5",   "0001-24-6"],
    "primaryDocument": ["q.htm",      "k2024.htm",   "e.htm",       "k2023.htm",   "ka.htm"],
    "filingDate":      ["2025-01-31", "2024-11-01",  "2024-10-30",  "2023-11-03",  "2024-12-01"],
    "reportDate":      ["2024-12-28", "2024-09-28",  "2024-10-30",  "2023-09-30",  "2024-09-28"],
}}}


def test_annual_filings_keeps_only_10k_newest_first():
    refs = edgar.annual_filings(SUBMISSIONS, "0000320193")
    assert [r.primary_document for r in refs] == ["k2024.htm", "k2023.htm"]


def test_filing_url():
    [ref, _] = edgar.annual_filings(SUBMISSIONS, "0000320193")
    assert ref.url == "https://www.sec.gov/Archives/edgar/data/320193/0001245/k2024.htm"


def test_candidates_prefer_same_calendar_year():
    refs = edgar.annual_filings(SUBMISSIONS, "0000320193")
    assert [r.report_date for r in edgar.candidates_for_fiscal_year(refs, 2023)] == ["2023-09-30", "2024-09-28"]


OLDER_PAGE = {
    "form":            ["10-K",       "8-K"],
    "accessionNumber": ["0001-22-5",  "0001-22-4"],
    "primaryDocument": ["k2022.htm",  "e.htm"],
    "filingDate":      ["2022-10-28", "2022-10-27"],
    "reportDate":      ["2022-09-24", "2022-10-27"],
}


def test_annual_filings_reads_older_pages():
    # A prolific filer's earlier 10-Ks fall out of `recent` into paged files.
    subs = {**SUBMISSIONS, "older_pages": [OLDER_PAGE]}
    refs = edgar.annual_filings(subs, "0000320193")
    assert [r.primary_document for r in refs] == ["k2024.htm", "k2023.htm", "k2022.htm"]


def test_get_submissions_fetches_older_pages_since_cutoff(monkeypatch):
    import json
    main = {"filings": {"recent": SUBMISSIONS["filings"]["recent"], "files": [
        {"name": "CIK0000320193-submissions-001.json", "filingTo": "2023-06-19"},
        {"name": "CIK0000320193-submissions-002.json", "filingTo": "2012-09-04"},
    ]}}
    fetched = []

    def fake_fetch(url, email):
        fetched.append(url)
        return json.dumps(OLDER_PAGE if url.endswith("-001.json") else main).encode()

    monkeypatch.setattr(edgar, "fetch", fake_fetch)
    subs = edgar.get_submissions("0000320193", "a@b.c", since="2015-01-01")
    assert fetched == ["https://data.sec.gov/submissions/CIK0000320193.json",
                       "https://data.sec.gov/submissions/CIK0000320193-submissions-001.json"]
    assert subs["older_pages"] == [OLDER_PAGE]


def test_sample_is_deterministic_and_checkable_only():
    rows = [{"id": f"X-{i}", "source_concept": c}
            for i, c in enumerate(["Assets", "Revenue YoY", "NetIncomeLoss", "Assets", "GrossProfit/Revenue"])]
    sample = spotcheck.sample_questions(rows, 2)
    assert [r["id"] for r in sample] == ["X-0", "X-2"]  # one Assets, one NetIncomeLoss
    assert spotcheck.sample_questions(rows, 2) == sample


def test_values_match_respects_rounding():
    assert spotcheck.values_match(93_736_000_000, 93_736_000_000, "-6")
    assert spotcheck.values_match(93_736_400_000, 93_736_000_000, "-6")
    assert not spotcheck.values_match(93_737_000_000, 93_736_000_000, "-6")


def test_current_period_fact_picks_latest_annual():
    doc = parse_ixbrl(FIXTURE.read_bytes())
    assert spotcheck.current_period_fact(doc, "NetIncomeLoss", instant=False).value == 93_736_000_000
    assert spotcheck.current_period_fact(doc, "Assets", instant=True).value == 364_980_000_000


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(edgar, "get_submissions", lambda cik, email: SUBMISSIONS)
    monkeypatch.setattr(edgar, "fetch", lambda url, email: FIXTURE.read_bytes())


def question(**kw):
    base = {"id": "AAPL-2024-T1-0", "ticker": "AAPL", "fiscal_year": 2024,
            "source_concept": "Assets", "gold_value": 364_980_000_000}
    base.update(kw)
    return base


def test_check_question_match(offline):
    r = spotcheck.check_question(question(), "a@b.c", {})
    assert r.status == "match"
    assert r.period_end == "2024-09-28"


def test_check_question_mismatch(offline):
    r = spotcheck.check_question(question(gold_value=352_583_000_000), "a@b.c", {})
    assert r.status == "mismatch"
    assert r.filed_value == 364_980_000_000


def test_check_question_skips_10k_for_other_fiscal_year(offline):
    # The fixture's DocumentFiscalYearFocus is 2024, so no candidate matches 2023.
    r = spotcheck.check_question(question(fiscal_year=2023), "a@b.c", {})
    assert r.status == "not_found"


def test_unknown_ticker_is_an_error(offline):
    assert spotcheck.check_question(question(ticker="ZZZZ"), "a@b.c", {}).status == "error"


def test_sample_covers_every_concept_even_when_ids_interleave():
    # The real bank orders each company-year as Assets, NetIncomeLoss, ...,
    # LiabilitiesCurrent, so a plain id stride of 3 picked Assets every time.
    concepts = ["Assets", "NetIncomeLoss", "LiabilitiesCurrent (distractor: Liabilities)"]
    rows = [{"id": f"T{y:02d}-{j}", "source_concept": c} for y in range(10) for j, c in enumerate(concepts)]
    sample = spotcheck.sample_questions(rows, 10)
    assert len(sample) == 10
    assert {r["source_concept"] for r in sample} == set(concepts)
