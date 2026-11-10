"""xbrlbench.ixbrl — read facts and statement tables out of an inline-XBRL
(iXBRL) 10-K, the actual HTML document a company files with the SEC.

This is the milestone-2 replacement for the synthetic snippet in
xbrlbench.generation: the model will see the filing's real statement table
(tags stripped, layout and "in millions" header kept), and the gold answer
comes from the XBRL tags embedded in that same table.

Stdlib only (xml.etree): iXBRL documents are required to be well-formed
XHTML, so no HTML parser dependency is needed. See docs/PRD.md (Decisions)
for why the parser is ours rather than a library's: truth extraction is the
core asset and has to be auditable.

Main entry points:
  parse_ixbrl(text) -> IXBRLDocument   contexts, numeric facts, dei facts
  statement_tables(doc) -> list[StatementTable]
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date

IX_NAMESPACES = (
    "http://www.xbrl.org/2013/inlineXBRL",  # iXBRL 1.1 (what EDGAR filings use)
    "http://www.xbrl.org/2008/inlineXBRL",  # iXBRL 1.0
)
XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
XSI = "http://www.w3.org/2001/XMLSchema-instance"

# Display text that means "zero" (iXBRL uses format ixt:fixed-zero for these).
_ZERO_TEXT = {"", "-", "—", "–", "‒"}

# Formats whose decimal separator is a comma (e.g. "1.234,5").
_COMMA_DECIMAL_FORMATS = ("numcommadecimal", "num-comma-decimal")

_SCALE_WORDS = {"thousands": 3, "millions": 6, "billions": 9}
_SCALE_PATTERN = re.compile(r"\bin\s+(thousands|millions|billions)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Context:
    id: str
    start: str | None  # ISO date; None for an instant
    end: str           # ISO date (the instant, or the period's end)
    dimensions: tuple[tuple[str, str], ...] = ()

    @property
    def is_instant(self) -> bool:
        return self.start is None

    @property
    def days(self) -> int | None:
        if self.start is None:
            return None
        return (date.fromisoformat(self.end) - date.fromisoformat(self.start)).days


@dataclass(frozen=True)
class Fact:
    concept: str            # e.g. "us-gaap:Assets"
    value: float | None     # fully scaled and signed; None if xsi:nil
    context_id: str
    unit: str | None
    decimals: str | None
    displayed: str          # the text as shown in the document


@dataclass
class IXBRLDocument:
    contexts: dict[str, Context]
    facts: list[Fact]
    dei: dict[str, str]     # e.g. {"DocumentFiscalYearFocus": "2024", ...}
    root: ET.Element = field(repr=False)
    tag_facts: dict[int, Fact] = field(default_factory=dict, repr=False)  # id(element) -> Fact

    def find(self, concept: str, *, end: str | None = None, instant: bool | None = None,
             min_days: int | None = None, max_days: int | None = None,
             dimensionless: bool = True) -> list[Fact]:
        """Facts for `concept` (with or without prefix) matching the period filters."""
        wanted = concept if ":" in concept else None
        out = []
        for f in self.facts:
            if wanted:
                if f.concept != wanted:
                    continue
            elif f.concept.split(":", 1)[-1] != concept:
                continue
            ctx = self.contexts.get(f.context_id)
            if ctx is None:
                continue
            if dimensionless and ctx.dimensions:
                continue
            if end is not None and ctx.end != end:
                continue
            if instant is not None and ctx.is_instant != instant:
                continue
            if ctx.days is not None:
                if min_days is not None and ctx.days < min_days:
                    continue
                if max_days is not None and ctx.days > max_days:
                    continue
            out.append(f)
        return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _ns(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _is_ix(el: ET.Element, name: str) -> bool:
    return _local(el.tag) == name and _ns(el.tag) in IX_NAMESPACES


def _text(el: ET.Element) -> str:
    return " ".join("".join(el.itertext()).split())


def parse_number(displayed: str, fmt: str | None = None, scale: str | None = None,
                 sign: str | None = None) -> float:
    """Turn an ix:nonFraction's displayed text into its XBRL value."""
    fmt = (fmt or "").lower()
    text = displayed.strip()
    if fmt.endswith("fixed-zero") or fmt.endswith("zerodash") or text in _ZERO_TEXT:
        number = 0.0
    else:
        text = text.replace(" ", "").replace(" ", "").strip("()$%")
        if any(f in fmt for f in _COMMA_DECIMAL_FORMATS):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
        number = float(text)
    number *= 10 ** int(scale or 0)
    if sign == "-":
        number = -number
    return number


def _parse_contexts(root: ET.Element) -> dict[str, Context]:
    contexts = {}
    for ctx in root.iter(f"{{{XBRLI}}}context"):
        period = ctx.find(f"{{{XBRLI}}}period")
        if period is None:
            continue
        instant = period.findtext(f"{{{XBRLI}}}instant")
        start = period.findtext(f"{{{XBRLI}}}startDate")
        end = period.findtext(f"{{{XBRLI}}}endDate")
        dims = tuple(sorted(
            (m.get("dimension", ""), (m.text or "").strip())
            for m in ctx.iter()
            if _local(m.tag) in ("explicitMember", "typedMember")
        ))
        if instant:
            contexts[ctx.get("id")] = Context(ctx.get("id"), None, instant.strip(), dims)
        elif start and end:
            contexts[ctx.get("id")] = Context(ctx.get("id"), start.strip(), end.strip(), dims)
    return contexts


def parse_ixbrl(text: str | bytes) -> IXBRLDocument:
    """Parse an iXBRL document. Raises ValueError if it isn't well-formed XML."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    # A few filings use HTML named entities that XML doesn't define.
    text = text.replace("&nbsp;", "&#160;")
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as e:
        raise ValueError(f"not well-formed iXBRL/XHTML: {e}") from e

    contexts = _parse_contexts(root)
    facts: list[Fact] = []
    tag_facts: dict[int, Fact] = {}
    dei: dict[str, str] = {}
    for el in root.iter():
        if _is_ix(el, "nonFraction"):
            displayed = _text(el)
            nil = el.get(f"{{{XSI}}}nil") == "true"
            try:
                value = None if nil else parse_number(
                    displayed, el.get("format"), el.get("scale"), el.get("sign"))
            except ValueError:
                continue  # unparseable display text; skip rather than guess
            fact = Fact(el.get("name", ""), value, el.get("contextRef", ""),
                        el.get("unitRef"), el.get("decimals"), displayed)
            facts.append(fact)
            tag_facts[id(el)] = fact
        elif _is_ix(el, "nonNumeric"):
            name = el.get("name", "")
            if name.startswith("dei:"):
                dei.setdefault(name.split(":", 1)[1], _text(el))
    return IXBRLDocument(contexts, facts, dei, root, tag_facts)


# ---------------------------------------------------------------------------
# Statement tables
# ---------------------------------------------------------------------------

# A table is classified by the primary-statement concepts tagged in it.
_STATEMENT_MARKERS = {
    "balance_sheet": ("Assets", "LiabilitiesAndStockholdersEquity"),
    "income_statement": ("NetIncomeLoss", "Revenues",
                         "RevenueFromContractWithCustomerExcludingAssessedTax", "EarningsPerShareBasic"),
    "cash_flow": ("NetCashProvidedByUsedInOperatingActivities",
                  "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect"),
}


@dataclass
class StatementTable:
    rows: list[list[str]]   # cleaned cell text, empty cells dropped
    facts: list[Fact]       # every numeric fact tagged inside the table
    kind: str | None        # "balance_sheet" | "income_statement" | "cash_flow" | None
    scale_label: str | None  # e.g. "millions", from an "(In millions...)" header in the table

    def render(self) -> str:
        """Plain-text rendering for a prompt: one row per line, cells
        separated by " | ", with no XBRL markup."""
        return "\n".join(" | ".join(row) for row in self.rows if row)


def _clean_row(cells: list[str]) -> list[str]:
    """Drop spacer cells and fold the separate "$", "%" and ")" cells 10-K
    tables use into their neighbours."""
    out: list[str] = []
    pending_prefix = ""
    for cell in cells:
        if not cell:
            continue
        if cell in ("$", "€", "£"):
            pending_prefix = cell
            continue
        if cell in (")", "%", ")%") and out:
            out[-1] += cell
            continue
        out.append(pending_prefix + cell if pending_prefix else cell)
        pending_prefix = ""
    return out


def _classify(facts: list[Fact]) -> str | None:
    local = {f.concept.split(":", 1)[-1] for f in facts}
    for kind, markers in _STATEMENT_MARKERS.items():
        if any(m in local for m in markers):
            return kind
    return None


def statement_tables(doc: IXBRLDocument, min_facts: int = 3) -> list[StatementTable]:
    """Every XHTML table in the document carrying at least `min_facts`
    tagged numbers, in document order."""
    tables = []
    for table in doc.root.iter():
        if _local(table.tag) != "table":
            continue
        facts = [doc.tag_facts[id(el)] for el in table.iter() if id(el) in doc.tag_facts]
        if len(facts) < min_facts:
            continue
        rows = []
        for tr in table.iter():
            if _local(tr.tag) != "tr":
                continue
            cells = [_text(td) for td in tr if _local(td.tag) in ("td", "th")]
            rows.append(_clean_row(cells))
        scale = _SCALE_PATTERN.search(_text(table))
        tables.append(StatementTable(
            rows=[r for r in rows if r],
            facts=facts,
            kind=_classify(facts),
            scale_label=scale.group(1).lower() if scale else None,
        ))
    return tables


def scale_exponent(label: str | None) -> int | None:
    """"millions" -> 6, etc."""
    return _SCALE_WORDS.get((label or "").lower())
