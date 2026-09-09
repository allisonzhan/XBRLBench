"""xbrlbench.inference — send the question bank to a set of models over
OpenRouter and record raw responses for grading.

Reads records shaped like xbrlbench.generation's output (see
xbrlbench.schema / docs/SCHEMA.md):
  id, ticker, fiscal_year, tier, question, gold_value, gold_unit,
  source_concept, context, reasoning_type, tolerance

Writes one record per (question, model) to the output JSONL:
  id, ticker, fiscal_year, tier, model, question, gold_value, gold_unit,
  source_concept, reasoning_type, tolerance, raw_response, extracted_answer,
  latency_s, prompt_tokens, completion_tokens, error

reasoning_type/tolerance are carried through so xbrlbench.grading can grade
each row with its own question's tolerance rather than one global default;
a responses file from before these fields existed still grades fine (grading
falls back to a default tolerance when the field is absent).

Auth: set OPENROUTER_API_KEY in the environment (or in a .env file — see
.env.example).

Run:    python -m xbrlbench run
Smoke:  python -m xbrlbench run --limit 4
One model: python -m xbrlbench run --models openai/gpt-4o-2024-11-20
Resume: python -m xbrlbench run --resume --out-dir results/runs/<run_id>

Every run gets its own directory (default: a new timestamped
results/runs/<run_id>/, see xbrlbench.experiment.generate_run_id) containing
responses.jsonl and a metadata.json recording enough to reproduce the run
later (models, temperature, prompt version, benchmark version, git commit —
see docs/METADATA.md). A run is never silently overwritten: re-running
against an --out-dir that already has a responses.jsonl requires --resume.

This module only performs inference — it never grades a response. Grading
(xbrlbench.grading) runs entirely offline against saved responses, so
re-grading never re-spends API budget.
"""

from __future__ import annotations

import argparse
import os
import random
import time
import urllib.error
import urllib.request
import json as _json
from pathlib import Path
from typing import Optional

from . import experiment
from .io_utils import append_jsonl, load_dotenv, load_jsonl
from .paths import DEFAULT_QUESTIONS_PATH, RUNS_DIR
from .schema import VALID_TIERS

API_BASE = "https://openrouter.ai/api/v1"

# Pinned, paid model IDs — one per major provider plus one strong open model.
# Pin exact IDs (not "-latest" aliases) so a run is reproducible months later.
MODELS = [
    "openai/gpt-4o-2024-11-20",
    "anthropic/claude-sonnet-4.5",
    "google/gemini-2.5-pro",
    "qwen/qwen-2.5-72b-instruct",
]

# Bump PROMPT_VERSION whenever SYSTEM_PROMPT's wording changes materially, so
# a run's metadata.json makes it obvious old and new runs weren't prompted
# identically.
PROMPT_VERSION = "v1"
SYSTEM_PROMPT = (
    "You are a careful financial analyst. You will be shown a messy excerpt "
    "from a company's financial statements and asked a question about it. "
    "Reason step by step, showing your work. Watch for unit traps (values "
    "may be presented in thousands) and for sibling line items that are "
    "similar but not what was asked for.\n\n"
    "End your response with your final answer alone on the very last line, "
    "in exactly this format (a bare number, no units, no commas, no $ sign, "
    "percentages as a plain number like 12.34 not 0.1234):\n"
    "ANSWER: <value>"
)

TEMPERATURE = 0
MAX_TOKENS = None  # no explicit cap -- provider default applies

MAX_RETRIES = 5
BASE_BACKOFF = 2.0  # seconds


def extract_answer(text: str | None) -> str | None:
    """Pull the value after the last 'ANSWER:' line; None if not present."""
    if not text:
        return None
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.upper().startswith("ANSWER:"):
            val = line.split(":", 1)[1].strip()
            val = val.replace(",", "").replace("$", "").rstrip("%").strip()
            return val
    return None


def call_openrouter(api_key: str, model: str, question: str, context: str) -> dict:
    url = f"{API_BASE}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{context}\n\nQuestion: {question}"},
        ],
        "temperature": TEMPERATURE,
    }
    if MAX_TOKENS is not None:
        payload["max_tokens"] = MAX_TOKENS
    data = _json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # OpenRouter asks for these two for attribution/rankings; harmless if ignored.
            "HTTP-Referer": "https://github.com/local/xbrlbench",
            "X-Title": "xbrlbench",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return _json.loads(r.read().decode("utf-8"))


def call_with_retry(api_key: str, model: str, question: str, context: str) -> dict:
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            t0 = time.time()
            resp = call_openrouter(api_key, model, question, context)
            latency = time.time() - t0
            choice = resp["choices"][0]["message"]["content"]
            usage = resp.get("usage", {})
            return {
                "raw_response": choice,
                "latency_s": round(latency, 3),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "error": None,
            }
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            last_err = f"HTTP {e.code}: {body[:300]}"
            # 429 (rate limit) and 5xx are worth retrying; other 4xx usually aren't.
            if e.code == 429 or e.code >= 500:
                sleep_s = BASE_BACKOFF * (2 ** attempt) + random.uniform(0, 1)
                print(f"    [retry] {model}: {last_err} — sleeping {sleep_s:.1f}s")
                time.sleep(sleep_s)
                continue
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = f"{type(e).__name__}: {e}"
            sleep_s = BASE_BACKOFF * (2 ** attempt) + random.uniform(0, 1)
            print(f"    [retry] {model}: {last_err} — sleeping {sleep_s:.1f}s")
            time.sleep(sleep_s)
            continue
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            break
    return {
        "raw_response": None,
        "latency_s": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "error": last_err,
    }


def already_done(out_path) -> set:
    """Resume support: (question id, model) pairs already recorded."""
    done = set()
    if os.path.exists(out_path):
        for row in load_jsonl(out_path):
            done.add((row.get("id"), row.get("model")))
    return done


def run(
    questions_path=DEFAULT_QUESTIONS_PATH,
    out_dir: Optional[str] = None,
    models: list[str] | None = None,
    limit: int | None = None,
    resume: bool = False,
    tier: Optional[str] = None,
    question_ids: list[str] | None = None,
) -> None:
    load_dotenv()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit(
            "Set OPENROUTER_API_KEY in the environment (or in a .env file — "
            "see .env.example) first."
        )

    if resume and out_dir is None:
        raise SystemExit(
            "--resume requires --out-dir pointing at the run directory to continue "
            "(e.g. --out-dir results/runs/20260101T000000Z)."
        )
    run_id = Path(out_dir).name if out_dir else experiment.generate_run_id()
    out_dir = Path(out_dir) if out_dir else RUNS_DIR / run_id
    responses_path = out_dir / "responses.jsonl"
    metadata_path = out_dir / "metadata.json"

    models = models if models else MODELS
    print("Models used for this run:")
    for m in models:
        print(f"  - {m}")

    questions = load_jsonl(questions_path)
    if tier:
        questions = [q for q in questions if q.get("tier") == tier]
    if question_ids:
        wanted = set(question_ids)
        questions = [q for q in questions if q.get("id") in wanted]
        missing = wanted - {q["id"] for q in questions}
        if missing:
            print(f"[warn] --question-id not found in {questions_path}: {sorted(missing)}")
    if limit:
        questions = questions[:limit]
    print(f"\nLoaded {len(questions)} questions from {questions_path}")

    if resume:
        if not responses_path.exists():
            raise SystemExit(f"--resume given but no existing responses file at {responses_path}")
        done = already_done(responses_path)
        print(f"Resuming {out_dir}: {len(done)} (question, model) pairs already recorded, will skip.")
    elif responses_path.exists():
        # Runs are never silently overwritten -- make the user choose.
        raise SystemExit(
            f"{responses_path} already exists. Pass --resume to continue that run, "
            f"or omit --out-dir (or pick a different one) to start a new run."
        )
    else:
        done = set()

    metadata = experiment.build_metadata(
        run_id=run_id,
        started_at=experiment.utc_now_iso(),
        models=models,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        prompt_version=PROMPT_VERSION,
        question_count=len(questions),
        resume=resume,
    )
    experiment.write_metadata(metadata, metadata_path)

    total = len(questions) * len(models)
    n = 0
    n_written = 0
    for q in questions:
        for model in models:
            n += 1
            if (q["id"], model) in done:
                continue
            print(f"[{n}/{total}] {q['id']} :: {model}")
            result = call_with_retry(api_key, model, q["question"], q["context"])
            extracted = extract_answer(result["raw_response"])
            row = {
                "id": q["id"],
                "ticker": q.get("ticker"),
                "fiscal_year": q.get("fiscal_year"),
                "tier": q.get("tier"),
                "model": model,
                "question": q["question"],
                "gold_value": q.get("gold_value"),
                "gold_unit": q.get("gold_unit"),
                "source_concept": q.get("source_concept"),
                "reasoning_type": q.get("reasoning_type"),
                "tolerance": q.get("tolerance"),
                "raw_response": result["raw_response"],
                "extracted_answer": extracted,
                "latency_s": result["latency_s"],
                "prompt_tokens": result["prompt_tokens"],
                "completion_tokens": result["completion_tokens"],
                "error": result["error"],
            }
            # Append immediately so a crash mid-run only loses the in-flight call.
            append_jsonl(responses_path, row)
            n_written += 1
            if result["error"]:
                print(f"    [error] {result['error']}")

    metadata["completed_at"] = experiment.utc_now_iso()
    metadata["response_count"] = len(done) + n_written
    experiment.write_metadata(metadata, metadata_path)

    print(f"\nDone. Wrote {n_written} new response(s) -> {responses_path}")
    print(f"Run metadata -> {metadata_path}")
    print("\nNext:")
    print(f"  python -m xbrlbench grade --in {responses_path}")
    print(f"  python -m xbrlbench report --in {responses_path}")
    print(f"  python -m xbrlbench errors --in {responses_path}")


def build_arg_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(
        description="Send benchmark questions to models via OpenRouter.")
    parser.add_argument("--in", dest="inp", default=str(DEFAULT_QUESTIONS_PATH))
    parser.add_argument("--out-dir", dest="out_dir", default=None,
                         help="directory to write responses.jsonl + metadata.json into "
                              "(default: a new timestamped results/runs/<run_id>/ directory)")
    parser.add_argument("--limit", type=int, default=None,
                         help="only run the first N questions (smoke test)")
    parser.add_argument("--models", nargs="*", default=None,
                         help="subset of MODELS to run (default: all configured models)")
    parser.add_argument("--tier", choices=sorted(VALID_TIERS), default=None,
                         help="only run questions in this difficulty tier (see docs/SCHEMA.md)")
    parser.add_argument("--question-id", dest="question_ids", nargs="*", default=None,
                         help="only run these specific question id(s)")
    parser.add_argument("--resume", action="store_true",
                         help="continue an existing run -- requires --out-dir pointing at it")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    run(args.inp, args.out_dir, args.models, args.limit, args.resume, args.tier, args.question_ids)


if __name__ == "__main__":
    main()
