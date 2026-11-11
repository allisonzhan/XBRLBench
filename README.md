# XBRLBench

[![CI](https://github.com/allisonzhan/XBRLBench/actions/workflows/ci.yml/badge.svg)](https://github.com/allisonzhan/XBRLBench/actions/workflows/ci.yml)

XBRLBench is a benchmark for evaluating LLM financial reasoning over real SEC XBRL filing data.

## Why XBRLBench

A model can retrieve a financial figure correctly and still fail the task: filings present values "in thousands," similarly-named line items sit next to the one actually asked for, and a correct answer often requires combining two or more values rather than reading off one. XBRLBench isolates that gap. Ground truth is computed directly from SEC-filed XBRL facts; the model only ever sees a rendered, scaled, distractor-bearing statement excerpt derived from those same facts. Any error is therefore attributable to extraction, unit-scaling, or reasoning over messy presentation — not to ambiguous or unverifiable ground truth.

## Benchmark

- **68 questions** across 5 companies (AAPL, MSFT, KO, WMT, NVDA) × FY2023–FY2024.
- **3 difficulty tiers**: T1 single lookup, T2 computation, T3 distractor/multi-hop — see [docs/SCHEMA.md](docs/SCHEMA.md#difficulty-tiers).
- **4 reasoning categories**: `direct_retrieval` (30 questions), `ratio` (18), `percentage_change` (10), `multi_period_comparison` (10) — an independent axis from tier; see [docs/SCHEMA.md](docs/SCHEMA.md#reasoning-types) for the exact definition and a worked example of each.
- **Ground truth** comes from SEC's free `companyfacts` XBRL API — machine-verified, not manually curated. See [Methodology](#methodology).
- **Models evaluated**: `openai/gpt-4o-2024-11-20`, `anthropic/claude-sonnet-4.5`, `google/gemini-2.5-pro`, `qwen/qwen-2.5-72b-instruct`, via [OpenRouter](https://openrouter.ai), pinned to exact IDs for reproducibility.

## Held-out real-filing baselines (milestone 3)

500 questions from real 10-Q/10-K statement tables of 95 companies never used for training, all filed on or after 2026-07-01. 95% bootstrap intervals over companies. Full write-up: [docs/M3_BASELINES.md](docs/M3_BASELINES.md).

| model | accuracy | 95% CI |
|---|---:|---:|
| google/gemini-3.1-pro-preview | 99.8% | 99.4–100.0% |
| openai/gpt-5.6-terra | 98.6% | 97.6–99.4% |
| anthropic/claude-sonnet-5.5 | 96.6% | 94.9–98.1% |
| qwen/qwen3-14b | 43.4% | 37.9–49.3% |
| Qwen3-4B (base, thinking) | 33.6% | 28.3–39.2% |
| Qwen3-1.7B (base, thinking) | 28.6% | 23.9–33.7% |

Small models mostly fail by leaving dollar answers in the table's thousands or millions; ratios and growth rates they largely get right. That gap is what the RL milestones target.

## Results (synthetic bank)

68 questions × 4 models = 272 graded responses on benchmark **v0.3**, each with its own tolerance (see [Methodology](#methodology)). Run `20261110T023920Z`, 2026-11-10, at commit `cea1514`. Reproduce this table with `python -m xbrlbench report --in results/baseline/responses.jsonl` — see [Reproducing Results](#reproducing-results).

| model | T1 | T2 | T3 | overall |
|---|---:|---:|---:|---:|
| anthropic/claude-sonnet-4.5 | 100.0% | 100.0% | 100.0% | 100.0% |
| google/gemini-2.5-pro | 100.0% | 100.0% | 100.0% | 100.0% |
| openai/gpt-4o-2024-11-20 | 100.0% | 100.0% | 70.0% | 91.2% |
| qwen/qwen-2.5-72b-instruct | 100.0% | 96.4% | 85.0% | 94.1% |
| **all models** | **100.0%** | **99.1%** | **88.8%** | **96.3%** |

10 of 272 responses are not correct: 9 incorrect, 1 `invalid_response`. 9 of the 10 are in T3. Full detail is in [results/baseline/errors.jsonl](results/baseline/errors.jsonl).

**Findings:**

1. **Claude and Gemini are perfect; GPT-4o and Qwen drop on T3.** Every model scores 100% on T1. On T3, Claude and Gemini stay at 100%, Qwen falls to 85%, and GPT-4o falls to 70%.
2. **GPT-4o's failures are a final-answer formatting slip, not a reasoning failure.** All 6 of its wrong answers follow the same pattern. The chain-of-thought computes the correct value and states the ×1,000 conversion in prose. The `ANSWER:` line then gives the pre-conversion intermediate value. The parsed answer is exactly `gold_value / 1000` in all 6 cases. Five of the six are `multi_period_comparison` questions (two-period dollar deltas), so GPT-4o scores 50% on that reasoning type. The sixth is a T3 distractor lookup (`WMT-2023-T3-4`). See the worked example below.
3. **Qwen's failures are sign errors on decreases.** Three `multi_period_comparison` questions have a negative answer, because total assets fell (`AAPL-2023-T3-6`, `NVDA-2023-T3-6`, `WMT-2023-T3-5`). Qwen got all three wrong the same way: it computed the negative delta correctly, then read the question's "(positive if increased)" as a request for the absolute value. The other three models answered all three with the correct negative sign. That wording doesn't explicitly say "negative if decreased", so some of this may be prompt ambiguity rather than a pure model error.
4. **One degenerate generation.** On `NVDA-2023-T2-4` (YoY revenue growth), Qwen computed the right figure, then repeated `= 0.2230 × 100` until it ran out of output tokens. It never wrote an `ANSWER:` line, so the response is graded `invalid_response`.

Across all four models, single-step (`direct_retrieval`) accuracy is 99.2% and multi-step accuracy is 94.1%. The gap comes from the two model-specific patterns above, not from questions that every model finds hard.

## Example

**Question** (`KO-2023-T3-6`, tier T3, reasoning type `multi_period_comparison`):

> By how much did KO's total assets change from FY2022 to FY2023? Answer the dollar difference in USD (positive if increased).

**Context shown to the model** (excerpt; full statement also includes revenue/income-statement lines):

```
KO — Consolidated Financial Statements (excerpt), FY2022
(in thousands, except per share data)
  ...
  Total assets .......................... 92,763,000
  ...

KO — Consolidated Financial Statements (excerpt), FY2023
(in thousands, except per share data)
  ...
  Total assets .......................... 97,703,000
  ...
```

**Gold value**: `4,940,000,000` USD, computed directly from XBRL (`Assets` FY2023 − `Assets` FY2022) — the model never sees this arithmetic, only the two rendered statements above.

**GPT-4o's response** (excerpt):

```
3. Calculate the change in total assets:
   - Change = Total assets in FY2023 - Total assets in FY2022
   - Change = 97,703,000 - 92,763,000
   - Change = 4,940,000 (in thousands).

4. Since the values are in thousands, the actual dollar difference is:
   - 4,940,000 × 1,000 = 4,940,000,000 USD.

Final step:
The total assets increased by 4,940,000,000 USD.

ANSWER: 4940000
```

The reasoning is entirely correct — including the ×1,000 unit conversion, stated explicitly in prose. The `ANSWER:` line then reports `4940000`, the pre-conversion intermediate value, instead of `4940000000`. Graded against a 1%-relative tolerance, this is off by nearly 100% and counts as fully incorrect — which is the right call for a benchmark that grades the stated final answer, but it's exactly the kind of nontrivial, non-obvious failure mode this benchmark is built to surface: a model can show correct work and still fail the task.

## Quick Start

```bash
git clone https://github.com/allisonzhan/XBRLBench.git
cd XBRLBench
pip install -e ".[dev]"

# Re-grade the committed example run — no API key, no network calls:
python -m xbrlbench grade  --in results/baseline/responses.jsonl --out-prefix results/baseline/report
python -m xbrlbench report --in results/baseline/responses.jsonl --out-prefix results/baseline/report
python -m xbrlbench errors --in results/baseline/responses.jsonl --out-prefix results/baseline/errors

# To run a fresh evaluation against live models (costs money, needs a key):
cp .env.example .env   # add your OPENROUTER_API_KEY
python -m xbrlbench run --limit 4   # smoke test on 4 questions first
```

`python -m xbrlbench --help` lists every command (`generate`, `run`, `grade`, `report`, `errors`, `validate`).

## Methodology

- **Question creation**: [`xbrlbench/generation.py`](xbrlbench/generation.py) pulls each company's structured facts from SEC's free `companyfacts` XBRL API, anchors each fiscal year to its own 10-K's period-end date (companyfacts' `fy` field labels the *filing*, not the period, so a FY2023 10-K also carries FY2021–FY2022 comparatives tagged `fy=2023`), keeps 10-K facts for exactly that period (350–380 days for income-statement items, to cover 52/53-week years), prefers the value as originally reported, and tries filer-specific concept-tag fallbacks (e.g. revenue is tagged differently across filers). Ground truth (T1: a direct lookup; T2: a formula over two or more lookups; T3: a lookup among similar-named siblings, or a cross-year delta) is computed straight from these facts — the messy rendering step never touches it.
- **SEC/XBRL source**: `data.sec.gov/api/xbrl/companyfacts/CIK*.json`, no API key required (SEC requires a contact email in the `User-Agent` header — see `--email`).
- **Prompting**: one fixed system prompt (`xbrlbench.inference.SYSTEM_PROMPT`, versioned via `PROMPT_VERSION`), temperature 0, asking for step-by-step reasoning ending in a bare `ANSWER: <value>` line.
- **Answer extraction**: the `ANSWER:` line is parsed first; if that's missing or empty, extraction falls back to the response's last line, then to the last number anywhere in the response — each level tagged with its `parse_source` for auditability. An answer containing more than one distinct number is never guessed at; it's graded `invalid_response`.
- **Grading**: numeric comparison against `gold_value` within a tolerance combined with `gold_unit` — relative (with a floor) for USD/percent, absolute for ratio. See [docs/SCHEMA.md § Tolerance](docs/SCHEMA.md#tolerance) for the exact formulas and why a single relative formula applied to all three units doesn't work.
- **Tolerances**: every question currently uses `0.01` (≈1% relative for USD/percent, ±0.01 absolute for ratio); `--epsilon` overrides it globally for experimentation.
- **Difficulty definitions**: tier (T1/T2/T3) and reasoning type are independent axes — see [docs/SCHEMA.md](docs/SCHEMA.md).

## Reproducing Results

Grading, reporting, and error analysis all read a saved responses file and never make API calls — re-running them (a different `--epsilon`, or after a code change) never re-spends budget:

```bash
python -m xbrlbench grade  --in results/baseline/responses.jsonl --out-prefix results/baseline/report
python -m xbrlbench report --in results/baseline/responses.jsonl --out-prefix results/baseline/report
python -m xbrlbench errors --in results/baseline/responses.jsonl --out-prefix results/baseline/errors
```

`results/baseline/` is the exact run this README's Results table is computed from. Its `metadata.json` was captured live at run time and records the git commit, benchmark version, models and prompt version (see [docs/METADATA.md](docs/METADATA.md)).

To run a fresh evaluation end to end:

```bash
python -m xbrlbench generate --email you@example.com   # rebuild data/questions.jsonl from live SEC data (optional -- the committed one is already current)
python -m xbrlbench validate                            # check the question bank's integrity first
python -m xbrlbench run --models openai/gpt-4o-2024-11-20   # one model
python -m xbrlbench run --tier T3                            # one difficulty tier
python -m xbrlbench run --question-id AAPL-2023-T1-0          # one question
python -m xbrlbench run --resume --out-dir results/runs/<id>  # continue an interrupted run
```

Every `run` writes to its own `results/runs/<timestamp>/` directory (never overwriting a previous run) and prints the exact `grade`/`report`/`errors` commands to run against it next.

## Repository Structure

```
xbrlbench/              the package: one module per concern
  generation.py         SEC XBRL -> data/questions.jsonl
  inference.py          questions.jsonl -> responses.jsonl (OpenRouter)
  grading.py            responses.jsonl -> correct/incorrect/invalid
  reporting.py          graded responses -> accuracy breakdowns
  errors.py             graded responses -> per-failure detail
  validation.py         benchmark integrity checks
  schema.py             Question dataclass + enforced field sets
  experiment.py         run metadata (git commit, models, prompt version, ...)
  cli.py / __main__.py  `python -m xbrlbench ...`
data/questions.jsonl  the benchmark itself (68 questions)
results/baseline/     the committed example run this README's numbers come from
results/runs/         local scratch output from your own runs (gitignored)
docs/                 SCHEMA.md (question format), METADATA.md (run metadata), PRD.md (roadmap)
tests/                unit tests, no network calls
```

## Limitations

- **Small**: 68 questions, 5 companies, 2 fiscal years. Per-cell breakdowns (e.g. "GPT-4o: 50% on `multi_period_comparison`") rest on as few as 10 questions — real but not statistically precise rates.
- **Narrow company selection**: five US mega-cap filers. XBRL tagging conventions and filing messiness vary far more across the full universe of SEC filers (smaller companies, foreign private issuers, unusual chart-of-accounts) than this sample captures.
- **Deterministic numeric grading** checks whether the final number is right within tolerance. It does not credit a correct method with a scale/sign slip (see the worked Example above — fully correct reasoning, graded fully incorrect) and doesn't evaluate explanation quality.
- **Synthetic messy presentation**: the statement excerpt is rendered programmatically from clean XBRL facts, not pulled from actual 10-K exhibit text. Real filings vary more in layout than this simulates (a planned v2 direction — see the note at the top of [`xbrlbench/generation.py`](xbrlbench/generation.py)).
- **Model drift**: results are tied to the pinned model IDs and OpenRouter's routing at run time (recorded in each run's `metadata.json`). A provider updating weights behind a stable-looking ID can silently change future results.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for adding a company/filer, extending the schema, and the validate → test → evaluate loop.

