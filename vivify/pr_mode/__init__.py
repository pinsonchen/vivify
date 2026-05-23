"""PR-mode subsystem — worktree → quality check → push → PR → optional auto-merge."""
from vivify.pr_mode.auto_merge import AutoMerge, AutoMergeConfig, MergeOutcome
from vivify.pr_mode.pr_creator import PrCreator, PrCreatorConfig, PullRequest
from vivify.pr_mode.quality_check import QualityCheckResult, run_quality_checks
from vivify.pr_mode.self_grow_guard import (
    DEFAULT_PLUGIN_PATHS,
    DiffClass,
    GuardDecision,
    classify_diff,
    classify_worktree,
)
from vivify.pr_mode.worktree import Worktree, WorktreeManager

__all__ = [
    "AutoMerge",
    "AutoMergeConfig",
    "DEFAULT_PLUGIN_PATHS",
    "DiffClass",
    "GuardDecision",
    "MergeOutcome",
    "PrCreator",
    "PrCreatorConfig",
    "PullRequest",
    "QualityCheckResult",
    "Worktree",
    "WorktreeManager",
    "classify_diff",
    "classify_worktree",
    "run_quality_checks",
]
