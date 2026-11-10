"""xbrlbench.spotcheck — check gold values against the actual filed 10-K.

Gold values come from SEC's companyfacts API, which is derived from filings
but is one processing step removed from them. This command takes a sample of
single-concept questions, downloads each one's 10-K from EDGAR, reads the
XBRL tag for the same concept and period straight out of the inline-XBRL
document (xbrlbench.ixbrl), and compares.

Run:  python -m xbrlbench spotcheck --email you@example.com [--n 10]

Exits non-zero if any sampled question's gold value disagrees with the
filing. Needs network access to www.sec.gov and data.sec.gov.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

from . import edgar, ixbrl
from .generation import COMPANIES
from .io_utils import load_jsonl
from .paths import DEFAULT_QUESTIONS_PATH

# source_concept -> (us-gaap concept, is_instant). Only questions whose gold
# value is a single tagged fact can be checked directly against the filing.
CHECKABLE = {
    "Assets": ("Assets", True),
    "NetIncomeLoss": ("NetIncomeLoss", False),
    "LiabilitiesCurrent (distractor: Liabilities)": ("LiabilitiesCurrent", True),
}

ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS = 350, 380


@dataclass
class SpotCheck:
    id: str
    ticker: str
    fiscal_year: int
    concept: str
    gold_value: float
    filed_value: float | None = None
    period_end: str | None = None
    filing_url: str | None = None
    status: str = "not_checked"   # match | mismatch | not_found | error
    note: str = ""


def sample_questions(rows: list[dict], n: int) -> list[dict]:
    """A deterministic, evenly spread sample of checkable questions."""
    checkable = sorted((r for r in rows if r.get("source_concept") in CHECKABLE), key=lambda r: r["id"])
    if n >= len(checkable):
        return checkable
    step = len(checkable) / n
    return [checkable[int(i * step)] for i in range(n)]


def current_period_fact(doc: ixbrl.IXBRLDocument, concept: str, instant: bool) -> ixbrl.Fact | None:
    """The fact for the document's own (latest) annual period."""
    if instant:
        facts = doc.find(concept, instant=True)
    else:
        facts = doc.find(concept, instant=False, min_days=ANNUAL_MIN_DAYS, max_days=ANNUAL_MAX_DAYS)
    facts = [f for f in facts if f.value is not None]
    if not facts:
        return None
    return max(facts, key=lambda f: doc.contexts[f.context_id].end)


def values_match(gold: float, filed: float, decimals: str | None) -> bool:
    """Equal to within the filed fact's stated rounding (e.g. decimals=-6 -> $0.5M)."""
    try:
        half_unit = 0.5 * 10 ** (-int(decimals)) if decimals not in (None, "INF") else 0.5
    except ValueError:
        half_unit = 0.5
    return abs(gold - filed) <= max(half_unit, 0.5)


def check_question(q: dict, email: str, submissions_cache: dict) -> SpotCheck:
    concept, instant = CHECKABLE[q["source_concept"]]
    result = SpotCheck(q["id"], q["ticker"], q["fiscal_year"], concept, q["gold_value"])
    cik = COMPANIES.get(q["ticker"])
    if cik is None:
        result.status, result.note = "error", "ticker not in generation.COMPANIES"
        return result
    if cik not in submissions_cache:
        submissions_cache[cik] = edgar.get_submissions(cik, email)
    refs = edgar.candidates_for_fiscal_year(edgar.annual_filings(submissions_cache[cik], cik), q["fiscal_year"])
    if not refs:
        result.status, result.note = "not_found", "no candidate 10-K in EDGAR submissions"
        return result

    for ref in refs:
        doc = ixbrl.parse_ixbrl(edgar.fetch(ref.url, email))
        focus = doc.dei.get("DocumentFiscalYearFocus")
        if focus and focus.strip() != str(q["fiscal_year"]):
            continue  # a neighbouring year's 10-K
        fact = current_period_fact(doc, concept, instant)
        result.filing_url = ref.url
        if fact is None:
            result.status, result.note = "not_found", f"no {concept} fact for the current annual period"
            return result
        result.filed_value = fact.value
        result.period_end = doc.contexts[fact.context_id].end
        result.status = "match" if values_match(q["gold_value"], fact.value, fact.decimals) else "mismatch"
        return result

    result.status, result.note = "not_found", "no 10-K with a matching DocumentFiscalYearFocus"
    return result


def spotcheck(email: str, n: int = 10, questions_path=DEFAULT_QUESTIONS_PATH, out: str | None = None) -> list[SpotCheck]:
    rows = load_jsonl(questions_path)
    cache: dict = {}
    results = []
    for q in sample_questions(rows, n):
        try:
            r = check_question(q, email, cache)
        except Exception as e:  # report and keep going; one bad fetch shouldn't hide the rest
            r = SpotCheck(q["id"], q["ticker"], q["fiscal_year"], CHECKABLE[q["source_concept"]][0],
                          q["gold_value"], status="error", note=f"{type(e).__name__}: {e}")
        results.append(r)
        print(f"{r.status:9} {r.id:18} gold={r.gold_value:>18,.0f} "
              f"filed={'' if r.filed_value is None else f'{r.filed_value:,.0f}':>18} {r.note}")
    counts = {s: sum(r.status == s for r in results) for s in ("match", "mismatch", "not_found", "error")}
    print(f"\n{len(results)} checked: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump([asdict(r) for r in results], f, indent=2)
        print(f"Wrote {out}")
    return results


def build_arg_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(description="Check gold values against the filed 10-Ks.")
    parser.add_argument("--email", required=True, help="contact email -- SEC requires it in the User-Agent")
    parser.add_argument("--n", type=int, default=10, help="number of questions to sample (default 10)")
    parser.add_argument("--in", dest="inp", default=str(DEFAULT_QUESTIONS_PATH))
    parser.add_argument("--out", default=None, help="optional JSON file for the per-question results")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    results = spotcheck(args.email, args.n, args.inp, args.out)
    if any(r.status != "match" for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
