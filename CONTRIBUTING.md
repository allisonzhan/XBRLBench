# Contributing

XBRLBench's questions are generated programmatically from SEC XBRL facts
([`xbrlbench/generation.py`](xbrlbench/generation.py)), not hand-authored —
so "adding a question" almost always means adding a company, and the
question templates apply to it automatically. See
[docs/SCHEMA.md](docs/SCHEMA.md) for the full field reference and
[docs/question_example.json](docs/question_example.json) for one complete,
real record, annotated.

## Adding a company

1. Find the company's 10-digit, zero-padded CIK (SEC's EDGAR full-text
   search, or `data.sec.gov/api/xbrl/companyfacts/CIK<cik>.json` — the file
   exists iff the CIK is right) and add it to `COMPANIES` in
   [`xbrlbench/generation.py`](xbrlbench/generation.py):
   ```python
   COMPANIES = {
       "AAPL": "0000320193",
       ...
       "XOM": "0000034088",   # <- your addition
   }
   ```
2. Regenerate the question bank:
   ```bash
   python -m xbrlbench generate --email you@example.com
   ```
   This re-fetches every company in `COMPANIES`, not just the new one —
   the whole file is regenerated so ids stay consistent.
3. Validate it:
   ```bash
   python -m xbrlbench validate
   ```
   Fix anything reported as an error before continuing. A common one for a
   new filer: `GrossProfit` or `RevenueFromContractWithCustomerExcludingAssessedTax`
   isn't tagged the way `CONCEPTS` in `generation.py` expects — some
   questions for that company/year will just be silently skipped (each
   question template's gold value requires all of its source facts to be
   present; see `make_questions`) rather than error, so check the printed
   per-tier counts look reasonable too.
4. Run the tests:
   ```bash
   pytest -q
   ```
5. If you want model results against the new questions, evaluate them:
   ```bash
   python -m xbrlbench run --tier T1        # or a specific --question-id
   python -m xbrlbench grade --in <the run's responses.jsonl>
   ```

Good candidates for expanding company coverage: filers whose statements are
tagged or laid out differently from the current five (foreign private
issuers, smaller-cap companies, non-calendar fiscal years) — see
[Limitations](README.md#limitations) on why the current five don't cover
that variance.

## Adding a new question template (reasoning type)

Only do this if an existing `reasoning_type` genuinely doesn't describe the
new question — see the "deliberately exhaustive" note in
[docs/SCHEMA.md](docs/SCHEMA.md#reasoning-types).

1. Add a new `q(...)` call inside `make_questions` in `generation.py`,
   following the existing tier/reasoning-type pattern.
2. Add the new category to `VALID_REASONING_TYPES` in
   [`xbrlbench/schema.py`](xbrlbench/schema.py) and document it in
   docs/SCHEMA.md's reasoning-type table.
3. Regenerate, validate, and test as above.
4. This changes benchmark content, so bump `BENCHMARK_VERSION` in
   [`xbrlbench/__init__.py`](xbrlbench/__init__.py) — see docs/SCHEMA.md's
   versioning policy.

## Adding a model

Add its OpenRouter model id to `MODELS` in
[`xbrlbench/inference.py`](xbrlbench/inference.py), or run it ad hoc without
editing anything:

```bash
python -m xbrlbench run --models <provider>/<model-id> --limit 4   # smoke test first
```

## Before opening a PR

```bash
python -m xbrlbench validate
pytest -q
```

Both also run in CI on every PR (see `.github/workflows/ci.yml`) — neither
makes a network call or needs a secret.

If your change affects `data/questions.jsonl` (new company, new question
template, corrected gold value), say so explicitly in the PR description and
bump `BENCHMARK_VERSION` — see docs/SCHEMA.md's versioning policy. If it
changes grading logic in a way that could change existing results, prefer
splitting it into a code commit and a separate data commit that recomputes
`results/baseline/*`, the way this repo's own history does (see `git log`).
