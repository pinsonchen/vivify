"""Fixer ABC + FixContext.

A Fixer is a *fast-path* remediation that does NOT need the coding agent. Examples:
``ruff --fix``, ``npm update``, deleting stale branches. Fixers always land via PR mode.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from logging import Logger
from pathlib import Path
from typing import TYPE_CHECKING

from auto_heal.models.fix_result import FixResult
from auto_heal.models.issue import Issue

if TYPE_CHECKING:
    from auto_heal.config.schema import AutoHealConfig
    from auto_heal.interfaces.storage import StorageProvider


@dataclass
class FixContext:
    repo_root: Path
    config: "AutoHealConfig"
    storage: "StorageProvider"
    logger: Logger
    workspace: Path | None = None  # optional override (worktree path)
    extra: dict = field(default_factory=dict)


class Fixer(ABC):
    id: str = ""
    description: str = ""
    handles_categories: tuple[str, ...] = ()

    @abstractmethod
    def can_fix(self, issue: Issue, ctx: FixContext) -> bool: ...

    @abstractmethod
    def fix(self, issue: Issue, ctx: FixContext) -> FixResult: ...
