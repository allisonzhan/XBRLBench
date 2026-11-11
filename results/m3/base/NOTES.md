# base: Qwen3 base models on the 500-question held-out sample (Kaggle)

These runs used the free Kaggle notebook `notebooks/m3_base_models.ipynb` at
a511070 (vLLM, 2x T4, max_tokens 8192, temperature 0). Each model was run with
thinking on (`default`) and off (`nothink`). The files were unzipped from
`m3_base.zip`; the vLLM server logs for the non-thinking runs are in `vllm_logs/`.

Question wording: the Kaggle dataset held the **v2** `heldout_500.jsonl`. These
are the same 500 ids with the same gold values, but 239 questions use the wording
from before 33a59ba, without the sign convention. The like-for-like frontier
comparison is therefore `../frontier` (v2 wording), not `../frontier-v2`.
`report.*` and `errors.*` were regenerated locally with
`--questions data/filings/v3/heldout_500.jsonl`. That file only backfills
metadata; grading uses each row's own gold value.
