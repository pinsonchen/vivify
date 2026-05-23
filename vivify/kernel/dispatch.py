"""Dispatch layer — decides what action to take for each Issue.

* ``should_skip``: cooldown / dedup logic (LOW issues throttle, recently fixed
  issues skipped, already-upgraded issues skipped).
* ``select_fixer``: pick a direct-fix candidate from the FixerRegistry; falls
  back to ``None`` when only the coding agent can handle it.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from vivify.fixers.registry import FixerRegistry
from vivify.interfaces.fixer import Fixer, FixContext
from vivify.models.issue import Issue, IssueLevel

logger = logging.getLogger(__name__)


@dataclass
class DispatchPolicy:
    low_cooldown_seconds: int = 21600  # 6 h
    medium_cooldown_seconds: int = 3600
    max_same_issue_rounds: int = 3


@dataclass
class DispatchState:
    """In-memory cooldown / fail-count tracking for the current run."""
    last_action_at: dict[str, float] = field(default_factory=dict)
    fail_counts: dict[str, int] = field(default_factory=dict)


def should_skip(
    issue: Issue,
    *,
    state: DispatchState,
    policy: DispatchPolicy,
    upgraded: bool,
) -> Optional[str]:
    """Return a human-readable reason if the Issue should be skipped, else None."""
    if upgraded:
        return "already escalated to FeatureRequest"
    last = state.last_action_at.get(issue.hash)
    if last is not None:
        elapsed = time.time() - last
        if issue.level == IssueLevel.LOW and elapsed < policy.low_cooldown_seconds:
            return f"LOW cooldown ({int(elapsed)}s < {policy.low_cooldown_seconds}s)"
        if issue.level == IssueLevel.MEDIUM and elapsed < policy.medium_cooldown_seconds:
            return f"MEDIUM cooldown ({int(elapsed)}s < {policy.medium_cooldown_seconds}s)"
    return None


def select_fixer(
    issue: Issue,
    *,
    registry: FixerRegistry,
    ctx: FixContext,
) -> Optional[Fixer]:
    """Return the first eligible fixer or ``None`` if only the agent can help."""
    for f in registry.candidates_for(issue.category):
        try:
            if f.can_fix(issue, ctx):
                return f
        except Exception as e:
            logger.warning("Fixer %s.can_fix raised: %s", f.id, e)
    return None


def mark_attempted(state: DispatchState, issue: Issue) -> None:
    state.last_action_at[issue.hash] = time.time()
    state.fail_counts[issue.hash] = state.fail_counts.get(issue.hash, 0) + 1


__all__ = [
    "DispatchPolicy",
    "DispatchState",
    "should_skip",
    "select_fixer",
    "mark_attempted",
]
