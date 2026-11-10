# XBRLBench results

480 questions, 1920 graded responses, graded with each question's own tolerance (see docs/SCHEMA.md).

## Accuracy by model x difficulty

| model | T1 | T2 | T3 | overall |
|---|---|---|---|---|
| anthropic/claude-sonnet-5.5 | 97.6% | 100.0% | 89.2% | 96.9% |
| google/gemini-3.1-pro-preview | 100.0% | 100.0% | 100.0% | 100.0% |
| openai/gpt-5.6-terra | 98.4% | 98.6% | 96.4% | 98.1% |
| qwen/qwen3-14b | 19.6% | 98.0% | 13.3% | 42.5% |

## Accuracy with 95% confidence intervals (bootstrap over company)

| model | n | accuracy | 95% CI | invalid | vs. best model (95% CI) | cost | mean output tokens |
|---|---|---|---|---|---|---|---|
| google/gemini-3.1-pro-preview | 480 | 100.0% | 100.0-100.0% | 0 | best | $5.25 | 738 |
| openai/gpt-5.6-terra | 480 | 98.1% | 96.8-99.4% | 0 | -1.9 pts (-3.2 to -0.6) | $1.40 | 108 |
| anthropic/claude-sonnet-5.5 | 480 | 96.9% | 94.7-98.8% | 0 | -3.1 pts (-5.3 to -1.2) | $2.13 | 214 |
| qwen/qwen3-14b | 480 | 42.5% | 36.9-48.4% | 31 | -57.5 pts (-63.1 to -51.6) | $0.28 | 2034 |

## Why answers missed

Mechanical slips are a wrong scale (an answer left in the table's thousands or millions) or a flipped sign; the last column forgives them.

| model | invalid | scale | sign | scale and sign | percent vs fraction | other | accuracy forgiving scale and sign |
|---|---|---|---|---|---|---|---|
| google/gemini-3.1-pro-preview | 0 | 0 | 0 | 0 | 0 | 0 | 100.0% |
| openai/gpt-5.6-terra | 0 | 7 | 0 | 0 | 1 | 1 | 99.6% |
| anthropic/claude-sonnet-5.5 | 0 | 15 | 0 | 0 | 0 | 0 | 100.0% |
| qwen/qwen3-14b | 31 | 242 | 0 | 0 | 2 | 1 | 92.9% |

## Accuracy by task family

| task family | n | anthropic/claude-sonnet-5.5 | google/gemini-3.1-pro-preview | openai/gpt-5.6-terra | qwen/qwen3-14b |
|---|---|---|---|---|---|
| current_ratio | 43 | 100.0% | 100.0% | 97.7% | 100.0% |
| free_cash_flow | 36 | 94.4% | 100.0% | 91.7% | 13.9% |
| gross_margin | 24 | 100.0% | 100.0% | 100.0% | 100.0% |
| lookup_assets | 60 | 100.0% | 100.0% | 100.0% | 23.3% |
| lookup_liabilities_current | 43 | 100.0% | 100.0% | 97.7% | 20.9% |
| lookup_net_income | 47 | 95.7% | 100.0% | 100.0% | 23.4% |
| lookup_operating_cash_flow | 54 | 96.3% | 100.0% | 94.4% | 11.1% |
| lookup_revenue | 46 | 95.7% | 100.0% | 100.0% | 19.6% |
| net_income_change | 47 | 85.1% | 100.0% | 100.0% | 12.8% |
| operating_margin | 35 | 100.0% | 100.0% | 97.1% | 94.3% |
| revenue_growth | 45 | 100.0% | 100.0% | 100.0% | 97.8% |

## Accuracy by sector (SIC division)

| sector (SIC division) | n | anthropic/claude-sonnet-5.5 | google/gemini-3.1-pro-preview | openai/gpt-5.6-terra | qwen/qwen3-14b |
|---|---|---|---|---|---|
| Agriculture | 7 | 100.0% | 100.0% | 85.7% | 42.9% |
| Construction | 13 | 100.0% | 100.0% | 100.0% | 38.5% |
| Finance | 97 | 96.9% | 100.0% | 95.9% | 21.6% |
| Manufacturing | 180 | 95.0% | 100.0% | 97.8% | 41.1% |
| Mining | 10 | 100.0% | 100.0% | 100.0% | 100.0% |
| Retail | 18 | 83.3% | 100.0% | 100.0% | 33.3% |
| Services | 117 | 100.0% | 100.0% | 100.0% | 56.4% |
| Transport & utilities | 13 | 100.0% | 100.0% | 100.0% | 53.8% |
| Wholesale | 25 | 100.0% | 100.0% | 100.0% | 48.0% |

## Accuracy by company size

| company size | n | anthropic/claude-sonnet-5.5 | google/gemini-3.1-pro-preview | openai/gpt-5.6-terra | qwen/qwen3-14b |
|---|---|---|---|---|---|
| large (>$10B) | 47 | 89.4% | 100.0% | 97.9% | 27.7% |
| mid ($1-10B) | 156 | 96.8% | 100.0% | 96.8% | 37.8% |
| small (<$1B assets) | 126 | 99.2% | 100.0% | 100.0% | 49.2% |
| unknown | 151 | 97.4% | 100.0% | 98.0% | 46.4% |

## Accuracy by form

| form | n | anthropic/claude-sonnet-5.5 | google/gemini-3.1-pro-preview | openai/gpt-5.6-terra | qwen/qwen3-14b |
|---|---|---|---|---|---|
| 10-K | 42 | 95.2% | 100.0% | 97.6% | 61.9% |
| 10-Q | 438 | 97.0% | 100.0% | 98.2% | 40.6% |

## Findings

- Easy (T1) -> hard (T3) accuracy drop, all models combined: 78.9% -> 74.7%.
- Single-step (direct retrieval) vs. multi-step reasoning, all models combined: 78.9% vs. 90.3%.
- anthropic/claude-sonnet-5.5: 97.6% on T1 vs. 89.2% on T3.
- openai/gpt-5.6-terra: 98.4% on T1 vs. 96.4% on T3.
- qwen/qwen3-14b: 19.6% on T1 vs. 13.3% on T3.
- 31/1920 responses were invalid/unparseable (excluded from accuracy).
