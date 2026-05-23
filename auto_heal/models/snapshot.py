"""Snapshot / log / knowledge value objects (persisted via StorageProvider)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class KpiSnapshot:
    source: str                                    # "auto_heal_before" / "auto_heal_after" / "kpi_monitor"
    metrics: dict = field(default_factory=dict)    # {kpi_name: numeric_value}
    overall_score: Optional[float] = None
    grade: Optional[str] = None
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: int = 0


@dataclass
class ActionLog:
    run_id: str
    round_num: int
    action_type: str        # 'detect' | 'direct_fix' | 'heal' | 'skip' | 'maintenance' | 'verify' | 'feature_dev' | 'goal_decompose'
    status: str = "running" # 'success' | 'failed' | 'skipped' | 'running'
    category: Optional[str] = None
    level: Optional[str] = None
    title: Optional[str] = None
    prompt: Optional[str] = None
    result_summary: Optional[str] = None
    improved: bool = False
    duration_seconds: Optional[float] = None
    details: dict = field(default_factory=dict)
    commit_hash: Optional[str] = None
    pr_url: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: int = 0


@dataclass
class KnowledgeEntry:
    category: str          # 'feature' | 'bug_fix' | 'probe_improvement' | 'fixer_improvement'
    pattern: str           # short matchable description (typically the issue/feature title)
    solution_summary: str  # 500-char distilled summary of what worked
    success: bool = True
    feature_id: Optional[int] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: int = 0
