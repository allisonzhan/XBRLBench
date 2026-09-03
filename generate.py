#!/usr/bin/env python3
"""
generate.py — build the financial-reasoning eval question bank from SEC XBRL.

What it does:
  1. Pulls each company's structured financial facts from SEC's free
     companyfacts API (no key needed).
  2. Computes VERIFIED gold answers for three question tiers.
  3. Renders a MESSY human-readable statement snippet as the model input
     (scaled "in thousands", sibling line items as distractors) so the eval
     measures whether a model can bridge clean-truth vs messy-presentation.
  4. Writes questions.jsonl.

Ground truth comes from XBRL (machine-verified). The model never sees XBRL —
it sees the messy snippet. That gap is the thing you're measuring.

Run:  python3 generate.py --email you@vt.edu
Output: questions.jsonl  (one record per line)

v1 note: the messy input here is rendered from the same XBRL facts with
realistic presentation noise. The v2 upgrade — and the thing that makes this
fully legit — is to swap render_snippet() for the ACTUAL financial-statement
exhibit text from each 10-K. Do that once v1 works end to end.
"""

import argparse
import json
import time
import urllib.request

# ticker -> 10-digit zero-padded CIK. Expand this; mix clean and messy filers.
COMPANIES = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "KO":   "0000021344",
    "WMT":  "0000104169",
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
}

UA_TEMPLATE = "financial-eval-research {email}"


def fetch_companyfacts(cik, email):
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    req = urllib.request.Request(url, headers={"User-Agent": UA_TEMPLATE.format(email=email)})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def annual_value(facts, concept_candidates, fy):
    """Return the annual (FY, 10-K) USD value for the first matching concept."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for concept in concept_candidates:
        node = gaap.get(concept)
        if not node:
            continue
        usd = node.get("units", {}).get("USD", [])
        # annual figures: full-year period, 10-K form. For duration items keep
        # the longest period per FY; for instant items there's one 'end'.
        best = None
        for row in usd:
            if row.get("fy") != fy or "FY" != row.get("fp"):
                continue
            if not str(row.get("form", "")).startswith("10-K"):
                continue
            span = 999
            if row.get("start") and row.get("end"):
                span = _days(row["start"], row["end"])
                if span < 300:  # drop quarterly/partial
                    continue
            if best is None or span < best[0]:
                best = (span, row["val"], concept)
        if best:
            return best[1], best[2]
    return None, None


def _days(start, end):
    from datetime import date
    a = date.fromisoformat(start); b = date.fromisoformat(end)
    return (b - a).days


def render_snippet(vals, ticker, fy):
    """Messy human-readable statement fragment: scaled to thousands, distractors,
    no answer-labeled line. Forces the model to locate + scale + reason."""
    def k(x):  # display in thousands, rounded — model must multiply back by 1000
        return f"{round(x/1000):,}" if x is not None else "—"
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


def make_questions(ticker, fy, vals, prev_vals):
    qs = []
    def q(tier, question, gold_value, gold_unit, concept):
        if gold_value is None:
            return
        qs.append({
            "id": f"{ticker}-{fy}-{tier}-{len(qs)}",
            "ticker": ticker, "fiscal_year": fy, "tier": tier,
            "question": question,
            "gold_value": round(gold_value, 4),
            "gold_unit": gold_unit,
            "source_concept": concept,
            "context": render_snippet(vals, ticker, fy),
        })

    # T1 — single lookup (answer in raw dollars; snippet shows thousands → unit trap)
    q("T1", f"What were {ticker}'s total assets for fiscal year {fy}? Answer in USD.",
      vals.get("assets"), "USD", "Assets")
    q("T1", f"What was {ticker}'s net income for fiscal year {fy}? Answer in USD.",
      vals.get("net_income"), "USD", "NetIncomeLoss")

    # T2 — computation
    if vals.get("assets_current") and vals.get("liabilities_current"):
        q("T2", f"What was {ticker}'s current ratio for fiscal year {fy}? "
                f"(current assets / current liabilities, 2 decimals)",
          vals["assets_current"] / vals["liabilities_current"], "ratio",
          "AssetsCurrent/LiabilitiesCurrent")
    if vals.get("gross_profit") and vals.get("revenue"):
        q("T2", f"What was {ticker}'s gross margin for fiscal year {fy}? "
                f"(gross profit / revenue, as a percentage)",
          100 * vals["gross_profit"] / vals["revenue"], "percent",
          "GrossProfit/Revenue")
    if prev_vals and prev_vals.get("revenue") and vals.get("revenue"):
        q("T2", f"What was {ticker}'s year-over-year revenue growth from FY{fy-1} "
                f"to FY{fy}? (as a percentage)",
          100 * (vals["revenue"] - prev_vals["revenue"]) / prev_vals["revenue"],
          "percent", "Revenue YoY")

    # T3 — distractor / multi-hop (siblings present in snippet to mislead)
    if vals.get("liabilities_current"):
        q("T3", f"From {ticker}'s FY{fy} balance sheet, what were total CURRENT "
                f"liabilities (not total liabilities)? Answer in USD.",
          vals["liabilities_current"], "USD", "LiabilitiesCurrent (distractor: Liabilities)")
    if prev_vals and prev_vals.get("assets") and vals.get("assets"):
        q("T3", f"By how much did {ticker}'s total assets change from FY{fy-1} to "
                f"FY{fy}? Answer the dollar difference in USD (positive if increased).",
          vals["assets"] - prev_vals["assets"], "USD", "Assets delta")
    return qs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True, help="your email — SEC requires it in User-Agent")
    ap.add_argument("--out", default="questions.jsonl")
    args = ap.parse_args()

    all_q = []
    for ticker, cik in COMPANIES.items():
        try:
            facts = fetch_companyfacts(cik, args.email)
        except Exception as e:
            print(f"[skip] {ticker}: {e}")
            continue
        # pull all concepts for each year
        by_year = {}
        for fy in YEARS + [min(YEARS) - 1]:  # include y-1 for growth/delta
            vals = {}
            for name, cands in CONCEPTS.items():
                v, _ = annual_value(facts, cands, fy)
                vals[name] = v
            by_year[fy] = vals
        for fy in YEARS:
            all_q += make_questions(ticker, fy, by_year[fy], by_year.get(fy - 1))
        print(f"[ok] {ticker}: cumulative {len(all_q)} questions")
        time.sleep(0.2)  # be polite to SEC

    with open(args.out, "w") as f:
        for q in all_q:
            f.write(json.dumps(q) + "\n")
    print(f"\nWrote {len(all_q)} questions -> {args.out}")
    tiers = {}
    for q in all_q:
        tiers[q["tier"]] = tiers.get(q["tier"], 0) + 1
    print("By tier:", tiers)


if __name__ == "__main__":
    main()
