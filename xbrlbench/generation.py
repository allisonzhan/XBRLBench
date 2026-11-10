"""xbrlbench.generation — build the financial-reasoning benchmark question
bank from SEC XBRL.

What it does:
  1. Pulls each company's structured financial facts from SEC's free
     companyfacts API (no key needed).
  2. Computes VERIFIED gold answers for three question tiers.
  3. Renders a MESSY human-readable statement snippet as the model input
     (scaled "in thousands", sibling line items as distractors) so the
     benchmark measures whether a model can bridge clean-truth vs
     messy-presentation.
  4. Writes questions.jsonl.

Ground truth comes from XBRL (machine-verified). The model never sees XBRL —
it sees the messy snippet. That gap is the thing you're measuring.

Run:    python -m xbrlbench generate --email you@example.com
Output: data/questions.jsonl (one record per line)

v1 note: the messy input here is rendered from the same XBRL facts with
realistic presentation noise. The v2 upgrade — and the thing that makes this
fully legit — is to swap render_snippet() for the ACTUAL financial-statement
exhibit text from each 10-K. Do that once v1 works end to end.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import date

from .grading import DEFAULT_TOLERANCE
from .io_utils import write_jsonl
from .paths import DEFAULT_QUESTIONS_PATH

# ticker -> 10-digit zero-padded CIK. Expand this; mix clean and messy filers.
COMPANIES = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "KO": "0000021344",
    "WMT": "0000104169",
    "NVDA": "0001045810",
}

# Fiscal years to build questions for (needs y and y-1 present for growth Qs).
YEARS = [2023, 2024]

# us-gaap concepts. Revenue/COGS tagging varies by filer — try in order.
CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"],
    "net_income": ["NetIncomeLoss"],
    "assets": ["Assets"],
    "assets_current": ["AssetsCurrent"],
    "liabilities": ["Liabilities"],
    "liabilities_current": ["LiabilitiesCurrent"],
    "equity": ["StockholdersEquity"],
    "gross_profit": ["GrossProfit"],
    "cogs": ["CostOfGoodsAndServicesSold", "CostOfRevenue"],
    # Inputs for derive_total_liabilities (not rendered in the snippet).
    "liabilities_and_equity": ["LiabilitiesAndStockholdersEquity"],
    "equity_incl_nci": ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
}

UA_TEMPLATE = "financial-eval-research {email}"


def fetch_companyfacts(cik: str, email: str) -> dict:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    req = urllib.request.Request(url, headers={"User-Agent": UA_TEMPLATE.format(email=email)})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _days(start: str, end: str) -> int:
    a = date.fromisoformat(start)
    b = date.fromisoformat(end)
    return (b - a).days


# A fiscal year is 52 or 53 weeks for many filers (e.g. AAPL, WMT, NVDA), so a
# full-year duration fact spans roughly 357-372 days. Anything outside this
# window is a quarter, a partial period, or a multi-year cumulative figure.
MIN_ANNUAL_DAYS = 350
MAX_ANNUAL_DAYS = 380


def fiscal_year_end(facts: dict, fy: int) -> str | None:
    """Return the balance-sheet date (ISO string) of `fy`'s own 10-K.

    In companyfacts, `fy`/`fp` describe the FILING a fact appeared in, not the
    period the fact covers: a FY2023 10-K also carries FY2022 (and, for income
    statement items, FY2021) comparatives, all tagged fy=2023. The filing's
    own reporting period is the latest `end` date among its facts, so that is
    what we anchor every lookup for `fy` to.
    """
    latest = None
    for node in facts.get("facts", {}).get("us-gaap", {}).values():
        for rows in node.get("units", {}).values():
            for row in rows:
                if row.get("fy") != fy or row.get("fp") != "FY":
                    continue
                if not str(row.get("form", "")).startswith("10-K"):
                    continue
                end = row.get("end")
                if end and (latest is None or end > latest):
                    latest = end
    return latest


def annual_value(facts: dict, concept_candidates: list[str], fy: int,
                 period_end: str | None = None):
    """Return (value, concept) for the fiscal year `fy`, or (None, None).

    Facts are selected by the PERIOD they describe (end date == the fiscal
    year end, and a ~1-year span for duration items), never by the `fy` tag
    alone -- see fiscal_year_end for why. If the same period was reported more
    than once (the original 10-K, a later 10-K's comparative column, a 10-K/A),
    the earliest-filed value wins: it is the figure as originally reported in
    that year's own annual report, which is what the rendered statement for
    that year represents.

    `period_end` can be passed in to avoid recomputing it per concept.
    """
    if period_end is None:
        period_end = fiscal_year_end(facts, fy)
    if period_end is None:
        return None, None
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for concept in concept_candidates:
        node = gaap.get(concept)
        if not node:
            continue
        usd = node.get("units", {}).get("USD", [])
        best = None  # (filed, value)
        for row in usd:
            if row.get("end") != period_end:
                continue
            if not str(row.get("form", "")).startswith("10-K"):
                continue
            if row.get("start"):
                span = _days(row["start"], row["end"])
                if not MIN_ANNUAL_DAYS <= span <= MAX_ANNUAL_DAYS:
                    continue  # quarterly, partial, or multi-year figure
            filed = row.get("filed", "")
            if best is None or filed < best[0]:
                best = (filed, row["val"])
        if best:
            return best[1], concept
    return None, None


def derive_total_liabilities(vals: dict) -> dict:
    """Fill in `liabilities` for filers that don't tag `Liabilities` (KO, WMT).

    Total liabilities = total liabilities-and-equity minus total equity
    INCLUDING noncontrolling interest. NCI is equity, not a liability, so
    subtracting only parent-company `StockholdersEquity` would overstate
    liabilities by the NCI amount. A reported `Liabilities` value always wins.
    """
    if vals.get("liabilities") is not None:
        return vals
    lse, eq = vals.get("liabilities_and_equity"), vals.get("equity_incl_nci")
    if lse is not None and eq is not None:
        vals["liabilities"] = lse - eq
    return vals


def render_snippet(vals: dict, ticker: str, fy: int) -> str:
    """Messy human-readable statement fragment: scaled to thousands, distractors,
    no answer-labeled line. Forces the model to locate + scale + reason."""

    def k(x):  # display in thousands, rounded — model must multiply back by 1000
        return f"{round(x / 1000):,}" if x is not None else "—"

    lines = [
        f"{ticker} — Consolidated Financial Statements (excerpt), FY{fy}",
        "(in thousands, except per share data)",
        "",
        "INCOME STATEMENT",
        f"  Net revenues .......................... {k(vals.get('revenue'))}",
        f"  Cost of sales ......................... {k(vals.get('cogs'))}",
        f"  Gross profit .......................... {k(vals.get('gross_profit'))}",
        f"  Net income ............................ {k(vals.get('net_income'))}",
        "",
        "BALANCE SHEET",
        f"  Total current assets .................. {k(vals.get('assets_current'))}",
        f"  Total assets .......................... {k(vals.get('assets'))}",
        f"  Total current liabilities ............. {k(vals.get('liabilities_current'))}",
        f"  Total liabilities ..................... {k(vals.get('liabilities'))}",
        f"  Total stockholders' equity ............ {k(vals.get('equity'))}",
    ]
    return "\n".join(lines)


def render_two_year_snippet(vals: dict, prev_vals: dict, ticker: str, fy: int) -> str:
    """Same messy rendering as render_snippet, but stacks FY-1 above FY so
    comparison questions (YoY growth, deltas) actually have both years'
    figures in front of the model instead of asking it to reason about data
    it was never shown."""
    return (
        render_snippet(prev_vals, ticker, fy - 1)
        + "\n\n"
        + render_snippet(vals, ticker, fy)
    )


def make_questions(ticker: str, fy: int, vals: dict, prev_vals: dict | None) -> list[dict]:
    qs: list[dict] = []

    def q(tier, question, gold_value, gold_unit, concept, reasoning_type, context=None):
        if gold_value is None:
            return
        qs.append({
            "id": f"{ticker}-{fy}-{tier}-{len(qs)}",
            "ticker": ticker, "fiscal_year": fy, "tier": tier,
            "question": question,
            "gold_value": round(gold_value, 4),
            "gold_unit": gold_unit,
            "source_concept": concept,
            "context": context if context is not None else render_snippet(vals, ticker, fy),
            "reasoning_type": reasoning_type,
            "tolerance": DEFAULT_TOLERANCE,
        })

    # T1 — single lookup (answer in raw dollars; snippet shows thousands → unit trap)
    q("T1", f"What were {ticker}'s total assets for fiscal year {fy}? Answer in USD.",
      vals.get("assets"), "USD", "Assets", "direct_retrieval")
    q("T1", f"What was {ticker}'s net income for fiscal year {fy}? Answer in USD.",
      vals.get("net_income"), "USD", "NetIncomeLoss", "direct_retrieval")

    # T2 — computation
    if vals.get("assets_current") and vals.get("liabilities_current"):
        q("T2", f"What was {ticker}'s current ratio for fiscal year {fy}? "
                f"(current assets / current liabilities, 2 decimals)",
          vals["assets_current"] / vals["liabilities_current"], "ratio",
          "AssetsCurrent/LiabilitiesCurrent", "ratio")
    if vals.get("gross_profit") and vals.get("revenue"):
        q("T2", f"What was {ticker}'s gross margin for fiscal year {fy}? "
                f"(gross profit / revenue, as a percentage)",
          100 * vals["gross_profit"] / vals["revenue"], "percent",
          "GrossProfit/Revenue", "ratio")
    if prev_vals and prev_vals.get("revenue") and vals.get("revenue"):
        q("T2", f"What was {ticker}'s year-over-year revenue growth from FY{fy - 1} "
                f"to FY{fy}? (as a percentage)",
          100 * (vals["revenue"] - prev_vals["revenue"]) / prev_vals["revenue"],
          "percent", "Revenue YoY", "percentage_change",
          context=render_two_year_snippet(vals, prev_vals, ticker, fy))

    # T3 — distractor / multi-hop (siblings present in snippet to mislead)
    if vals.get("liabilities_current"):
        q("T3", f"From {ticker}'s FY{fy} balance sheet, what were total CURRENT "
                f"liabilities (not total liabilities)? Answer in USD.",
          vals["liabilities_current"], "USD", "LiabilitiesCurrent (distractor: Liabilities)",
          "direct_retrieval")
    if prev_vals and prev_vals.get("assets") and vals.get("assets"):
        q("T3", f"By how much did {ticker}'s total assets change from FY{fy - 1} to "
                f"FY{fy}? Answer the dollar difference in USD (positive if increased).",
          vals["assets"] - prev_vals["assets"], "USD", "Assets delta", "multi_period_comparison",
          context=render_two_year_snippet(vals, prev_vals, ticker, fy))
    return qs


def generate(email: str, out_path=DEFAULT_QUESTIONS_PATH) -> list[dict]:
    """Build the full question bank and write it to `out_path`. Returns the
    list of question records written."""
    all_q: list[dict] = []
    for ticker, cik in COMPANIES.items():
        try:
            facts = fetch_companyfacts(cik, email)
        except Exception as e:
            print(f"[skip] {ticker}: {e}")
            continue
        by_year = {}
        for fy in YEARS + [min(YEARS) - 1]:  # include y-1 for growth/delta
            period_end = fiscal_year_end(facts, fy)
            if period_end is None:
                print(f"[warn] {ticker}: no FY{fy} 10-K found in companyfacts")
                continue
            vals = {}
            for name, cands in CONCEPTS.items():
                v, _ = annual_value(facts, cands, fy, period_end=period_end)
                vals[name] = v
            by_year[fy] = derive_total_liabilities(vals)
        for fy in YEARS:
            if fy not in by_year:
                continue
            all_q += make_questions(ticker, fy, by_year[fy], by_year.get(fy - 1))
        print(f"[ok] {ticker}: cumulative {len(all_q)} questions")
        time.sleep(0.2)  # be polite to SEC

    write_jsonl(out_path, all_q)
    print(f"\nWrote {len(all_q)} questions -> {out_path}")
    tiers: dict[str, int] = {}
    for question in all_q:
        tiers[question["tier"]] = tiers.get(question["tier"], 0) + 1
    print("By tier:", tiers)
    return all_q


def build_arg_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(
        description="Build the benchmark question bank from SEC XBRL.")
    parser.add_argument("--email", required=True,
                         help="your email -- SEC requires it in the User-Agent header")
    parser.add_argument("--out", default=str(DEFAULT_QUESTIONS_PATH))
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    generate(args.email, args.out)


if __name__ == "__main__":
    main()
