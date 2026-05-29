"""Workspace health check - pre-flight validation before agent execution."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class HealthCheckResult:
    """Result of a workspace health check."""
    passed: bool
    checks: List[dict] = field(default_factory=list)  # [{name, passed, message}]

    @property
    def summary(self) -> str:
        failed = [c for c in self.checks if not c["passed"]]
        if not failed:
            return "All pre-flight checks passed"
        return "; ".join(f"{c['name']}: {c['message']}" for c in failed)


def check_workspace_health(workspace: Path) -> HealthCheckResult:
    """Run all pre-flight checks on the workspace.

    Checks:
    1. No uncommitted changes (dirty working tree)
    2. No rebase/merge in progress
    3. No worktree lock files

    If the workspace is not a git repository or checks themselves fail,
    the result is treated as passed (fail-open) to maintain backward
    compatibility.
    """
    # Fail-open: if workspace doesn't exist or isn't a git repo, pass through
    if not workspace.exists():
        return HealthCheckResult(passed=True, checks=[])

    git_dir = workspace / ".git"
    if not git_dir.exists():
        return HealthCheckResult(passed=True, checks=[])

    checks = []

    # Check 1: Uncommitted changes
    checks.append(_check_uncommitted(workspace))

    # Check 2: Rebase/merge state
    checks.append(_check_rebase_state(workspace))

    # Check 3: Worktree locks
    checks.append(_check_worktree_locks(workspace))

    passed = all(c["passed"] for c in checks)
    return HealthCheckResult(passed=passed, checks=checks)


def _check_uncommitted(workspace: Path) -> dict:
    """Check for uncommitted changes in the workspace."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(workspace),
            capture_output=True, text=True, timeout=10
        )
        has_changes = bool(result.stdout.strip())
        return {
            "name": "uncommitted_changes",
            "passed": not has_changes,
            "message": "Working tree has uncommitted changes" if has_changes else "Clean"
        }
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {"name": "uncommitted_changes", "passed": True, "message": f"Check skipped: {e}"}


def _check_rebase_state(workspace: Path) -> dict:
    """Check if a rebase or merge is in progress."""
    git_dir = workspace / ".git"

    # Check for rebase
    rebase_markers = [
        git_dir / "rebase-merge",
        git_dir / "rebase-apply",
    ]
    for marker in rebase_markers:
        if marker.exists():
            return {"name": "rebase_state", "passed": False, "message": "Rebase in progress"}

    # Check for merge
    merge_head = git_dir / "MERGE_HEAD"
    if merge_head.exists():
        return {"name": "rebase_state", "passed": False, "message": "Merge in progress"}

    return {"name": "rebase_state", "passed": True, "message": "No rebase/merge in progress"}


def _check_worktree_locks(workspace: Path) -> dict:
    """Check for worktree lock files."""
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(workspace),
            capture_output=True, text=True, timeout=10
        )
        # Check for "locked" in output
        if "locked" in result.stdout:
            return {"name": "worktree_locks", "passed": False, "message": "Worktree is locked"}
        return {"name": "worktree_locks", "passed": True, "message": "No worktree locks"}
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {"name": "worktree_locks", "passed": True, "message": f"Check skipped: {e}"}
