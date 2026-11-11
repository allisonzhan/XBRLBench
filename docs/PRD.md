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

**Headline result:** a ~4B open model (Qwen3-4B), trained with RL on generated XBRL
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
6. **Training.** Debug on Qwen3-1.7B, main run on Qwen3-4B; LoRA plus
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
| 4 | RL debug on Qwen3-1.7B | Held-out accuracy clearly improves | weeks 5–6 |
| 5 | Main Qwen3-4B run + ablations | Headline before/after numbers | weeks 7–8 |
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

## Decisions

Decided 2026-11-10. Model names reflect releases known at that date; revisit
if a clearly better small open model ships before milestone 3.

| Question | Decision | Why | Alternatives considered |
| --- | --- | --- | --- |
| Base model | **Qwen3-4B** for the main run, **Qwen3-1.7B** for RL debugging | Apache-2.0 license, strong math/reasoning for its size, best supported by TRL and verl | Llama 3.2 3B (more restrictive license), SmolLM3-3B (fully open, weaker at math) |
| Filing parser | **A small lxml parser over inline-XBRL `ix:` tags**, with edgartools used only to download filings | Truth extraction is the core asset, so it must be auditable; iXBRL tags (`ix:nonFraction` with `name`, `contextRef`, `scale`, `sign`) are simple to read directly | edgartools end to end (fast start, harder to audit), Arelle (authoritative but heavy and slow) |
| Held-out split | **Company-disjoint, filed on or after 2026-07-01, 10-K and 10-Q**, refreshed with the Feb–Mar 2027 10-K season | Most calendar-year FY2025 10-Ks were filed before the cutoff, so 10-Qs keep the held-out set large and sector-balanced | 10-K only after the cutoff (small, skewed to non-calendar filers), company-disjoint only (weak contamination control) |

Check each compared frontier model's published training cutoff before freezing
the held-out set; move the date later if any cutoff falls after 2026-07-01.

Checked 2026-11-11 for the milestone 3 models: Claude Sonnet 5.5, June 2026
(training data and reliable knowledge, Anthropic's model page); GPT-5.6
Terra, February 2026 (llm-stats.com; OpenAI's own page didn't state it);
Gemini 3.1 Pro, January 2025 (llm-stats.com); Qwen3, released April 2025.
All fall before 2026-07-01, so the cutoff date stands. Sonnet 5.5 leaves only
one month of margin, so recheck it for any model released after mid-2026.
