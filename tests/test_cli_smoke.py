"""Smoke tests for Commit 1: the package imports cleanly and every subcommand
is discoverable and shows help without error. These are not correctness
tests for grading/generation logic (those land with the commits that harden
that logic) — just proof the CLI skeleton works.
"""

import subprocess
import sys

import pytest

SUBCOMMANDS = ["generate", "run", "grade"]


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "xbrlbench", *args],
        capture_output=True, text=True,
    )


def test_top_level_help():
    result = run_cli("--help")
    assert result.returncode == 0
    for cmd in SUBCOMMANDS:
        assert cmd in result.stdout


def test_version():
    result = run_cli("--version")
    assert result.returncode == 0
    assert "xbrlbench" in result.stdout


@pytest.mark.parametrize("cmd", SUBCOMMANDS)
def test_subcommand_help(cmd):
    result = run_cli(cmd, "--help")
    assert result.returncode == 0, result.stderr


def test_no_command_errors_cleanly():
    result = run_cli()
    assert result.returncode != 0


def test_modules_import():
    import xbrlbench.generation  # noqa: F401
    import xbrlbench.inference  # noqa: F401
    import xbrlbench.grading  # noqa: F401
    import xbrlbench.cli  # noqa: F401
