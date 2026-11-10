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
# "(In millions...)", "(millions, except per share amounts)", "(MILLIONS)",
# "Millions of dollars", "Dollars in millions" -- but not a shares row such
# as "Average shares outstanding (in millions of shares)".
_SCALE_PATTERN = re.compile(r"\b(thousands|millions|billions)\b(?!\s+of\s+shares)", re.IGNORECASE)

# Zero-width characters some filing tools put in empty cells and headings.
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"))


# A value cell: a comma-grouped number or a dollar sign. Bare years in the
# column headers ("2025") don't match.
_VALUE_CELL = re.compile(r"\d,\d{3}|\$")


def find_scale(*texts: str) -> str | None:
    """The first scale word ("thousands"/"millions"/"billions") in `texts`, lowercased."""
    for text in texts:
        m = _SCALE_PATTERN.search(text or "")
        if m:
            return m.group(1).lower()
    return None


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
    unit: str | None        # resolved measure, e.g. "USD", "USD/shares", "shares"
    decimals: str | None
    displayed: str          # the text as shown in the document
    scale: int = 0          # the ix `scale` attribute (6 = shown in millions)


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


# Elements that start a new line of text. Everything else (span, font, a, b,
# ix:*) is inline: filings style small caps as adjacent spans ("S" + "TATEMENTS"),
# so inline text must be joined with no separator at all.
_BLOCK_TAGS = {"div", "p", "br", "tr", "td", "th", "li", "h1", "h2", "h3", "h4", "h5", "h6",
               "table", "hr", "ul", "ol", "center", "section", "body"}


def _lines(el: ET.Element, skip_tables: bool = False) -> list[str]:
    """Visible text of `el` split into lines at block-element boundaries,
    whitespace-normalised, empty lines dropped."""
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        block = _local(node.tag) in _BLOCK_TAGS
        if block:
            parts.append("\n")
        parts.append(node.text or "")
        for child in node:
            if not (skip_tables and _local(child.tag) == "table"):
                walk(child)
            parts.append(child.tail or "")
        if block:
            parts.append("\n")

    walk(el)
    text = "".join(parts).translate(_INVISIBLE)
    return [" ".join(line.split()) for line in text.split("\n") if line.strip()]


def _cell_text(td: ET.Element) -> str:
    """A table cell's text, with line breaks inside the cell (a date split
    over two divs) turned into spaces."""
    return " ".join(_lines(td))


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


def _parse_units(root: ET.Element) -> dict[str, str]:
    """unit id -> readable measure. The id is the filer's choice ("usd",
    "U_USD", "Unit1"), so facts must be compared by measure, never by id.
    A divide unit becomes "numerator/denominator", e.g. "USD/shares"."""
    def measures(el: ET.Element | None) -> str:
        if el is None:
            return ""
        return "*".join((m.text or "").strip().split(":")[-1]
                        for m in el.iter(f"{{{XBRLI}}}measure"))

    units = {}
    for unit in root.iter(f"{{{XBRLI}}}unit"):
        divide = unit.find(f"{{{XBRLI}}}divide")
        if divide is not None:
            num = measures(divide.find(f"{{{XBRLI}}}unitNumerator"))
            den = measures(divide.find(f"{{{XBRLI}}}unitDenominator"))
            units[unit.get("id")] = f"{num}/{den}"
        else:
            units[unit.get("id")] = measures(unit)
    return units


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
    units = _parse_units(root)
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
            try:
                scale = int(el.get("scale") or 0)
            except ValueError:
                continue
            unit_ref = el.get("unitRef")
            fact = Fact(el.get("name", ""), value, el.get("contextRef", ""),
                        units.get(unit_ref, unit_ref), el.get("decimals"), displayed, scale)
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

# A table is classified by the dimensionless primary-statement concepts
# tagged in it. Each kind needs one fact from EVERY group, which keeps out
# note tables that share a single concept with a statement (a revenue
# disaggregation table tags Revenues; a parent-only schedule tags
# NetIncomeLoss but no EPS).
_STATEMENT_MARKERS = {
    "balance_sheet": (("Assets",), ("LiabilitiesAndStockholdersEquity", "LiabilitiesCurrent", "Liabilities")),
    "income_statement": (("NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"),
                         ("EarningsPerShareBasic", "EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted",
                          "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax")),
    "cash_flow": (("NetCashProvidedByUsedInOperatingActivities",
                   "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
                  ("NetCashProvidedByUsedInInvestingActivities",
                   "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations",
                   "NetCashProvidedByUsedInFinancingActivities")),
}

# Words that mark a heading line as a statement title.
_TITLE_PATTERN = re.compile(r"statements?\s+of|balance\s+sheets?|financial\s+condition|financial\s+position",
                            re.IGNORECASE)
_HEADING_SNIPPETS = 6       # how many preceding text chunks to look back through
_HEADING_MAX_CHARS = 400


@dataclass
class StatementTable:
    rows: list[list[str]]   # cleaned cell text, empty cells dropped
    facts: list[Fact]       # every numeric fact tagged inside the table
    kind: str | None        # "balance_sheet" | "income_statement" | "cash_flow" | None
    scale_label: str | None  # e.g. "millions", from "(In millions...)" in the table or its heading
    heading: str = ""       # text just above the table: statement title, "(In millions...)" line
    labels: list[str | None] = field(default_factory=list)  # row label of each fact, parallel to facts
    position: int = 0       # index among the document's tagged tables
    in_text: list[bool] = field(default_factory=list)  # fact sits inside label wording, parallel to facts

    def render(self) -> str:
        """Plain-text rendering for a prompt: one row per line, cells
        separated by " | ", with no XBRL markup."""
        return "\n".join(" | ".join(row) for row in self.rows if row)

    def render_with_heading(self) -> str:
        return f"{self.heading}\n{self.render()}" if self.heading else self.render()

    def label_of(self, fact: Fact) -> str | None:
        for f, label in zip(self.facts, self.labels, strict=True):
            if f is fact:
                return label
        return None


def _clean_row(cells: list[str]) -> list[str]:
    """Drop spacer cells and fold the separate "$", "%" and ")" cells 10-K
    tables use into their neighbours."""
    out: list[str] = []
    pending_prefix = ""
    for cell in cells:
        if not cell:
            continue
        if cell in ("$", "€", "£", "(", "$(", "($"):
            pending_prefix += cell  # "(" alone in its cell opens a negative number
            continue
        if cell in (")", "%", ")%") and out:
            out[-1] += cell
            continue
        out.append(pending_prefix + cell if pending_prefix else cell)
        pending_prefix = ""
    return out


def _classify(facts: list[Fact], contexts: dict[str, Context]) -> str | None:
    local = {f.concept.split(":", 1)[-1] for f in facts
             if (c := contexts.get(f.context_id)) is not None and not c.dimensions}
    for kind, groups in _STATEMENT_MARKERS.items():
        if all(any(m in local for m in group) for group in groups):
            return kind
    return None


def _heading(snippets: list[str]) -> str:
    """The statement title and scale line from the text chunks that precede
    a table: everything from the last title-looking chunk on, or failing
    that the last two chunks."""
    start = max(0, len(snippets) - 2)
    for i in range(len(snippets) - 1, -1, -1):
        if _TITLE_PATTERN.search(snippets[i]):
            start = i
            break
    text = "\n".join(snippets[start:])
    return text[-_HEADING_MAX_CHARS:]


def _table_rows(doc: IXBRLDocument, table: ET.Element):
    """(facts, labels, in_text flags, cleaned rows) for one XHTML table.

    A row's label is its first cell containing letters. A fact inside such a
    cell (a par value or share count tagged in "Common stock, $0.01 par
    value; 500 shares authorized") is flagged in_text: it is part of the
    label's wording, not one of the table's value columns."""
    facts, labels, in_text, rows = [], [], [], []
    for tr in table.iter():
        if _local(tr.tag) != "tr":
            continue
        cells = [td for td in tr if _local(td.tag) in ("td", "th")]
        texts = [_cell_text(td) for td in cells]
        label = next((t for t in texts if any(ch.isalpha() for ch in t)), None)
        for td, text in zip(cells, texts, strict=True):
            wordy = any(ch.isalpha() for ch in text)
            for x in td.iter():
                if id(x) in doc.tag_facts:
                    facts.append(doc.tag_facts[id(x)])
                    labels.append(label)
                    in_text.append(wordy)
        rows.append(_clean_row(texts))
    return facts, labels, in_text, [r for r in rows if r]


def statement_tables(doc: IXBRLDocument, min_facts: int = 3) -> list[StatementTable]:
    """Every XHTML table in the document carrying at least `min_facts`
    tagged numbers (outside label text), in document order."""
    tables: list[StatementTable] = []
    parts: list[str] = []  # visible text since the previous table, with "\n" at block boundaries

    def visit(table: ET.Element) -> None:
        facts, labels, in_text, rows = _table_rows(doc, table)
        if sum(not t for t in in_text) < min_facts:
            # Not a data table: a title or "(Dollars in millions)" line laid
            # out as a small table. Keep its text as part of the next heading.
            parts.append("\n" + "\n".join(" | ".join(r) for r in rows) + "\n")
            return
        text = "".join(parts).translate(_INVISIBLE)
        recent = [" ".join(line.split()) for line in text.split("\n") if line.strip()]
        parts.clear()
        heading = _heading(recent[-_HEADING_SNIPPETS:])
        # The scale line is in the heading or in the column-header rows above
        # the first number; rows below that may say "(in thousands)" about
        # share counts, so they are not searched.
        first_value_row = next((i for i, r in enumerate(rows) if any(_VALUE_CELL.search(c) for c in r[1:])), len(rows))
        header_text = "\n".join(" ".join(r) for r in rows[:first_value_row])
        tables.append(StatementTable(
            rows=rows, facts=facts, kind=_classify(facts, doc.contexts),
            scale_label=find_scale(heading, header_text),
            heading=heading, labels=labels, position=len(tables), in_text=in_text,
        ))

    def walk(node: ET.Element) -> None:
        name = _local(node.tag)
        if name == "header" and _ns(node.tag) in IX_NAMESPACES:
            return
        if name == "table":
            visit(node)
            return
        block = name in _BLOCK_TAGS
        if block:
            parts.append("\n")
        parts.append(node.text or "")
        for child in node:
            walk(child)
            parts.append(child.tail or "")
        if block:
            parts.append("\n")

    walk(doc.root)
    return tables


def _dimensionless_concepts(table: StatementTable, contexts: dict[str, Context]) -> set[str]:
    return {f.concept.split(":", 1)[-1] for f in table.facts
            if (c := contexts.get(f.context_id)) is not None and not c.dimensions}


def _merge(first: StatementTable, second: StatementTable, kind: str) -> StatementTable:
    return StatementTable(
        rows=first.rows + second.rows, facts=first.facts + second.facts, kind=kind,
        scale_label=first.scale_label if first.scale_label == second.scale_label else None,
        heading=first.heading, labels=first.labels + second.labels, position=first.position,
        in_text=first.in_text + second.in_text)


def primary_statements(doc: IXBRLDocument, tables: list[StatementTable] | None = None
                       ) -> dict[str, StatementTable]:
    """The first table of each statement kind, in document order (the
    primary statements come before the notes in a 10-K).

    A balance sheet split over a page break arrives as two tables, assets in
    one and liabilities and equity in the next; those are merged. If the two
    halves state different scales, the merged table has no scale_label and
    the caller should drop it."""
    tables = statement_tables(doc) if tables is None else tables
    out: dict[str, StatementTable] = {}
    for i, t in enumerate(tables):
        kind = t.kind
        if kind is None and i + 1 < len(tables) and "balance_sheet" not in out:
            here = _dimensionless_concepts(t, doc.contexts)
            nxt = _dimensionless_concepts(tables[i + 1], doc.contexts)
            if "Assets" in here and tables[i + 1].kind is None and \
                    nxt & {"LiabilitiesAndStockholdersEquity", "LiabilitiesCurrent", "Liabilities"}:
                t, kind = _merge(t, tables[i + 1], "balance_sheet"), "balance_sheet"
        if kind and kind not in out:
            out[kind] = t
    return out


def scale_exponent(label: str | None) -> int | None:
    """"millions" -> 6, etc."""
    return _SCALE_WORDS.get((label or "").lower())
