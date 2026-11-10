"""xbrlbench.filings — questions about REAL 10-K statement tables.

Milestone 2 (docs/PRD.md). For each filed inline-XBRL 10-K:

  1. Find the primary statements (income statement, balance sheet, cash flow)
     with xbrlbench.ixbrl.
  2. Read gold values from the XBRL tags inside those same tables, so the
     answer is exactly what the filing shows the model.
  3. Render the needed table(s) as plain text, tags stripped, with the
     filing's own title and "(In millions...)" line, as the question context.

Questions name line items by the filing's own row label (e.g. "Total net
sales") rather than a generic name ("revenue"), so every question has
exactly one right answer whatever the filer calls the line. Formulas are
stated in the question.

Safety rules (a question is dropped, never guessed, when one fails):
  - every USD fact in a table must carry the same ix `scale`, and the scale
    stated in the table or its heading must agree with it;
  - the row label used in a question must be unique within its table;
  - a concept must resolve to exactly one value for the period.

`build_filing_questions(doc, meta)` is pure (no network) and is what the
tests exercise. `build_corpus(...)` walks EDGAR, downloads filings and calls
it for each; that part needs data.sec.gov and www.sec.gov.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date

from . import edgar, ixbrl
from .grading import DEFAULT_TOLERANCE

# Held-out split (PRD, Decisions): company-disjoint, and filed on or after the
# cutoff. A company is in the held-out group if a stable hash of its CIK says
# so; its filings count only if filed on/after the cutoff. Train companies'
# filings count only if filed before it. Everything else is unused.
HELDOUT_CUTOFF = "2026-07-01"
HELDOUT_FRACTION = 0.2
_SPLIT_SALT = "xbrlbench-split-v1"

# SIC codes skipped when sampling companies: blank-check shells (SPACs)
# carry almost no operating statements.
SKIP_SIC = {"6770"}

ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS = 350, 380

# name -> (statement kind, us-gaap concepts in order of preference)
LINE_ITEMS: dict[str, tuple[str, tuple[str, ...]]] = {
    "revenue": ("income_statement", ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                                     "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet")),
    "cost_of_revenue": ("income_statement", ("CostOfRevenue", "CostOfGoodsAndServicesSold")),
    "gross_profit": ("income_statement", ("GrossProfit",)),
    "operating_income": ("income_statement", ("OperatingIncomeLoss",)),
    "net_income": ("income_statement", ("NetIncomeLoss",)),
    "assets": ("balance_sheet", ("Assets",)),
    "assets_current": ("balance_sheet", ("AssetsCurrent",)),
    "liabilities_current": ("balance_sheet", ("LiabilitiesCurrent",)),
    "liabilities": ("balance_sheet", ("Liabilities",)),
    "equity": ("balance_sheet", ("StockholdersEquity",)),
    "operating_cash_flow": ("cash_flow", ("NetCashProvidedByUsedInOperatingActivities",)),
    "capex": ("cash_flow", ("PaymentsToAcquirePropertyPlantAndEquipment",)),
}

STATEMENT_ORDER = ("income_statement", "balance_sheet", "cash_flow")

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")


def long_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{_MONTHS[d.month - 1]} {d.day}, {d.year}"


# ---------------------------------------------------------------------------
# Splits
# ---------------------------------------------------------------------------

def company_group(cik: str | int) -> str:
    """"test" for the held-out company group, else "train". Stable across runs."""
    digest = hashlib.sha256(f"{_SPLIT_SALT}:{int(cik)}".encode()).digest()
    return "test" if digest[0] / 256 < HELDOUT_FRACTION else "train"


def assign_split(cik: str | int, filing_date: str) -> str | None:
    group = company_group(cik)
    if group == "test":
        return "test" if filing_date >= HELDOUT_CUTOFF else None
    return "train" if filing_date < HELDOUT_CUTOFF else None


# ---------------------------------------------------------------------------
# Reading values out of a statement table
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Item:
    name: str
    concept: str
    value: float
    label: str
    end: str


@dataclass
class FilingMeta:
    cik: str
    ticker: str = ""
    company: str = ""
    form: str = "10-K"
    accession: str = ""
    filing_date: str = ""
    sic: str = ""
    url: str = ""


@dataclass
class FilingReport:
    accession: str
    cik: str
    status: str = "ok"     # ok | not_ixbrl | no_statements | no_questions | error
    questions: int = 0
    statements: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)   # why tables/questions were left out
    note: str = ""


def table_scale_problem(table: ixbrl.StatementTable) -> str | None:
    """None if the table's stated scale matches its USD facts' ix scale."""
    scales = {f.scale for f in table.facts if f.unit == "USD"}
    if not scales:
        return "no USD facts"
    if len(scales) > 1:
        return f"mixed USD scales {sorted(scales)}"
    [scale] = scales
    stated = ixbrl.scale_exponent(table.scale_label)
    if stated is None:
        return None if scale == 0 else f"tagged scale {scale} but no '(In thousands/millions)' line"
    if stated != scale:
        return f"stated {table.scale_label} but tagged scale {scale}"
    return None


def _unique_labels(table: ixbrl.StatementTable) -> set[str]:
    counts = Counter(row[0] for row in table.rows if row)
    return {label for label, n in counts.items() if n == 1}


def _is_annual(ctx: ixbrl.Context) -> bool:
    return ctx.is_instant or ANNUAL_MIN_DAYS <= (ctx.days or 0) <= ANNUAL_MAX_DAYS


def find_item(doc: ixbrl.IXBRLDocument, table: ixbrl.StatementTable, name: str, end: str) -> Item | None:
    """The value of line item `name` for the period ending `end`, read from
    `table`. None if absent, ambiguous, or its row label isn't unique."""
    _, concepts = LINE_ITEMS[name]
    unique = _unique_labels(table)
    for concept in concepts:
        hits = []
        for fact, label in zip(table.facts, table.labels, strict=True):
            if fact.concept.split(":", 1)[-1] != concept or fact.value is None or fact.unit != "USD":
                continue
            ctx = doc.contexts.get(fact.context_id)
            if ctx is None or ctx.dimensions or ctx.end != end or not _is_annual(ctx):
                continue
            hits.append((fact, label))
        if not hits:
            continue
        values = {f.value for f, _ in hits}
        labels = {lbl for _, lbl in hits}
        if len(values) != 1 or len(labels) != 1:
            return None  # same concept on two rows with different numbers: ambiguous
        [label] = labels
        if not label or label not in unique:
            return None
        fact = hits[0][0]
        return Item(name, fact.concept, fact.value, label, end)
    return None


def annual_ends(doc: ixbrl.IXBRLDocument, table: ixbrl.StatementTable) -> list[str]:
    """Distinct period ends of the table's dimensionless annual USD facts, newest first."""
    ends = set()
    for f in table.facts:
        ctx = doc.contexts.get(f.context_id)
        if f.unit == "USD" and ctx is not None and not ctx.dimensions and _is_annual(ctx):
            ends.add(ctx.end)
    return sorted(ends, reverse=True)


# ---------------------------------------------------------------------------
# Question templates
# ---------------------------------------------------------------------------

def _q(label: str) -> str:
    return f'"{label}"'


def build_filing_questions(doc: ixbrl.IXBRLDocument, meta: FilingMeta) -> tuple[list[dict], FilingReport]:
    report = FilingReport(meta.accession, meta.cik)
    statements = ixbrl.primary_statements(doc)
    usable: dict[str, ixbrl.StatementTable] = {}
    for kind, table in statements.items():
        problem = table_scale_problem(table)
        if problem:
            report.dropped.append(f"{kind}: {problem}")
        else:
            usable[kind] = table
    report.statements = sorted(usable)
    if not usable:
        report.status = "no_statements"
        return [], report

    # The filing's own fiscal period: the latest annual end on the income
    # statement (or whichever statement we have).
    anchor = usable.get("income_statement") or usable.get("cash_flow") or usable.get("balance_sheet")
    ends = annual_ends(doc, anchor)
    if not ends:
        report.status, report.note = "no_statements", "no annual periods"
        return [], report
    cur = ends[0]
    prev = ends[1] if len(ends) > 1 else None

    company = doc.dei.get("EntityRegistrantName") or meta.company or meta.ticker
    ticker = (meta.ticker or doc.dei.get("TradingSymbol") or meta.cik).upper()
    fiscal_year = int(doc.dei.get("DocumentFiscalYearFocus") or cur[:4])
    header = f"{company} — Form {meta.form} for the fiscal year ended {long_date(cur)}"

    def get(name: str, end: str | None) -> Item | None:
        kind = LINE_ITEMS[name][0]
        if end is None or kind not in usable:
            return None
        return find_item(doc, usable[kind], name, end)

    def context(*kinds: str) -> str:
        parts = [header] + [usable[k].render_with_heading() for k in STATEMENT_ORDER if k in kinds]
        return "\n\n".join(parts)

    questions: list[dict] = []

    def add(slug, tier, reasoning, question, gold, unit, source, kinds):
        questions.append({
            "id": f"{ticker}-{fiscal_year}-{slug}",
            "ticker": ticker,
            "fiscal_year": fiscal_year,
            "tier": tier,
            "question": question,
            "gold_value": round(gold, 4),
            "gold_unit": unit,
            "source_concept": source,
            "context": context(*kinds),
            "reasoning_type": reasoning,
            "tolerance": DEFAULT_TOLERANCE,
            "family": slug,
            "cik": meta.cik,
            "company": company,
            "form": meta.form,
            "accession": meta.accession,
            "filing_date": meta.filing_date,
            "period_end": cur,
            "sic": meta.sic,
            "split": assign_split(meta.cik, meta.filing_date) if meta.filing_date else None,
        })

    when = long_date(cur)

    # --- T1: single lookups --------------------------------------------------
    for name in ("revenue", "net_income", "assets", "liabilities_current", "operating_cash_flow"):
        it = get(name, cur)
        if it is None:
            continue
        kind = LINE_ITEMS[name][0]
        period = f"as of {when}" if kind == "balance_sheet" else f"for the fiscal year ended {when}"
        add(f"lookup_{name}", "T1", "direct_retrieval",
            f"According to {company}'s {meta.form}, what was {_q(it.label)} {period}? Answer in USD.",
            it.value, "USD", it.concept, [kind])

    # --- T2: ratios and margins ----------------------------------------------
    ca, cl = get("assets_current", cur), get("liabilities_current", cur)
    if ca and cl and cl.value > 0:
        add("current_ratio", "T2", "ratio",
            f"What was {company}'s current ratio as of {when}, computed as {_q(ca.label)} divided by "
            f"{_q(cl.label)}? Answer as a ratio with 2 decimals.",
            ca.value / cl.value, "ratio", f"{ca.concept}/{cl.concept}", ["balance_sheet"])

    rev = get("revenue", cur)
    if rev and rev.value > 0:
        gp = get("gross_profit", cur)
        if gp:
            add("gross_margin", "T2", "ratio",
                f"What was {company}'s gross margin for the fiscal year ended {when}, computed as "
                f"{_q(gp.label)} divided by {_q(rev.label)}? Answer as a percentage.",
                100 * gp.value / rev.value, "percent", f"{gp.concept}/{rev.concept}", ["income_statement"])
        oi = get("operating_income", cur)
        if oi:
            add("operating_margin", "T2", "ratio",
                f"What was {company}'s operating margin for the fiscal year ended {when}, computed as "
                f"{_q(oi.label)} divided by {_q(rev.label)}? Answer as a percentage.",
                100 * oi.value / rev.value, "percent", f"{oi.concept}/{rev.concept}", ["income_statement"])

    # --- T2: year-over-year change -------------------------------------------
    rev_prev = get("revenue", prev)
    if rev and rev_prev and rev_prev.value > 0 and rev_prev.label == rev.label:
        add("revenue_growth", "T2", "percentage_change",
            f"By what percentage did {company}'s {_q(rev.label)} change from the fiscal year ended "
            f"{long_date(prev)} to the fiscal year ended {when}? Answer as a percentage "
            f"(negative if it decreased).",
            100 * (rev.value - rev_prev.value) / rev_prev.value, "percent",
            f"{rev.concept} YoY", ["income_statement"])

    # --- T3: multi-period and derived ----------------------------------------
    ni, ni_prev = get("net_income", cur), get("net_income", prev)
    if ni and ni_prev and ni.label == ni_prev.label and ni.value != ni_prev.value:
        add("net_income_change", "T3", "multi_period_comparison",
            f"By how much did {company}'s {_q(ni.label)} change from the fiscal year ended "
            f"{long_date(prev)} to the fiscal year ended {when}? Answer the difference in USD "
            f"(positive if it increased, negative if it decreased).",
            ni.value - ni_prev.value, "USD", f"{ni.concept} delta", ["income_statement"])

    ocf, capex = get("operating_cash_flow", cur), get("capex", cur)
    if ocf and capex and capex.value >= 0:
        add("free_cash_flow", "T3", "derived_metric",
            f"What was {company}'s free cash flow for the fiscal year ended {when}, defined as "
            f"{_q(ocf.label)} minus {_q(capex.label)} (capital expenditures, as a positive amount)? "
            f"Answer in USD.",
            ocf.value - capex.value, "USD", f"{ocf.concept}-{capex.concept}", ["cash_flow"])

    report.questions = len(questions)
    if not questions:
        report.status = "no_questions"
    return questions, report


# ---------------------------------------------------------------------------
# Corpus build (network)
# ---------------------------------------------------------------------------

def spread_sample(items: list, k: int) -> list:
    """k items at evenly spaced positions (deterministic). SEC's ticker list
    is ordered roughly by market value, so this spreads across company sizes."""
    if k >= len(items):
        return list(items)
    step = len(items) / k
    return [items[int(i * step)] for i in range(k)]


def interleaved_order(items: list) -> list:
    """All items, ordered so that any prefix is spread across the list:
    positions 0, n/2, n/4, 3n/4, ... (bit-reversal style). Lets the corpus
    build stop at any company count and still cover every size band."""
    n = len(items)
    order, seen, denom = [], set(), 1
    while len(order) < n:
        for num in range(denom):
            i = (num * n) // denom
            if i not in seen:
                seen.add(i)
                order.append(i)
        denom *= 2
    return [items[i] for i in order]


def _filings_for_split(refs: list[edgar.FilingRef], cik: str, per_company: int, since: str,
                       any_split: bool = False) -> list[edgar.FilingRef]:
    refs = [r for r in refs if r.filing_date >= since and r.primary_document.lower().endswith((".htm", ".html"))]
    keep = refs if any_split else [r for r in refs if assign_split(cik, r.filing_date) is not None]
    return keep[:per_company]  # refs are newest first


def _read_done(log_path: str) -> set[str]:
    done = set()
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    done.add(json.loads(line).get("accession"))
    return done


def build_corpus(email: str, out: str, n_companies: int = 500, per_company: int = 2,
                 since: str = "2021-01-01", keep_failures: str | None = None,
                 max_attempts: int | None = None, tickers: list[str] | None = None) -> Counter:
    """Download 10-Ks for up to `n_companies` companies that yield questions
    and append the questions to `out` (JSONL). Every filing tried is logged
    to `out + ".log.jsonl"` with its status, which is also the resume point:
    re-running skips logged accessions and counts their companies.

    `tickers` restricts the build to those companies and takes their newest
    10-Ks whatever their split (split is then null for filings outside it):
    a quick trial on chosen filers before a full run."""
    log_path = out + ".log.jsonl"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    done = _read_done(log_path)
    companies = edgar.company_list(email)
    if tickers:
        wanted = {t.upper() for t in tickers}
        companies = [c for c in companies if c["ticker"].upper() in wanted]
        missing = wanted - {c["ticker"].upper() for c in companies}
        if missing:
            print(f"[warn] not in SEC's ticker list: {sorted(missing)}")
    ok_companies: set[str] = set()
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                if line.strip() and (rec := json.loads(line)).get("status") == "ok":
                    ok_companies.add(rec["cik"])

    stats: Counter = Counter()
    attempts = 0
    max_attempts = max_attempts or 4 * n_companies
    with open(out, "a", encoding="utf-8") as qf, open(log_path, "a", encoding="utf-8") as lf:
        for comp in (companies if tickers else interleaved_order(companies)):
            if len(ok_companies) >= n_companies or attempts >= max_attempts:
                break
            cik = comp["cik"]
            if cik in ok_companies:
                continue
            attempts += 1
            try:
                subs = edgar.get_submissions(cik, email, since=since)
            except Exception as e:  # keep going; one company shouldn't stop the build
                stats["submissions_error"] += 1
                print(f"[error] {comp['ticker']}: submissions: {type(e).__name__}: {e}")
                continue
            sic = str(subs.get("sic") or "")
            if sic in SKIP_SIC:
                stats["skipped_sic"] += 1
                continue
            refs = _filings_for_split(edgar.annual_filings(subs, cik), cik, per_company, since,
                                      any_split=bool(tickers))
            if not refs:
                stats["no_filings"] += 1
                continue
            for ref in refs:
                if ref.accession in done:
                    continue
                meta = FilingMeta(cik=cik, ticker=comp["ticker"], company=subs.get("name") or comp["title"],
                                  form=ref.form, accession=ref.accession, filing_date=ref.filing_date,
                                  sic=sic, url=ref.url)
                report = FilingReport(ref.accession, cik)
                raw = b""
                try:
                    raw = edgar.fetch(ref.url, email)
                    doc = ixbrl.parse_ixbrl(raw)
                    qs, report = build_filing_questions(doc, meta)
                except ValueError as e:
                    report.status, report.note, qs = "not_ixbrl", str(e)[:200], []
                except Exception as e:
                    report.status, report.note, qs = "error", f"{type(e).__name__}: {e}"[:200], []
                for q in qs:
                    qf.write(json.dumps(q, ensure_ascii=False) + "\n")
                rec = asdict(report) | {"ticker": meta.ticker, "filing_date": meta.filing_date,
                                        "sic": sic, "url": ref.url}
                lf.write(json.dumps(rec) + "\n")
                qf.flush()
                lf.flush()
                stats[report.status] += 1
                stats["questions"] += report.questions
                if report.status == "ok":
                    ok_companies.add(cik)
                elif keep_failures and raw:
                    os.makedirs(keep_failures, exist_ok=True)
                    with open(os.path.join(keep_failures, f"{ref.accession}.htm"), "wb") as f:
                        f.write(raw)
                print(f"{report.status:13} {meta.ticker:6} {ref.filing_date} {report.questions:2}q "
                      f"{','.join(report.statements)} {'; '.join(report.dropped)} {report.note}")
    stats["companies_ok"] = len(ok_companies)
    print("\n" + ", ".join(f"{k}={v}" for k, v in sorted(stats.items())))
    return stats


def summarize(path: str) -> dict:
    """Counts by split, family and statement status for a built corpus (offline)."""
    if not os.path.exists(path):
        return {"questions": 0}
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return {
        "questions": len(rows),
        "companies": len({r["cik"] for r in rows}),
        "filings": len({r["accession"] for r in rows}),
        "by_split": dict(Counter(str(r.get("split")) for r in rows)),
        "by_family": dict(Counter(r.get("family") for r in rows)),
    }


def build_arg_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(description="Build questions from real 10-K filings.")
    parser.add_argument("--email", required=True, help="contact email -- SEC requires it in the User-Agent")
    parser.add_argument("--out", default="data/filings/questions.jsonl")
    parser.add_argument("--companies", type=int, default=500, help="stop after this many companies yield questions")
    parser.add_argument("--per-company", type=int, default=2, help="10-Ks per company (newest in its split)")
    parser.add_argument("--since", default="2021-01-01", help="ignore filings before this date")
    parser.add_argument("--keep-failures", default=None, help="directory to save filings that failed, for debugging")
    parser.add_argument("--tickers", default=None,
                        help="comma-separated tickers for a trial run (newest 10-Ks, any split)")
    parser.add_argument("--max-attempts", type=int, default=None, help="companies to try (default 4x --companies)")
    return parser


def run(args: argparse.Namespace) -> None:
    build_corpus(args.email, args.out, args.companies, args.per_company, args.since,
                 args.keep_failures, args.max_attempts,
                 args.tickers.split(",") if args.tickers else None)
    print(json.dumps(summarize(args.out), indent=2))


def main(argv: list[str] | None = None) -> None:
    run(build_arg_parser().parse_args(argv))


if __name__ == "__main__":
    main()
