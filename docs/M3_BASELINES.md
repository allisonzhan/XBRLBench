# Milestone 3: baselines on the held-out set

Status: frontier results final; base models (Kaggle) pending. Owner: Allison. See [PRD.md](PRD.md) and issue #4.

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

## Results

Final frontier numbers are on the reworded questions (`frontier-v2`), scored
on the 480 of 500 questions all four models answered: 20 reworded questions
lost one model's call to an OpenRouter key limit and are left out rather
than counted wrong ([`excluded_ids.txt`](../results/m3/frontier-v2/excluded_ids.txt)).

| model | accuracy | 95% CI | gap to best (95% CI) |
|---|---|---|---|
| Gemini 3.1 Pro | 100.0% | 100.0–100.0% | best |
| GPT-5.6 Terra | 98.1% | 96.8–99.4% | −1.9 pts (−3.2 to −0.6) |
| Claude Sonnet 5.5 | 96.9% | 94.7–98.8% | −3.1 pts (−5.3 to −1.2) |
| Qwen3-14B | 42.5% | 36.9–48.4% | −57.5 pts (−63.1 to −51.6) |
| Qwen3-4B, Qwen3-1.7B | pending | | |

Full tables, with breakdowns by task family, sector, company size and form:
[`results/m3/frontier-v2/report.md`](../results/m3/frontier-v2/report.md).
The first pass on the original wording (all 500 questions:
Gemini 99.8%, GPT 98.6%, Sonnet 96.6%, Qwen3-14B 43.4%) is in
[`results/m3/frontier/`](../results/m3/frontier/report.md).

## What the misses are

`xbrlbench report` labels each miss by plain arithmetic against the gold
value. Same 480 questions, before and after the rewording:

| model | wording | scale (incl. scale+sign) | sign | other | invalid | accuracy forgiving scale and sign |
|---|---|---|---|---|---|---|
| Gemini 3.1 Pro | first | 0 | 1 | 0 | 0 | 100.0% |
| | reworded | 0 | 0 | 0 | 0 | 100.0% |
| GPT-5.6 Terra | first | 1 | 4 | 1 | 0 | 99.8% |
| | reworded | 7 | 0 | 2 | 0 | 99.6% |
| Claude Sonnet 5.5 | first | 9 | 6 | 0 | 0 | 100.0% |
| | reworded | 15 | 0 | 0 | 0 | 100.0% |
| Qwen3-14B | first | 237 | 3 | 4 | 26 | 93.8% |
| | reworded | 242 | 0 | 3 | 31 | 92.9% |

Three findings follow.

1. **The frontier models' sign errors were our wording, and the fix worked.**
   When a filing's row is labelled "Net loss" or "Net cash used in ...", a
   narrowing loss is a negative change in the loss but a positive change in
   net income, and the question didn't say which; the models' reasoning
   argued both readings. Commit `33a59ba` states the sign convention in every
   question about a signed item (239 of 500 reworded, no gold value changed).
   Sign flips went from 11 to 0, and Sonnet's `net_income_change` score rose
   from 74.5% to 85.1%.
2. **Frontier models' remaining misses are scale, and the longer wording
   added a few.** Sonnet and GPT sometimes answer "128.9" (the table's
   millions) for $128,900,000; this rose slightly after the rewording.
   "Answer in USD" is clear but evidently not emphatic enough. Every frontier
   model is at or above 99.6% with scale and sign forgiven.
3. **The open model's gap is almost all scale.** Qwen3-14B gets 94–100% on
   ratios, margins and growth rates but 11–23% on dollar amounts, because it
   answers with the table's figure in thousands or millions. Forgiving that
   alone takes it from 42.5% to 92.9%. The headroom is real, and it is
   exactly the error the PRD names first, but it is narrow: RL mostly has to
   teach the model to apply "(In thousands)". Milestone 4 should therefore
   (a) report a prompt-only baseline that says "in whole US dollars, not
   thousands or millions" so the RL gain is measured against the cheapest
   alternative, and (b) bring in the harder task families early if the base
   model closes the scale gap quickly.

## Cost

$9.38 for the first frontier pass and $6.60 for the re-ask (about $2 of it
paid twice when two resumed runs overlapped; `run` now refuses a second
process on the same directory). $0 for the base models on Kaggle. Milestone
total: about $16.

## Reproduce

```bash
python -m xbrlbench sample --in data/filings/v3/questions.jsonl --out data/filings/v3/heldout_500.jsonl --n 500
python -m xbrlbench run --in data/filings/v3/heldout_500.jsonl \
    --models anthropic/claude-sonnet-5.5 openai/gpt-5.6-terra google/gemini-3.1-pro-preview qwen/qwen3-14b \
    --workers 4 --max-tokens 16384 --max-cost 20 --out-dir results/m3/frontier-v2
python -m xbrlbench report --in results/m3/frontier-v2/responses.jsonl \
    --questions data/filings/v3/heldout_500.jsonl --out-prefix results/m3/frontier-v2/report
```
