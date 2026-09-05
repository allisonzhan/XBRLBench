#!/usr/bin/env python3
"""
run.py — send the question bank (questions.jsonl from generate.py) to a set of
models over OpenRouter and record raw responses for grading.

Reads records shaped like generate.py's output:
  id, ticker, fiscal_year, tier, question, gold_value, gold_unit,
  source_concept, context

Writes one record per (question, model) to the output JSONL:
  id, ticker, fiscal_year, tier, model, question, gold_value, gold_unit,
  source_concept, raw_response, extracted_answer, latency_s,
  prompt_tokens, completion_tokens, error

Auth: set OPENROUTER_API_KEY in the environment.

Run:   python3 run.py --in questions.jsonl --out responses.jsonl
Smoke: python3 run.py --in questions.jsonl --out responses.jsonl --limit 4
"""

import argparse
import json
import os
import random
import time
import urllib.error
import urllib.request

API_BASE = "https://openrouter.ai/api/v1"

# Pinned, paid model IDs — one per major provider plus one strong open model.
# Pin exact IDs (not "-latest" aliases) so a run is reproducible months later.
MODELS = [
    "openai/gpt-4o-2024-11-20",
    "anthropic/claude-sonnet-4.5",
    "google/gemini-2.5-pro",
    "qwen/qwen-2.5-72b-instruct",
]

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

MAX_RETRIES = 5
BASE_BACKOFF = 2.0  # seconds


def load_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def append_jsonl(path, row):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def extract_answer(text):
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


def call_openrouter(api_key, model, question, context):
    url = f"{API_BASE}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{context}\n\nQuestion: {question}"},
        ],
        "temperature": 0,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # OpenRouter asks for these two for attribution/rankings; harmless if ignored.
            "HTTP-Referer": "https://github.com/local/financial-eval",
            "X-Title": "financial-eval",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def call_with_retry(api_key, model, question, context):
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


def already_done(out_path):
    """Resume support: (question id, model) pairs already recorded."""
    done = set()
    if os.path.exists(out_path):
        for row in load_jsonl(out_path):
            done.add((row.get("id"), row.get("model")))
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="questions.jsonl")
    ap.add_argument("--out", dest="out", default="responses.jsonl")
    ap.add_argument("--limit", type=int, default=None,
                     help="only run the first N questions (smoke test)")
    ap.add_argument("--models", nargs="*", default=None,
                     help="subset of MODELS to run (default: all configured models)")
    ap.add_argument("--resume", action="store_true",
                     help="skip (question id, model) pairs already in --out")
    args = ap.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("Set OPENROUTER_API_KEY in the environment first.")

    models = args.models if args.models else MODELS
    print("Models used for this run:")
    for m in models:
        print(f"  - {m}")

    questions = load_jsonl(args.inp)
    if args.limit:
        questions = questions[: args.limit]
    print(f"\nLoaded {len(questions)} questions from {args.inp}")

    done = already_done(args.out) if args.resume else set()
    if done:
        print(f"Resuming: {len(done)} (question, model) pairs already recorded, will skip.")
    elif not args.resume and os.path.exists(args.out):
        # Fresh run overwrites; make that explicit rather than silently appending.
        os.remove(args.out)

    total = len(questions) * len(models)
    n = 0
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
                "raw_response": result["raw_response"],
                "extracted_answer": extracted,
                "latency_s": result["latency_s"],
                "prompt_tokens": result["prompt_tokens"],
                "completion_tokens": result["completion_tokens"],
                "error": result["error"],
            }
            # Append immediately so a crash mid-run only loses the in-flight call.
            append_jsonl(args.out, row)
            if result["error"]:
                print(f"    [error] {result['error']}")

    print(f"\nDone. Wrote responses -> {args.out}")


if __name__ == "__main__":
    main()
