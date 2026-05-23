"""Result types for fix attempts (direct fixers + coding agent)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FixResult:
    """Returned by ``Fixer.fix(issue, ctx)``."""

    fixed: bool
    message: str = ""
    changed_files: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    artifacts: dict = field(default_factory=dict)
    pr_url: str | None = None
    branch: str | None = None
    commit_hash: str | None = None
