# CLAUDE.md

Guidance for Claude (and humans) working in this repo. The product
direction lives in [docs/PRD.md](docs/PRD.md); read it before starting
anything beyond a bug fix.

## What this project is

XBRLBench builds questions about company financial statements whose
answers are computed exactly from SEC XBRL data, then measures how well
LLMs answer them. The **end goal** (see the PRD) is to use that
automatically verifiable data as a reinforcement-learning environment and
train a small open model (Qwen3-4B) that matches frontier models at reading real
10-K filings it has never seen, on a budget of under ~$100.

Today the repo is the benchmark half: 68 questions, 5 companies, FY2023–2024,
4 models via OpenRouter. The RL half does not exist yet.

## Commands

```bash
pip install -e ".[dev]"                         # dev install (pytest)
ruff check .                                     # lint (CI runs this)
pytest -q                                        # full suite, no network
python -m xbrlbench validate                     # question-bank integrity; CI runs this
python -m xbrlbench generate --email <contact>   # rebuild data/questions.jsonl from SEC (network)
python -m xbrlbench run --limit 4                # live model eval (needs OPENROUTER_API_KEY, costs money)
python -m xbrlbench grade|report|errors --in <responses.jsonl>   # offline, free
```

Network: `generate` needs `data.sec.gov`. The "XBRLBench" cloud environment
allows it; the Default environment does not. SEC requires a real contact
email in the User-Agent (`--email`) — ask the user, never invent one.

## Layout

```
xbrlbench/generation.py   SEC companyfacts -> data/questions.jsonl
xbrlbench/validation.py   integrity checks (run before any eval)
xbrlbench/inference.py    questions -> responses via OpenRouter
xbrlbench/grading.py      responses -> correct / incorrect / invalid_response
xbrlbench/reporting.py, errors.py   accuracy tables, failure detail
xbrlbench/schema.py       Question dataclass + allowed values
data/questions.jsonl      the benchmark (generated, committed)
results/baseline/         the committed reference run
docs/                     SCHEMA.md, METADATA.md, PRD.md
```

## Rules

- **Runtime stays stdlib-only.** `dependencies = []` in pyproject is
  deliberate. Heavy tooling (training, HTML parsing) goes behind an optional
  extra, e.g. `pip install -e ".[train]"`, never into the core package.
- **Tests make no network calls.** Mock SEC/OpenRouter or use recorded
  fixtures under `tests/`.
- **Grading never calls a model.** Inference writes responses; everything
  downstream re-reads them. Keep it that way so re-grading is free.
- **Ground truth is never hand-edited.** Fix the generator and regenerate.
- **Version bumps** (policy in docs/SCHEMA.md): bump `BENCHMARK_VERSION` in
  `xbrlbench/__init__.py` whenever `data/questions.jsonl` content changes;
  bump `PROMPT_VERSION` in `inference.py` whenever the system prompt changes.
- **Commits:** small and single-purpose, conventional prefixes
  (`fix:`, `feat:`, `test:`, `docs:`, `data:`, `chore:`), body explains why.
  Regenerated data goes in its own `data:` commit.
- Never commit `.env` or API keys.

## XBRL gotchas (learned the hard way)

- **companyfacts `fy`/`fp` label the filing, not the period.** A FY2023 10-K
  carries FY2021–FY2023 income-statement facts all tagged `fy=2023`. Select
  by period `end` (and a 350–380 day span for duration items), as
  `generation.fiscal_year_end` / `annual_value` do. The original code got
  this wrong and every question was labelled with the wrong year.
- 52/53-week fiscal years are common (AAPL, WMT, NVDA): spans of 364 or 371
  days are both "annual".
- Filers tag differently: revenue may be `Revenues` or
  `RevenueFromContractWithCustomerExcludingAssessedTax`; KO and WMT don't tag
  `Liabilities`; WMT doesn't tag `GrossProfit`.
- The same period appears in several filings (original, next year's
  comparative, 10-K/A). We take the earliest-filed value (as originally
  reported).
- Large filers report "in millions"; the current synthetic snippet renders
  "in thousands". Real-filing rendering is milestone 2 in the PRD.

## Current status (update as milestones land)

- Milestone 1 (data correctness): generator and validation fixed;
  `data/questions.jsonl` and `results/baseline/` still need regenerating from
  live SEC data, after which `BENCHMARK_VERSION` goes to v0.3. Until then the
  real-bank validation tests fail on purpose.
