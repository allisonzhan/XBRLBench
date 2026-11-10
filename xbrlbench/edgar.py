"""xbrlbench.edgar — locate and download 10-K filings from SEC EDGAR.

Stdlib only. SEC requires a descriptive User-Agent with a contact email and
asks clients to stay under 10 requests/second; every request here goes
through `fetch`, which sends the header and sleeps between calls.

  find_10k(cik, fiscal_year, email) -> FilingRef   (via the submissions API)
  company_list(email) -> [{"cik", "ticker", "title"}]  (every listed filer)
  fetch(url, email) -> bytes
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass

UA_TEMPLATE = "xbrlbench research {email}"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
OLDER_PAGE_URL = "https://data.sec.gov/submissions/{name}"
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{document}"
REQUEST_INTERVAL_S = 0.2

_last_request = 0.0


def fetch(url: str, email: str, timeout: int = 60) -> bytes:
    global _last_request
    wait = REQUEST_INTERVAL_S - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA_TEMPLATE.format(email=email)})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    finally:
        _last_request = time.monotonic()


@dataclass(frozen=True)
class FilingRef:
    cik: str
    form: str
    accession: str        # with dashes, e.g. "0000320193-24-000123"
    primary_document: str
    filing_date: str
    report_date: str      # the period end the filing reports on

    @property
    def url(self) -> str:
        return ARCHIVE_URL.format(cik_int=int(self.cik), accession=self.accession.replace("-", ""),
                                  document=self.primary_document)


def annual_filings(submissions: dict, cik: str, forms: tuple[str, ...] = ("10-K",)) -> list[FilingRef]:
    """All filings of `forms` listed in a submissions-API response, newest first.

    Reads `filings.recent` plus any older pages that `get_submissions` attached
    under `older_pages`. `recent` alone holds only the last ~1,000 filings of
    any form, which for a prolific filer (WMT) is under three years."""
    blocks = [submissions.get("filings", {}).get("recent", {}), *submissions.get("older_pages", [])]
    columns = ("form", "accessionNumber", "primaryDocument", "filingDate", "reportDate")
    refs = []
    for block in blocks:
        rows = zip(*(block.get(c, []) for c in columns), strict=True)
        refs += [FilingRef(cik, form, acc, doc, filed, report)
                 for form, acc, doc, filed, report in rows if form in forms]
    return sorted(refs, key=lambda r: r.filing_date, reverse=True)


def candidates_for_fiscal_year(refs: list[FilingRef], fiscal_year: int) -> list[FilingRef]:
    """10-Ks likely to be `fiscal_year`'s annual report, most likely first.

    Most filers' fiscal year N ends in calendar year N; some (e.g. a year
    ending in January) label it by the year it started, so N+1 is the
    fallback. The caller confirms with the document's own
    dei:DocumentFiscalYearFocus."""
    by_year = {fiscal_year: 0, fiscal_year + 1: 1}
    picked = [r for r in refs if r.report_date[:4].isdigit() and int(r.report_date[:4]) in by_year]
    return sorted(picked, key=lambda r: by_year[int(r.report_date[:4])])


def get_submissions(cik: str, email: str, since: str = "2015-01-01") -> dict:
    """The submissions-API response for `cik`, with the older pages listed in
    `filings.files` that reach back to `since` fetched and attached as
    `older_pages` (same column layout as `filings.recent`)."""
    submissions = json.loads(fetch(SUBMISSIONS_URL.format(cik=cik), email))
    submissions["older_pages"] = [
        json.loads(fetch(OLDER_PAGE_URL.format(name=page["name"]), email))
        for page in submissions.get("filings", {}).get("files", [])
        if page.get("filingTo", "9999") >= since
    ]
    return submissions


def parse_company_tickers(data: dict) -> list[dict]:
    """SEC's company_tickers.json -> one entry per CIK (first ticker wins,
    which drops extra share classes), in SEC's order (roughly by market value)."""
    out, seen = [], set()
    for key in sorted(data, key=lambda k: int(k)):
        row = data[key]
        cik = f"{int(row['cik_str']):010d}"
        if cik in seen:
            continue
        seen.add(cik)
        out.append({"cik": cik, "ticker": row.get("ticker", ""), "title": row.get("title", "")})
    return out


def company_list(email: str) -> list[dict]:
    return parse_company_tickers(json.loads(fetch(COMPANY_TICKERS_URL, email)))
