"""Tests for vivify.kernel.workspace_health module."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from vivify.kernel.workspace_health import (
    HealthCheckResult,
    check_workspace_health,
    _check_uncommitted,
    _check_rebase_state,
    _check_worktree_locks,
)


# ────────────────────────────────────────────────────────────────────────────────
# HealthCheckResult
# ────────────────────────────────────────────────────────────────────────────────


class TestHealthCheckResult:
    def test_summary_all_passed(self):
        result = HealthCheckResult(
            passed=True,
            checks=[
                {"name": "uncommitted_changes", "passed": True, "message": "Clean"},
                {"name": "rebase_state", "passed": True, "message": "No rebase/merge"},
            ],
        )
        assert result.summary == "All pre-flight checks passed"

    def test_summary_with_failures(self):
        result = HealthCheckResult(
            passed=False,
            checks=[
                {"name": "uncommitted_changes", "passed": False, "message": "Dirty tree"},
                {"name": "rebase_state", "passed": True, "message": "OK"},
            ],
        )
        assert "uncommitted_changes: Dirty tree" in result.summary

    def test_summary_multiple_failures(self):
        result = HealthCheckResult(
            passed=False,
            checks=[
                {"name": "uncommitted_changes", "passed": False, "message": "Dirty"},
                {"name": "rebase_state", "passed": False, "message": "Rebase in progress"},
            ],
        )
        assert "uncommitted_changes" in result.summary
        assert "rebase_state" in result.summary


# ────────────────────────────────────────────────────────────────────────────────
# check_workspace_health (integration-level with mocked subprocess)
# ────────────────────────────────────────────────────────────────────────────────


class TestCheckWorkspaceHealth:
    def test_nonexistent_workspace_passes(self, tmp_path):
        """Non-existent workspace should pass (fail-open)."""
        result = check_workspace_health(tmp_path / "nonexistent")
        assert result.passed is True
        assert result.checks == []

    def test_non_git_workspace_passes(self, tmp_path):
        """Workspace without .git directory should pass (fail-open)."""
        result = check_workspace_health(tmp_path)
        assert result.passed is True
        assert result.checks == []

    def test_clean_workspace(self, tmp_path):
        """Clean git repo should pass all checks."""
        (tmp_path / ".git").mkdir()
        with patch(
            "vivify.kernel.workspace_health.subprocess.run"
        ) as mock_run:
            # git status --porcelain returns empty (clean)
            clean_result = MagicMock()
            clean_result.stdout = ""
            # git worktree list --porcelain returns no locked
            worktree_result = MagicMock()
            worktree_result.stdout = "worktree /path\nHEAD abc123\n"
            mock_run.side_effect = [clean_result, worktree_result]

            result = check_workspace_health(tmp_path)

        assert result.passed is True
        assert len(result.checks) == 3
        assert all(c["passed"] for c in result.checks)

    def test_dirty_workspace(self, tmp_path):
        """Workspace with uncommitted changes should fail."""
        (tmp_path / ".git").mkdir()
        with patch(
            "vivify.kernel.workspace_health.subprocess.run"
        ) as mock_run:
            # git status --porcelain returns modified file
            dirty_result = MagicMock()
            dirty_result.stdout = " M some_file.py\n"
            # git worktree list
            worktree_result = MagicMock()
            worktree_result.stdout = "worktree /path\n"
            mock_run.side_effect = [dirty_result, worktree_result]

            result = check_workspace_health(tmp_path)

        assert result.passed is False
        uncommitted = next(c for c in result.checks if c["name"] == "uncommitted_changes")
        assert uncommitted["passed"] is False
        assert "uncommitted changes" in uncommitted["message"]

    def test_rebase_in_progress(self, tmp_path):
        """Workspace with rebase-merge marker should fail."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "rebase-merge").mkdir()

        with patch(
            "vivify.kernel.workspace_health.subprocess.run"
        ) as mock_run:
            # git status clean
            clean_result = MagicMock()
            clean_result.stdout = ""
            # git worktree list clean
            worktree_result = MagicMock()
            worktree_result.stdout = ""
            mock_run.side_effect = [clean_result, worktree_result]

            result = check_workspace_health(tmp_path)

        assert result.passed is False
        rebase = next(c for c in result.checks if c["name"] == "rebase_state")
        assert rebase["passed"] is False
        assert "Rebase in progress" in rebase["message"]

    def test_merge_in_progress(self, tmp_path):
        """Workspace with MERGE_HEAD should fail."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        with patch(
            "vivify.kernel.workspace_health.subprocess.run"
        ) as mock_run:
            clean_result = MagicMock()
            clean_result.stdout = ""
            worktree_result = MagicMock()
            worktree_result.stdout = ""
            mock_run.side_effect = [clean_result, worktree_result]

            result = check_workspace_health(tmp_path)

        assert result.passed is False
        rebase = next(c for c in result.checks if c["name"] == "rebase_state")
        assert rebase["passed"] is False
        assert "Merge in progress" in rebase["message"]

    def test_locked_worktree(self, tmp_path):
        """Workspace with locked worktree should fail."""
        (tmp_path / ".git").mkdir()
        with patch(
            "vivify.kernel.workspace_health.subprocess.run"
        ) as mock_run:
            clean_result = MagicMock()
            clean_result.stdout = ""
            locked_result = MagicMock()
            locked_result.stdout = "worktree /path\nHEAD abc123\nlocked\n"
            mock_run.side_effect = [clean_result, locked_result]

            result = check_workspace_health(tmp_path)

        assert result.passed is False
        locks = next(c for c in result.checks if c["name"] == "worktree_locks")
        assert locks["passed"] is False
        assert "locked" in locks["message"].lower()


# ────────────────────────────────────────────────────────────────────────────────
# Individual check functions — edge cases
# ────────────────────────────────────────────────────────────────────────────────


class TestCheckUncommitted:
    def test_timeout_is_fail_open(self, tmp_path):
        """Timeout should result in pass (fail-open)."""
        with patch(
            "vivify.kernel.workspace_health.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=10),
        ):
            result = _check_uncommitted(tmp_path)
        assert result["passed"] is True
        assert "skipped" in result["message"].lower()

    def test_file_not_found_is_fail_open(self, tmp_path):
        """Missing git binary should result in pass (fail-open)."""
        with patch(
            "vivify.kernel.workspace_health.subprocess.run",
            side_effect=FileNotFoundError("git not found"),
        ):
            result = _check_uncommitted(tmp_path)
        assert result["passed"] is True
        assert "skipped" in result["message"].lower()


class TestCheckRebaseState:
    def test_no_markers(self, tmp_path):
        """No rebase/merge markers → passed."""
        (tmp_path / ".git").mkdir()
        result = _check_rebase_state(tmp_path)
        assert result["passed"] is True

    def test_rebase_apply_marker(self, tmp_path):
        """rebase-apply marker → failed."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "rebase-apply").mkdir()
        result = _check_rebase_state(tmp_path)
        assert result["passed"] is False
        assert "Rebase" in result["message"]


class TestCheckWorktreeLocks:
    def test_timeout_is_fail_open(self, tmp_path):
        """Timeout should result in pass (fail-open)."""
        with patch(
            "vivify.kernel.workspace_health.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=10),
        ):
            result = _check_worktree_locks(tmp_path)
        assert result["passed"] is True
        assert "skipped" in result["message"].lower()

    def test_no_locks(self, tmp_path):
        """No locks in output → passed."""
        with patch(
            "vivify.kernel.workspace_health.subprocess.run"
        ) as mock_run:
            mock_run.return_value = MagicMock(stdout="worktree /foo\nHEAD abc\n")
            result = _check_worktree_locks(tmp_path)
        assert result["passed"] is True
