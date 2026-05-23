"""Before/after verifier — re-runs the source probe and compares Issues.

If the probe that originally produced an Issue still emits the same hash after
the fix landed, we conclude the fix did not work. If the issue disappears (or
its hash changes), we report it as resolved.
"""
from __future__ import annotations

import logging
from typing import Optional

from vivify.interfaces.probe import Probe, ProbeContext
from vivify.interfaces.verifier import VerifyResult, Verifier
from vivify.models.feature import FeatureRequest
from vivify.models.issue import Issue

logger = logging.getLogger(__name__)


class BeforeAfterVerifier(Verifier):
    """Run the source probe again and compare hashes."""

    def __init__(self, *, probes: list[Probe]):
        self._probes_by_id = {p.id: p for p in probes}

    def name(self) -> str:
        return "before_after"

    def verify_issue(self, issue: Issue, ctx: ProbeContext) -> Optional[VerifyResult]:
        probe = self._probes_by_id.get(issue.source_probe)
        if probe is None:
            return VerifyResult(
                verified=False, summary=f"source probe '{issue.source_probe}' not registered",
                uncertain=True,
            )
        try:
            raw = probe.collect(ctx)
            after = probe.analyze(raw or {}, ctx) or []
        except Exception as e:  # pragma: no cover — defensive
            logger.warning("BeforeAfterVerifier: probe %s raised: %s", probe.id, e)
            return VerifyResult(
                verified=False, summary=f"probe error: {e!r}", uncertain=True,
            )
        # Compare by hash; if the same hash recurs the issue was not resolved.
        still_present = any(i.hash == issue.hash for i in after)
        if still_present:
            return VerifyResult(
                verified=False,
                summary=f"issue {issue.hash} still present after fix",
                issues=(issue.title,),
            )
        return VerifyResult(
            verified=True, summary=f"issue {issue.hash} no longer detected",
            metrics={"remaining_issues": len(after)},
        )

    def verify_feature(
        self, feature: FeatureRequest, ctx: ProbeContext
    ) -> Optional[VerifyResult]:
        # Feature verification is handled by the agent in the feature pipeline;
        # this verifier abstains.
        return None


__all__ = ["BeforeAfterVerifier"]
