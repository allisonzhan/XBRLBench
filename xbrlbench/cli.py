"""xbrlbench.cli — single entry point for the benchmark pipeline.

    python -m xbrlbench generate --email you@example.com
    python -m xbrlbench validate
    python -m xbrlbench run [--models ...] [--limit N] [--resume]
    python -m xbrlbench grade [--epsilon 0.01]
    python -m xbrlbench report [--epsilon 0.01]
    python -m xbrlbench errors [--epsilon 0.01]

Inference (`run`) and everything downstream of it (`grade`/`report`/
`errors`) are deliberately separate commands backed by separate modules:
grading, reporting, and error analysis only ever read a saved responses
file, so re-running any of them (a different --epsilon, or a grading-logic
fix) never repeats paid API calls.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from . import errors, generation, grading, inference, reporting, validation


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
    reporting.build_arg_parser(subparsers.add_parser(
        "report", help="generate accuracy/failure breakdowns from a responses file"))
    errors.build_arg_parser(subparsers.add_parser(
        "errors", help="deterministic per-response detail on every non-correct answer"))

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
    elif args.command == "report":
        reporting.report(args.inp, args.questions, args.out_prefix, args.epsilon)
    elif args.command == "errors":
        errors.errors(args.inp, args.questions, args.out_prefix, args.epsilon)
    else:  # pragma: no cover - argparse enforces `required=True` above
        parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
