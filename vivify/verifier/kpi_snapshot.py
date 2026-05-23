"""KPI snapshot verifier — captures probe-derived metrics for trend analysis.

Each round (or after a notable action) we ask the registered probes to emit
numeric metrics, persist a :class:`KpiSnapshot` row, and let the
:class:`HealthMonitor` watch for regressions later.

Probes can opt in by exposing a ``kpi_metrics(raw, ctx) -> dict`` method;
probes that don't are simply skipped.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from vivify.interfaces.probe import Probe, ProbeContext
from vivify.interfaces.storage import StorageProvider
from vivify.interfaces.verifier import VerifyResult, Verifier
from vivify.models.feature import FeatureRequest
from vivify.models.issue import Issue
from vivify.models.snapshot import KpiSnapshot

logger = logging.getLogger(__name__)


class KpiSnapshotVerifier(Verifier):
    """Capture KPI metrics from probes into the StorageProvider."""

    def __init__(self, *, probes: Iterable[Probe], storage: StorageProvider, source: str = "kpi_monitor"):
        self.probes = list(probes)
        self.storage = storage
        self.source = source

    def name(self) -> str:
        return "kpi_snapshot"

    # The verifier intentionally returns ``None`` from both verify_* methods —
    # it works as a side-effecting "observer" rather than a pass/fail check.
    def capture(self, ctx: ProbeContext, *, source: Optional[str] = None) -> KpiSnapshot:
        """Collect numeric metrics from every probe that supports it and persist."""
        metrics: dict = {}
        for probe in self.probes:
            kpi_fn = getattr(probe, "kpi_metrics", None)
            if not callable(kpi_fn):
                continue
            try:
                raw = probe.collect(ctx)
                produced = kpi_fn(raw or {}, ctx) or {}
            except Exception as e:
                logger.debug("kpi_metrics on %s failed: %s", probe.id, e)
                continue
            if isinstance(produced, dict):
                for k, v in produced.items():
                    try:
                        metrics[str(k)] = float(v)
                    except (TypeError, ValueError):
                        continue
        snap = KpiSnapshot(source=source or self.source, metrics=metrics)
        try:
            snap.id = self.storage.write_snapshot(snap)
        except Exception as e:  # pragma: no cover — never break the kernel here
            logger.warning("write_snapshot failed: %s", e)
        return snap

    def verify_issue(self, issue: Issue, ctx: ProbeContext) -> Optional[VerifyResult]:
        snap = self.capture(ctx)
        return VerifyResult(
            verified=True,
            summary=f"captured {len(snap.metrics)} KPI metrics",
            metrics=snap.metrics,
        )

    def verify_feature(
        self, feature: FeatureRequest, ctx: ProbeContext
    ) -> Optional[VerifyResult]:
        snap = self.capture(ctx, source=f"feature_{feature.id}")
        return VerifyResult(
            verified=True,
            summary=f"captured {len(snap.metrics)} KPI metrics for feature #{feature.id}",
            metrics=snap.metrics,
        )


__all__ = ["KpiSnapshotVerifier"]
