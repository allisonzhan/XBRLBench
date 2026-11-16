# Milestone 4: first 300-step run (collapsed)

Same setup as results/m4/test20 with MAX_STEPS=300 (lr 2e-5, beta 0,
reward = accuracy + 0.1 format reward for a well-formed wrong answer).

Held-out accuracy: 0.0% (base 22.0%, base + units prompt 19.4%). Every
held-out answer is "ANSWER: 12.34", the example in the system prompt.

From the logged training completions (8 per step, binned by 25 steps):

| steps | accuracy | bare "ANSWER: x" replies | constant 12.34 |
|---|---|---|---|
| 1-25 | 0.18 | 26% | 0% |
| 26-50 | 0.25 | 6% | 0% |
| 51-75 | 0.26 | 36% | 0% |
| 76-100 | 0.11 | 100% | 6% |
| 101-125 | 0.00 | 100% | 36% |
| 126-150 | 0.00 | 100% | 83% |
| 151-300 | ~0.00 | 100% | 93-100% |

Cause: the format reward. A constant wrong answer earned a sure 0.1, while
attempting a dollar figure usually earned 0 (scale error). Once all eight
completions in a group match, the group has no reward variance and GRPO
gets no gradient, so the collapse is permanent. Fixed in scripts/train_grpo.py
(format weight 0, KL beta 0.02, lr 1e-5).
