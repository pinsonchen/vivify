"""Lightweight dataclass models — no behavior, just shape."""
from auto_heal.models.issue import Issue, IssueLevel
from auto_heal.models.fix_result import FixResult
from auto_heal.models.agent_result import AgentResult
from auto_heal.models.feature import KPI, Goal, FeatureSpec, FeatureRequest
from auto_heal.models.snapshot import KpiSnapshot, ActionLog, KnowledgeEntry

__all__ = [
    "Issue", "IssueLevel",
    "FixResult", "AgentResult",
    "KPI", "Goal", "FeatureSpec", "FeatureRequest",
    "KpiSnapshot", "ActionLog", "KnowledgeEntry",
]
