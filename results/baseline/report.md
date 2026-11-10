# XBRLBench results

68 questions, 272 graded responses, graded with each question's own tolerance (see docs/SCHEMA.md).

## Accuracy by model x difficulty

| model | T1 | T2 | T3 | overall |
|---|---|---|---|---|
| anthropic/claude-sonnet-4.5 | 100.0% | 100.0% | 100.0% | 100.0% |
| google/gemini-2.5-pro | 100.0% | 100.0% | 100.0% | 100.0% |
| openai/gpt-4o-2024-11-20 | 100.0% | 100.0% | 70.0% | 91.2% |
| qwen/qwen-2.5-72b-instruct | 100.0% | 96.4% | 85.0% | 94.1% |

## Findings

- Easy (T1) -> hard (T3) accuracy drop, all models combined: 100.0% -> 88.8%.
- Single-step (direct retrieval) vs. multi-step reasoning, all models combined: 99.2% vs. 94.1%.
- openai/gpt-4o-2024-11-20: 100.0% on T1 vs. 70.0% on T3.
- qwen/qwen-2.5-72b-instruct: 100.0% on T1 vs. 85.0% on T3.
- 1/272 responses were invalid/unparseable (excluded from accuracy).
