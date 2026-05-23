"""Fixers — deterministic remediations that bypass the coding agent.

All fixers must land changes via PR mode (the kernel handles ``git push`` +
``gh pr create`` after the fixer commits inside the worktree).
"""
from auto_heal.fixers.base import BaseFixer
from auto_heal.fixers.registry import FixerRegistry, build_default_registry
from auto_heal.interfaces.fixer import Fixer, FixContext

__all__ = ["Fixer", "FixContext", "BaseFixer", "FixerRegistry", "build_default_registry"]
