# Benchmark schema

`data/questions.jsonl` is the benchmark: one JSON object per line, produced
by `xbrlbench.generation` (`python -m xbrlbench generate`) and consumed
verbatim by `xbrlbench.inference` and `xbrlbench.grading`. Nothing in this
repo renames these fields — treat this file as the schema's source of truth,
mirrored by the `Question` dataclass in [`xbrlbench/schema.py`](../xbrlbench/schema.py).

## Fields

| field             | type    | meaning                                                                                     |
|-------------------|---------|-----------------------------------------------------------------------------------------------|
| `id`              | string  | Stable, unique question id: `{ticker}-{fiscal_year}-{tier}-{n}`, e.g. `AAPL-2023-T2-2`.       |
| `ticker`          | string  | Company ticker the question is about.                                                        |
| `fiscal_year`     | int     | Fiscal year the question is about.                                                            |
| `tier`            | string  | Difficulty tier: `T1`, `T2`, or `T3` — see [Difficulty tiers](#difficulty-tiers).              |
| `question`        | string  | The prompt shown to the model, appended after `context`.                                     |
| `gold_value`      | number  | Verified gold answer, computed directly from XBRL (rounded to 4 decimals).                   |
| `gold_unit`       | string  | `USD`, `ratio`, or `percent` — the unit the gold answer (and expected model answer) is in.   |
| `source_concept`  | string  | The us-gaap XBRL concept(s) the gold answer was derived from — provenance, not shown to the model. |
| `context`         | string  | The messy rendered statement snippet the model actually sees.                                |
| `reasoning_type`  | string  | What kind of reasoning the question requires — see [Reasoning types](#reasoning-types).      |
| `tolerance`       | number  | Per-question numeric tolerance. Currently informational — every question is set to the historical global default (`0.01`); grading still applies a single CLI-wide `--epsilon`. `xbrlbench.grading` starts honoring this field, and where the value needs to change per `gold_unit` (e.g. an absolute tolerance for `ratio`/`percent` instead of relative), when the grading logic is hardened — see the `fix: harden numeric answer grading` commit. |

## Difficulty tiers

- **T1 — single lookup.** One value, directly present in the snippet (e.g.
  total assets, net income). Tests basic extraction plus the unit trap: the
  snippet is presented "in thousands" but the gold answer — and the answer
  the model must give — is in raw USD.
- **T2 — computation.** Requires combining two or more extracted values with
  a formula the question states explicitly (current ratio, gross margin, YoY
  revenue growth). Tests arithmetic on correctly-extracted, correctly-scaled
  inputs.
- **T3 — distractor / multi-hop.** The snippet contains a similarly-named
  sibling line item (e.g. total liabilities vs. total *current* liabilities)
  or requires reasoning across two fiscal years (e.g. an asset delta). Tests
  whether the model locates the *correct* line rather than a plausible
  near-miss.

## Reasoning types

Four categories currently describe every question template in
`xbrlbench.generation.make_questions`. This list is deliberately exhaustive,
not aspirational — a category is added only when a new question template
actually needs it (`xbrlbench.schema.VALID_REASONING_TYPES` is the
enforced set; `xbrlbench.validation`, added in a later commit, rejects any
other value).

| `reasoning_type`           | what it requires                                                              | example (from the bank)                                             |
|------------------------------|--------------------------------------------------------------------------------|-------------------------------------------------------------------------|
| `direct_retrieval`         | Locate one value in the snippet and unit-descale it (×1000). No combining facts. | "What were AAPL's total assets for fiscal year 2023?"                |
| `ratio`                    | Combine two values from the *same* period by division into a financial ratio. | "What was AAPL's current ratio for fiscal year 2023?"                |
| `percentage_change`        | `(new - old) / old` across two periods, expressed as a percentage.            | "What was AAPL's year-over-year revenue growth from FY2022 to FY2023?" |
| `multi_period_comparison`  | A value compared/subtracted across two fiscal years, not expressed as a percentage. | "By how much did AAPL's total assets change from FY2022 to FY2023?"  |

Note the T3 "distractor" question (current *current* liabilities, next to a
total-liabilities sibling line) is tagged `direct_retrieval` — it's still a
single-value lookup; what makes it T3-difficulty is the nearby distractor
line, not extra computation. Tier and reasoning type are independent axes:
tier is about *how hard it is to get right*, reasoning type is about *what
kind of operation is required*.

## Provenance

`source_concept` records the us-gaap XBRL concept(s) `xbrlbench.generation`
computed the gold answer from (e.g. `Assets`, `AssetsCurrent/LiabilitiesCurrent`,
or an annotated distractor like `LiabilitiesCurrent (distractor: Liabilities)`
for T3). It is never shown to the model — only `context` and `question` are.
Because gold values come straight from SEC-filed structured data (see the
main README's Methodology section for the exact XBRL lookup rules), they are
machine-verified, not manually curated.

## Benchmark versioning

`xbrlbench.BENCHMARK_VERSION` (in [`xbrlbench/__init__.py`](../xbrlbench/__init__.py))
identifies the benchmark's *content*, separately from `xbrlbench.__version__`
(the code/package version):

- **Bump `BENCHMARK_VERSION`** when `data/questions.jsonl` changes in a way
  that could change results: added/removed/reworded questions, changed gold
  values, changed schema fields, changed tolerance semantics.
- **Don't bump it** for code-only changes (grading fixes, new CLI commands,
  reporting/tooling) that don't touch the question bank's content.

`v0.1` was the original 68-question bank (`id`..`context` fields only).
`v0.2` adds `reasoning_type` and `tolerance` to every record with no changes
to `question`, `gold_value`, or `gold_unit` — existing results remain valid,
but any tooling built against the `v0.1` field set needs the two new fields.
