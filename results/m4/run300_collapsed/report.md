# XBRLBench results

500 questions, 1500 graded responses, graded with each question's own tolerance (see docs/SCHEMA.md).

## Accuracy by model x difficulty

| model | T1 | T2 | T3 | overall |
|---|---|---|---|---|
| qwen3-1.7b-grpo | 0.0% | 0.0% | 0.0% | 0.0% |
| qwen3-1.7b-nothink | 10.8% | 51.3% | 5.5% | 22.0% |
| qwen3-1.7b-nothink-units | 10.4% | 43.3% | 5.5% | 19.4% |

## Accuracy with 95% confidence intervals (bootstrap over company)

| model | n | accuracy | 95% CI | invalid | vs. best model (95% CI) | cost | mean output tokens |
|---|---|---|---|---|---|---|---|
| qwen3-1.7b-nothink | 500 | 22.0% | 17.1-27.0% | 7 | best | n/a | 69 |
| qwen3-1.7b-nothink-units | 500 | 19.4% | 15.2-23.8% | 12 | -2.6 pts (-5.0 to -0.4) | n/a | 61 |
| qwen3-1.7b-grpo | 500 | 0.0% | 0.0-0.0% | 0 | -22.0 pts (-27.1 to -17.4) | n/a | 10 |

## Why answers missed

Mechanical slips are a wrong scale (an answer left in the table's thousands or millions) or a flipped sign; the last column forgives them.

| model | invalid | scale | sign | scale and sign | percent vs fraction | other | accuracy forgiving scale and sign |
|---|---|---|---|---|---|---|---|
| qwen3-1.7b-nothink | 7 | 216 | 15 | 29 | 2 | 121 | 74.0% |
| qwen3-1.7b-nothink-units | 12 | 213 | 14 | 25 | 2 | 137 | 69.8% |
| qwen3-1.7b-grpo | 0 | 8 | 0 | 1 | 0 | 491 | 1.8% |

## Accuracy by task family

| task family | n | qwen3-1.7b-grpo | qwen3-1.7b-nothink | qwen3-1.7b-nothink-units |
|---|---|---|---|---|
| current_ratio | 43 | 0.0% | 46.5% | 37.2% |
| free_cash_flow | 40 | 0.0% | 5.0% | 5.0% |
| gross_margin | 24 | 0.0% | 95.8% | 91.7% |
| lookup_assets | 60 | 0.0% | 11.7% | 11.7% |
| lookup_liabilities_current | 43 | 0.0% | 16.3% | 16.3% |
| lookup_net_income | 51 | 0.0% | 3.9% | 3.9% |
| lookup_operating_cash_flow | 59 | 0.0% | 11.9% | 11.9% |
| lookup_revenue | 46 | 0.0% | 10.9% | 8.7% |
| net_income_change | 51 | 0.0% | 5.9% | 5.9% |
| operating_margin | 38 | 0.0% | 44.7% | 34.2% |
| revenue_growth | 45 | 0.0% | 37.8% | 31.1% |

## Accuracy by sector (SIC division)

| sector (SIC division) | n | qwen3-1.7b-grpo | qwen3-1.7b-nothink | qwen3-1.7b-nothink-units |
|---|---|---|---|---|
| Agriculture | 7 | 0.0% | 42.9% | 42.9% |
| Construction | 14 | 0.0% | 35.7% | 21.4% |
| Finance | 100 | 0.0% | 7.0% | 7.0% |
| Manufacturing | 184 | 0.0% | 20.7% | 19.0% |
| Mining | 11 | 0.0% | 45.5% | 45.5% |
| Retail | 19 | 0.0% | 15.8% | 10.5% |
| Services | 124 | 0.0% | 32.3% | 26.6% |
| Transport & utilities | 13 | 0.0% | 30.8% | 30.8% |
| Wholesale | 27 | 0.0% | 18.5% | 18.5% |
| unknown | 1 | 0.0% | 0.0% | 0.0% |

## Accuracy by company size

| company size | n | qwen3-1.7b-grpo | qwen3-1.7b-nothink | qwen3-1.7b-nothink-units |
|---|---|---|---|---|
| large (>$10B) | 48 | 0.0% | 18.8% | 14.6% |
| mid ($1-10B) | 162 | 0.0% | 19.8% | 16.0% |
| small (<$1B assets) | 133 | 0.0% | 18.8% | 18.0% |
| unknown | 157 | 0.0% | 28.0% | 25.5% |

## Accuracy by form

| form | n | qwen3-1.7b-grpo | qwen3-1.7b-nothink | qwen3-1.7b-nothink-units |
|---|---|---|---|---|
| 10-K | 45 | 0.0% | 48.9% | 40.0% |
| 10-Q | 455 | 0.0% | 19.3% | 17.4% |

## Findings

- Easy (T1) -> hard (T3) accuracy drop, all models combined: 7.1% -> 3.7%.
- Single-step (direct retrieval) vs. multi-step reasoning, all models combined: 7.1% vs. 21.0%.
- qwen3-1.7b-nothink: 10.8% on T1 vs. 5.5% on T3.
- qwen3-1.7b-nothink-units: 10.4% on T1 vs. 5.5% on T3.
- 19/1500 responses were invalid/unparseable (excluded from accuracy).
