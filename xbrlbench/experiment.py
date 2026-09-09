"""xbrlbench.experiment — run metadata: enough about how a set of responses
was produced (when, against which code/benchmark version, with which models
and generation parameters) that a result can later be reproduced or sanity
checked instead of taken on faith.

xbrlbench.inference.run() writes one metadata.json per run directory
alongside responses.jsonl. See docs/METADATA.md for the full field list and
the results/ directory structure.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import BENCHMARK_VERSION, __version__
from .paths import REPO_ROOT


def generate_run_id() -> str:
    """A sortable, filesystem-safe run identifier: a UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_commit_hash() -> Optional[str]:
    """Best-effort current commit hash of the repo this package is running
    from. Returns None if git isn't available, this isn't a git checkout, or
    anything else goes wrong -- capturing metadata must never block a run."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def build_metadata(
    run_id: str,
    started_at: str,
    models: list[str],
    temperature: float,
    max_tokens: Optional[int],
    prompt_version: str,
    question_count: int,
    resume: bool,
) -> dict:
    return {
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": None,
        "xbrlbench_version": __version__,
        "benchmark_version": BENCHMARK_VERSION,
        "git_commit": git_commit_hash(),
        "models": list(models),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "prompt_version": prompt_version,
        "question_count": question_count,
        "response_count": None,
        "resume": resume,
    }


def write_metadata(metadata: dict, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
