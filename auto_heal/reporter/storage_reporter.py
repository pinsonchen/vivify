"""Reporter that persists :class:`ActionLog` records via a :class:`StorageProvider`.

The kernel logs actions both via the storage provider (durable, queryable) and
through reporters (fan-out to logs, GitHub mirroring, etc.). This reporter is
the canonical bridge for users who want all events in their SQLite DB.
"""
from __future__ import annotations

import logging

from auto_heal.interfaces.reporter import Reporter
from auto_heal.interfaces.storage import StorageProvider
from auto_heal.models.snapshot import ActionLog

logger = logging.getLogger(__name__)


class StorageReporter(Reporter):
    """Forward :class:`ActionLog` rows into a :class:`StorageProvider`."""

    def __init__(self, storage: StorageProvider):
        self.storage = storage

    def report(self, action: ActionLog) -> None:
        try:
            self.storage.log_action(action)
        except Exception as e:  # pragma: no cover — must never raise
            logger.debug("StorageReporter swallowed: %s", e)


__all__ = ["StorageReporter"]
