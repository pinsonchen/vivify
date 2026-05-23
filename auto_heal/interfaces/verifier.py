"""Verifier interface — confirms a fix or feature actually worked.

After a Fixer or CodingAgent finishes, the kernel runs verifiers to confirm
the original issue is resolved (``before_after``) and to capture KPI deltas
(``kpi_snapshot``). Verifiers never modify code; they only observe.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from auto_heal.interfaces.probe import ProbeContext
from auto_heal.models import FeatureRequest, Issue


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of a single verifier run."""

    verified: bool
    """``True`` = success; ``False`` = regression detected; ``None``-equivalent
    callers should use :pyattr:`uncertain` for "could not determine"."""

    summary: str
    """Human-readable one-liner shown in logs / PR comments."""

    issues: tuple[str, ...] = field(default_factory=tuple)
    """Specific problems found during verification (empty when verified)."""

    uncertain: bool = False
    """``True`` when the verifier could not reach a conclusion (e.g. flaky
    network, missing signal). Callers typically retry or escalate."""

    metrics: dict = field(default_factory=dict)
    """Optional structured payload (durations, counts, KPI deltas) for later
    analysis or storage as a :class:`KpiSnapshot`."""


class Verifier(ABC):
    """Pluggable post-action verifier."""

    @abstractmethod
    def name(self) -> str:
        """Stable identifier (e.g. ``"before_after"``, ``"kpi_snapshot"``)."""

    def verify_issue(self, issue: Issue, ctx: ProbeContext) -> Optional[VerifyResult]:
        """Verify that ``issue`` has been resolved.

        Default ``None`` means "this verifier does not apply to issues";
        override in verifiers that do issue-level checks.
        """
        return None

    def verify_feature(
        self, feature: FeatureRequest, ctx: ProbeContext
    ) -> Optional[VerifyResult]:
        """Verify that ``feature`` was implemented correctly.

        Default ``None`` means "this verifier does not apply to features".
        """
        return None
