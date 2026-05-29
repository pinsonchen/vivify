"""Tests for vivify.pr_mode.isolation — worktree physical isolation."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from vivify.pr_mode.isolation import (
    DEFAULT_SENSITIVE_PATTERNS,
    IsolationConfig,
    IsolationResult,
    WorktreeIsolator,
)


# ── IsolationConfig ──────────────────────────────────────────────────────────


class TestIsolationConfig:
    def test_defaults(self):
        cfg = IsolationConfig()
        assert cfg.enabled is True
        assert cfg.sensitive_patterns == DEFAULT_SENSITIVE_PATTERNS
        assert cfg.extra_exclude_patterns == []

    def test_all_patterns_includes_extras(self):
        cfg = IsolationConfig(extra_exclude_patterns=["*.secret.yaml", "deploy/"])
        assert "*.secret.yaml" in cfg.all_patterns
        assert "deploy/" in cfg.all_patterns
        # Should also include all defaults
        for p in DEFAULT_SENSITIVE_PATTERNS:
            assert p in cfg.all_patterns

    def test_disabled(self):
        cfg = IsolationConfig(enabled=False)
        assert cfg.enabled is False


# ── WorktreeIsolator._build_rules ────────────────────────────────────────────


class TestBuildRules:
    def test_format(self):
        cfg = IsolationConfig(
            sensitive_patterns=[".env", "*.pem"],
            extra_exclude_patterns=["secrets/"],
        )
        isolator = WorktreeIsolator(cfg)
        rules = isolator._build_rules()
        assert rules[0] == "/*"
        assert "!/.env" in rules
        assert "!/*.pem" in rules
        assert "!/secrets/" in rules

    def test_default_rules_start_with_include_all(self):
        isolator = WorktreeIsolator()
        rules = isolator._build_rules()
        assert rules[0] == "/*"
        assert len(rules) == 1 + len(DEFAULT_SENSITIVE_PATTERNS)


# ── WorktreeIsolator._get_git_dir ────────────────────────────────────────────


class TestGetGitDir:
    def test_regular_git_dir(self, tmp_path):
        """When .git is a directory, return it directly."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        isolator = WorktreeIsolator()
        assert isolator._get_git_dir(tmp_path) == git_dir

    def test_linked_worktree_git_file(self, tmp_path):
        """When .git is a file (linked worktree), parse gitdir pointer."""
        actual_git_dir = tmp_path / "repo" / ".git" / "worktrees" / "feat-1"
        actual_git_dir.mkdir(parents=True)
        
        wt_path = tmp_path / "worktree"
        wt_path.mkdir()
        git_file = wt_path / ".git"
        git_file.write_text(f"gitdir: {actual_git_dir}\n")
        
        isolator = WorktreeIsolator()
        result = isolator._get_git_dir(wt_path)
        assert result == actual_git_dir

    def test_linked_worktree_relative_path(self, tmp_path):
        """When .git file contains a relative gitdir path."""
        actual_git_dir = tmp_path / ".git" / "worktrees" / "feat-1"
        actual_git_dir.mkdir(parents=True)

        wt_path = tmp_path / "worktrees" / "feat-1"
        wt_path.mkdir(parents=True)
        git_file = wt_path / ".git"
        # relative path from worktree to actual git dir
        git_file.write_text(f"gitdir: ../../.git/worktrees/feat-1\n")

        isolator = WorktreeIsolator()
        result = isolator._get_git_dir(wt_path)
        assert result.resolve() == actual_git_dir.resolve()


# ── apply_isolation ──────────────────────────────────────────────────────────


class TestApplyIsolation:
    def test_disabled_returns_success(self, tmp_path):
        cfg = IsolationConfig(enabled=False)
        isolator = WorktreeIsolator(cfg)
        result = isolator.apply_isolation(tmp_path)
        assert result.success is True
        assert result.excluded_patterns == []
        assert result.error == "isolation disabled"

    def test_successful_apply(self, tmp_path):
        """Test successful isolation apply with mocked git commands."""
        # Setup: simulate a linked worktree with a .git file
        git_dir = tmp_path / "git_dir"
        git_dir.mkdir()
        git_file = tmp_path / ".git"
        git_file.write_text(f"gitdir: {git_dir}\n")

        cfg = IsolationConfig(sensitive_patterns=[".env", "*.pem"])
        isolator = WorktreeIsolator(cfg)

        with patch.object(isolator, "_run_git") as mock_git:
            mock_git.return_value = subprocess.CompletedProcess([], 0)
            result = isolator.apply_isolation(tmp_path)

        assert result.success is True
        assert ".env" in result.excluded_patterns
        assert "*.pem" in result.excluded_patterns

        # Verify sparse-checkout file was written
        sparse_file = git_dir / "info" / "sparse-checkout"
        assert sparse_file.exists()
        content = sparse_file.read_text()
        assert "/*" in content
        assert "!/.env" in content
        assert "!/*.pem" in content

    def test_git_failure_returns_error(self, tmp_path):
        """Git failure should not raise, returns IsolationResult with error."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        cfg = IsolationConfig(sensitive_patterns=[".env"])
        isolator = WorktreeIsolator(cfg)

        with patch.object(isolator, "_run_git") as mock_git:
            mock_git.side_effect = subprocess.CalledProcessError(1, "git")
            result = isolator.apply_isolation(tmp_path)

        assert result.success is False
        assert "returned non-zero" in result.error or "CalledProcessError" in result.error

    def test_os_error_returns_error(self, tmp_path):
        """OSError during isolation should not raise."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        isolator = WorktreeIsolator()

        with patch.object(isolator, "_run_git") as mock_git:
            mock_git.side_effect = OSError("Permission denied")
            result = isolator.apply_isolation(tmp_path)

        assert result.success is False
        assert "Permission denied" in result.error


# ── verify_isolation ─────────────────────────────────────────────────────────


class TestVerifyIsolation:
    def test_no_sensitive_files_visible(self, tmp_path):
        """If no sensitive files exist, verification passes."""
        isolator = WorktreeIsolator(
            IsolationConfig(sensitive_patterns=[".env", "*.pem"])
        )
        result = isolator.verify_isolation(tmp_path)
        assert result["isolated"] is True
        assert result["visible_sensitive_files"] == []

    def test_sensitive_file_visible(self, tmp_path):
        """If a sensitive file is visible, verification fails."""
        (tmp_path / ".env").write_text("SECRET=value")
        isolator = WorktreeIsolator(
            IsolationConfig(sensitive_patterns=[".env"])
        )
        result = isolator.verify_isolation(tmp_path)
        assert result["isolated"] is False
        assert ".env" in result["visible_sensitive_files"]

    def test_multiple_sensitive_files(self, tmp_path):
        """Multiple sensitive file patterns detected."""
        (tmp_path / ".env").write_text("A=1")
        (tmp_path / "server.pem").write_text("cert")
        isolator = WorktreeIsolator(
            IsolationConfig(sensitive_patterns=[".env", "*.pem"])
        )
        result = isolator.verify_isolation(tmp_path)
        assert result["isolated"] is False
        assert len(result["visible_sensitive_files"]) == 2


# ── remove_isolation ─────────────────────────────────────────────────────────


class TestRemoveIsolation:
    def test_successful_remove(self, tmp_path):
        isolator = WorktreeIsolator()
        with patch.object(isolator, "_run_git") as mock_git:
            mock_git.return_value = subprocess.CompletedProcess([], 0)
            assert isolator.remove_isolation(tmp_path) is True
            mock_git.assert_called_once_with(
                tmp_path, ["sparse-checkout", "disable"]
            )

    def test_failure_returns_false(self, tmp_path):
        isolator = WorktreeIsolator()
        with patch.object(isolator, "_run_git") as mock_git:
            mock_git.side_effect = subprocess.CalledProcessError(1, "git")
            assert isolator.remove_isolation(tmp_path) is False


# ── DEFAULT_SENSITIVE_PATTERNS ───────────────────────────────────────────────


class TestDefaultPatterns:
    def test_covers_env_files(self):
        assert ".env" in DEFAULT_SENSITIVE_PATTERNS
        assert ".env.*" in DEFAULT_SENSITIVE_PATTERNS

    def test_covers_key_files(self):
        assert "*.pem" in DEFAULT_SENSITIVE_PATTERNS
        assert "*.key" in DEFAULT_SENSITIVE_PATTERNS
        assert "*.p12" in DEFAULT_SENSITIVE_PATTERNS
        assert "*.pfx" in DEFAULT_SENSITIVE_PATTERNS
        assert "*.keystore" in DEFAULT_SENSITIVE_PATTERNS

    def test_covers_credential_patterns(self):
        assert "*secret*" in DEFAULT_SENSITIVE_PATTERNS
        assert "*credential*" in DEFAULT_SENSITIVE_PATTERNS

    def test_covers_cloud_dirs(self):
        assert ".aws/" in DEFAULT_SENSITIVE_PATTERNS
        assert ".gcp/" in DEFAULT_SENSITIVE_PATTERNS
        assert ".ssh/" in DEFAULT_SENSITIVE_PATTERNS

    def test_covers_service_accounts(self):
        assert "service-account*.json" in DEFAULT_SENSITIVE_PATTERNS
        assert "firebase-adminsdk*.json" in DEFAULT_SENSITIVE_PATTERNS

    def test_covers_vivify_env(self):
        assert ".vivify/env" in DEFAULT_SENSITIVE_PATTERNS


# ── Integration with real git (optional, requires git) ───────────────────────


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repo with a sensitive file."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path), capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path), capture_output=True,
    )
    # Create a normal file and a sensitive file
    (tmp_path / "app.py").write_text("print('hello')")
    (tmp_path / ".env").write_text("SECRET=leaked")
    (tmp_path / "server.pem").write_text("CERT_DATA")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path), capture_output=True,
    )
    return tmp_path


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not available",
)
class TestIntegrationRealGit:
    def test_apply_and_verify(self, git_repo):
        """Full integration: apply isolation and verify sensitive files gone."""
        cfg = IsolationConfig(sensitive_patterns=[".env", "*.pem"])
        isolator = WorktreeIsolator(cfg)

        result = isolator.apply_isolation(git_repo)
        assert result.success is True

        # Sensitive files should be physically removed from working tree
        assert not (git_repo / ".env").exists()
        assert not (git_repo / "server.pem").exists()
        # Normal files still visible
        assert (git_repo / "app.py").exists()

        # Verify confirms isolation
        verify = isolator.verify_isolation(git_repo)
        assert verify["isolated"] is True

    def test_remove_restores_files(self, git_repo):
        """After removing isolation, sensitive files are visible again."""
        cfg = IsolationConfig(sensitive_patterns=[".env", "*.pem"])
        isolator = WorktreeIsolator(cfg)

        isolator.apply_isolation(git_repo)
        assert not (git_repo / ".env").exists()

        # Remove isolation
        assert isolator.remove_isolation(git_repo) is True

        # Files should be back
        assert (git_repo / ".env").exists()
        assert (git_repo / "server.pem").exists()
