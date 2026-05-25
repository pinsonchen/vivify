"""Goal / KPI / FeatureSpec / FeatureRequest models.

Goals are user-declared in ``GOALS.md``; the goal-decomposer turns them into FeatureSpecs
which the storage layer materializes as FeatureRequests with persistent ids and status.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Literal, Optional


@dataclass(frozen=True)
class KPI:
    name: str
    target: str       # e.g. ">=98%", "<=8min", "0"
    direction: Literal["up", "down", "stable"] = "up"
    unit: str = ""

    def is_met(self, current: float | int | str) -> bool:
        """Best-effort comparison given a numeric current value.

        Falls back to True (skip) if the target can't be parsed; keeps decomposer robust.
        """
        try:
            target_num = float(
                self.target.replace(">=", "").replace("<=", "").replace("%", "").replace("min", "").strip()
            )
            cur_num = float(str(current).replace("%", "").replace("min", "").strip())
        except (TypeError, ValueError):
            return True
        if self.direction == "up":
            return cur_num >= target_num
        if self.direction == "down":
            return cur_num <= target_num
        return abs(cur_num - target_num) < 1e-6


@dataclass(frozen=True)
class Goal:
    name: str
    description: str = ""
    kpis: tuple[KPI, ...] = ()
    deadline: Optional[date] = None
    notes: str = ""


# ────────────────────────────────────────────────────────────────────────────────
# Feature pipeline
# ────────────────────────────────────────────────────────────────────────────────

FeatureType = Literal["feature", "bug", "optimization"]
FeaturePriority = Literal["P0", "P1", "P2", "P3"]
FeatureStatus = Literal[
    "pending", "evaluating", "approved", "rejected",
    "developing", "deployed", "verifying", "verified",
    "deployed_with_issues",
]


@dataclass
class FeatureSpec:
    title: str
    description: str
    type: FeatureType = "feature"
    parent_goal: Optional[str] = None
    parent_id: Optional[int] = None
    priority: Optional[FeaturePriority] = None
    verification_method: Optional[str] = None
    idea_id: Optional[int] = None


@dataclass
class FeatureRequest:
    title: str
    description: str
    type: FeatureType = "feature"
    parent_goal: Optional[str] = None
    parent_id: Optional[int] = None
    priority: Optional[FeaturePriority] = None
    verification_method: Optional[str] = None
    id: int = 0
    status: FeatureStatus = "pending"
    development_result: str = ""
    commit_hash: Optional[str] = None
    pr_url: Optional[str] = None
    feasibility: str = ""
    summary: str = ""
    # ── channels-monitor inspired lifecycle/tracking fields (migration 0003) ──
    image_urls: Optional[str] = None  # JSON array of URLs
    idea_id: Optional[int] = None
    retry_count: int = 0
    batch_commit_hash: Optional[str] = None
    verification_result: Optional[str] = None  # JSON string
    evaluated_at: Optional[str] = None  # ISO format timestamp
    started_at: Optional[str] = None
    verified_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
