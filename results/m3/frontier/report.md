# XBRLBench results

500 questions, 2000 graded responses, graded with each question's own tolerance (see docs/SCHEMA.md).

## Accuracy by model x difficulty

| model | T1 | T2 | T3 | overall |
|---|---|---|---|---|
| anthropic/claude-sonnet-5.5 | 98.8% | 100.0% | 84.6% | 96.6% |
| google/gemini-3.1-pro-preview | 100.0% | 100.0% | 98.9% | 99.8% |
| openai/gpt-5.6-terra | 98.5% | 99.3% | 97.8% | 98.6% |
| qwen/qwen3-14b | 20.5% | 98.0% | 18.7% | 43.4% |

## Accuracy with 95% confidence intervals (bootstrap over company)

| model | n | accuracy | 95% CI | invalid | vs. best model (95% CI) | cost | mean output tokens |
|---|---|---|---|---|---|---|---|
| google/gemini-3.1-pro-preview | 500 | 99.8% | 99.4-100.0% | 0 | best | $5.35 | 719 |
| openai/gpt-5.6-terra | 500 | 98.6% | 97.6-99.4% | 0 | -1.2 pts (-2.3 to -0.2) | $1.51 | 109 |
| anthropic/claude-sonnet-5.5 | 500 | 96.6% | 94.9-98.1% | 0 | -3.2 pts (-4.8 to -1.7) | $2.20 | 212 |
| qwen/qwen3-14b | 500 | 43.4% | 37.9-49.3% | 27 | -56.4 pts (-61.9 to -50.5) | $0.28 | 1953 |

## Why answers missed

Mechanical slips are a wrong scale (an answer left in the table's thousands or millions) or a flipped sign; the last column forgives them.

| model | invalid | scale | sign | scale and sign | percent vs fraction | other | accuracy forgiving scale and sign |
|---|---|---|---|---|---|---|---|
| google/gemini-3.1-pro-preview | 0 | 0 | 1 | 0 | 0 | 0 | 100.0% |
| openai/gpt-5.6-terra | 0 | 1 | 5 | 0 | 0 | 1 | 99.8% |
| anthropic/claude-sonnet-5.5 | 0 | 10 | 6 | 0 | 0 | 1 | 99.8% |
| qwen/qwen3-14b | 27 | 237 | 3 | 12 | 2 | 2 | 93.8% |

## Accuracy by task family

| task family | n | anthropic/claude-sonnet-5.5 | google/gemini-3.1-pro-preview | openai/gpt-5.6-terra | qwen/qwen3-14b |
|---|---|---|---|---|---|
| current_ratio | 43 | 100.0% | 100.0% | 97.7% | 100.0% |
| free_cash_flow | 40 | 97.5% | 100.0% | 100.0% | 25.0% |
| gross_margin | 24 | 100.0% | 100.0% | 100.0% | 100.0% |
| lookup_assets | 60 | 100.0% | 100.0% | 100.0% | 23.3% |
| lookup_liabilities_current | 43 | 100.0% | 100.0% | 97.7% | 20.9% |
| lookup_net_income | 51 | 98.0% | 100.0% | 98.0% | 23.5% |
| lookup_operating_cash_flow | 59 | 100.0% | 100.0% | 96.6% | 15.3% |
| lookup_revenue | 46 | 95.7% | 100.0% | 100.0% | 19.6% |
| net_income_change | 51 | 74.5% | 98.0% | 96.1% | 13.7% |
| operating_margin | 38 | 100.0% | 100.0% | 100.0% | 94.7% |
| revenue_growth | 45 | 100.0% | 100.0% | 100.0% | 97.8% |

## Accuracy by sector (SIC division)

| sector (SIC division) | n | anthropic/claude-sonnet-5.5 | google/gemini-3.1-pro-preview | openai/gpt-5.6-terra | qwen/qwen3-14b |
|---|---|---|---|---|---|
| Agriculture | 7 | 100.0% | 100.0% | 100.0% | 42.9% |
| Construction | 14 | 100.0% | 100.0% | 100.0% | 42.9% |
| Finance | 100 | 96.0% | 100.0% | 100.0% | 23.0% |
| Manufacturing | 184 | 94.6% | 99.5% | 96.7% | 41.3% |
| Mining | 11 | 100.0% | 100.0% | 100.0% | 100.0% |
| Retail | 19 | 94.7% | 100.0% | 100.0% | 31.6% |
| Services | 124 | 99.2% | 100.0% | 100.0% | 58.1% |
| Transport & utilities | 13 | 100.0% | 100.0% | 92.3% | 61.5% |
| Wholesale | 27 | 96.3% | 100.0% | 100.0% | 44.4% |
| unknown | 1 | 100.0% | 100.0% | 100.0% | 0.0% |

## Accuracy by company size

| company size | n | anthropic/claude-sonnet-5.5 | google/gemini-3.1-pro-preview | openai/gpt-5.6-terra | qwen/qwen3-14b |
|---|---|---|---|---|---|
| large (>$10B) | 48 | 95.8% | 100.0% | 100.0% | 29.2% |
| mid ($1-10B) | 162 | 96.9% | 100.0% | 98.1% | 37.0% |
| small (<$1B assets) | 133 | 97.7% | 99.2% | 97.0% | 51.1% |
| unknown | 157 | 95.5% | 100.0% | 100.0% | 47.8% |

## Accuracy by form

| form | n | anthropic/claude-sonnet-5.5 | google/gemini-3.1-pro-preview | openai/gpt-5.6-terra | qwen/qwen3-14b |
|---|---|---|---|---|---|
| 10-K | 45 | 97.8% | 100.0% | 100.0% | 66.7% |
| 10-Q | 455 | 96.5% | 99.8% | 98.5% | 41.1% |

## Findings

- Easy (T1) -> hard (T3) accuracy drop, all models combined: 79.4% -> 75.0%.
- Single-step (direct retrieval) vs. multi-step reasoning, all models combined: 79.4% vs. 90.1%.
- anthropic/claude-sonnet-5.5: 98.8% on T1 vs. 84.6% on T3.
- google/gemini-3.1-pro-preview: 100.0% on T1 vs. 98.9% on T3.
- openai/gpt-5.6-terra: 98.5% on T1 vs. 97.8% on T3.
- qwen/qwen3-14b: 20.5% on T1 vs. 18.7% on T3.
- 27/2000 responses were invalid/unparseable (excluded from accuracy).
