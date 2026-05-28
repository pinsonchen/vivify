"""Knowledge graph maintainer for incremental updates during daemon execution.

Responsibilities:
- Check for code changes (git commits) periodically
- Trigger incremental knowledge graph updates
- Rate-limit updates (max once per 10 minutes)
- Non-blocking: failures never affect the main loop
"""
from pathlib import Path
from typing import Optional
import time
import logging

from vivify.knowledge.builder import KnowledgeBuilder

logger = logging.getLogger(__name__)


class KnowledgeMaintainer:
    """Maintains knowledge graph freshness during daemon execution."""

    def __init__(
        self,
        project_root: Path,
        qodercli_binary: str = "qodercli",
        wiki_path: str = "",
        permission_mode: str = "bypass_permissions",
        min_interval_seconds: int = 600,  # 10 分钟最小间隔
    ):
        self.root = project_root
        self.builder = KnowledgeBuilder(
            project_root,
            qodercli_binary=qodercli_binary,
            wiki_path=wiki_path,
            permission_mode=permission_mode,
            timeout=60,  # 增量更新超时较短
        )
        self.min_interval = min_interval_seconds
        self._last_check_time: float = 0
        self._update_needed: bool = False

    def maybe_update(self) -> None:
        """Check and update if needed. Safe to call every loop iteration.

        Rate-limited: actual check runs at most once per min_interval.
        Never raises exceptions.
        """
        now = time.time()
        if now - self._last_check_time < self.min_interval:
            return

        self._last_check_time = now

        try:
            result = self.builder.build_incremental()
            if result:
                logger.info("Knowledge graph updated incrementally (%d nodes)", len(result.nodes))
        except Exception as e:
            logger.debug("Knowledge maintenance skipped: %s", e)

    def mark_update_needed(self) -> None:
        """Mark that an update is needed (e.g. after PR merge).

        This resets the rate limiter so next maybe_update() will run immediately.
        """
        self._last_check_time = 0
        self._update_needed = True

    def is_available(self) -> bool:
        """Check if knowledge graph exists for this project."""
        from vivify.knowledge.storage import KnowledgeStorage
        storage = KnowledgeStorage(self.root)
        return storage.load_graph() is not None
