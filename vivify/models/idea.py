"""Idea model — intermediate layer between Goal and FeatureRequest.

An Idea groups related FeatureRequests under a common theme/initiative.
Goals are high-level objectives declared in GOALS.md; Ideas are the concrete
initiatives extracted during decomposition; FeatureRequests are the atomic
development tasks that implement each Idea.

Lifecycle: proposed → approved → decomposed → completed
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


IdeaStatus = str  # "proposed" | "approved" | "decomposed" | "completed"

IDEA_STATUSES = ("proposed", "approved", "decomposed", "completed")


@dataclass
class Idea:
    """An Idea groups related FeatureRequests under a common theme.

    Lifecycle: proposed → approved → decomposed → completed
    """

    id: Optional[int] = None
    title: str = ""
    description: str = ""
    goal_id: Optional[int] = None       # 来源 Goal (GOALS.md 中的哪个目标)
    status: IdeaStatus = "proposed"     # proposed / approved / decomposed / completed
    priority: int = 50                   # 0-100, 越高越优先

    # 可行性评估
    feasibility_score: Optional[float] = None  # 0.0-1.0
    estimated_effort: Optional[str] = None     # small / medium / large

    # 时间追踪
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    approved_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # 关联 — FR ids populated externally by storage queries
    feature_request_ids: List[int] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return self.status in ("proposed", "approved", "decomposed")


__all__ = ["Idea", "IdeaStatus", "IDEA_STATUSES"]
