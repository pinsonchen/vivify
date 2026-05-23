"""Default values shared by config + init commands.

Centralised so the ``init`` flow and the schema's defaults stay in sync.
"""
from __future__ import annotations

DEFAULT_BUILTIN_PROBES: tuple[str, ...] = (
    "ci_status",
    "dependency_vulnerabilities",
    "test_coverage",
    "error_log_patterns",
    "lint_typecheck",
    "github_issue_backlog",
    "build_duration",
    "repo_size",
    "doc_staleness",
    "dead_code",
    "stale_branches",
    "secrets_scan",
)

DEFAULT_BUILTIN_FIXERS: tuple[str, ...] = (
    "dependency_bump",
    "lint_autofix",
    "format_autofix",
    "test_flake_retry",
    "stale_branch_prune",
    "doc_link_check",
)

DEFAULT_GITIGNORE_ENTRIES: tuple[str, ...] = (
    ".auto-heal/state.db",
    ".auto-heal/logs/",
    ".auto-heal/worktrees/",
)


__all__ = [
    "DEFAULT_BUILTIN_FIXERS",
    "DEFAULT_BUILTIN_PROBES",
    "DEFAULT_GITIGNORE_ENTRIES",
]
