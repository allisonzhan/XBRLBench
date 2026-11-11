# XBRLBench results

500 questions, 2000 graded responses, graded with each question's own tolerance (see docs/SCHEMA.md).

## Accuracy by model x difficulty

| model | T1 | T2 | T3 | overall |
|---|---|---|---|---|
| qwen3-1.7b | 10.0% | 74.7% | 5.5% | 28.6% |
| qwen3-1.7b-nothink | 8.5% | 50.7% | 6.6% | 20.8% |
| qwen3-4b | 10.8% | 86.7% | 11.0% | 33.6% |
| qwen3-4b-nothink | 10.0% | 84.7% | 7.7% | 32.0% |

## Accuracy with 95% confidence intervals (bootstrap over company)

| model | n | accuracy | 95% CI | invalid | vs. best model (95% CI) | cost | mean output tokens |
|---|---|---|---|---|---|---|---|
| qwen3-4b | 500 | 33.6% | 28.3-39.2% | 21 | best | n/a | 1537 |
| qwen3-4b-nothink | 500 | 32.0% | 26.8-37.4% | 0 | -1.6 pts (-4.2 to +0.8) | n/a | 131 |
| qwen3-1.7b | 500 | 28.6% | 23.9-33.7% | 6 | -5.0 pts (-8.1 to -2.0) | n/a | 1490 |
| qwen3-1.7b-nothink | 500 | 20.8% | 16.5-25.4% | 10 | -12.8 pts (-16.5 to -9.5) | n/a | 69 |

## Why answers missed

Mechanical slips are a wrong scale (an answer left in the table's thousands or millions) or a flipped sign; the last column forgives them.

| model | invalid | scale | sign | scale and sign | percent vs fraction | other | accuracy forgiving scale and sign |
|---|---|---|---|---|---|---|---|
| qwen3-4b | 21 | 245 | 4 | 24 | 17 | 21 | 88.2% |
| qwen3-4b-nothink | 0 | 236 | 31 | 45 | 0 | 28 | 94.4% |
| qwen3-1.7b | 6 | 210 | 20 | 31 | 3 | 87 | 80.8% |
| qwen3-1.7b-nothink | 10 | 205 | 17 | 39 | 3 | 122 | 73.0% |

## Accuracy by task family

| task family | n | qwen3-1.7b | qwen3-1.7b-nothink | qwen3-4b | qwen3-4b-nothink |
|---|---|---|---|---|---|
| current_ratio | 43 | 100.0% | 48.8% | 97.7% | 100.0% |
| free_cash_flow | 40 | 7.5% | 5.0% | 10.0% | 7.5% |
| gross_margin | 24 | 91.7% | 95.8% | 75.0% | 100.0% |
| lookup_assets | 60 | 15.0% | 11.7% | 11.7% | 13.3% |
| lookup_liabilities_current | 43 | 14.0% | 16.3% | 16.3% | 16.3% |
| lookup_net_income | 51 | 5.9% | 2.0% | 5.9% | 5.9% |
| lookup_operating_cash_flow | 59 | 3.4% | 3.4% | 10.2% | 3.4% |
| lookup_revenue | 46 | 13.0% | 10.9% | 10.9% | 13.0% |
| net_income_change | 51 | 3.9% | 7.8% | 11.8% | 7.8% |
| operating_margin | 38 | 57.9% | 39.5% | 73.7% | 65.8% |
| revenue_growth | 45 | 55.6% | 37.8% | 93.3% | 77.8% |

## Accuracy by sector (SIC division)

| sector (SIC division) | n | qwen3-1.7b | qwen3-1.7b-nothink | qwen3-4b | qwen3-4b-nothink |
|---|---|---|---|---|---|
| Agriculture | 7 | 42.9% | 42.9% | 42.9% | 42.9% |
| Construction | 14 | 35.7% | 35.7% | 35.7% | 35.7% |
| Finance | 100 | 12.0% | 7.0% | 14.0% | 15.0% |
| Manufacturing | 184 | 27.2% | 19.0% | 33.2% | 30.4% |
| Mining | 11 | 36.4% | 45.5% | 72.7% | 45.5% |
| Retail | 19 | 26.3% | 15.8% | 21.1% | 21.1% |
| Services | 124 | 40.3% | 30.6% | 45.2% | 45.2% |
| Transport & utilities | 13 | 46.2% | 30.8% | 53.8% | 46.2% |
| Wholesale | 27 | 29.6% | 14.8% | 37.0% | 37.0% |
| unknown | 1 | 0.0% | 0.0% | 0.0% | 0.0% |

## Accuracy by company size

| company size | n | qwen3-1.7b | qwen3-1.7b-nothink | qwen3-4b | qwen3-4b-nothink |
|---|---|---|---|---|---|
| large (>$10B) | 48 | 18.8% | 18.8% | 22.9% | 27.1% |
| mid ($1-10B) | 162 | 30.2% | 19.8% | 30.2% | 30.2% |
| small (<$1B assets) | 133 | 25.6% | 18.0% | 35.3% | 27.8% |
| unknown | 157 | 32.5% | 24.8% | 38.9% | 38.9% |

## Accuracy by form

| form | n | qwen3-1.7b | qwen3-1.7b-nothink | qwen3-4b | qwen3-4b-nothink |
|---|---|---|---|---|---|
| 10-K | 45 | 53.3% | 44.4% | 53.3% | 53.3% |
| 10-Q | 455 | 26.2% | 18.5% | 31.6% | 29.9% |

## Findings

- Easy (T1) -> hard (T3) accuracy drop, all models combined: 9.8% -> 7.7%.
- Single-step (direct retrieval) vs. multi-step reasoning, all models combined: 9.8% vs. 49.1%.
- qwen3-1.7b: 10.0% on T1 vs. 5.5% on T3.
- qwen3-1.7b-nothink: 8.5% on T1 vs. 6.6% on T3.
- qwen3-4b-nothink: 10.0% on T1 vs. 7.7% on T3.
- 37/2000 responses were invalid/unparseable (excluded from accuracy).
