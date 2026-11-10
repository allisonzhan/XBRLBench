# PRD: XBRL-RL — training a small model to read real filings

Status: draft, 2026-11-10 · Owner: Allison

## Problem

Companies are putting LLMs on financial filings, but models still make
basic, costly mistakes: wrong scale (thousands vs. millions), a look-alike
line item, the wrong year. Improving that with reinforcement learning needs
tasks whose answers can be checked automatically and in bulk. Hand-labelled
finance datasets (FinQA, TAT-QA, FinanceBench) are small, static and
contaminated, so they can't serve as training environments.

SEC XBRL solves the labelling problem: every number in every filing is
tagged by the company itself. That gives unlimited, exactly checkable tasks,
refreshed every quarter.

## Goal

Ship an open RL environment built on XBRL, and use it to train a small open
model that reads filings as well as frontier models.

**Headline result:** a ~3B open model, trained with RL on generated XBRL
tasks, reaches frontier-model accuracy on questions about real 10-K filings
submitted after the training cutoff, from companies it never trained on.

## Audience

The result is aimed at AI labs and ML engineering teams. It demonstrates the
full loop they hire for: environment design, a reward that can't be gamed,
training, and an evaluation clean of contamination.

## Success metrics

| Metric | Target |
| --- | --- |
| Held-out accuracy gain over the untrained base model | at least +20 points |
| Held-out accuracy vs. best frontier model | within its 95% confidence interval, or above |
| General-capability regression | no meaningful drop on one general benchmark |
| Ground-truth correctness | above 99% on a 1,000-task hand-checked sample |
| Total spend | under ~$100 |

## Scope

### In

1. **Correct data.** Fiscal-period selection fixed, validation that catches
   period mix-ups, bank regenerated (v0.3).
2. **Real-filing task generator.** Inline-XBRL 10-K HTML from EDGAR; tags
   stripped from the statement tables to form the context; truth read from
   the same tags. At least 500 filers across sectors and sizes. Prompts
   contain only the 1–2 tables a question needs.
3. **Task families**, easiest to hardest: single lookups; ratios and
   margins; YoY change and CAGR; derived metrics (free cash flow, EBITDA,
   ROE); finding a planted arithmetic error via calculation relationships;
   abstaining when a figure isn't reported.
4. **Splits.** Train on earlier filings from one set of companies; test on
   later filings (after model cutoffs) from different companies; plus an
   anonymized variant to separate reading from memory.
5. **Reward.** 1 if the parsed answer is within the unit-aware tolerance
   (reuse `xbrlbench.grading`), else 0; small format reward; zero for answers
   with several numbers; no credit for ×1,000 scale errors.
6. **Training.** Debug on a 1.5B model, main run on a 3B model; LoRA plus
   GRPO (TRL or verl); free Kaggle/Colab GPUs or credits first.
7. **Evaluation.** About 500 held-out questions; base model plus 2–3
   frontier models; bootstrap confidence intervals; breakdown by task family,
   sector and company size; transfer check on FinQA/TAT-QA.
8. **Release.** Model and dataset on Hugging Face, environment in this repo,
   a write-up.

### Out (for now)

- 7B or larger training runs (stretch goal only if credits appear).
- Non-US filings (ESEF/IFRS).
- Agentic EDGAR search tasks.
- A hosted leaderboard.

## Milestones

| # | Milestone | Exit criterion | Est. |
| --- | --- | --- | --- |
| 1 | Fix the data | No repeated cross-year values; spot checks match 10-Ks; v0.3 | week 1 |
| 2 | Real-filing generator | 500+ filers; 1,000 sampled tasks above 99% correct | weeks 2–3 |
| 3 | Baselines | Results table with CIs; base model clearly below frontier (headroom) | week 4 |
| 4 | RL debug on 1.5B | Held-out accuracy clearly improves | weeks 5–6 |
| 5 | Main 3B run + ablations | Headline before/after numbers | weeks 7–8 |
| 6 | Release + write-up | Public model, dataset, post | weeks 9–10 |

## Risks

| Risk | Mitigation |
| --- | --- |
| No headroom: base model already strong | Milestone 3 gate; lead with the harder task families and real contexts |
| Reward hacking | Strict parsing; zero for multiple numbers; manual review of high-reward samples |
| Train/test leakage | Split by company and filing date; anonymized variant |
| Company tagging errors corrupt truth | Keep facts consistent with calculation relationships; drop later-amended facts |
| Messy 10-K HTML | Primary statements first; existing XBRL parsing library; hand-check samples |
| Compute overrun | Small-model iteration first; hard spend cap per milestone |

## Open questions

- Which base model family (license, tokenizer handling of numbers)?
- Which XBRL/HTML parsing library to adopt for milestone 2?
- Cutoff date for the held-out split, given the frontier models chosen?
