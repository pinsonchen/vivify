"""KPI snapshot verifier — captures probe-derived metrics for trend analysis.

Each round (or after a notable action) we ask the registered probes to emit
numeric metrics, persist a :class:`KpiSnapshot` row, and let the
:class:`HealthMonitor` watch for regressions later.

Probes can opt in by exposing a ``kpi_metrics(raw, ctx) -> dict`` method;
probes that don't are simply skipped. As a fallback, the verifier also
automatically derives a baseline set of feature-lifecycle KPIs (completion
rate, success rate, cycle time, …) from the feature_requests table so the
trends Tab always has at least *some* data to show.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

from vivify.interfaces.probe import Probe, ProbeContext
from vivify.interfaces.storage import StorageProvider
from vivify.interfaces.verifier import VerifyResult, Verifier
from vivify.models.feature import FeatureRequest
from vivify.models.issue import Issue
from vivify.models.snapshot import KpiSnapshot

logger = logging.getLogger(__name__)


_DONE_STATUSES = {"verified", "deployed"}
_BAD_STATUSES = {"rejected", "deployed_with_issues"}
_ACTIVE_STATUSES = {"developing", "evaluating", "verifying"}


def _parse_dt(value) -> Optional[datetime]:
    """Best-effort ISO datetime parser; returns None on failure."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        s = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def compute_feature_kpis(storage: StorageProvider) -> dict:
    """Derive lifecycle KPIs from feature_requests as a probe-independent fallback.

    All access is wrapped in try/except so an empty / corrupt table never breaks
    the kernel.
    """
    metrics: dict = {}
    try:
        # list_features() with no status returns all features (limit 200 by default;
        # bump to a larger ceiling so stats stay representative on busy projects).
        features = list(storage.list_features(limit=2000))
    except Exception as e:  # pragma: no cover
        logger.debug("compute_feature_kpis: list_features failed: %s", e)
        return metrics
    if not features:
        return metrics

    total = len(features)
    done = sum(1 for f in features if (getattr(f, "status", "") or "") in _DONE_STATUSES)
    bad = sum(1 for f in features if (getattr(f, "status", "") or "") in _BAD_STATUSES)
    verified = sum(1 for f in features if (getattr(f, "status", "") or "") == "verified")
    active = sum(1 for f in features if (getattr(f, "status", "") or "") in _ACTIVE_STATUSES)
    pr_count = sum(1 for f in features if getattr(f, "pr_url", None))

    metrics["feature_total"] = float(total)
    metrics["feature_completion_rate"] = round(done / total * 100, 2) if total else 0.0
    denom = verified + bad
    metrics["feature_success_rate"] = round(verified / denom * 100, 2) if denom else 0.0
    metrics["active_features"] = float(active)
    metrics["pr_merge_rate"] = round(pr_count / total * 100, 2) if total else 0.0

    # Average cycle time (created_at → verified_at), only for verified features.
    durations: list[float] = []
    for f in features:
        if (getattr(f, "status", "") or "") != "verified":
            continue
        created = _parse_dt(getattr(f, "created_at", None))
        verified_at = _parse_dt(getattr(f, "verified_at", None))
        if not created or not verified_at:
            continue
        delta = (verified_at - created).total_seconds() / 3600.0
        if delta >= 0:
            durations.append(delta)
    if durations:
        metrics["avg_cycle_time_hours"] = round(sum(durations) / len(durations), 2)
    else:
        metrics["avg_cycle_time_hours"] = 0.0

    # Overall composite score: completion 40% + success 40% + (100 - normalized cycle) 20%
    cycle = metrics["avg_cycle_time_hours"]
    cycle_score = 100.0 if cycle <= 0 else max(0.0, 100.0 - min(cycle, 168.0) / 1.68)
    metrics["overall_score"] = round(
        metrics["feature_completion_rate"] * 0.4
        + metrics["feature_success_rate"] * 0.4
        + cycle_score * 0.2,
        2,
    )
    return metrics


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
        """Collect numeric metrics from every probe that supports it and persist.

        If no probe produces metrics, fall back to feature-lifecycle KPIs derived
        directly from the database so the trends view stays informative.
        """
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

        # Always overlay the feature-derived KPIs (probe metrics win on collisions).
        try:
            derived = compute_feature_kpis(self.storage)
        except Exception as e:  # pragma: no cover
            logger.debug("compute_feature_kpis failed: %s", e)
            derived = {}
        for k, v in derived.items():
            metrics.setdefault(k, v)

        overall = metrics.get("overall_score")
        snap = KpiSnapshot(
            source=source or self.source,
            metrics=metrics,
            overall_score=float(overall) if isinstance(overall, (int, float)) else None,
        )
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


__all__ = ["KpiSnapshotVerifier", "compute_feature_kpis"]
