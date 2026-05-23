"""Escalator — turns chronic Issues into FeatureRequests.

When the same problem recurs ``upgrade_threshold`` times without resolution,
the escalator creates a ``bug`` FeatureRequest so a heavier-weight pipeline
(coding agent + worktree + PR) can take over.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from vivify.interfaces.storage import StorageProvider
from vivify.kernel.failure_tracker import FailureTracker
from vivify.models.feature import FeatureRequest
from vivify.models.issue import Issue, IssueLevel

logger = logging.getLogger(__name__)


@dataclass
class EscalationPolicy:
    upgrade_threshold: int = 3
    upgrade_levels: tuple[IssueLevel, ...] = (
        IssueLevel.CRITICAL,
        IssueLevel.HIGH,
        IssueLevel.MEDIUM,
    )


class Escalator:
    """Convert chronic Issues into FeatureRequests."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        tracker: FailureTracker,
        policy: EscalationPolicy | None = None,
    ):
        self.storage = storage
        self.tracker = tracker
        self.policy = policy or EscalationPolicy()

    def maybe_escalate(self, issue: Issue) -> Optional[int]:
        """Return the new feature id if escalation happened, else None."""
        if issue.level not in self.policy.upgrade_levels:
            return None
        if self.tracker.already_upgraded(issue.hash):
            return None
        state = self.tracker.state(issue.hash)
        if state.fail_count < self.policy.upgrade_threshold:
            return None

        fr = FeatureRequest(
            title=f"[vivify escalation] {issue.title}",
            description=self._render_description(issue, state.fail_count),
            type="bug",
            priority="P1" if issue.level in (IssueLevel.CRITICAL, IssueLevel.HIGH) else "P2",
        )
        try:
            new_id = self.storage.create_feature(fr)
        except Exception as e:
            logger.error("Escalation create_feature failed for %s: %s", issue.hash, e)
            return None

        self.storage.mark_upgraded(issue.hash, new_id)
        logger.warning(
            "Escalated %s/%s to FeatureRequest #%s after %d failures",
            issue.category, issue.title, new_id, state.fail_count,
        )
        return new_id

    @staticmethod
    def _render_description(issue: Issue, fail_count: int) -> str:
        return (
            f"Auto-escalated by vivify after {fail_count} failed "
            f"remediation rounds.\n\n"
            f"- category: `{issue.category}`\n"
            f"- level: `{issue.level.value}`\n"
            f"- source probe: `{issue.source_probe}`\n"
            f"- hash: `{issue.hash}`\n\n"
            f"### Original detection\n"
            f"{issue.description or '(no description provided)'}\n"
        )


__all__ = ["Escalator", "EscalationPolicy"]
