"""Health monitor — watches KPI snapshots for regressions and emits FeatureRequests.

Replaces ``main.py::_check_system_health_metrics`` from channels-monitor with
a generic implementation that reads recent snapshots from the StorageProvider
and compares each KPI against its 30-day baseline.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from vivify.interfaces.storage import StorageProvider
from vivify.models.feature import FeatureRequest, KPI
from vivify.models.snapshot import KpiSnapshot

logger = logging.getLogger(__name__)


@dataclass
class HealthMonitorConfig:
    enabled: bool = True
    check_interval_hours: int = 24
    degrade_ratio: float = 0.8        # current < baseline * ratio  ⇒ regression
    baseline_window_days: int = 30
    min_samples: int = 5
    metrics: tuple[KPI, ...] = field(default_factory=tuple)


@dataclass
class Regression:
    metric: str
    current: float
    baseline: float
    direction: str
    severity: str  # "warning" | "critical"


def _collect_metric(snapshots: Iterable[KpiSnapshot], metric: str) -> list[float]:
    out: list[float] = []
    for s in snapshots:
        v = s.metrics.get(metric)
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def detect_regressions(
    snapshots: list[KpiSnapshot],
    *,
    config: HealthMonitorConfig,
) -> list[Regression]:
    if not snapshots or not config.metrics:
        return []
    snapshots_sorted = sorted(snapshots, key=lambda s: s.captured_at)
    if len(snapshots_sorted) < config.min_samples:
        return []
    latest = snapshots_sorted[-1]
    historical = snapshots_sorted[:-1]
    out: list[Regression] = []
    for kpi in config.metrics:
        current_vals = _collect_metric([latest], kpi.name)
        baseline_vals = _collect_metric(historical, kpi.name)
        if not current_vals or len(baseline_vals) < config.min_samples - 1:
            continue
        current = current_vals[0]
        baseline = statistics.median(baseline_vals)
        if baseline == 0:
            continue
        ratio = current / baseline if baseline else 1.0
        regressed = (
            (kpi.direction == "up" and ratio < config.degrade_ratio) or
            (kpi.direction == "down" and ratio > (2 - config.degrade_ratio))
        )
        if regressed:
            out.append(
                Regression(
                    metric=kpi.name,
                    current=current,
                    baseline=baseline,
                    direction=kpi.direction,
                    severity="critical" if abs(1 - ratio) > 0.4 else "warning",
                )
            )
    return out


class HealthMonitor:
    """Reads KPI snapshots, detects regressions, files FeatureRequests."""

    def __init__(self, *, storage: StorageProvider, config: HealthMonitorConfig | None = None):
        self.storage = storage
        self.config = config or HealthMonitorConfig()
        self._last_check: Optional[datetime] = None

    def due(self, now: Optional[datetime] = None) -> bool:
        if not self.config.enabled:
            return False
        now = now or datetime.now(timezone.utc)
        if self._last_check is None:
            return True
        return (now - self._last_check) >= timedelta(hours=self.config.check_interval_hours)

    def run(self) -> list[Regression]:
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=self.config.baseline_window_days)
        snapshots = self.storage.read_snapshots(since=since)
        regressions = detect_regressions(snapshots, config=self.config)
        for r in regressions:
            self._file_optimization(r)
        self._last_check = now
        return regressions

    def _file_optimization(self, r: Regression) -> None:
        title = f"[health-monitor] KPI '{r.metric}' regressed"
        description = (
            f"Auto-detected regression on `{r.metric}`.\n\n"
            f"- direction: `{r.direction}`\n"
            f"- current: `{r.current:.4g}`\n"
            f"- baseline (median, last {self.config.baseline_window_days}d): `{r.baseline:.4g}`\n"
            f"- severity: `{r.severity}`\n"
        )
        try:
            self.storage.create_feature(
                FeatureRequest(
                    title=title,
                    description=description,
                    type="optimization",
                    priority="P1" if r.severity == "critical" else "P2",
                )
            )
        except Exception as e:  # pragma: no cover
            logger.warning("create_feature(health regression) failed: %s", e)


__all__ = ["HealthMonitor", "HealthMonitorConfig", "Regression", "detect_regressions"]
