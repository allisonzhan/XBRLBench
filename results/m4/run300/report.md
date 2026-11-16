# XBRLBench results

500 questions, 1500 graded responses, graded with each question's own tolerance (see docs/SCHEMA.md).

## Accuracy by model x difficulty

| model | T1 | T2 | T3 | overall |
|---|---|---|---|---|
| qwen3-1.7b-grpo | 12.0% | 70.7% | 12.1% | 29.6% |
| qwen3-1.7b-nothink | 10.8% | 52.0% | 5.5% | 22.2% |
| qwen3-1.7b-nothink-units | 10.4% | 43.3% | 5.5% | 19.4% |

## Accuracy with 95% confidence intervals (bootstrap over company)

| model | n | accuracy | 95% CI | invalid | vs. best model (95% CI) | cost | mean output tokens |
|---|---|---|---|---|---|---|---|
| qwen3-1.7b-grpo | 500 | 29.6% | 24.1-35.4% | 0 | best | n/a | 78 |
| qwen3-1.7b-nothink | 500 | 22.2% | 17.5-27.4% | 7 | -7.4 pts (-9.6 to -5.4) | n/a | 69 |
| qwen3-1.7b-nothink-units | 500 | 19.4% | 15.0-23.9% | 12 | -10.2 pts (-13.2 to -7.4) | n/a | 62 |

## Why answers missed

Mechanical slips are a wrong scale (an answer left in the table's thousands or millions) or a flipped sign; the last column forgives them.

| model | invalid | scale | sign | scale and sign | percent vs fraction | other | accuracy forgiving scale and sign |
|---|---|---|---|---|---|---|---|
| qwen3-1.7b-grpo | 0 | 245 | 3 | 9 | 3 | 92 | 81.0% |
| qwen3-1.7b-nothink | 7 | 217 | 15 | 28 | 2 | 120 | 74.2% |
| qwen3-1.7b-nothink-units | 12 | 212 | 14 | 26 | 2 | 137 | 69.8% |

## Accuracy by task family

| task family | n | qwen3-1.7b-grpo | qwen3-1.7b-nothink | qwen3-1.7b-nothink-units |
|---|---|---|---|---|
| current_ratio | 43 | 90.7% | 48.8% | 37.2% |
| free_cash_flow | 40 | 15.0% | 5.0% | 5.0% |
| gross_margin | 24 | 100.0% | 95.8% | 91.7% |
| lookup_assets | 60 | 11.7% | 11.7% | 11.7% |
| lookup_liabilities_current | 43 | 16.3% | 16.3% | 16.3% |
| lookup_net_income | 51 | 7.8% | 3.9% | 3.9% |
| lookup_operating_cash_flow | 59 | 13.6% | 11.9% | 11.9% |
| lookup_revenue | 46 | 10.9% | 10.9% | 8.7% |
| net_income_change | 51 | 9.8% | 5.9% | 5.9% |
| operating_margin | 38 | 63.2% | 44.7% | 34.2% |
| revenue_growth | 45 | 42.2% | 37.8% | 31.1% |

## Accuracy by sector (SIC division)

| sector (SIC division) | n | qwen3-1.7b-grpo | qwen3-1.7b-nothink | qwen3-1.7b-nothink-units |
|---|---|---|---|---|
| Agriculture | 7 | 42.9% | 42.9% | 42.9% |
| Construction | 14 | 35.7% | 35.7% | 21.4% |
| Finance | 100 | 9.0% | 7.0% | 7.0% |
| Manufacturing | 184 | 29.3% | 21.2% | 19.0% |
| Mining | 11 | 72.7% | 45.5% | 45.5% |
| Retail | 19 | 21.1% | 15.8% | 10.5% |
| Services | 124 | 42.7% | 32.3% | 26.6% |
| Transport & utilities | 13 | 46.2% | 30.8% | 30.8% |
| Wholesale | 27 | 22.2% | 18.5% | 18.5% |
| unknown | 1 | 0.0% | 0.0% | 0.0% |

## Accuracy by company size

| company size | n | qwen3-1.7b-grpo | qwen3-1.7b-nothink | qwen3-1.7b-nothink-units |
|---|---|---|---|---|
| large (>$10B) | 48 | 20.8% | 18.8% | 14.6% |
| mid ($1-10B) | 162 | 25.3% | 19.8% | 16.0% |
| small (<$1B assets) | 133 | 31.6% | 19.5% | 18.0% |
| unknown | 157 | 35.0% | 28.0% | 25.5% |

## Accuracy by form

| form | n | qwen3-1.7b-grpo | qwen3-1.7b-nothink | qwen3-1.7b-nothink-units |
|---|---|---|---|---|
| 10-K | 45 | 60.0% | 48.9% | 40.0% |
| 10-Q | 455 | 26.6% | 19.6% | 17.4% |

## Findings

- Easy (T1) -> hard (T3) accuracy drop, all models combined: 11.1% -> 7.7%.
- Single-step (direct retrieval) vs. multi-step reasoning, all models combined: 11.1% vs. 37.3%.
- qwen3-1.7b-nothink: 10.8% on T1 vs. 5.5% on T3.
- qwen3-1.7b-nothink-units: 10.4% on T1 vs. 5.5% on T3.
- 19/1500 responses were invalid/unparseable (excluded from accuracy).
