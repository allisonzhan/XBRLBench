# Run metadata & results directory structure

Every `python -m xbrlbench run` writes into its own directory instead of a
shared file, so a run is never silently overwritten:

```
results/
  runs/                        <- gitignored: local scratch output
    20260909T023000Z/
      responses.jsonl
      metadata.json
  baseline/                    <- committed: a specific, reproducible example run
    responses.jsonl
    report_summary.json
    report_summary.csv
    report_incorrect.jsonl
```

`results/runs/` is gitignored — it's local scratch output, expected to
accumulate and be cleaned up freely. `results/baseline/` is the one run
intentionally committed to the repo as a citable, reproducible example (see
the README's Results section and the "recompute baseline" commit).

Re-running `xbrlbench run` against an `--out-dir` that already has a
`responses.jsonl` requires `--resume`; without it, the run refuses to start
rather than silently overwriting or appending.

## metadata.json

| field                | meaning                                                                                  |
|----------------------|---------------------------------------------------------------------------------------------|
| `run_id`             | the run's directory name — a UTC timestamp, e.g. `20260909T023000Z`.                       |
| `started_at` / `completed_at` | ISO 8601 UTC timestamps. `completed_at` is `null` until the run finishes.        |
| `xbrlbench_version`  | the package's code version (`xbrlbench.__version__`).                                      |
| `benchmark_version`  | the question bank's content version (`xbrlbench.BENCHMARK_VERSION`) — see docs/SCHEMA.md.  |
| `git_commit`         | the repo's current commit hash at run time, if this is a git checkout with `git` available; `null` otherwise. Best-effort — never blocks a run. |
| `models`             | the exact model IDs sent to OpenRouter for this run.                                       |
| `temperature`        | generation temperature (`xbrlbench.inference.TEMPERATURE`, currently `0`).                 |
| `max_tokens`         | generation token cap; `null` if unset (provider default applies).                          |
| `prompt_version`     | identifies the system prompt template (`xbrlbench.inference.PROMPT_VERSION`) — bumped whenever the prompt wording changes materially, so old and new runs aren't silently treated as prompted identically. |
| `question_count`     | number of questions loaded for this run (after `--limit`, if given).                       |
| `response_count`     | total responses in `responses.jsonl` once the run completes; `null` until then.            |
| `resume`             | whether this run was started with `--resume`.                                              |

## Reproducing a run

`git_commit` + `benchmark_version` + `xbrlbench_version` together identify
everything code- and data-side that could affect a result. To reproduce a
run: `git checkout <git_commit>`, confirm `python -m xbrlbench validate`
still passes against `data/questions.jsonl`, then re-run with the same
`models` (and `--limit`, if the original run used one).
