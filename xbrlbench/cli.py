"""xbrlbench.cli — single entry point for the benchmark pipeline.

    python -m xbrlbench generate --email you@example.com
    python -m xbrlbench validate
    python -m xbrlbench run [--models ...] [--limit N] [--resume]
    python -m xbrlbench grade [--epsilon 0.01]

Inference (`run`) and grading (`grade`) are deliberately separate commands
backed by separate modules: grading only ever reads a saved responses file,
so re-grading (a different --epsilon, or a grading-logic fix) never repeats
paid API calls.

More subcommands (`report`, `errors`) are added incrementally in later
commits — see docs/SCHEMA.md and the README for what's currently available.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from . import generation, grading, inference, validation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m xbrlbench",
        description="XBRLBench: a benchmark for LLM financial reasoning over SEC XBRL data.",
    )
    parser.add_argument("--version", action="version", version=f"xbrlbench {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    generation.build_arg_parser(subparsers.add_parser(
        "generate", help="build the question bank from SEC XBRL"))
    inference.build_arg_parser(subparsers.add_parser(
        "run", help="send questions to models via OpenRouter"))
    grading.build_arg_parser(subparsers.add_parser(
        "grade", help="grade saved responses against gold answers"))
    validation.build_arg_parser(subparsers.add_parser(
        "validate", help="check benchmark question bank integrity"))

    return parser


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "generate":
        generation.generate(args.email, args.out)
    elif args.command == "run":
        inference.run(args.inp, args.out, args.models, args.limit, args.resume)
    elif args.command == "grade":
        grading.grade(args.inp, args.out_prefix, args.epsilon)
    elif args.command == "validate":
        issues = validation.validate_and_report(args.inp)
        if any(i.severity == validation.ERROR for i in issues):
            raise SystemExit(1)
    else:  # pragma: no cover - argparse enforces `required=True` above
        parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
