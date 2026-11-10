"""xbrlbench.reporting — turn a graded responses file into the breakdowns
someone actually wants to look at: accuracy by model/difficulty/reasoning
type, valid vs. invalid response counts, which questions failed and for
which model, and a couple of specific comparisons (easy vs. hard tier,
single-step vs. multi-step reasoning, strongest/weakest reasoning category).

Run:
  python -m xbrlbench report
    -> results/report_analysis.json, results/report_analysis.csv,
       results/report.md (a Markdown table ready to paste into a README)

This reads the same raw responses file xbrlbench.grading does (not
grading's output) and grades it itself via xbrlbench.grading.summarize, so
`report` never depends on having run `grade` first, and re-running it with a
different --epsilon never repeats paid API calls.

Older responses files (from before reasoning_type was propagated into
responses.jsonl -- see the grading-hardening commit) are missing that field
on every row, which would otherwise make the by-reasoning-type breakdown
useless. This module backfills reasoning_type by joining each response back
to its question in data/questions.jsonl by id, so the breakdown works
whether or not the responses file itself carries the field.
"""

from __future__ import annotations

import argparse
import csv
import json
import random

from .grading import summarize
from .io_utils import load_jsonl
from .paths import DEFAULT_QUESTIONS_PATH, DEFAULT_REPORT_PREFIX, DEFAULT_RESPONSES_PATH
from .schema import VALID_REASONING_TYPES

# The reasoning-type-based single-step / multi-step split (distinct from the
# tier-based easy/hard split below): direct_retrieval is "locate one value
# and (if needed) unit-descale it" -- no combining facts. Everything else
# requires combining at least two values, which is what "multi-step" means
# here. See docs/SCHEMA.md's reasoning-type table for the full definitions.
SINGLE_STEP_REASONING_TYPES = {"direct_retrieval"}
MULTI_STEP_REASONING_TYPES = VALID_REASONING_TYPES - SINGLE_STEP_REASONING_TYPES

# The tier-based easy/hard split: T1 is the easiest tier (single lookup),
# T3 is the hardest (distractor / multi-hop) -- see docs/SCHEMA.md.
EASY_TIER = "T1"
HARD_TIER = "T3"


def _bucket(rows: list[dict], key: str) -> dict:
    b: dict = {}
    for r in rows:
        cell = b.setdefault(r.get(key), {"n": 0, "correct": 0, "invalid": 0})
        cell["n"] += 1
        if r["status"] == "correct":
            cell["correct"] += 1
        elif r["status"] == "invalid_response":
            cell["invalid"] += 1
    for cell in b.values():
        cell["accuracy"] = cell["correct"] / cell["n"] if cell["n"] else 0.0
    return b


def _grid(rows: list[dict], key1: str, key2: str) -> dict:
    g: dict = {}
    for r in rows:
        cell = g.setdefault((r.get(key1), r.get(key2)), {"n": 0, "correct": 0, "invalid": 0})
        cell["n"] += 1
        if r["status"] == "correct":
            cell["correct"] += 1
        elif r["status"] == "invalid_response":
            cell["invalid"] += 1
    for cell in g.values():
        cell["accuracy"] = cell["correct"] / cell["n"] if cell["n"] else 0.0
    return g


def _accuracy_of(rows: list[dict]) -> float | None:
    if not rows:
        return None
    correct = sum(1 for r in rows if r["status"] == "correct")
    return correct / len(rows)


# Fields backfilled from the question bank when a responses row lacks them:
# reasoning_type (responses predating the field) and the real-filing fields
# the breakdowns below group by.
BACKFILL_FIELDS = ("reasoning_type", "family", "cik", "company", "form", "sic")


def _load_questions(questions_path) -> list[dict]:
    try:
        return load_jsonl(questions_path)
    except (FileNotFoundError, OSError, TypeError):
        return []


def enrich_reasoning_type(rows: list[dict], questions_path, questions: list[dict] | None = None) -> list[dict]:
    """Backfill each row's reasoning_type (and the real-filing fields in
    BACKFILL_FIELDS) by joining on id against the question bank, for a
    responses file that predates a field being propagated into
    responses.jsonl. Shared with xbrlbench.errors."""
    questions = _load_questions(questions_path) if questions is None else questions
    if not questions:
        return rows
    questions_by_id = {q.get("id"): q for q in questions}
    enriched = []
    for row in rows:
        q = questions_by_id.get(row.get("id"))
        if q is not None:
            missing = {f: q[f] for f in BACKFILL_FIELDS if not row.get(f) and q.get(f) is not None}
            if missing:
                row = {**row, **missing}
        enriched.append(row)
    return enriched


# ---------------------------------------------------------------------------
# Real-filing breakdowns: sector and company size
# ---------------------------------------------------------------------------

# SEC SIC code divisions (first two digits), the standard top-level grouping.
SIC_DIVISIONS = (
    (1, 9, "Agriculture"),
    (10, 14, "Mining"),
    (15, 17, "Construction"),
    (20, 39, "Manufacturing"),
    (40, 49, "Transport & utilities"),
    (50, 51, "Wholesale"),
    (52, 59, "Retail"),
    (60, 67, "Finance"),
    (70, 89, "Services"),
    (91, 99, "Public administration"),
)

# Company size by total assets (USD). Fixed edges rather than quantiles, so a
# bucket means the same thing across runs and samples.
SIZE_BUCKETS = ((1e9, "small (<$1B assets)"), (10e9, "mid ($1-10B)"), (float("inf"), "large (>$10B)"))
UNKNOWN = "unknown"


def sic_division(sic) -> str:
    try:
        major = int(str(sic).strip()[:2])
    except (TypeError, ValueError):
        return UNKNOWN
    for lo, hi, name in SIC_DIVISIONS:
        if lo <= major <= hi:
            return name
    return UNKNOWN


def company_assets(questions: list[dict]) -> dict:
    """Company key -> total assets from its latest-period total-assets
    lookup question (the filing's own tagged value)."""
    best: dict = {}
    for q in questions:
        if q.get("family") != "lookup_assets" or q.get("gold_value") is None:
            continue
        key = _company_key(q)
        end = q.get("period_end") or ""
        if key not in best or end > best[key][0]:
            best[key] = (end, q["gold_value"])
    return {k: v for k, (_, v) in best.items()}


def size_bucket(assets) -> str:
    if assets is None:
        return UNKNOWN
    for edge, name in SIZE_BUCKETS:
        if assets < edge:
            return name
    return UNKNOWN


def _company_key(row: dict):
    return row.get("cik") or row.get("ticker")


def add_breakdown_fields(rows: list[dict], questions: list[dict]) -> list[dict]:
    assets = company_assets(questions)
    return [{**r, "sector": sic_division(r.get("sic")) if r.get("sic") else UNKNOWN,
             "size": size_bucket(assets.get(_company_key(r)))} for r in rows]


# ---------------------------------------------------------------------------
# Bootstrap confidence intervals
# ---------------------------------------------------------------------------

N_BOOT = 2000
CI_SEED = 0


def _clusters(rows: list[dict], cluster: str) -> dict:
    """Cluster key -> [n, correct]. Questions about the same company aren't
    independent (one hard table sinks several questions), so the default
    resamples companies, not questions."""
    out: dict = {}
    for i, r in enumerate(rows):
        key = _company_key(r) if cluster == "company" else (r.get("id") or i)
        cell = out.setdefault(key, [0, 0])
        cell[0] += 1
        cell[1] += r["status"] == "correct"
    return out


def _percentiles(values: list[float], lo=0.025, hi=0.975) -> tuple[float, float]:
    values = sorted(values)
    pick = lambda p: values[min(len(values) - 1, max(0, round(p * (len(values) - 1))))]  # noqa: E731
    return pick(lo), pick(hi)


def bootstrap_ci(rows: list[dict], cluster: str = "company", n_boot: int = N_BOOT,
                 seed: int = CI_SEED) -> tuple[float, float] | None:
    """95% percentile-bootstrap CI for accuracy (correct / n)."""
    cells = list(_clusters(rows, cluster).values())
    if not cells:
        return None
    rng = random.Random(seed)
    stats = []
    for _ in range(n_boot):
        n = c = 0
        for _ in cells:
            cn, cc = cells[rng.randrange(len(cells))]
            n += cn
            c += cc
        stats.append(c / n)
    return _percentiles(stats)


def paired_difference(rows_a: list[dict], rows_b: list[dict], cluster: str = "company",
                      n_boot: int = N_BOOT, seed: int = CI_SEED) -> dict | None:
    """Accuracy of A minus accuracy of B on the questions both answered, with
    a paired bootstrap 95% CI (the same resampled clusters for both)."""
    b_by_id = {r.get("id"): r for r in rows_b}
    pairs = [(a, b_by_id[a.get("id")]) for a in rows_a if a.get("id") in b_by_id]
    if not pairs:
        return None
    clusters: dict = {}
    for i, (a, b) in enumerate(pairs):
        key = _company_key(a) if cluster == "company" else (a.get("id") or i)
        cell = clusters.setdefault(key, [0, 0])
        cell[0] += 1
        cell[1] += (a["status"] == "correct") - (b["status"] == "correct")
    cells = list(clusters.values())
    diff = sum(c for _, c in cells) / len(pairs)
    rng = random.Random(seed)
    stats = []
    for _ in range(n_boot):
        n = d = 0
        for _ in cells:
            cn, cd = cells[rng.randrange(len(cells))]
            n += cn
            d += cd
        stats.append(d / n)
    lo, hi = _percentiles(stats)
    return {"n": len(pairs), "difference": diff, "ci95": [lo, hi]}


def model_summary(graded: list[dict], cluster: str = "company") -> dict:
    """Per model: accuracy with a bootstrap CI, spend and token use, and the
    paired gap to the most accurate model."""
    out = {}
    by_model = {m: [r for r in graded if r.get("model") == m] for m in _models_of(graded)}
    for model, rows in by_model.items():
        n = len(rows)
        correct = sum(r["status"] == "correct" for r in rows)
        costs = [r["cost"] for r in rows if isinstance(r.get("cost"), (int, float))]
        toks = [r["completion_tokens"] for r in rows if isinstance(r.get("completion_tokens"), (int, float))]
        out[model] = {
            "n": n,
            "correct": correct,
            "invalid": sum(r["status"] == "invalid_response" for r in rows),
            "accuracy": correct / n if n else 0.0,
            "ci95": list(bootstrap_ci(rows, cluster) or []),
            "cost_usd": round(sum(costs), 4) if costs else None,
            "mean_completion_tokens": round(sum(toks) / len(toks)) if toks else None,
        }
    if out:
        best = max(out, key=lambda m: (out[m]["accuracy"], m))
        for model in out:
            out[model]["vs_best"] = None if model == best else \
                paired_difference(by_model[model], by_model[best], cluster)
            out[model]["best_model"] = best
    return out


def _models_of(graded: list[dict]) -> list[str]:
    return sorted({r.get("model") for r in graded if r.get("model") is not None})


def easy_to_hard_drop(graded: list[dict]) -> dict:
    """Per model (and overall): accuracy on the easiest tier minus accuracy
    on the hardest tier. A large positive drop means the model does fine on
    simple lookups but falls apart as the question gets harder."""
    result = {}
    for model in _models_of(graded) + [None]:
        model_rows = graded if model is None else [r for r in graded if r.get("model") == model]
        easy_acc = _accuracy_of([r for r in model_rows if r.get("tier") == EASY_TIER])
        hard_acc = _accuracy_of([r for r in model_rows if r.get("tier") == HARD_TIER])
        key = "overall" if model is None else model
        result[key] = {
            f"{EASY_TIER}_accuracy": easy_acc,
            f"{HARD_TIER}_accuracy": hard_acc,
            "drop": (easy_acc - hard_acc) if (easy_acc is not None and hard_acc is not None) else None,
        }
    return result


def single_vs_multi_step(graded: list[dict]) -> dict:
    """Per model (and overall): accuracy on direct_retrieval questions vs.
    accuracy on every other (multi-step) reasoning type combined."""
    result = {}
    for model in _models_of(graded) + [None]:
        model_rows = graded if model is None else [r for r in graded if r.get("model") == model]
        single = _accuracy_of([r for r in model_rows if r.get("reasoning_type") in SINGLE_STEP_REASONING_TYPES])
        multi = _accuracy_of([r for r in model_rows if r.get("reasoning_type") in MULTI_STEP_REASONING_TYPES])
        key = "overall" if model is None else model
        result[key] = {
            "single_step_accuracy": single,
            "multi_step_accuracy": multi,
            "drop": (single - multi) if (single is not None and multi is not None) else None,
        }
    return result


def strongest_weakest_reasoning_type(graded: list[dict]) -> dict:
    """Per model (and overall): the reasoning_type with the highest and the
    lowest accuracy, among types this model was actually asked (n > 0)."""
    result = {}
    for model in _models_of(graded) + [None]:
        model_rows = graded if model is None else [r for r in graded if r.get("model") == model]
        by_type = _bucket(model_rows, "reasoning_type")
        by_type = {t: cell for t, cell in by_type.items() if t in VALID_REASONING_TYPES and cell["n"] > 0}
        key = "overall" if model is None else model
        if not by_type:
            result[key] = {"strongest": None, "weakest": None}
            continue
        strongest_type = max(by_type, key=lambda t: (by_type[t]["accuracy"], t))
        weakest_type = min(by_type, key=lambda t: (by_type[t]["accuracy"], t))
        result[key] = {
            "strongest": {"reasoning_type": strongest_type, "accuracy": by_type[strongest_type]["accuracy"]},
            "weakest": {"reasoning_type": weakest_type, "accuracy": by_type[weakest_type]["accuracy"]},
        }
    return result


BREAKDOWNS = (("family", "task family"), ("sector", "sector (SIC division)"),
              ("size", "company size"), ("form", "form"))


def build_report(rows: list[dict], questions_path=DEFAULT_QUESTIONS_PATH, epsilon: float | None = None,
                 cluster: str = "company") -> dict:
    questions = _load_questions(questions_path)
    rows = enrich_reasoning_type(rows, questions_path, questions)
    rows = add_breakdown_fields(rows, questions)
    # summarize()'s own tier_table/model_table/grid are ignored here in favor
    # of this module's _bucket/_grid, which add an "accuracy" field to every
    # cell (summarize()'s only does that on the top-level `overall` dict).
    graded, _tier_table, _model_table, _tier_model_grid, overall = summarize(rows, epsilon)

    tier_table = _bucket(graded, "tier")
    model_table = _bucket(graded, "model")
    tier_model_grid = _grid(graded, "tier", "model")
    reasoning_type_table = _bucket(graded, "reasoning_type")
    reasoning_type_model_grid = _grid(graded, "reasoning_type", "model")

    incorrect_ids = sorted({r["id"] for r in graded if r["status"] == "incorrect" and r.get("id")})
    invalid_ids = sorted({r["id"] for r in graded if r["status"] == "invalid_response" and r.get("id")})

    per_question: dict = {}
    for r in graded:
        per_question.setdefault(r.get("id"), {})[r.get("model")] = r["status"]

    question_bank_size = len(questions) if questions else None

    # Real-filing breakdowns, only for fields the rows actually carry.
    breakdowns = {}
    for field, _title in BREAKDOWNS:
        if field in ("sector", "size"):
            present = any(r.get("sic") for r in graded) if field == "sector" else \
                any(r[field] != UNKNOWN for r in graded)
        else:
            present = any(r.get(field) for r in graded)
        if present:
            breakdowns[field] = {f"{k}|{m}": v for (k, m), v in _grid(graded, field, "model").items()}

    return {
        "epsilon": epsilon,
        "question_bank_size": question_bank_size,
        "questions_evaluated": len({r.get("id") for r in graded}),
        "response_count": len(graded),
        "overall": overall,
        "by_model": model_table,
        "by_difficulty": tier_table,
        "by_reasoning_type": reasoning_type_table,
        "by_difficulty_and_model": {f"{t}|{m}": v for (t, m), v in tier_model_grid.items()},
        "by_reasoning_type_and_model": {f"{t}|{m}": v for (t, m), v in reasoning_type_model_grid.items()},
        "incorrect_question_ids": incorrect_ids,
        "invalid_question_ids": invalid_ids,
        "per_question": per_question,
        "easy_to_hard_drop": easy_to_hard_drop(graded),
        "single_vs_multi_step": single_vs_multi_step(graded),
        "strongest_weakest_reasoning_type": strongest_weakest_reasoning_type(graded),
        "ci_cluster": cluster,
        "models": model_summary(graded, cluster),
        "breakdowns": breakdowns,
    }


def _fmt_pct(acc) -> str:
    return f"{100 * acc:.1f}%" if acc is not None else "n/a"


def _fmt_tolerance_mode(epsilon: float | None) -> str:
    return f"epsilon={epsilon} (overridden for every question)" if epsilon is not None \
        else "each question's own tolerance (see docs/SCHEMA.md)"


def to_markdown(report: dict) -> str:
    lines = ["# XBRLBench results", ""]
    lines.append(
        f"{report['questions_evaluated']} questions, {report['response_count']} graded responses, "
        f"graded with {_fmt_tolerance_mode(report['epsilon'])}."
    )
    lines.append("")

    tiers = sorted({k.split("|")[0] for k in report["by_difficulty_and_model"]})
    models = sorted(report["by_model"].keys(), key=lambda m: (m is None, m))

    lines.append("## Accuracy by model x difficulty")
    lines.append("")
    lines.append("| model | " + " | ".join(tiers) + " | overall |")
    lines.append("|" + "---|" * (len(tiers) + 2))
    for model in models:
        cells = [_fmt_pct(report["by_difficulty_and_model"].get(f"{t}|{model}", {}).get("accuracy")) for t in tiers]
        overall_acc = report["by_model"].get(model, {}).get("accuracy")
        lines.append(f"| {model} | " + " | ".join(cells) + f" | {_fmt_pct(overall_acc)} |")
    lines.append("")

    lines.extend(_ci_table(report))
    for field, title in BREAKDOWNS:
        if field in report.get("breakdowns", {}):
            lines.extend(_breakdown_table(report["breakdowns"][field], title, models))

    lines.append("## Findings")
    lines.append("")
    drop = report["easy_to_hard_drop"].get("overall", {})
    lines.append(
        f"- Easy ({EASY_TIER}) -> hard ({HARD_TIER}) accuracy drop, all models combined: "
        f"{_fmt_pct(drop.get(f'{EASY_TIER}_accuracy'))} -> {_fmt_pct(drop.get(f'{HARD_TIER}_accuracy'))}."
    )
    step = report["single_vs_multi_step"].get("overall", {})
    lines.append(
        f"- Single-step (direct retrieval) vs. multi-step reasoning, all models combined: "
        f"{_fmt_pct(step.get('single_step_accuracy'))} vs. {_fmt_pct(step.get('multi_step_accuracy'))}."
    )
    for model in models:
        d = report["easy_to_hard_drop"].get(model, {})
        if d.get("drop") is not None and d["drop"] > 0:
            lines.append(
                f"- {model}: {_fmt_pct(d[f'{EASY_TIER}_accuracy'])} on {EASY_TIER} "
                f"vs. {_fmt_pct(d[f'{HARD_TIER}_accuracy'])} on {HARD_TIER}."
            )
    if report["overall"]["invalid"]:
        lines.append(
            f"- {report['overall']['invalid']}/{report['overall']['n']} responses were invalid/unparseable "
            f"(excluded from accuracy)."
        )

    return "\n".join(lines) + "\n"


def _fmt_ci(ci) -> str:
    return f"{100 * ci[0]:.1f}-{100 * ci[1]:.1f}%" if ci else "n/a"


def _ci_table(report: dict) -> list[str]:
    summary = report.get("models") or {}
    if not summary:
        return []
    unit = "company" if report.get("ci_cluster") == "company" else "question"
    lines = [f"## Accuracy with 95% confidence intervals (bootstrap over {unit})", "",
             "| model | n | accuracy | 95% CI | invalid | vs. best model (95% CI) | cost | mean output tokens |",
             "|---|---|---|---|---|---|---|---|"]
    for model, s in sorted(summary.items(), key=lambda kv: -kv[1]["accuracy"]):
        gap = s.get("vs_best")
        gap_txt = "best" if gap is None else \
            f"{100 * gap['difference']:+.1f} pts ({100 * gap['ci95'][0]:+.1f} to {100 * gap['ci95'][1]:+.1f})"
        cost = f"${s['cost_usd']:.2f}" if s.get("cost_usd") is not None else "n/a"
        toks = s.get("mean_completion_tokens")
        lines.append(f"| {model} | {s['n']} | {_fmt_pct(s['accuracy'])} | {_fmt_ci(s['ci95'])} | "
                     f"{s['invalid']} | {gap_txt} | {cost} | {toks if toks is not None else 'n/a'} |")
    lines.append("")
    return lines


def _breakdown_table(grid: dict, title: str, models: list) -> list[str]:
    keys = sorted({k.split("|", 1)[0] for k in grid})
    lines = [f"## Accuracy by {title}", "",
             "| " + title + " | n | " + " | ".join(str(m) for m in models) + " |",
             "|" + "---|" * (len(models) + 2)]
    for key in keys:
        cells = [grid.get(f"{key}|{m}") for m in models]
        n = max((c["n"] for c in cells if c), default=0)
        lines.append(f"| {key} | {n} | " + " | ".join(_fmt_pct(c["accuracy"]) if c else "n/a" for c in cells) + " |")
    lines.append("")
    return lines


def write_outputs(report: dict, out_prefix) -> None:
    json_path = f"{out_prefix}_analysis.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    print(f"Wrote {json_path}")

    csv_path = f"{out_prefix}_analysis.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["dimension", "key", "model", "n", "correct", "invalid", "accuracy"])
        for key, cell in sorted(report["by_difficulty_and_model"].items()):
            tier, model = key.split("|", 1)
            w.writerow(["difficulty", tier, model, cell["n"], cell["correct"], cell["invalid"], round(cell["accuracy"], 4)])
        for key, cell in sorted(report["by_reasoning_type_and_model"].items()):
            rtype, model = key.split("|", 1)
            w.writerow(["reasoning_type", rtype, model, cell["n"], cell["correct"], cell["invalid"], round(cell["accuracy"], 4)])
        for field, grid in sorted(report.get("breakdowns", {}).items()):
            for key, cell in sorted(grid.items()):
                value, model = key.split("|", 1)
                w.writerow([field, value, model, cell["n"], cell["correct"], cell["invalid"], round(cell["accuracy"], 4)])
    print(f"Wrote {csv_path}")

    md_path = f"{out_prefix}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(to_markdown(report))
    print(f"Wrote {md_path}")


def report(
    responses_path=DEFAULT_RESPONSES_PATH,
    questions_path=DEFAULT_QUESTIONS_PATH,
    out_prefix=DEFAULT_REPORT_PREFIX,
    epsilon: float | None = None,
    cluster: str = "company",
) -> dict:
    rows = load_jsonl(responses_path)
    result = build_report(rows, questions_path, epsilon, cluster)
    write_outputs(result, out_prefix)
    print(f"\nOverall: {result['overall']['correct']}/{result['overall']['n']} = "
          f"{_fmt_pct(result['overall']['accuracy'])}")
    return result


def build_arg_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(
        description="Generate accuracy/failure breakdowns from a saved responses file.")
    parser.add_argument("--in", dest="inp", default=str(DEFAULT_RESPONSES_PATH))
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS_PATH),
                         help="question bank, used to backfill reasoning_type on older responses files")
    parser.add_argument("--out-prefix", default=str(DEFAULT_REPORT_PREFIX))
    parser.add_argument("--epsilon", type=float, default=None,
                         help="override every row's tolerance (see `xbrlbench grade --help`)")
    parser.add_argument("--ci-cluster", dest="cluster", choices=["company", "question"], default="company",
                         help="bootstrap unit for confidence intervals (default: company)")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    report(args.inp, args.questions, args.out_prefix, args.epsilon, args.cluster)


if __name__ == "__main__":
    main()
