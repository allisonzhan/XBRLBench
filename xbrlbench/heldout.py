"""xbrlbench.heldout — draw the fixed evaluation sample from a real-filing
corpus (milestone 3).

    python -m xbrlbench sample --in data/filings/v2/questions.jsonl \
        --split test --n 500 --out data/filings/v2/heldout_500.jsonl

The sample is deterministic (hash-ordered, no RNG state), so anyone with the
same corpus gets the same questions. It keeps each task family's share of
the split (largest-remainder quotas) and, within a family, takes questions
round-robin over companies, so no single filer dominates. The output order
is hash-shuffled too, which makes `run --limit N` a fair smoke subset.

Also writes `<out>.ids.txt`, one question id per line, small enough to
commit next to a results directory.
"""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter, defaultdict

from .io_utils import load_jsonl, write_jsonl

SALT = "xbrlbench-heldout-v1"


def _h(*parts) -> str:
    return hashlib.sha256("|".join([SALT, *map(str, parts)]).encode("utf-8")).hexdigest()


def quotas(counts: dict[str, int], n: int) -> dict[str, int]:
    """Split n across keys in proportion to counts (largest remainder),
    never giving a key more than it has."""
    total = sum(counts.values())
    if n >= total:
        return dict(counts)
    exact = {k: n * c / total for k, c in counts.items()}
    out = {k: int(v) for k, v in exact.items()}
    for k in sorted(counts, key=lambda k: (-(exact[k] - out[k]), k)):
        if sum(out.values()) >= n:
            break
        if out[k] < counts[k]:
            out[k] += 1
    return out


def sample(rows: list[dict], n: int, split: str | None = "test") -> list[dict]:
    pool = [r for r in rows if split is None or r.get("split") == split]
    by_family: dict[str, list[dict]] = defaultdict(list)
    for r in pool:
        by_family[r.get("family") or r.get("reasoning_type") or ""].append(r)
    quota = quotas({f: len(v) for f, v in by_family.items()}, n)

    picked: list[dict] = []
    for family, items in sorted(by_family.items()):
        by_company: dict[str, list[dict]] = defaultdict(list)
        for r in items:
            by_company[str(r.get("cik") or r.get("ticker"))].append(r)
        queues = [sorted(qs, key=lambda r: _h(r["id"]))
                  for _, qs in sorted(by_company.items(), key=lambda kv: _h(family, kv[0]))]
        take: list[dict] = []
        depth = 0
        while len(take) < quota[family]:
            layer = [q[depth] for q in queues if len(q) > depth]
            if not layer:
                break
            take.extend(layer[: quota[family] - len(take)])
            depth += 1
        picked.extend(take)
    return sorted(picked, key=lambda r: _h(r["id"]))


def summary(rows: list[dict]) -> dict:
    return {
        "questions": len(rows),
        "companies": len({r.get("cik") or r.get("ticker") for r in rows}),
        "by_family": dict(sorted(Counter(r.get("family") for r in rows).items())),
        "by_form": dict(sorted(Counter(str(r.get("form")) for r in rows).items())),
    }


def run(args: argparse.Namespace) -> list[dict]:
    rows = sample(load_jsonl(args.inp), args.n, None if args.split == "all" else args.split)
    write_jsonl(args.out, rows)
    with open(f"{args.out}.ids.txt", "w", encoding="utf-8") as f:
        f.writelines(r["id"] + "\n" for r in rows)
    s = summary(rows)
    print(f"Wrote {s['questions']} questions from {s['companies']} companies -> {args.out}")
    print(f"  by family: {s['by_family']}")
    print(f"  by form:   {s['by_form']}")
    return rows


def build_arg_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(description="Draw a fixed evaluation sample.")
    parser.add_argument("--in", dest="inp", required=True, help="real-filing questions.jsonl")
    parser.add_argument("--out", required=True)
    parser.add_argument("--n", type=int, default=500)
    parser.add_argument("--split", choices=["test", "train", "all"], default="test")
    return parser


def main(argv: list[str] | None = None) -> None:
    run(build_arg_parser().parse_args(argv))


if __name__ == "__main__":
    main()
