"""xbrlbench.audit — re-derive real-filing gold values from the context text.

The gold value of a real-filing question (xbrlbench.filings) comes from the
XBRL tags. The model sees only the rendered table text. This check works
from that text, as a reader would, and confirms the two agree:

  1. each input's row (by its quoted label) is in the context and shows the
     tagged number in one of its columns;
  2. that number times the scale the context states ("In millions") equals
     the tagged value;
  3. the question's formula applied to the inputs gives gold_value.

It is the automated half of the PRD's "1,000 sampled tasks above 99%
correct" exit criterion; a person still reads a small sample. Offline.

Run:  python -m xbrlbench audit --in data/filings/questions.jsonl [--n 1000]
"""

from __future__ import annotations

import argparse
import random
import re
from collections import Counter

from .io_utils import load_jsonl
from .ixbrl import find_scale, scale_exponent

_FORMULAS = {
    "current_ratio": lambda a, b: a / b,
    "gross_margin": lambda a, b: 100 * a / b,
    "operating_margin": lambda a, b: 100 * a / b,
    "revenue_growth": lambda a, b: 100 * (a - b) / b,
    "net_income_change": lambda a, b: a - b,
    "free_cash_flow": lambda a, b: a - b,
}


def _digits(text: str) -> str:
    return re.sub(r"[^\d.]", "", text)


def _find_row(context: str, label: str) -> tuple[str, str] | None:
    """(text of the context before the row, the row) for the first line
    whose first cell is `label`."""
    pos = 0
    for line in context.split("\n"):
        if line == label or line.startswith(label + " | "):
            return context[:pos], line
        pos += len(line) + 1
    return None


def _block_before(text: str) -> str:
    """The statement block (context sections are separated by blank lines)
    that ends at the end of `text`."""
    return text.rsplit("\n\n", 1)[-1]


def audit_question(q: dict) -> list[str]:
    problems = []
    inputs = q.get("inputs") or []
    if not inputs:
        return ["no inputs recorded"]
    for inp in inputs:
        found = _find_row(q["context"], inp["label"])
        if found is None:
            problems.append(f"row {inp['label']!r} not in context")
            continue
        before, row = found
        target = _digits(inp["displayed"])
        cells = row.split(" | ")[1:]
        if not any(_digits(c) == target for c in cells):
            problems.append(f"{inp['displayed']!r} not shown in row {row!r}")
            continue
        exp = scale_exponent(find_scale(_block_before(before))) or 0
        shown = float(target or 0) * 10 ** exp
        if abs(abs(inp["value"]) - shown) > 1e-6 * max(1.0, shown):
            problems.append(f"{inp['label']!r}: shown {inp['displayed']} x 1e{exp} != tagged {inp['value']}")
    if problems:
        return problems
    values = [i["value"] for i in inputs]
    family = q.get("family", "")
    if family.startswith("lookup_"):
        expected = values[0]
    elif family in _FORMULAS and len(values) == 2:
        expected = _FORMULAS[family](*values)
    else:
        return [f"no formula for family {family!r}"]
    if abs(round(expected, 4) - q["gold_value"]) > 1e-4 * max(1.0, abs(expected)):
        problems.append(f"formula gives {expected}, gold_value is {q['gold_value']}")
    return problems


def audit(path: str, n: int | None = None, seed: int = 0) -> tuple[int, list[tuple[dict, list[str]]]]:
    rows = load_jsonl(path)
    if n is not None and n < len(rows):
        rows = random.Random(seed).sample(rows, n)
    failures = [(q, p) for q in rows if (p := audit_question(q))]
    return len(rows), failures


def build_arg_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(description="Re-derive real-filing gold values from the context.")
    parser.add_argument("--in", dest="inp", default="data/filings/questions.jsonl")
    parser.add_argument("--n", type=int, default=None, help="audit a random sample of this many (default: all)")
    parser.add_argument("--seed", type=int, default=0)
    return parser


def run(args: argparse.Namespace) -> bool:
    total, failures = audit(args.inp, args.n, args.seed)
    for q, problems in failures:
        print(f"FAIL {q['id']}: {'; '.join(problems)}")
    by_family = Counter(q.get("family") for q, _ in failures)
    passed = total - len(failures)
    print(f"\n{passed}/{total} passed ({100 * passed / total:.2f}%)" if total else "no questions")
    if by_family:
        print("failures by family:", dict(by_family))
    return not failures


def main(argv: list[str] | None = None) -> None:
    if not run(build_arg_parser().parse_args(argv)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
