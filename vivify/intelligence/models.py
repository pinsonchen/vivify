"""Data models for intelligence analysis (trend reports, anomalies, RCA, etc.)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from vivify.models.issue import Issue


@dataclass
class RcaReport:
    """Root Cause Analysis report for a recurring issue."""
    issue_hash: str = ""
    recurrence_count: int = 0
    root_cause: str = ""              # AI 生成的根因分析
    pattern: str = ""                 # 识别的问题模式
    suggested_strategy: str = ""      # 建议策略
    related_issues: List[str] = field(default_factory=list)
    confidence: float = 0.5           # 0-1 置信度
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: int = 0


@dataclass
class IssueCluster:
    """A cluster of similar issues grouped by category and title similarity."""
    representative: Optional[Issue] = None
    members: List[Issue] = field(default_factory=list)
    category: str = ""
    common_pattern: str = ""         # 聚类共性描述


@dataclass
class KpiTrend:
    """Trend analysis result for a single KPI."""

    direction: str = "stable"  # "improving" | "stable" | "degrading"
    slope: float = 0.0
    current_value: float = 0.0
    predicted_value: float = 0.0
    confidence: float = 0.0  # R² value


@dataclass
class Anomaly:
    """A detected anomaly in KPI data."""

    kpi_name: str = ""
    value: float = 0.0
    z_score: float = 0.0
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    context: str = ""


@dataclass
class Correlation:
    """Correlation between a code change and KPI movement."""

    action_id: int = 0
    action_title: str = ""
    kpi_name: str = ""
    delta: float = 0.0
    direction: str = ""  # "positive" | "negative"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class TrendReport:
    """Complete trend analysis report."""

    kpi_trends: Dict[str, KpiTrend] = field(default_factory=dict)
    anomalies: List[Anomaly] = field(default_factory=list)
    predictions: Dict[str, float] = field(default_factory=dict)
    period_start: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    period_end: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class HealthSummary:
    """Project health summary derived from trend analysis."""

    grade: str = "B"  # A/B/C/D
    improving: List[str] = field(default_factory=list)
    degrading: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
