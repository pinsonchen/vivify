"""Worktree physical isolation — sensitive files completely invisible to agents.

Uses git sparse-checkout to exclude sensitive file patterns (secrets, keys,
credentials) from worktrees, providing a physical isolation layer that
complements rule-based constraints.

Requires git >= 2.25.0 (sparse-checkout support).
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


# 默认排除的敏感文件模式
DEFAULT_SENSITIVE_PATTERNS: List[str] = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*secret*",
    "*credential*",
    ".aws/",
    ".gcp/",
    ".ssh/",
    "*.keystore",
    "service-account*.json",
    "firebase-adminsdk*.json",
    ".vivify/env",
]


@dataclass
class IsolationConfig:
    """Configuration for worktree isolation."""

    enabled: bool = True
    sensitive_patterns: List[str] = field(
        default_factory=lambda: list(DEFAULT_SENSITIVE_PATTERNS)
    )
    extra_exclude_patterns: List[str] = field(default_factory=list)

    @property
    def all_patterns(self) -> List[str]:
        return self.sensitive_patterns + self.extra_exclude_patterns


@dataclass
class IsolationResult:
    """Result of applying isolation to a worktree."""

    success: bool
    worktree_path: Path
    excluded_patterns: List[str]
    error: str = ""


class WorktreeIsolator:
    """Applies physical file isolation to git worktrees.

    Uses git sparse-checkout to exclude sensitive files,
    making them completely invisible in the worktree.
    """

    def __init__(self, config: IsolationConfig | None = None):
        self._config = config or IsolationConfig()

    @property
    def config(self) -> IsolationConfig:
        return self._config

    def apply_isolation(self, worktree_path: Path) -> IsolationResult:
        """Apply sparse-checkout isolation to a worktree.

        Steps:
        1. Enable sparse-checkout in no-cone mode (supports negation patterns)
        2. Write rules to include everything except sensitive files
        3. Reapply to physically remove excluded files from working tree

        On failure, logs a warning but does NOT raise — fail-open for
        availability, log for audit.
        """
        if not self._config.enabled:
            return IsolationResult(
                success=True,
                worktree_path=worktree_path,
                excluded_patterns=[],
                error="isolation disabled",
            )

        try:
            # Enable sparse-checkout (no-cone mode for negation pattern support)
            self._run_git(worktree_path, ["sparse-checkout", "init", "--no-cone"])

            # Build sparse-checkout rules: include everything, exclude sensitive
            rules = self._build_rules()

            # Write rules to sparse-checkout file
            git_dir = self._get_git_dir(worktree_path)
            sparse_file = git_dir / "info" / "sparse-checkout"
            sparse_file.parent.mkdir(parents=True, exist_ok=True)
            sparse_file.write_text("\n".join(rules) + "\n")

            # Reapply so files are physically removed
            self._run_git(worktree_path, ["sparse-checkout", "reapply"])

            logger.info(
                "Applied worktree isolation to %s (%d patterns excluded)",
                worktree_path,
                len(self._config.all_patterns),
            )
            return IsolationResult(
                success=True,
                worktree_path=worktree_path,
                excluded_patterns=list(self._config.all_patterns),
            )
        except (subprocess.CalledProcessError, OSError) as e:
            logger.warning(
                "Failed to apply isolation to %s: %s (continuing without isolation)",
                worktree_path,
                e,
            )
            return IsolationResult(
                success=False,
                worktree_path=worktree_path,
                excluded_patterns=[],
                error=str(e),
            )

    def remove_isolation(self, worktree_path: Path) -> bool:
        """Remove sparse-checkout isolation (restore full visibility).

        Should be called before worktree removal to ensure clean state.
        """
        try:
            self._run_git(worktree_path, ["sparse-checkout", "disable"])
            return True
        except (subprocess.CalledProcessError, OSError) as e:
            logger.warning(
                "Failed to remove isolation from %s: %s", worktree_path, e
            )
            return False

    def verify_isolation(self, worktree_path: Path) -> dict:
        """Verify that sensitive files are properly excluded.

        Returns dict with:
            - isolated: bool — True if no sensitive files are visible
            - visible_sensitive_files: list — should be empty if isolated
        """
        visible: List[str] = []
        for pattern in self._config.all_patterns:
            # Check if any matching files are visible in the worktree
            matches = list(worktree_path.glob(pattern))
            visible.extend(
                str(m.relative_to(worktree_path)) for m in matches if m.exists()
            )

        return {
            "isolated": len(visible) == 0,
            "visible_sensitive_files": visible,
        }

    def _build_rules(self) -> List[str]:
        """Build sparse-checkout rules (no-cone mode).

        Format:
            /*            # Include everything
            !.env         # Exclude .env
            !*.pem        # Exclude all .pem files
        """
        rules = ["/*"]  # Include everything by default
        for pattern in self._config.all_patterns:
            rules.append(f"!/{pattern}")
        return rules

    def _get_git_dir(self, worktree_path: Path) -> Path:
        """Get the actual .git directory for a worktree.

        For linked worktrees, .git is a file containing ``gitdir: <path>``.
        """
        git_path = worktree_path / ".git"
        if git_path.is_file():
            content = git_path.read_text().strip()
            if content.startswith("gitdir:"):
                resolved = Path(content.split(":", 1)[1].strip())
                if not resolved.is_absolute():
                    resolved = (worktree_path / resolved).resolve()
                return resolved
        return git_path

    def _run_git(self, cwd: Path, args: List[str]) -> subprocess.CompletedProcess:
        """Run a git command in the given directory."""
        return subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )


__all__ = [
    "DEFAULT_SENSITIVE_PATTERNS",
    "IsolationConfig",
    "IsolationResult",
    "WorktreeIsolator",
]
