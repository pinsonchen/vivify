"""Failure tracker — wraps the StorageProvider's failure-count primitives.

The kernel uses this to:
* detect when an Issue has been seen but unresolved for N rounds
* feed the escalator (which converts repeated failures into FeatureRequests)
* reset counters once an Issue stops appearing in detection rounds
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from auto_heal.interfaces.storage import StorageProvider
from auto_heal.models.issue import Issue

logger = logging.getLogger(__name__)


@dataclass
class FailureState:
    fail_count: int
    upgraded_feature_id: Optional[int] = None


class FailureTracker:
    """Thin façade so kernel code does not depend on StorageProvider directly."""

    def __init__(self, storage: StorageProvider):
        self.storage = storage

    def record(self, issue: Issue) -> int:
        """Increment the failure count for ``issue`` and return the new value."""
        return self.storage.record_failure(issue.hash, issue.category, issue.title)

    def reset(self, issue_hash: str) -> None:
        self.storage.reset_failure(issue_hash)

    def state(self, issue_hash: str) -> FailureState:
        return FailureState(
            fail_count=self.storage.get_failure_count(issue_hash),
            upgraded_feature_id=self.storage.get_upgraded_feature_id(issue_hash),
        )

    def already_upgraded(self, issue_hash: str) -> bool:
        return self.storage.get_upgraded_feature_id(issue_hash) is not None

    def reset_resolved(self, current_hashes: set[str], known_hashes: set[str]) -> None:
        """Reset counters for hashes that no longer appear in detection."""
        for h in known_hashes - current_hashes:
            try:
                self.storage.reset_failure(h)
            except Exception as e:
                logger.debug("reset_failure(%s) failed: %s", h, e)


__all__ = ["FailureTracker", "FailureState"]
