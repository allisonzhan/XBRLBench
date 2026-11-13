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
python -m xbrlbench spotcheck --email <contact> # compare gold values with the filed 10-Ks (network)
python -m xbrlbench build --email <contact> --tickers AAPL,JPM   # real-filing questions, trial (network)
python -m xbrlbench build --email <contact> --companies 500      # full real-filing corpus (network, resumable)
python -m xbrlbench audit --in data/filings/questions.jsonl --n 1000   # re-derive gold from context text (offline)
python -m xbrlbench export-rl --in data/filings/v3/questions.jsonl --out data/rl/train.jsonl   # train split -> RL prompts (offline)
python -m xbrlbench reward --in <responses.jsonl>  # score saved responses with the RL reward (offline)
python scripts/train_grpo.py --train data/rl/train.jsonl --out runs/grpo   # GRPO + LoRA (GPU, pip install -e ".[train]")
```

Network: `generate`, `spotcheck` and `build` need `data.sec.gov` and `www.sec.gov`. The "XBRLBench" cloud environment
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
xbrlbench/ixbrl.py        inline-XBRL 10-K -> facts + statement tables (milestone 2)
xbrlbench/edgar.py        EDGAR submissions lookup + rate-limited downloads
xbrlbench/spotcheck.py    gold values vs. the filed 10-K's own tags
xbrlbench/filings.py      real 10-K tables -> questions, train/test split, corpus build
xbrlbench/audit.py        checks real-filing gold values against the context text
xbrlbench/rl.py           RL reward (strict parsing, no credit for scale errors) + train-split export
scripts/train_grpo.py     GRPO + LoRA trainer (TRL); notebooks/m4_grpo_debug.ipynb runs it on Kaggle
tests/fixtures/           hand-written iXBRL samples (realistic_10k.htm mirrors EDGAR markup)
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
  `Liabilities` (we derive it as `LiabilitiesAndStockholdersEquity` minus
  equity *including* noncontrolling interest — `generation.derive_total_liabilities`);
  WMT doesn't tag `GrossProfit`.
- The same period appears in several filings (original, next year's
  comparative, 10-K/A). We take the earliest-filed value (as originally
  reported).
- Large filers report "in millions"; the current synthetic snippet renders
  "in thousands". Real-filing rendering is milestone 2 in the PRD.
- In filed 10-Ks, unit ids (`unitRef`) are arbitrary: compare `Fact.unit`
  (the resolved measure), never the id. The "(In millions...)" line and the
  statement title usually sit in divs above the table, not inside it, and
  a balance sheet can be split over two tables by a page break
  (`ixbrl.primary_statements` merges them).
- Never trust a stated scale blindly: `filings.table_scale_problem` drops a
  statement whose "(In thousands/millions)" text disagrees with its tags'
  `scale`.

## Current status (update as milestones land)

- Milestone 1 (data correctness): done. Generator and validation fixed,
  `data/questions.jsonl` regenerated at v0.3, and `results/baseline/`
  re-run against it (run `20261110T023920Z`, commit `cea1514`).
  `spotcheck --n 30` passed 30/30 against the filed 10-Ks on 2026-11-10,
  covering every checkable question (total assets, net income and current
  liabilities for all 5 companies, FY2023–2024).
- Milestone 2 (real filings): done. On 2026-11-10 `build --companies 500
  --per-company 2` produced 8,110 questions from 962 filings of 500
  companies (all SIC divisions); `audit` passed 8,110/8,110 and 18
  hand-checked questions (10 from 10-Ks, 8 from 10-Qs) all matched. Split:
  6,743 train (10-Ks), 1,367 test from 95 held-out companies (1,247 from
  10-Qs, 120 from 10-Ks). The corpus is not committed: `data/filings/` is
  gitignored (on allison's PC under data/filings/v2).
  Rebuilt as data/filings/v3 after 33a59ba reworded the sign-convention
  questions: 8,138 questions from 965 filings (28 new, from three
  2026-09-30 10-Qs filed since v2; 6,743 train, 1,395 test), 3,773
  reworded, 0 gold values changed; `audit --n 1000` passed 1,000/1,000.
  data/filings/v3/heldout_500.jsonl holds the same 500 ids as v2.
  `data/questions.jsonl` (synthetic bank) is at v0.4.
- Milestone 3 (baselines): done 2026-11-11. 500-question held-out sample
  (`sample`, ids in results/m3/heldout_500_ids.txt). Frontier: Gemini 3.1
  Pro 99.8%, GPT-5.6 Terra 98.6%, Sonnet 5.5 96.6%; Qwen3-14B 43.4%; base
  Qwen3-4B 33.6% (thinking) / 32.0% (no thinking), Qwen3-1.7B 28.6% / 20.8%.
  Gate passed. Most small-model misses are scale (answer left in thousands
  or millions). Base models run on Kaggle via notebooks/m3_base_models.ipynb
  (OpenRouter doesn't serve them). Write-up: docs/M3_BASELINES.md.
- Milestone 4 (RL debug on Qwen3-1.7B): tooling ready, not yet run.
  `xbrlbench/rl.py` holds the reward; on the committed M3 and baseline
  responses it agrees with grading on every correct answer and scores 239
  of Qwen3-14B's 283 misses as scale errors. Next: run
  `notebooks/m4_grpo_debug.ipynb` on Kaggle with the corpus's
  questions.jsonl and heldout_500.jsonl from data/filings/v3.
