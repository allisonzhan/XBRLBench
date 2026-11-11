# XBRLBench results

500 questions, 4000 graded responses, graded with each question's own tolerance (see docs/SCHEMA.md).

## Accuracy by model x difficulty

| model | T1 | T2 | T3 | overall |
|---|---|---|---|---|
| anthropic/claude-sonnet-5.5 | 98.8% | 100.0% | 84.6% | 96.6% |
| google/gemini-3.1-pro-preview | 100.0% | 100.0% | 98.9% | 99.8% |
| openai/gpt-5.6-terra | 98.5% | 99.3% | 97.8% | 98.6% |
| qwen/qwen3-14b | 20.5% | 98.0% | 18.7% | 43.4% |
| qwen3-1.7b | 10.0% | 74.7% | 5.5% | 28.6% |
| qwen3-1.7b-nothink | 8.5% | 50.7% | 6.6% | 20.8% |
| qwen3-4b | 10.8% | 86.7% | 11.0% | 33.6% |
| qwen3-4b-nothink | 10.0% | 84.7% | 7.7% | 32.0% |

## Accuracy with 95% confidence intervals (bootstrap over company)

| model | n | accuracy | 95% CI | invalid | vs. best model (95% CI) | cost | mean output tokens |
|---|---|---|---|---|---|---|---|
| google/gemini-3.1-pro-preview | 500 | 99.8% | 99.4-100.0% | 0 | best | $5.35 | 719 |
| openai/gpt-5.6-terra | 500 | 98.6% | 97.6-99.4% | 0 | -1.2 pts (-2.3 to -0.2) | $1.51 | 109 |
| anthropic/claude-sonnet-5.5 | 500 | 96.6% | 94.9-98.1% | 0 | -3.2 pts (-4.8 to -1.7) | $2.20 | 212 |
| qwen/qwen3-14b | 500 | 43.4% | 37.9-49.3% | 27 | -56.4 pts (-61.9 to -50.5) | $0.28 | 1953 |
| qwen3-4b | 500 | 33.6% | 28.3-39.2% | 21 | -66.2 pts (-71.5 to -60.5) | n/a | 1537 |
| qwen3-4b-nothink | 500 | 32.0% | 26.8-37.4% | 0 | -67.8 pts (-72.9 to -62.4) | n/a | 131 |
| qwen3-1.7b | 500 | 28.6% | 23.9-33.7% | 6 | -71.2 pts (-75.7 to -66.2) | n/a | 1490 |
| qwen3-1.7b-nothink | 500 | 20.8% | 16.5-25.4% | 10 | -79.0 pts (-83.3 to -74.4) | n/a | 69 |

## Why answers missed

Mechanical slips are a wrong scale (an answer left in the table's thousands or millions) or a flipped sign; the last column forgives them.

| model | invalid | scale | sign | scale and sign | percent vs fraction | other | accuracy forgiving scale and sign |
|---|---|---|---|---|---|---|---|
| google/gemini-3.1-pro-preview | 0 | 0 | 1 | 0 | 0 | 0 | 100.0% |
| openai/gpt-5.6-terra | 0 | 1 | 5 | 0 | 0 | 1 | 99.8% |
| anthropic/claude-sonnet-5.5 | 0 | 10 | 6 | 0 | 0 | 1 | 99.8% |
| qwen/qwen3-14b | 27 | 237 | 3 | 12 | 2 | 2 | 93.8% |
| qwen3-4b | 21 | 245 | 4 | 24 | 17 | 21 | 88.2% |
| qwen3-4b-nothink | 0 | 236 | 31 | 45 | 0 | 28 | 94.4% |
| qwen3-1.7b | 6 | 210 | 20 | 31 | 3 | 87 | 80.8% |
| qwen3-1.7b-nothink | 10 | 205 | 17 | 39 | 3 | 122 | 73.0% |

## Accuracy by task family

| task family | n | anthropic/claude-sonnet-5.5 | google/gemini-3.1-pro-preview | openai/gpt-5.6-terra | qwen/qwen3-14b | qwen3-1.7b | qwen3-1.7b-nothink | qwen3-4b | qwen3-4b-nothink |
|---|---|---|---|---|---|---|---|---|---|
| current_ratio | 43 | 100.0% | 100.0% | 97.7% | 100.0% | 100.0% | 48.8% | 97.7% | 100.0% |
| free_cash_flow | 40 | 97.5% | 100.0% | 100.0% | 25.0% | 7.5% | 5.0% | 10.0% | 7.5% |
| gross_margin | 24 | 100.0% | 100.0% | 100.0% | 100.0% | 91.7% | 95.8% | 75.0% | 100.0% |
| lookup_assets | 60 | 100.0% | 100.0% | 100.0% | 23.3% | 15.0% | 11.7% | 11.7% | 13.3% |
| lookup_liabilities_current | 43 | 100.0% | 100.0% | 97.7% | 20.9% | 14.0% | 16.3% | 16.3% | 16.3% |
| lookup_net_income | 51 | 98.0% | 100.0% | 98.0% | 23.5% | 5.9% | 2.0% | 5.9% | 5.9% |
| lookup_operating_cash_flow | 59 | 100.0% | 100.0% | 96.6% | 15.3% | 3.4% | 3.4% | 10.2% | 3.4% |
| lookup_revenue | 46 | 95.7% | 100.0% | 100.0% | 19.6% | 13.0% | 10.9% | 10.9% | 13.0% |
| net_income_change | 51 | 74.5% | 98.0% | 96.1% | 13.7% | 3.9% | 7.8% | 11.8% | 7.8% |
| operating_margin | 38 | 100.0% | 100.0% | 100.0% | 94.7% | 57.9% | 39.5% | 73.7% | 65.8% |
| revenue_growth | 45 | 100.0% | 100.0% | 100.0% | 97.8% | 55.6% | 37.8% | 93.3% | 77.8% |

## Accuracy by sector (SIC division)

| sector (SIC division) | n | anthropic/claude-sonnet-5.5 | google/gemini-3.1-pro-preview | openai/gpt-5.6-terra | qwen/qwen3-14b | qwen3-1.7b | qwen3-1.7b-nothink | qwen3-4b | qwen3-4b-nothink |
|---|---|---|---|---|---|---|---|---|---|
| Agriculture | 7 | 100.0% | 100.0% | 100.0% | 42.9% | 42.9% | 42.9% | 42.9% | 42.9% |
| Construction | 14 | 100.0% | 100.0% | 100.0% | 42.9% | 35.7% | 35.7% | 35.7% | 35.7% |
| Finance | 100 | 96.0% | 100.0% | 100.0% | 23.0% | 12.0% | 7.0% | 14.0% | 15.0% |
| Manufacturing | 184 | 94.6% | 99.5% | 96.7% | 41.3% | 27.2% | 19.0% | 33.2% | 30.4% |
| Mining | 11 | 100.0% | 100.0% | 100.0% | 100.0% | 36.4% | 45.5% | 72.7% | 45.5% |
| Retail | 19 | 94.7% | 100.0% | 100.0% | 31.6% | 26.3% | 15.8% | 21.1% | 21.1% |
| Services | 124 | 99.2% | 100.0% | 100.0% | 58.1% | 40.3% | 30.6% | 45.2% | 45.2% |
| Transport & utilities | 13 | 100.0% | 100.0% | 92.3% | 61.5% | 46.2% | 30.8% | 53.8% | 46.2% |
| Wholesale | 27 | 96.3% | 100.0% | 100.0% | 44.4% | 29.6% | 14.8% | 37.0% | 37.0% |
| unknown | 1 | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |

## Accuracy by company size

| company size | n | anthropic/claude-sonnet-5.5 | google/gemini-3.1-pro-preview | openai/gpt-5.6-terra | qwen/qwen3-14b | qwen3-1.7b | qwen3-1.7b-nothink | qwen3-4b | qwen3-4b-nothink |
|---|---|---|---|---|---|---|---|---|---|
| large (>$10B) | 48 | 95.8% | 100.0% | 100.0% | 29.2% | 18.8% | 18.8% | 22.9% | 27.1% |
| mid ($1-10B) | 162 | 96.9% | 100.0% | 98.1% | 37.0% | 30.2% | 19.8% | 30.2% | 30.2% |
| small (<$1B assets) | 133 | 97.7% | 99.2% | 97.0% | 51.1% | 25.6% | 18.0% | 35.3% | 27.8% |
| unknown | 157 | 95.5% | 100.0% | 100.0% | 47.8% | 32.5% | 24.8% | 38.9% | 38.9% |

## Accuracy by form

| form | n | anthropic/claude-sonnet-5.5 | google/gemini-3.1-pro-preview | openai/gpt-5.6-terra | qwen/qwen3-14b | qwen3-1.7b | qwen3-1.7b-nothink | qwen3-4b | qwen3-4b-nothink |
|---|---|---|---|---|---|---|---|---|---|
| 10-K | 45 | 97.8% | 100.0% | 100.0% | 66.7% | 53.3% | 44.4% | 53.3% | 53.3% |
| 10-Q | 455 | 96.5% | 99.8% | 98.5% | 41.1% | 26.2% | 18.5% | 31.6% | 29.9% |

## Findings

- Easy (T1) -> hard (T3) accuracy drop, all models combined: 44.6% -> 41.3%.
- Single-step (direct retrieval) vs. multi-step reasoning, all models combined: 44.6% vs. 69.6%.
- anthropic/claude-sonnet-5.5: 98.8% on T1 vs. 84.6% on T3.
- google/gemini-3.1-pro-preview: 100.0% on T1 vs. 98.9% on T3.
- openai/gpt-5.6-terra: 98.5% on T1 vs. 97.8% on T3.
- qwen/qwen3-14b: 20.5% on T1 vs. 18.7% on T3.
- qwen3-1.7b: 10.0% on T1 vs. 5.5% on T3.
- qwen3-1.7b-nothink: 8.5% on T1 vs. 6.6% on T3.
- qwen3-4b-nothink: 10.0% on T1 vs. 7.7% on T3.
- 64/4000 responses were invalid/unparseable (excluded from accuracy).
