# Milestone 4: 300-step GRPO run on Qwen3-1.7B (accuracy-only reward)

Kaggle T4, one GPU, plain HF generation. LoRA r=32, lr 1e-5, KL beta 0.02,
reward = accuracy only (format weight 0), temperature 1.0, 300 steps x 4
prompts x 8 completions (1,200 prompts, ~19% of the train split), non-thinking
mode, ~115 s per step. Held-out: v3 500-question sample.

| model | accuracy | 95% CI |
|---|---|---|
| qwen3-1.7b-grpo (300 steps) | 29.6% | 24.1-35.4% |
| qwen3-1.7b-nothink (base) | 22.2% | 17.5-27.4% |
| base + "whole US dollars" prompt | 19.4% | 15.0-23.9% |

Paired bootstrap over companies: trained minus base = +7.4 pts (5.4 to 9.6).

- Gains: current_ratio 48.8 -> 90.7%, operating_margin 44.7 -> 63.2%,
  free_cash_flow 5.0 -> 15.0%, net_income_change 5.9 -> 9.8%, revenue_growth
  37.8 -> 42.2%, gross_margin 95.8 -> 100%. Sign errors 43 -> 12.
- Not fixed: scale. Dollar lookups are unchanged (assets 11.7%, current
  liabilities 16.3%, revenue 10.9%), and scale misses rose 217 -> 245 (some
  former "other" misses became right-but-for-scale).
- Why: frac_reward_zero_std was ~0.9 throughout, i.e. in 9 of 10 groups all 8
  completions scored the same. On dollar questions the model leaves the
  figure in thousands/millions every time, so there is nothing to compare and
  no gradient. Training-batch accuracy rose 0.31 -> 0.49; the 64-prompt
  validation reward plateaued at ~0.34 from step 150.
- No collapse: completions stayed 70-90 tokens, KL 0.01-0.15.
