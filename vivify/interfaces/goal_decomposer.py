"""Goal-to-Feature decomposer interface.

Periodically (or on ``GOALS.md`` change) the kernel asks a ``GoalDecomposer``
to translate user-defined ``Goal``s into concrete ``FeatureSpec``s that can be
fed into the regular feature pipeline. The default implementation in
``vivify.goals.decomposer`` delegates the heavy lifting to a ``CodingAgent``
with a Jinja2 prompt template; tests stub this interface directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Sequence

from vivify.models import FeatureRequest, FeatureSpec, Goal, KpiSnapshot


@dataclass(frozen=True)
class RepoState:
    """Lightweight snapshot of the repo handed to the decomposer.

    The intent is to give the agent enough context to avoid duplicating
    in-flight work without dumping the entire git history into the prompt.
    """

    repo_root: str
    default_branch: str
    top_level_paths: Sequence[str] = field(default_factory=tuple)
    recent_commits: Sequence[str] = field(default_factory=tuple)  # ``"sha title"`` lines
    languages: Sequence[str] = field(default_factory=tuple)       # e.g. ``("python", "typescript")``
    extra: dict = field(default_factory=dict)


class GoalDecomposer(ABC):
    """Parse ``GOALS.md`` and turn unmet goals into proposed features."""

    @abstractmethod
    def parse_goals(self, md_text: str) -> List[Goal]:
        """Parse the textual ``GOALS.md`` body into validated :class:`Goal` objects.

        MUST raise ``ValueError`` (with a human-readable message) on malformed
        input rather than silently dropping goals — ``vivify goals show``
        surfaces these errors to the user.
        """

    @abstractmethod
    def decompose(
        self,
        goal: Goal,
        repo_state: RepoState,
        open_features: Sequence[FeatureRequest],
        recent_snapshots: Sequence[KpiSnapshot],
        deployed_features: Sequence[FeatureRequest] = (),
    ) -> List[FeatureSpec]:
        """Propose new features for ``goal``.

        Implementations should:

        * Skip the goal entirely when its KPIs are already satisfied.
        * De-duplicate against ``open_features`` (titles + descriptions) so we
          don't churn out the same feature every cycle.
        * De-duplicate against ``deployed_features`` to avoid re-proposing
          features that have already been deployed or verified.
        * Cap the number of features returned (see
          ``goals.max_features_per_decompose`` in config) to bound cost.
        """
