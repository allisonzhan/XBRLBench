# financial-eval

An eval that measures whether LLMs can extract and compute financial facts
from a *messy, realistic* statement excerpt — not from clean structured data.
Ground truth is machine-verified from SEC XBRL; the model only ever sees a
rendered, scaled-to-thousands snippet with sibling-line distractors. The gap
between what the model reports and the XBRL truth is the thing being
measured.

## Pipeline

```
generate.py  -->  questions.jsonl  -->  run.py  -->  responses.jsonl  -->  grade.py  -->  report_*
   (XBRL,           question bank      (OpenRouter,    raw model            (numeric        summary
   ground truth)                        multi-model)    outputs)             grading)        CSV/JSON
```

- **generate.py** — pulls each company's structured financial facts from
  SEC's `companyfacts` XBRL API, computes verified gold answers, and renders
  a messy human-readable statement snippet (values scaled "in thousands",
  sibling line items included as distractors) as the model-facing input.
  Ground truth never touches the messy rendering step — it's computed
  straight from the XBRL facts.
- **run.py** — sends each question to a configured set of models via
  [OpenRouter](https://openrouter.ai) and records raw responses.
- **grade.py** — parses each model's final answer, grades it numerically
  against gold with a relative-tolerance epsilon, and reports accuracy broken
  down by tier and by model.

## Question bank schema (`questions.jsonl`)

One JSON object per line, written by `generate.py`:

| field            | meaning                                                              |
|------------------|-----------------------------------------------------------------------|
| `id`             | unique question id, e.g. `AAPL-2024-T2-3`                            |
| `ticker`         | company ticker (the "company" field)                                  |
| `fiscal_year`    | fiscal year the question is about                                     |
| `tier`           | `T1` / `T2` / `T3` — see tier definitions below                       |
| `question`       | the prompt shown to the model, appended after `context`               |
| `gold_value`     | verified gold answer, computed directly from XBRL                     |
| `gold_unit`      | `USD`, `ratio`, or `percent`                                           |
| `source_concept` | the us-gaap XBRL concept(s) the gold answer was derived from          |
| `context`        | the messy rendered statement snippet the model actually sees          |

`run.py` and `grade.py` are built strictly against this schema — no renaming.

## Tier definitions

- **T1 — single lookup.** One value, directly present in the snippet (e.g.
  total assets, net income). Tests basic extraction plus the unit trap: the
  snippet is presented "in thousands" but the gold answer — and the answer
  the model must give — is in raw USD.
- **T2 — computation.** Requires combining two or more extracted values with
  a formula the question states explicitly (current ratio, gross margin,
  YoY revenue growth). Tests arithmetic on correctly-extracted, correctly-scaled
  inputs.
- **T3 — distractor / multi-hop.** The snippet contains a similarly-named
  sibling line item (e.g. total liabilities vs. total *current* liabilities)
  or requires reasoning across two fiscal years (e.g. an asset delta). Tests
  whether the model locates the *correct* line rather than a plausible
  near-miss.

## How ground truth is derived from XBRL

`generate.py` calls SEC's free `data.sec.gov/api/xbrl/companyfacts/CIK*.json`
endpoint per company, which returns every XBRL fact the company has ever
filed, tagged by us-gaap concept (e.g. `Assets`, `NetIncomeLoss`,
`GrossProfit`). For each fiscal year it:

1. Filters each concept's reported values down to the annual, 10-K-form,
   full-fiscal-year period (`fp == "FY"`, form starts with `10-K`, duration
   ≥ 300 days for duration-type facts — this drops quarterly/partial-period
   rows), preferring the tightest matching span if more than one qualifies.
2. Tries concept name candidates in order for facts filers tag differently
   (e.g. revenue may be tagged `RevenueFromContractWithCustomerExcludingAssessedTax`
   or the older `Revenues`).
3. Computes T1 answers as the raw looked-up value; T2 answers as a formula
   over two or more looked-up values (e.g. `assets_current /
   liabilities_current`); T3 answers as either a distractor-adjacent lookup
   or a year-over-year delta.

Because these gold values come straight from SEC-filed structured data, they
are machine-verified — not manually curated — and the messy snippet the
model sees is a *rendering* of the same facts, not an independent source. Any
model error is therefore attributable to extraction/scaling/reasoning over
the messy presentation, not to ambiguous or unverifiable ground truth.

v1 renders the messy snippet programmatically from XBRL facts with
realistic-looking presentation noise. The planned v2 upgrade is to swap in
the actual financial-statement exhibit text pulled from each 10-K filing,
which is the more rigorous version of this eval — see the note at the top of
`generate.py`.

## Running the pipeline

```bash
# 1. Build the question bank from SEC XBRL (no API key needed; SEC requires
#    a contact email in the User-Agent header).
python3 generate.py --email you@example.com --out questions.jsonl

# 2. Send questions to models via OpenRouter.
export OPENROUTER_API_KEY=sk-or-...
python3 run.py --in questions.jsonl --out responses.jsonl

#    Smoke test on a handful of questions first:
python3 run.py --in questions.jsonl --out responses.jsonl --limit 4

#    Resume an interrupted run without re-billing completed calls:
python3 run.py --in questions.jsonl --out responses.jsonl --resume

# 3. Grade and report.
python3 grade.py --in responses.jsonl --out-prefix report
#    Adjust tolerance if needed (default 1% relative):
python3 grade.py --in responses.jsonl --out-prefix report --epsilon 0.02
```

`grade.py` prints a tier x model accuracy table to stdout and writes:

- `report_summary.csv` / `report_summary.json` — accuracy broken down by
  tier, by model, and by tier x model cell (the headline result).
- `report_incorrect.jsonl` — every incorrect response with question, gold,
  parsed model answer, and full raw transcript, for manual failure-mode
  analysis.

### Models used

Configured at the top of `run.py`, pinned to exact IDs for reproducibility.
`run.py` prints the exact IDs it used at the start of every run.

## Results

Full run: 5 companies (AAPL, MSFT, KO, WMT, NVDA) × FY2023–FY2024 × 68
questions × 4 models = 272 graded responses. Epsilon = 1% relative tolerance.

| model                        |    T1 |    T2 |    T3 | overall |
|-------------------------------|------:|------:|------:|--------:|
| anthropic/claude-sonnet-4.5    | 100.0%| 100.0%| 100.0%|  100.0% |
| google/gemini-2.5-pro          |  95.0%| 100.0%| 100.0%|   98.5% |
| openai/gpt-4o-2024-11-20       | 100.0%| 100.0%|  60.0%|   88.2% |
| qwen/qwen-2.5-72b-instruct     | 100.0%| 100.0%|  90.0%|   97.1% |
| **ALL MODELS**                 |**98.8%**|**100.0%**|**87.5%**| **96.0%** |

**Overall: 261/272 = 96.0%.**

Note on how this number came to be: the first full run scored 70.2%, but 79
of those 81 "failures" turned out to be an eval bug, not a model failure —
`render_snippet()` showed only the current fiscal year, while 20 of 68
questions (all "YoY revenue growth" and "asset delta" items) asked the model
to compare against a prior year it was never shown. Fixed by
`render_two_year_snippet()`, which stacks the FY-1 and FY statements when a
question needs both (see `generate.py`); the 20 affected questions were
regenerated and re-run against all 4 models before the table above was
produced. The lesson — and the reason this is called out here instead of
quietly fixed — is that a low score on an eval like this always needs a
"could the model have possibly known this from what it was shown" check
before it's attributed to the model.

## Failure Taxonomy

11 genuine incorrect responses survived the fix above, cleanly split into
three reproducible modes:

1. **Answer-line scale mismatch after correct reasoning (9/11, 8 of them
   GPT-4o).** The model's prose gets the computation and the ×1000 unit
   conversion exactly right — e.g. "364,840,000 − 333,779,000 = 31,061,000
   (in thousands)... = 31,061,000,000 USD" — but then the `ANSWER:` line
   repeats the pre-conversion intermediate value (`31061000`) instead of the
   converted one. This happened on 8 of GPT-4o's 10 "assets delta"
   questions plus one "current liabilities" question — i.e. it concentrates
   specifically in two-step calculations (subtract-then-scale, or
   locate-then-scale) rather than single-step lookups, where GPT-4o was
   perfect. One qwen response shows the same pattern once. This is the
   headline model-level finding: GPT-4o's structured final-answer token is
   unreliable exactly where its own chain-of-thought had already computed
   the right number, dragging its T3 accuracy down to 60% against 90–100%
   for the other three models.
2. **Unit-trap failure on a single lookup (1/11, Gemini 2.5 Pro).** On one
   T1 question, the model talked itself out of applying the "in thousands"
   scale factor, reasoning from an unrelated same-order-of-magnitude
   coincidence ("Gross profit works out even without scaling") that the
   snippet must already be in full USD, and answered 97,703,000 instead of
   97,703,000,000.
3. **Sign/instruction misread (1/11, qwen).** Asked for "the dollar
   difference... positive if increased" on a year where assets *decreased*,
   qwen computed the (correctly negative) delta, then re-read "positive if
   increased" as "always report a positive number" and flipped the sign —
   answering +7,636,000,000 against a gold value of −7,636,000,000.

No responses were unparseable or relied on the last-number fallback in the
final corrected run — every model consistently produced a clean `ANSWER:`
line once given the data it needed.
