"""Prompt builders — render Jinja2 templates with the right snippets injected.

Each public ``build_*`` returns a fully-formed prompt string ready to pass to
:meth:`CodingAgent.heal`. They sit on top of :mod:`vivify.agents.prompts.snippets`
and the templates under ``templates/``.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Sequence

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from vivify.agents.prompts import snippets
from vivify.models import FeatureRequest, Goal, Issue
from vivify.interfaces.goal_decomposer import RepoState

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@lru_cache(maxsize=1)
def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(default=False),  # plain text, not HTML
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
    )
    # ``tojson`` is built-in but we re-register so callers can override later.
    return env


def _render(template_name: str, **vars: Any) -> str:
    return _env().get_template(template_name).render(**vars)


# ── public builders ──────────────────────────────────────────────────────────
def build_fix_issue(
    issue: Issue,
    *,
    workspace: str,
    recent_history: str = "",
    remediation_hint: str = "",
    auth_block: Optional[str] = None,
    enable_self_improve: bool = False,
) -> str:
    """Prompt for fixing an :class:`Issue` the direct-fix path could not handle."""
    return _render(
        "fix_issue.md.j2",
        issue=issue,
        workspace=workspace,
        recent_history=recent_history,
        remediation_hint=remediation_hint,
        auth_block=auth_block or "",
        git_pr_snippet=snippets.GIT_PR_SNIPPET,
        next_steps_snippet=snippets.NEXT_STEPS_SNIPPET,
        self_improve_snippet=snippets.SELF_IMPROVE_SNIPPET if enable_self_improve else "",
    )


def build_feature_evaluate(
    feature: FeatureRequest,
    *,
    repo_summary: str = "",
    related_knowledge: str = "",
) -> str:
    """Prompt for the read-only evaluation phase of a feature request."""
    return _render(
        "feature_evaluate.md.j2",
        feature=feature,
        repo_summary=repo_summary,
        related_knowledge=related_knowledge,
    )


def build_feature_develop(
    feature: FeatureRequest,
    *,
    workspace: str,
    recent_history: str = "",
    related_knowledge: str = "",
    implementation_approach: Optional[str] = None,
) -> str:
    """Prompt for actually implementing a feature inside a worktree."""
    # Allow callers to inject the approach decided during evaluation.
    feat = feature
    if implementation_approach:
        # Avoid mutating the caller's dataclass; use a shallow copy with attr.
        from dataclasses import replace as _replace  # local to dodge cycles
        feat = _replace(feature)
        # Stash the approach on a transient attribute the template reads.
        object.__setattr__(feat, "implementation_approach", implementation_approach)
    elif not hasattr(feat, "implementation_approach"):
        object.__setattr__(feat, "implementation_approach", "")
    return _render(
        "feature_develop.md.j2",
        feature=feat,
        workspace=workspace,
        recent_history=recent_history,
        related_knowledge=related_knowledge,
        git_pr_snippet=snippets.GIT_PR_SNIPPET,
        next_steps_snippet=snippets.NEXT_STEPS_SNIPPET,
    )


def build_feature_verify(feature: FeatureRequest) -> str:
    """Prompt for the verification phase of a merged feature."""
    return _render("feature_verify.md.j2", feature=feature)


def build_goal_decompose(
    goal: Goal,
    *,
    repo_state: RepoState,
    open_features: Sequence[FeatureRequest] = (),
    recent_snapshots: str = "",
    kpi_status: str = "",
    max_features: int = 3,
    existing_features: Sequence[dict] = (),
    kpi_snapshots: Sequence[dict] = (),
) -> str:
    """Prompt for breaking a :class:`Goal` into :class:`FeatureSpec`s."""
    return _render(
        "goal_decompose.md.j2",
        goal=goal,
        repo_state=repo_state,
        open_features=open_features,
        recent_snapshots=recent_snapshots,
        kpi_status=kpi_status,
        max_features=int(max_features),
        existing_features=existing_features,
        kpi_snapshots=kpi_snapshots,
    )


__all__ = [
    "build_fix_issue",
    "build_feature_evaluate",
    "build_feature_develop",
    "build_feature_verify",
    "build_goal_decompose",
]
