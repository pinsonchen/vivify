"""Runs a set of probes and aggregates their Issues.

Each probe runs in the calling thread under an overall budget — failure of one
probe never affects another. Errors are surfaced as info-level logs (probe
authors can opt into raising by overriding ``analyze`` to throw).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from auto_heal.interfaces.probe import Probe, ProbeContext
from auto_heal.models import Issue

logger = logging.getLogger(__name__)


@dataclass
class ProbeRunReport:
    """Per-probe outcome the kernel uses for action logging and metrics."""

    probe_id: str
    issues: list[Issue] = field(default_factory=list)
    duration_seconds: float = 0.0
    error: str | None = None  # filled when the probe raised
    skipped_reason: str | None = None


def run_probes(
    probes: Sequence[Probe],
    ctx: ProbeContext,
    *,
    per_probe_timeout_seconds: int | None = None,
    enabled_ids: Iterable[str] | None = None,
) -> list[ProbeRunReport]:
    """Sequentially run probes; return one :class:`ProbeRunReport` per probe.

    ``enabled_ids`` restricts execution to a subset (the kernel uses this to
    honour ``probes.enabled`` in ``.auto-heal.yml``). The ``per_probe_timeout``
    is a soft hint — built-in YAML probes pass it down to subprocess steps via
    their own ``timeout_seconds``.
    """
    enabled = set(enabled_ids) if enabled_ids is not None else None
    reports: list[ProbeRunReport] = []
    for probe in probes:
        if enabled is not None and probe.id not in enabled:
            reports.append(ProbeRunReport(probe_id=probe.id, skipped_reason="disabled"))
            continue
        report = ProbeRunReport(probe_id=probe.id)
        t0 = time.time()
        try:
            ok, hint = probe.healthcheck(ctx)
            if not ok:
                report.skipped_reason = f"healthcheck failed: {hint}"
                logger.info("[probe %s] skipped — %s", probe.id, report.skipped_reason)
                report.duration_seconds = time.time() - t0
                reports.append(report)
                continue
            raw = probe.collect(ctx)
            issues = probe.analyze(raw or {}, ctx) or []
            report.issues.extend(issues)
        except Exception as e:
            logger.exception("[probe %s] crashed: %s", probe.id, e)
            report.error = str(e)
        finally:
            report.duration_seconds = time.time() - t0
        reports.append(report)
    return reports


def aggregate_issues(reports: Sequence[ProbeRunReport]) -> list[Issue]:
    """Flatten reports into a single Issue list sorted by severity then category."""
    issues: list[Issue] = []
    for r in reports:
        issues.extend(r.issues)
    issues.sort(key=lambda i: (i.level.priority(), i.category, i.title))
    return issues


__all__ = ["ProbeRunReport", "run_probes", "aggregate_issues"]
