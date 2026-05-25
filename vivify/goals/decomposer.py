"""Default :class:`GoalDecomposer` that delegates to a :class:`CodingAgent`.

The decomposer reads ``GOALS.md``, asks the coding agent (via the
``goal_decompose.md.j2`` template) to propose follow-up FeatureSpecs that
close the gap to the goal's KPIs, and returns a deduplicated list.

The agent is read-only: it only emits JSON; this module then validates and
filters the output before any FeatureRequest is created.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import List, Optional, Sequence

from vivify.agents.prompts import builders
from vivify.agents.prompts.parsers import _FENCED_JSON_RE  # type: ignore[attr-defined]
from vivify.goals.differ import is_duplicate
from vivify.goals.parser import parse_goals
from vivify.interfaces.agent import CodingAgent
from vivify.interfaces.goal_decomposer import GoalDecomposer, RepoState
from vivify.models.feature import FeatureRequest, FeatureSpec, Goal
from vivify.models.snapshot import KpiSnapshot
from pathlib import Path

logger = logging.getLogger(__name__)


_VALID_TYPES = {"feature", "bug", "optimization"}
_VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}


@dataclass
class GoalDecomposerConfig:
    max_features_per_decompose: int = 3
    max_turns: int = 30
    timeout_seconds: int = 600
    dedupe_threshold: float = 0.85
    plan_agent_for_decompose: bool = True


def _last_json_block(output: str, key: str) -> Optional[dict]:
    """Find the last fenced ```json``` block that contains ``"<key>"``."""
    for raw in reversed(_FENCED_JSON_RE.findall(output or "")):
        if f'"{key}"' not in raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and key in obj:
            return obj
    return None


def _spec_from_dict(d: dict, *, parent_goal: str) -> Optional[FeatureSpec]:
    title = (d.get("title") or "").strip()
    desc = (d.get("description") or "").strip()
    if not title or not desc:
        return None
    ftype = d.get("type") or "feature"
    if ftype not in _VALID_TYPES:
        ftype = "feature"
    priority = d.get("priority") or None
    if priority and priority not in _VALID_PRIORITIES:
        priority = None
    verification_method = (d.get("verification_method") or "").strip() or None
    # Optional traceability: link this feature back to a specific sub-idea
    # of the goal so we can later show idea → feature → PR provenance.
    idea_id_raw = d.get("idea_id")
    idea_id: Optional[int] = None
    if idea_id_raw is not None:
        try:
            idea_id = int(idea_id_raw)
        except (TypeError, ValueError):
            idea_id = None
    return FeatureSpec(
        title=title[:200],
        description=desc[:4000],
        type=ftype,
        parent_goal=d.get("parent_goal") or parent_goal,
        priority=priority,
        verification_method=verification_method,
        idea_id=idea_id,
    )


def _format_kpi_status(goal: Goal, snapshots: Sequence[KpiSnapshot]) -> str:
    if not goal.kpis:
        return "(no KPIs declared)"
    latest = snapshots[-1].metrics if snapshots else {}
    lines = []
    for kpi in goal.kpis:
        cur = latest.get(kpi.name, "?")
        lines.append(f"- {kpi.name}: target={kpi.target} direction={kpi.direction} current={cur}")
    return "\n".join(lines)


def _format_open_features(open_features: Sequence[FeatureRequest]) -> str:
    if not open_features:
        return "(none)"
    lines = []
    for fr in open_features[:25]:
        lines.append(f"- #{fr.id} [{fr.status}] {fr.title}")
    return "\n".join(lines)


def _format_recent_snapshots(snapshots: Sequence[KpiSnapshot]) -> str:
    if not snapshots:
        return "(no snapshots)"
    lines = []
    for s in list(snapshots)[-5:]:
        date = s.captured_at.strftime("%Y-%m-%d")
        metrics = ", ".join(f"{k}={v}" for k, v in (s.metrics or {}).items())
        lines.append(f"- {date} [{s.source}] {metrics}")
    return "\n".join(lines)


class AgentGoalDecomposer(GoalDecomposer):
    """Default decomposer that delegates the LLM work to a :class:`CodingAgent`."""

    def __init__(
        self,
        *,
        agent: CodingAgent,
        repo_root: Path | str,
        config: GoalDecomposerConfig | None = None,
    ):
        self.agent = agent
        self.repo_root = Path(repo_root)
        self.config = config or GoalDecomposerConfig()

    # ── KPI achievement helpers ───────────────────────────────────────────
    def _count_met_kpis(self, goal: Goal, recent_snapshots: Sequence[KpiSnapshot]) -> int:
        """计算已达标的 KPI 数量。"""
        if not goal.kpis or not recent_snapshots:
            return 0

        latest = recent_snapshots[-1].metrics
        if not latest:
            return 0

        met = 0
        for kpi in goal.kpis:
            current_value = latest.get(kpi.name)
            if current_value is not None and kpi.is_met(current_value):
                met += 1
        return met

    # ── context helpers ────────────────────────────────────────────────────
    def _get_existing_features(self, db_path: Path) -> list[dict]:
        """获取已有 feature_requests 列表（排除 rejected/cancelled）。"""
        if not db_path.exists():
            return []
        try:
            conn = sqlite3.connect(str(db_path))
            c = conn.cursor()
            c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='feature_requests'"
            )
            if not c.fetchone():
                conn.close()
                return []
            c.execute("""
                SELECT title, status, parent_goal, pr_url
                FROM feature_requests
                WHERE status NOT IN ('rejected', 'cancelled')
                ORDER BY id
            """)
            features = [
                {"title": r[0], "status": r[1], "parent_goal": r[2], "pr_url": r[3]}
                for r in c.fetchall()
            ]
            conn.close()
            return features
        except Exception:
            return []

    def _get_kpi_snapshots(self, db_path: Path) -> list[dict]:
        """获取最新 KPI 快照数据。"""
        if not db_path.exists():
            return []
        try:
            conn = sqlite3.connect(str(db_path))
            c = conn.cursor()
            c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='kpi_snapshots'"
            )
            if not c.fetchone():
                conn.close()
                return []
            c.execute("""
                SELECT metric_name, value, target_value
                FROM kpi_snapshots
                ORDER BY created_at DESC
                LIMIT 20
            """)
            snapshots = [
                {"name": r[0], "current": r[1], "target": r[2]}
                for r in c.fetchall()
            ]
            conn.close()
            return snapshots
        except Exception:
            return []

    # ── interface ──────────────────────────────────────────────────────────
    def parse_goals(self, md_text: str) -> List[Goal]:
        return parse_goals(md_text).goals

    def decompose(
        self,
        goal: Goal,
        repo_state: RepoState,
        open_features: Sequence[FeatureRequest],
        recent_snapshots: Sequence[KpiSnapshot],
    ) -> List[FeatureSpec]:
        # ── KPI 达成度停止条件 ─────────────────────────────────────────────
        max_features = self.config.max_features_per_decompose

        if goal.kpis and recent_snapshots:
            met_count = self._count_met_kpis(goal, recent_snapshots)
            total = len(goal.kpis)

            if met_count == total:
                logger.info(
                    "Goal '%s': all %d KPIs met, skipping decomposition",
                    goal.name, total,
                )
                return []

            if total > 1 and met_count >= total * 0.85:
                logger.info(
                    "Goal '%s': %d/%d KPIs met (>=85%%), limiting to 1 feature",
                    goal.name, met_count, total,
                )
                max_features = 1

        # ── Collect additional context from state.db ──────────────────────
        db_path = self.repo_root / ".vivify" / "state.db"
        existing_features = self._get_existing_features(db_path)
        kpi_snapshots_db = self._get_kpi_snapshots(db_path)

        prompt = builders.build_goal_decompose(
            goal,
            repo_state=repo_state,
            open_features=tuple(open_features),
            recent_snapshots=_format_recent_snapshots(recent_snapshots),
            kpi_status=_format_kpi_status(goal, recent_snapshots),
            max_features=max_features,
            existing_features=existing_features,
            kpi_snapshots=kpi_snapshots_db,
        )
        result = self.agent.heal(
            prompt,
            max_turns=self.config.max_turns,
            category="goal_decompose",
            workspace=self.repo_root,
            timeout_seconds=self.config.timeout_seconds,
            agent_name="Plan" if self.config.plan_agent_for_decompose else None,
        )
        output = result.output or ""
        payload = _last_json_block(output, "new_features") or {}
        raw_specs = payload.get("new_features") or []
        if not isinstance(raw_specs, list):
            logger.warning("decompose: 'new_features' is not a list; output ignored")
            return []

        specs: List[FeatureSpec] = []
        existing_titles = [fr.title for fr in open_features]
        for raw in raw_specs[:max_features]:
            if not isinstance(raw, dict):
                continue
            spec = _spec_from_dict(raw, parent_goal=goal.name)
            if spec is None:
                continue
            if is_duplicate(spec.title, existing_titles,
                            threshold=self.config.dedupe_threshold):
                logger.info("decompose: dropping dup title '%s'", spec.title)
                continue
            existing_titles.append(spec.title)
            specs.append(spec)
        return specs


__all__ = ["AgentGoalDecomposer", "GoalDecomposerConfig"]
