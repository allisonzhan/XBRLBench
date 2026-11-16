# Milestone 4: 20-step GRPO test run (Qwen3-1.7B, Kaggle T4)

Run 2026-11-12 on Kaggle (GPU T4 x2, training on one GPU, plain HF
generation; vLLM server mode hung twice). Settings in run_config.json:
LoRA r=32, lr 2e-5, 20 steps x 4 prompts x 8 completions (80 prompts),
temperature 1.0, non-thinking mode. Evaluation: the v3 500-question
held-out sample, non-thinking, through `xbrlbench run`.

| model | accuracy | 95% CI |
|---|---|---|
| qwen3-1.7b-grpo (20 steps) | 25.2% | 20.6-29.8% |
| qwen3-1.7b-nothink (base) | 22.2% | 17.5-27.3% |
| qwen3-1.7b-nothink-units (base + "whole US dollars" prompt) | 19.4% | 15.1-23.9% |

Paired bootstrap over companies: trained minus base = +3.0 pts (0.4 to 5.6).

- The gain is almost all format on ratio questions: current_ratio 48.8% ->
  90.7%, operating_margin 44.7% -> 57.9%.
- Scale errors (the main failure) are untouched: 206 vs 216 responses.
- The units prompt makes the base model worse, so the cheapest fix does not
  close the gap.
- The adapter (adapter/, 70 MB) is in m4.zip in the project files, not in git.
