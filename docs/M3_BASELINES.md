# Milestone 3: baselines on the held-out set

Status: frontier results final for the first wording; sign-wording re-ask and
base models pending. Owner: Allison. See [PRD.md](PRD.md) and issue #4.

## Question

Before spending anything on RL, is there headroom? The PRD's gate: the
untrained base model must score clearly below frontier models on held-out
real filings.

## Setup

**Held-out sample.** 500 questions drawn with `xbrlbench sample` from the
test split of the real-filing corpus (companies never used for training,
10-K/10-Q filed on or after 2026-07-01): 95 companies, 455 from 10-Qs and 45
from 10-Ks, every task family in proportion. The ids are committed in
[`results/m3/heldout_500_ids.txt`](../results/m3/heldout_500_ids.txt); the
sample is deterministic, so the same corpus always gives the same 500.

**Models.**

| role | model | served by |
|---|---|---|
| frontier | `anthropic/claude-sonnet-5.5` | OpenRouter |
| frontier | `openai/gpt-5.6-terra` | OpenRouter |
| frontier | `google/gemini-3.1-pro-preview` | OpenRouter |
| open reference | `qwen/qwen3-14b` | OpenRouter |
| base (to train) | `Qwen/Qwen3-4B`, thinking on and off | vLLM on a Kaggle T4 x2 |
| base (debug) | `Qwen/Qwen3-1.7B`, thinking on and off | vLLM on a Kaggle T4 x2 |

Qwen3-4B and 1.7B aren't served by OpenRouter, so they run through the same
`xbrlbench run` code against a local vLLM server
([`notebooks/m3_base_models.ipynb`](../notebooks/m3_base_models.ipynb)).
Everything uses the same system prompt (`PROMPT_VERSION` v2), temperature 0,
and a 16,384-token cap (8,192 for the base models).

**Statistics.** 95% percentile-bootstrap intervals, resampling companies
rather than questions (questions about one filing share a table, so they
aren't independent). Gaps between models are paired bootstraps on the same
questions.

**Contamination.** Every filing in the sample was filed on or after
2026-07-01. OpenAI publishes a 2026-02-16 training cutoff for GPT-5.6;
Gemini 3.1 Pro was released 2026-02-19; Anthropic and Qwen don't publish one
on OpenRouter. Sonnet 5.5 (released 2026-09-28) is the one model whose data
could overlap the filing window; its scores are not higher than the others',
so there is no sign it saw them.

## Results (first wording)

| model | accuracy | 95% CI | cost |
|---|---|---|---|
| Gemini 3.1 Pro | 99.8% | 99.4–100.0% | $5.35 |
| GPT-5.6 Terra | 98.6% | 97.6–99.4% | $1.51 |
| Claude Sonnet 5.5 | 96.6% | 94.9–98.1% | $2.20 |
| Qwen3-14B | 43.4% | 37.9–49.3% | $0.28 |

Full tables, including breakdowns by task family, sector, company size and
form: [`results/m3/frontier/report.md`](../results/m3/frontier/report.md).

## What the misses are

`xbrlbench report` labels each miss by plain arithmetic against the gold
value:

| model | scale | sign | scale and sign | other | invalid | accuracy forgiving scale and sign |
|---|---|---|---|---|---|---|
| Gemini 3.1 Pro | 0 | 1 | 0 | 0 | 0 | 100.0% |
| GPT-5.6 Terra | 1 | 5 | 0 | 1 | 0 | 99.8% |
| Claude Sonnet 5.5 | 10 | 6 | 0 | 1 | 0 | 99.8% |
| Qwen3-14B | 237 | 3 | 12 | 4 | 27 | 93.8% |

Two findings follow.

1. **The frontier models' sign errors were our wording.** When a filing's row
   is labelled "Net loss" or "Net cash used in ...", a narrowing loss is a
   negative change in the loss but a positive change in net income, and the
   question didn't say which. The models' reasoning argued both readings,
   which explains most of Sonnet's 74.5% on `net_income_change`. Commit
   `33a59ba` states the sign convention in every question about a signed
   item; 239 of the 500 questions were reworded (no gold value changed) and
   re-asked.
2. **The open model's gap is almost all scale.** Qwen3-14B gets 95–100% on
   ratios, margins and growth rates but about 20% on dollar amounts, because
   it answers with the table's figure in thousands or millions instead of
   dollars. Forgiving that alone takes it from 43% to 94%. The headroom is
   real, and it is exactly the error the PRD names first, but it is narrow:
   RL mostly has to teach the model to apply "(In thousands)". Milestone 4
   should therefore also report a prompt-only baseline (an explicit "convert
   to whole dollars" instruction) so the RL gain is measured against the
   cheapest alternative, and should add the harder task families if the base
   model closes the scale gap early.

## Cost

$9.38 for the first frontier pass, about $4.50 for the re-ask, $0 for the
base models on Kaggle.

## Reproduce

```bash
python -m xbrlbench sample --in data/filings/v3/questions.jsonl --out data/filings/v3/heldout_500.jsonl --n 500
python -m xbrlbench run --in data/filings/v3/heldout_500.jsonl \
    --models anthropic/claude-sonnet-5.5 openai/gpt-5.6-terra google/gemini-3.1-pro-preview qwen/qwen3-14b \
    --workers 4 --max-tokens 16384 --max-cost 20 --out-dir results/m3/frontier-v2
python -m xbrlbench report --in results/m3/frontier-v2/responses.jsonl \
    --questions data/filings/v3/heldout_500.jsonl --out-prefix results/m3/frontier-v2/report
```
