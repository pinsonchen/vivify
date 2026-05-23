"""Reporter interface — sinks for ``ActionLog`` events.

Reporters receive structured records about every meaningful action the kernel
takes (probe runs, fix attempts, agent invocations, PR creations, ...). The
default fan-out is ``logger`` (writes to the local log file) +
``StorageReporter`` (persists to SQLite); when configured, a
``GitHubIssueReporter`` mirrors high-severity events as GitHub issues.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from auto_heal.models import ActionLog


class Reporter(ABC):
    """Pluggable sink for :class:`ActionLog` records."""

    @abstractmethod
    def report(self, action: ActionLog) -> None:
        """Persist or forward ``action``.

        Implementations MUST be best-effort: a reporter failure must never
        propagate up to the kernel. Wrap exceptions internally and log them.
        """

    def flush(self) -> None:
        """Optional flush hook (override for buffered reporters)."""

    def close(self) -> None:
        """Optional cleanup hook (override for reporters that hold resources)."""

    def name(self) -> Optional[str]:
        """Stable identifier — useful for ``auto-heal doctor`` diagnostics."""
        return self.__class__.__name__
