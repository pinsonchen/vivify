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
    return FeatureSpec(
        title=title[:200],
        description=desc[:4000],
        type=ftype,
        parent_goal=d.get("parent_goal") or parent_goal,
        priority=priority,
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
        # Quick exit: every KPI already met → no work.
        if goal.kpis and recent_snapshots:
            latest = recent_snapshots[-1].metrics
            if all(
                k.is_met(latest.get(k.name)) for k in goal.kpis if latest.get(k.name) is not None
            ):
                logger.info("Goal '%s' already meets all KPIs; skipping", goal.name)
                return []

        prompt = builders.build_goal_decompose(
            goal,
            repo_state=repo_state,
            open_features=tuple(open_features),
            recent_snapshots=_format_recent_snapshots(recent_snapshots),
            kpi_status=_format_kpi_status(goal, recent_snapshots),
            max_features=self.config.max_features_per_decompose,
        )
        result = self.agent.heal(
            prompt,
            max_turns=self.config.max_turns,
            category="goal_decompose",
            workspace=self.repo_root,
            timeout_seconds=self.config.timeout_seconds,
        )
        output = result.output or ""
        payload = _last_json_block(output, "new_features") or {}
        raw_specs = payload.get("new_features") or []
        if not isinstance(raw_specs, list):
            logger.warning("decompose: 'new_features' is not a list; output ignored")
            return []

        specs: List[FeatureSpec] = []
        existing_titles = [fr.title for fr in open_features]
        for raw in raw_specs[: self.config.max_features_per_decompose]:
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
