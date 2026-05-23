"""Fixers — deterministic remediations that bypass the coding agent.

All fixers must land changes via PR mode (the kernel handles ``git push`` +
``gh pr create`` after the fixer commits inside the worktree).
"""
from vivify.fixers.base import BaseFixer
from vivify.fixers.registry import FixerRegistry, build_default_registry
from vivify.interfaces.fixer import Fixer, FixContext

__all__ = ["Fixer", "FixContext", "BaseFixer", "FixerRegistry", "build_default_registry"]
