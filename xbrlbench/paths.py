"""Default file locations, resolved relative to the repository root (the
parent of this package directory) so `python -m xbrlbench ...` works the same
way regardless of the caller's current working directory.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
RESULTS_DIR = REPO_ROOT / "results"

DEFAULT_QUESTIONS_PATH = DATA_DIR / "questions.jsonl"
DEFAULT_RESPONSES_PATH = RESULTS_DIR / "responses.jsonl"
DEFAULT_REPORT_PREFIX = RESULTS_DIR / "report"
