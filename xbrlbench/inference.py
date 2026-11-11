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
import json as _json
import os
import random
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import experiment
from .io_utils import append_jsonl, load_dotenv, load_jsonl
from .paths import DEFAULT_QUESTIONS_PATH, RUNS_DIR
from .schema import VALID_TIERS

API_BASE = "https://openrouter.ai/api/v1"

# Real-filing question fields copied into each response row, so reports can
# break results down by family, sector, company and form without the corpus.
CARRY_FIELDS = ("family", "fiscal_period", "cik", "company", "form", "sic", "split", "period_end")

# --reasoning choices. "default" sends nothing (the provider's default);
# "off" disables thinking (e.g. Qwen3's /no_think mode); the others set an
# effort level. Only OpenRouter's `reasoning` parameter is used.
REASONING_CHOICES = ("default", "off", "low", "medium", "high")

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
PROMPT_VERSION = "v2"
SYSTEM_PROMPT = (
    "You are a careful financial analyst. You will be shown an excerpt "
    "from a company's financial statements and asked a question about it. "
    "Reason step by step, showing your work. Watch for unit traps (values "
    "may be presented in thousands or millions) and for sibling line items "
    "that are similar but not what was asked for.\n\n"
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


def is_openrouter(api_base: str) -> bool:
    return "openrouter.ai" in api_base


def build_messages(question: str, context: str) -> list[dict]:
    """The chat messages every model sees for one question. RL training
    (xbrlbench.rl) uses the same messages, so a trained model is evaluated
    on exactly the prompt it was trained on."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{context}\n\nQuestion: {question}"},
    ]


def build_payload(model: str, question: str, context: str, api_base: str = API_BASE,
                  max_tokens: int | None = MAX_TOKENS, reasoning: str = "default") -> dict:
    payload = {
        "model": model,
        "messages": build_messages(question, context),
        "temperature": TEMPERATURE,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if is_openrouter(api_base):
        # Ask OpenRouter to report each call's cost, which --max-cost enforces.
        payload["usage"] = {"include": True}
        if reasoning == "off":
            payload["reasoning"] = {"enabled": False}
        elif reasoning != "default":
            payload["reasoning"] = {"effort": reasoning}
    elif reasoning == "off":
        # OpenAI-compatible local servers (vLLM, Ollama) serving Qwen3 honor
        # its chat-template switch; other models ignore the extra field.
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return payload


def call_openrouter(api_key: str | None, model: str, question: str, context: str,
                    api_base: str = API_BASE, max_tokens: int | None = MAX_TOKENS,
                    reasoning: str = "default", timeout: float = 600) -> dict:
    url = f"{api_base.rstrip('/')}/chat/completions"
    payload = build_payload(model, question, context, api_base, max_tokens, reasoning)
    data = _json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        # OpenRouter asks for these two for attribution/rankings; harmless if ignored.
        "HTTP-Referer": "https://github.com/local/xbrlbench",
        "X-Title": "xbrlbench",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _json.loads(r.read().decode("utf-8"))


def call_with_retry(api_key: str | None, model: str, question: str, context: str, **opts) -> dict:
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            t0 = time.time()
            resp = call_openrouter(api_key, model, question, context, **opts)
            latency = time.time() - t0
            if "choices" not in resp:
                # OpenRouter reports some upstream failures as a 200 with an error body.
                raise _UpstreamError(_json.dumps(resp.get("error", resp))[:300])
            choice = resp["choices"][0]
            message = choice.get("message") or {}
            usage = resp.get("usage") or {}
            details = usage.get("completion_tokens_details") or {}
            return {
                # A thinking model that runs out of tokens returns no content;
                # record an empty answer (graded unparseable), not an API error.
                "raw_response": message.get("content") or "",
                "latency_s": round(latency, 3),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "reasoning_tokens": details.get("reasoning_tokens"),
                "cost": usage.get("cost"),
                "finish_reason": choice.get("finish_reason"),
                "provider": resp.get("provider"),
                "error": None,
            }
        except _UpstreamError as e:
            last_err = f"upstream: {e}"
            sleep_s = BASE_BACKOFF * (2 ** attempt) + random.uniform(0, 1)
            print(f"    [retry] {model}: {last_err} — sleeping {sleep_s:.1f}s")
            time.sleep(sleep_s)
            continue
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


class _UpstreamError(Exception):
    """A 200 response that carries an error instead of choices."""


def already_done(out_path) -> set:
    """Resume support: (question id, model) pairs already recorded.

    Rows whose call failed (`error` set) don't count, so --resume retries them;
    the stale error rows stay in the file and are superseded by the retry (see
    xbrlbench.grading.latest_rows)."""
    done = set()
    if os.path.exists(out_path):
        for row in load_jsonl(out_path):
            if row.get("error") is None:
                done.add((row.get("id"), row.get("model")))
    return done


LOCK_NAME = "run.lock"


def acquire_lock(out_dir: Path) -> Path:
    """Refuse to start if another run is writing to out_dir. Two runs on the
    same directory both pay for every question and interleave their writes
    (seen in M3 after a process restart). A crash leaves the lock behind;
    the message says how to clear it."""
    out_dir.mkdir(parents=True, exist_ok=True)
    lock = out_dir / LOCK_NAME
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        holder = lock.read_text(encoding="utf-8", errors="replace").strip()
        raise SystemExit(
            f"Another run appears to be writing to {out_dir} ({holder}). If it is not "
            f"running any more (e.g. it crashed), delete {lock} and try again."
        ) from None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"pid {os.getpid()} started {experiment.utc_now_iso()}\n")
    return lock


def select_questions(questions: list[dict], tier: str | None = None, split: str | None = None,
                     question_ids: list[str] | None = None, limit: int | None = None,
                     source: str = "") -> list[dict]:
    if tier:
        questions = [q for q in questions if q.get("tier") == tier]
    if split:
        questions = [q for q in questions if q.get("split") == split]
    if question_ids:
        wanted = set(question_ids)
        questions = [q for q in questions if q.get("id") in wanted]
        missing = wanted - {q["id"] for q in questions}
        if missing:
            print(f"[warn] --question-id not found in {source}: {sorted(missing)}")
    if limit:
        questions = questions[:limit]
    return questions


def response_row(q: dict, model: str, result: dict) -> dict:
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
    }
    for field in CARRY_FIELDS:
        if field in q:
            row[field] = q[field]
    row.update({
        "raw_response": result.get("raw_response"),
        "extracted_answer": extract_answer(result.get("raw_response")),
        "latency_s": result.get("latency_s"),
        "prompt_tokens": result.get("prompt_tokens"),
        "completion_tokens": result.get("completion_tokens"),
        "reasoning_tokens": result.get("reasoning_tokens"),
        "cost": result.get("cost"),
        "finish_reason": result.get("finish_reason"),
        "provider": result.get("provider"),
        "error": result.get("error"),
    })
    return row


def run(
    questions_path=DEFAULT_QUESTIONS_PATH,
    out_dir: str | None = None,
    models: list[str] | None = None,
    limit: int | None = None,
    resume: bool = False,
    tier: str | None = None,
    question_ids: list[str] | None = None,
    split: str | None = None,
    workers: int = 1,
    api_base: str | None = None,
    max_tokens: int | None = MAX_TOKENS,
    reasoning: str = "default",
    max_cost: float | None = None,
    timeout: float = 600,
) -> None:
    load_dotenv()
    api_base = api_base or os.environ.get("XBRLBENCH_API_BASE") or API_BASE
    api_key = os.environ.get("OPENROUTER_API_KEY") if is_openrouter(api_base) \
        else os.environ.get("XBRLBENCH_API_KEY")
    if is_openrouter(api_base) and not api_key:
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
    print(f"Models used for this run (via {api_base}):")
    for m in models:
        print(f"  - {m}")

    questions = select_questions(load_jsonl(questions_path), tier, split, question_ids, limit,
                                 source=str(questions_path))
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
        max_tokens=max_tokens,
        prompt_version=PROMPT_VERSION,
        question_count=len(questions),
        resume=resume,
    )
    metadata.update({
        "questions_path": str(questions_path),
        "split": split,
        "api_base": api_base,
        "reasoning": reasoning,
    })
    lock = acquire_lock(out_dir)
    try:
        _run_jobs(questions, models, done, api_key, responses_path, metadata, metadata_path,
                  workers, api_base, max_tokens, reasoning, max_cost, questions_path, timeout)
    finally:
        lock.unlink(missing_ok=True)


def _run_jobs(questions, models, done, api_key, responses_path, metadata, metadata_path,
              workers, api_base, max_tokens, reasoning, max_cost, questions_path, timeout=600) -> None:
    experiment.write_metadata(metadata, metadata_path)

    # Interleave models so a run stopped by --max-cost (or by hand) leaves
    # every model with a comparable set of answered questions.
    jobs = [(q, model) for q in questions for model in models if (q["id"], model) not in done]
    total = len(questions) * len(models)
    print(f"{len(jobs)} call(s) to make ({total - len(jobs)} already done).")

    lock = threading.Lock()
    state = {"written": 0, "spent": 0.0, "stopped": False}
    opts = {"api_base": api_base, "max_tokens": max_tokens, "reasoning": reasoning, "timeout": timeout}

    def work(job):
        q, model = job
        with lock:
            if state["stopped"]:
                return
        result = call_with_retry(api_key, model, q["question"], q["context"], **opts)
        row = response_row(q, model, result)
        with lock:
            # Append immediately so a crash mid-run only loses the in-flight calls.
            append_jsonl(responses_path, row)
            state["written"] += 1
            state["spent"] += result.get("cost") or 0.0
            n = state["written"]
            tag = f"[{n}/{len(jobs)}] {q['id']} :: {model}"
            if result.get("error"):
                print(f"{tag} [error] {result['error']}")
            elif n % 25 == 0 or workers == 1:
                print(f"{tag} (spent ${state['spent']:.2f})")
            if max_cost is not None and state["spent"] >= max_cost and not state["stopped"]:
                state["stopped"] = True
                print(f"[stop] spend ${state['spent']:.2f} reached --max-cost ${max_cost:.2f}; "
                      f"finishing in-flight calls. Continue later with --resume.")

    if workers <= 1:
        for job in jobs:
            work(job)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(work, jobs))

    metadata["completed_at"] = None if state["stopped"] else experiment.utc_now_iso()
    metadata["response_count"] = len(done) + state["written"]
    metadata["cost_usd_this_session"] = round(state["spent"], 4)
    experiment.write_metadata(metadata, metadata_path)

    print(f"\nDone. Wrote {state['written']} new response(s) -> {responses_path} "
          f"(spent ${state['spent']:.2f})")
    print(f"Run metadata -> {metadata_path}")
    print("\nNext:")
    print(f"  python -m xbrlbench grade --in {responses_path}")
    print(f"  python -m xbrlbench report --in {responses_path} --questions {questions_path}")
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
    parser.add_argument("--split", choices=["train", "test"], default=None,
                         help="only run real-filing questions in this split (see docs/SCHEMA.md)")
    parser.add_argument("--workers", type=int, default=1,
                         help="parallel API calls (default 1)")
    parser.add_argument("--api-base", default=None,
                         help="OpenAI-compatible endpoint (default: OpenRouter, or $XBRLBENCH_API_BASE); "
                              "e.g. http://localhost:8000/v1 for a local vLLM server")
    parser.add_argument("--max-tokens", type=int, default=MAX_TOKENS,
                         help="completion token cap (default: provider default)")
    parser.add_argument("--reasoning", choices=REASONING_CHOICES, default="default",
                         help="thinking mode: provider default, off, or an effort level")
    parser.add_argument("--max-cost", type=float, default=None,
                         help="stop starting new calls once OpenRouter-reported spend reaches this many USD")
    parser.add_argument("--timeout", type=float, default=600,
                         help="seconds to wait for one response (default 600); raise it for a busy "
                              "local server, where a long thinking trace queues behind many others")
    return parser


def run_from_args(args: argparse.Namespace) -> None:
    run(args.inp, args.out_dir, args.models, args.limit, args.resume, args.tier, args.question_ids,
        split=args.split, workers=args.workers, api_base=args.api_base, max_tokens=args.max_tokens,
        reasoning=args.reasoning, max_cost=args.max_cost, timeout=args.timeout)


def main(argv: list[str] | None = None) -> None:
    run_from_args(build_arg_parser().parse_args(argv))


if __name__ == "__main__":
    main()
