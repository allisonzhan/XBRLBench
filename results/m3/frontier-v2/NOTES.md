# frontier-v2: held-out sample re-asked after the sign-convention rewording

Questions: `data/filings/v3/heldout_500.jsonl`, with the same 500 ids as
`results/m3/heldout_500_ids.txt` and gold values identical to v2. 239 of them were
reworded by 33a59ba, which states the sign convention for net income,
operating cash flow, operating margin, free cash flow and net income change.

- `responses.jsonl`: the raw log. It starts with 1,044 rows copied from
  `../frontier/responses.jsonl` (the latest row per id+model) for the 261
  questions whose text is unchanged. Every other row is a new call.
- `responses_scored.jsonl`: one successful row per (id, model) for the 480
  questions all four models answered. `report.*` and `errors.*` are built
  from this file.
- `excluded_ids.txt`: 20 reworded questions left out because at least one
  model's call never succeeded (the OpenRouter key hit its credit limit).
  None of them is scored, so a missing call is never counted as a wrong
  answer.

New-call spend recorded in `responses.jsonl`: $6.60 (Gemini $3.97, Sonnet $1.49,
GPT $0.99, Qwen $0.15). About $2 of that was paid twice: two `run --resume`
processes overlapped for a while. Both asked the same questions. Grading
keeps the latest row per (id, model), as usual. One row was corrupted by the
overlap and removed; error rows that came after a successful answer were also
removed.

The `cost` column in `report.md` sums each scored row's own cost, including the
rows copied from `../frontier`, so it is not this run's spend.
