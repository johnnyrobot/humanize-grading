"""Package validation must ignore Git worktree metadata and checkouts."""

import subprocess
import sys
from pathlib import Path

import pytest


def test_package_validator_ignores_nested_worktree_skills():
    worktree_root = Path(__file__).resolve().parents[2]
    git_common = subprocess.check_output(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=worktree_root,
        text=True,
    ).strip()
    checkout_root = (worktree_root / git_common).resolve().parent
    nested_skills = list((checkout_root / ".worktrees").rglob("SKILL.md"))
    if not nested_skills:
        pytest.skip("This checkout has no nested Git worktree skill files")

    result = subprocess.run(
        [sys.executable, "scripts/validate-package.py"],
        cwd=checkout_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
