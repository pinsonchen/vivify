"""StorageProvider ABC.

The kernel persists feature requests, action logs, failure tracking, knowledge entries,
and KPI snapshots through this single interface. Default implementation is SQLite
(``vivify/storage/sqlite_provider.py``); users can swap to a remote API by setting
``storage.type: remote`` in ``.vivify.yml``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional

from vivify.models.feature import FeatureRequest, FeatureStatus
from vivify.models.snapshot import ActionLog, KnowledgeEntry, KpiSnapshot


class StorageProvider(ABC):
    # ── lifecycle ──
    @abstractmethod
    def initialize(self) -> None:
        """Create schema if missing (run migrations)."""

    @abstractmethod
    def close(self) -> None: ...

    # ── feature requests ──
    @abstractmethod
    def create_feature(self, fr: FeatureRequest) -> int: ...
    @abstractmethod
    def get_feature(self, fid: int) -> Optional[FeatureRequest]: ...
    @abstractmethod
    def list_features(
        self,
        status: Optional[FeatureStatus] = None,
        limit: int = 200,
    ) -> list[FeatureRequest]: ...
    @abstractmethod
    def update_feature(self, fid: int, **fields) -> None: ...

    # ── action logs ──
    @abstractmethod
    def log_action(self, record: ActionLog) -> int: ...
    @abstractmethod
    def search_action_logs(
        self,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> list[ActionLog]: ...

    # ── failure tracking (dedup + escalation) ──
    @abstractmethod
    def record_failure(
        self, problem_hash: str, category: str, title: str
    ) -> int:
        """Increment counter; return new fail_count."""

    @abstractmethod
    def reset_failure(self, problem_hash: str) -> None: ...
    @abstractmethod
    def mark_upgraded(self, problem_hash: str, feature_id: int) -> None: ...
    @abstractmethod
    def get_failure_count(self, problem_hash: str) -> int: ...
    @abstractmethod
    def get_upgraded_feature_id(self, problem_hash: str) -> Optional[int]: ...

    # ── knowledge ──
    @abstractmethod
    def add_knowledge(self, k: KnowledgeEntry) -> int: ...
    @abstractmethod
    def search_knowledge(
        self, category: str, pattern: str, limit: int = 5
    ) -> list[KnowledgeEntry]: ...

    # ── KPI snapshots ──
    @abstractmethod
    def write_snapshot(self, snap: KpiSnapshot) -> int: ...
    @abstractmethod
    def read_snapshots(self, since: datetime) -> list[KpiSnapshot]: ...

    # ── goal decompose awareness ──
    def get_recent_kpi_snapshots(self, days: int = 7) -> list[KpiSnapshot]:
        """Return KPI snapshots from the last *days* days.

        Default implementation delegates to :meth:`read_snapshots`.
        """
        since = datetime.now(tz=timezone.utc) - timedelta(days=days)
        return self.read_snapshots(since)

    def get_deployed_features(self, days: int = 30) -> list[FeatureRequest]:
        """Return features deployed/verified within the last *days* days.

        Subclasses should override for efficiency; the default returns an
        empty list (backward-compatible no-op).
        """
        return []
