# XBRLBench results

68 questions, 272 graded responses (epsilon=None).

## Accuracy by model x difficulty

| model | T1 | T2 | T3 | overall |
|---|---|---|---|---|
| anthropic/claude-sonnet-4.5 | 100.0% | 100.0% | 100.0% | 100.0% |
| google/gemini-2.5-pro | 95.0% | 100.0% | 100.0% | 98.5% |
| openai/gpt-4o-2024-11-20 | 100.0% | 100.0% | 60.0% | 88.2% |
| qwen/qwen-2.5-72b-instruct | 100.0% | 100.0% | 90.0% | 97.1% |

## Findings

- Easy (T1) -> hard (T3) accuracy drop, all models combined: 98.8% -> 87.5%.
- Single-step (direct retrieval) vs. multi-step reasoning, all models combined: 98.3% vs. 94.1%.
- openai/gpt-4o-2024-11-20: 100.0% on T1 vs. 60.0% on T3.
- qwen/qwen-2.5-72b-instruct: 100.0% on T1 vs. 90.0% on T3.
