"""Knowledge Graph Garbage Collection - prevents unbounded growth.

Implements a three-phase lifecycle for knowledge nodes:
  active → stale → archived → deleted

Activity tracking persists to `.vivify/knowledge/activity.json`.
GC runs periodically (default: every 24h) and enforces both time-based
aging and hard node-count limits.
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class GCConfig:
    """GC configuration with sensible defaults."""

    max_nodes: int = 500  # 图谱节点硬限
    max_modules: int = 50  # 模块卡片硬限
    stale_days: int = 30  # N 天未被引用视为 stale
    archive_after_days: int = 60  # N 天后归档
    delete_after_days: int = 90  # N 天后删除
    min_access_count: int = 2  # 最低引用次数（低于此值加速老化）
    gc_interval_hours: int = 24  # GC 执行间隔


@dataclass
class NodeActivity:
    """Tracks activity/usage of a knowledge node."""

    node_id: str
    access_count: int = 0  # 被查询的总次数
    last_accessed: Optional[str] = None  # ISO format datetime string
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    status: str = "active"  # active / stale / archived / deleted

    @property
    def last_accessed_dt(self) -> Optional[datetime]:
        if self.last_accessed is None:
            return None
        try:
            return datetime.fromisoformat(self.last_accessed)
        except (ValueError, TypeError):
            return None

    @property
    def created_at_dt(self) -> datetime:
        try:
            return datetime.fromisoformat(self.created_at)
        except (ValueError, TypeError):
            return datetime.now()

    @property
    def days_since_access(self) -> int:
        ref = self.last_accessed_dt if self.last_accessed_dt else self.created_at_dt
        return (datetime.now() - ref).days

    @property
    def staleness_score(self) -> float:
        """0.0 = fresh, 1.0 = extremely stale."""
        days = self.days_since_access
        # 低访问量加速老化
        access_penalty = 1.0 if self.access_count >= 2 else 1.5
        return min(1.0, (days * access_penalty) / 90.0)

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "access_count": self.access_count,
            "last_accessed": self.last_accessed,
            "created_at": self.created_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NodeActivity":
        return cls(
            node_id=data["node_id"],
            access_count=data.get("access_count", 0),
            last_accessed=data.get("last_accessed"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            status=data.get("status", "active"),
        )


@dataclass
class GCReport:
    """Report of GC actions taken."""

    marked_stale: List[str] = field(default_factory=list)
    archived: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    evicted: List[str] = field(default_factory=list)

    @property
    def total_actions(self) -> int:
        return (
            len(self.marked_stale)
            + len(self.archived)
            + len(self.deleted)
            + len(self.evicted)
        )

    @property
    def summary(self) -> str:
        parts = []
        if self.marked_stale:
            parts.append(f"{len(self.marked_stale)} marked stale")
        if self.archived:
            parts.append(f"{len(self.archived)} archived")
        if self.deleted:
            parts.append(f"{len(self.deleted)} deleted")
        if self.evicted:
            parts.append(f"{len(self.evicted)} evicted (over limit)")
        return "; ".join(parts) if parts else "No actions needed"


class KnowledgeGC:
    """Garbage collector for knowledge graph nodes."""

    def __init__(self, config: GCConfig, knowledge_dir: Path):
        self._config = config
        self._knowledge_dir = knowledge_dir
        self._activity_file = knowledge_dir / "activity.json"
        self._archive_dir = knowledge_dir / "archive"
        self._activities: Dict[str, NodeActivity] = {}
        self._load_activities()

    @property
    def config(self) -> GCConfig:
        return self._config

    @property
    def activities(self) -> Dict[str, NodeActivity]:
        return self._activities

    def record_access(self, node_id: str) -> None:
        """Record that a node was accessed/used."""
        if node_id not in self._activities:
            self._activities[node_id] = NodeActivity(node_id=node_id)
        activity = self._activities[node_id]
        activity.access_count += 1
        activity.last_accessed = datetime.now().isoformat()
        self._save_activities()

    def run_gc(self) -> GCReport:
        """Execute garbage collection pass.

        Returns report of actions taken.
        """
        report = GCReport()

        # Phase 1: Mark stale nodes
        for node_id, activity in self._activities.items():
            if activity.status != "active":
                continue
            if activity.days_since_access >= self._config.stale_days:
                if activity.access_count < self._config.min_access_count:
                    activity.status = "stale"
                    report.marked_stale.append(node_id)

        # Phase 2: Archive stale nodes
        for node_id, activity in self._activities.items():
            if activity.status != "stale":
                continue
            if activity.days_since_access >= self._config.archive_after_days:
                activity.status = "archived"
                report.archived.append(node_id)
                self._archive_node(node_id)

        # Phase 3: Delete archived nodes past retention
        for node_id, activity in list(self._activities.items()):
            if activity.status != "archived":
                continue
            if activity.days_since_access >= self._config.delete_after_days:
                activity.status = "deleted"
                report.deleted.append(node_id)
                self._delete_node(node_id)

        # Phase 4: Enforce hard limits (by staleness score, evict most stale first)
        active_count = sum(
            1 for a in self._activities.values() if a.status == "active"
        )
        if active_count > self._config.max_nodes:
            excess = active_count - self._config.max_nodes
            # Sort by staleness, evict the most stale
            candidates = sorted(
                [
                    (nid, a)
                    for nid, a in self._activities.items()
                    if a.status == "active"
                ],
                key=lambda x: x[1].staleness_score,
                reverse=True,
            )
            for nid, activity in candidates[:excess]:
                activity.status = "archived"
                report.evicted.append(nid)
                self._archive_node(nid)

        self._save_activities()
        return report

    def get_node_weight(self, node_id: str) -> float:
        """Get relevance weight for a node (used by context_provider).

        Fresh, frequently-used nodes get weight 1.0.
        Stale nodes get reduced weight.
        """
        activity = self._activities.get(node_id)
        if activity is None:
            return 0.5  # Unknown nodes get neutral weight
        if activity.status != "active":
            return 0.1  # Non-active nodes get minimal weight
        return max(0.1, 1.0 - activity.staleness_score * 0.8)

    def get_stats(self) -> Dict[str, int]:
        """Return summary statistics of node statuses."""
        stats: Dict[str, int] = {
            "active": 0,
            "stale": 0,
            "archived": 0,
            "deleted": 0,
        }
        for activity in self._activities.values():
            stats[activity.status] = stats.get(activity.status, 0) + 1
        return stats

    def _archive_node(self, node_id: str) -> None:
        """Move node data to archive directory."""
        try:
            self._archive_dir.mkdir(parents=True, exist_ok=True)
            # Try to archive module JSON if it exists
            # node_id format: "module:vivify/kernel" or "file:path"
            module_name = self._node_id_to_module_name(node_id)
            if module_name:
                src = self._knowledge_dir / "modules" / f"{module_name}.json"
                if src.exists():
                    dst = self._archive_dir / f"{module_name}.json"
                    shutil.move(str(src), str(dst))
                    logger.debug("Archived module %s to %s", module_name, dst)
        except Exception as e:
            logger.debug("_archive_node(%s) failed: %s", node_id, e)

    def _delete_node(self, node_id: str) -> None:
        """Permanently delete node data."""
        try:
            module_name = self._node_id_to_module_name(node_id)
            if module_name:
                # Delete from archive if present
                archived = self._archive_dir / f"{module_name}.json"
                if archived.exists():
                    archived.unlink()
                    logger.debug("Deleted archived module %s", module_name)
                # Also check modules dir (shouldn't be there, but defensive)
                live = self._knowledge_dir / "modules" / f"{module_name}.json"
                if live.exists():
                    live.unlink()
        except Exception as e:
            logger.debug("_delete_node(%s) failed: %s", node_id, e)

    def _node_id_to_module_name(self, node_id: str) -> Optional[str]:
        """Extract a file-safe module name from a node ID.

        node_id format examples:
          "module:vivify/kernel" → "kernel"
          "module:vivify/knowledge" → "knowledge"
          "file:vivify/kernel/loop.py" → None (only modules are archived)
        """
        if not node_id.startswith("module:"):
            return None
        # Take the last path component
        path_part = node_id.split(":", 1)[1] if ":" in node_id else node_id
        parts = path_part.rstrip("/").split("/")
        return parts[-1] if parts else None

    def _load_activities(self) -> None:
        """Load activity tracking from JSON."""
        if not self._activity_file.exists():
            self._activities = {}
            return
        try:
            data = json.loads(self._activity_file.read_text(encoding="utf-8"))
            self._activities = {
                item["node_id"]: NodeActivity.from_dict(item)
                for item in data
                if "node_id" in item
            }
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("Failed to load activity.json: %s", e)
            self._activities = {}

    def _save_activities(self) -> None:
        """Persist activity tracking to JSON."""
        try:
            self._knowledge_dir.mkdir(parents=True, exist_ok=True)
            data = [a.to_dict() for a in self._activities.values()]
            self._activity_file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug("Failed to save activity.json: %s", e)


__all__ = ["GCConfig", "GCReport", "KnowledgeGC", "NodeActivity"]
