"""Issue + IssueLevel — the canonical detection unit.

An ``Issue`` is the standardized output every probe emits. The kernel groups, prioritizes,
and routes Issues to fixers/agents. Mirrors `_issue()` from
``/tmp/channels-monitor/auto_heal/analyzer.py`` but without channels-business specifics.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class IssueLevel(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    def priority(self) -> int:
        return {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}[self.value]


@dataclass(frozen=True)
class Issue:
    category: str
    level: IssueLevel
    title: str
    description: str = ""
    data: dict = field(default_factory=dict)
    source_probe: str = "unknown"
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    hash: str = ""  # filled by ``factory`` when omitted

    @classmethod
    def factory(
        cls,
        *,
        category: str,
        level: IssueLevel | str,
        title: str,
        description: str = "",
        data: dict | None = None,
        source_probe: str = "unknown",
    ) -> "Issue":
        """Build an Issue with a deterministic hash.

        Hash inputs include ``source_probe`` to avoid collisions when two probes happen to
        report the same category/title (a real risk noted in plan §14).
        """
        data = data or {}
        level_enum = IssueLevel(level) if isinstance(level, str) else level
        payload = json.dumps(data, ensure_ascii=False, default=str, sort_keys=True)[:200]
        h = hashlib.md5(
            f"{source_probe}:{category}:{title}:{payload}".encode("utf-8")
        ).hexdigest()[:12]
        return cls(
            category=category,
            level=level_enum,
            title=title,
            description=description,
            data=data,
            source_probe=source_probe,
            hash=h,
        )

    @property
    def is_blocking(self) -> bool:
        """Whether a CRITICAL infra issue should pause feature development.

        Generic heuristic: CRITICAL issues whose category contains infra/runtime keywords
        block dev work. Plug-in code can override in the kernel via config.
        """
        if self.level != IssueLevel.CRITICAL:
            return False
        cat = (self.category or "").lower()
        blocking_keywords = ("backend", "api", "service", "deploy", "build", "ci", "secret")
        non_blocking_keywords = ("doc", "lint", "format", "stale", "size")
        if any(k in cat for k in non_blocking_keywords):
            return False
        return any(k in cat for k in blocking_keywords)

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "level": self.level.value,
            "title": self.title,
            "description": self.description,
            "data": self.data,
            "source_probe": self.source_probe,
            "detected_at": self.detected_at.isoformat(),
            "hash": self.hash,
        }
