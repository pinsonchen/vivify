"""Data-driven verification — quantitative metrics collection and comparison.

Collects project quality metrics (test results, lint warnings, build time,
type errors) before and after a code change, then uses threshold-based rules
to produce an automated verification verdict.  The LLM agent is only invoked
when confidence is below the configured threshold.

The MetricsCollector reuses subprocess patterns from
``vivify.harness.sensors`` but focuses on *numeric extraction* rather than
pass/fail signal.
"""
from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────────
# Data models
# ────────────────────────────────────────────────────────────────────────────────


@dataclass
class MetricSnapshot:
    """A point-in-time snapshot of project quality metrics."""

    timestamp: str = ""
    test_count: Optional[int] = None          # 测试用例数
    test_pass_count: Optional[int] = None     # 通过数
    test_coverage: Optional[float] = None     # 覆盖率 (0.0-100.0)
    lint_warning_count: Optional[int] = None  # lint 警告数
    lint_error_count: Optional[int] = None    # lint 错误数
    build_time_seconds: Optional[float] = None  # 构建耗时
    type_errors: Optional[int] = None         # 类型检查错误数

    @property
    def quality_score(self) -> float:
        """Composite quality score (0.0 - 1.0).

        Weighted combination of available metrics.
        """
        scores: List[float] = []
        weights: List[float] = []

        if self.test_count is not None and self.test_count > 0:
            pass_rate = (self.test_pass_count or 0) / self.test_count
            scores.append(pass_rate)
            weights.append(3.0)  # Tests have highest weight

        if self.lint_error_count is not None:
            lint_score = 1.0 if self.lint_error_count == 0 else max(0.0, 1.0 - self.lint_error_count * 0.1)
            scores.append(lint_score)
            weights.append(2.0)

        if self.type_errors is not None:
            type_score = 1.0 if self.type_errors == 0 else max(0.0, 1.0 - self.type_errors * 0.1)
            scores.append(type_score)
            weights.append(2.0)

        if not scores:
            return 0.5  # No data → neutral

        return sum(s * w for s, w in zip(scores, weights)) / sum(weights)


@dataclass
class MetricsDelta:
    """Comparison between baseline and post-change metrics."""

    baseline: MetricSnapshot
    current: MetricSnapshot

    @property
    def quality_delta(self) -> float:
        """Change in quality score. Positive = improvement."""
        return self.current.quality_score - self.baseline.quality_score

    @property
    def test_regression(self) -> bool:
        """Whether tests regressed."""
        if self.baseline.test_pass_count is None or self.current.test_pass_count is None:
            return False
        return self.current.test_pass_count < self.baseline.test_pass_count

    @property
    def lint_regression(self) -> bool:
        """Whether lint errors increased."""
        if self.baseline.lint_error_count is None or self.current.lint_error_count is None:
            return False
        return self.current.lint_error_count > self.baseline.lint_error_count

    @property
    def summary(self) -> str:
        """Human-readable delta summary."""
        parts: List[str] = []
        parts.append(
            f"Quality: {self.baseline.quality_score:.2f} → "
            f"{self.current.quality_score:.2f} (Δ{self.quality_delta:+.2f})"
        )
        if self.test_regression:
            parts.append(
                f"⚠️ Test regression: {self.baseline.test_pass_count} → "
                f"{self.current.test_pass_count}"
            )
        if self.lint_regression:
            parts.append(
                f"⚠️ Lint regression: {self.baseline.lint_error_count} → "
                f"{self.current.lint_error_count}"
            )
        return "; ".join(parts)


@dataclass
class VerificationVerdict:
    """Automated verification verdict based on metrics."""

    passed: bool
    confidence: float           # 0.0-1.0, how confident the verdict is
    reason: str
    requires_llm_review: bool   # Whether LLM should also review
    metrics_delta: Optional[MetricsDelta] = None


# ────────────────────────────────────────────────────────────────────────────────
# Metrics Collector
# ────────────────────────────────────────────────────────────────────────────────


class MetricsCollector:
    """Collects quantitative metrics from the project workspace.

    Reuses the subprocess execution pattern from HarnessSensorEngine but
    focuses on extracting *numeric values* from command output rather than
    simple pass/fail.
    """

    DEFAULT_TIMEOUT = 120  # seconds

    def __init__(self, workspace: Path, config: dict):
        """
        Args:
            workspace: Project root directory
            config: Dict with test_command, lint_command, etc. from HarnessConfig
        """
        self._workspace = workspace
        self._test_command = config.get("test_command", "")
        self._lint_command = config.get("lint_command", "")
        self._typecheck_command = config.get("typecheck_command", "")
        self._build_command = config.get("build_command", "")
        self._timeout = config.get("feedback_timeout_seconds", self.DEFAULT_TIMEOUT)

    def collect_snapshot(self) -> MetricSnapshot:
        """Collect current metrics snapshot.

        Each sub-command failure is handled gracefully (returns None for that
        metric) so the pipeline is never blocked.
        """
        snapshot = MetricSnapshot()
        snapshot.timestamp = datetime.now(timezone.utc).isoformat()

        # Collect test metrics
        if self._test_command:
            test_result = self._run_tests()
            snapshot.test_count = test_result.get("total")
            snapshot.test_pass_count = test_result.get("passed")
            snapshot.test_coverage = test_result.get("coverage")

        # Collect lint metrics
        if self._lint_command:
            lint_result = self._run_lint()
            snapshot.lint_warning_count = lint_result.get("warnings")
            snapshot.lint_error_count = lint_result.get("errors")

        # Collect typecheck metrics
        if self._typecheck_command:
            type_result = self._run_typecheck()
            snapshot.type_errors = type_result.get("errors")

        # Collect build time
        if self._build_command:
            build_result = self._run_build()
            snapshot.build_time_seconds = build_result.get("duration")

        return snapshot

    def _run_command(self, command: str) -> tuple[int, str]:
        """Run a shell command, return (exit_code, combined_output).

        Returns (-1, "") on timeout or error.
        """
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                cwd=str(self._workspace),
            )
            output = (proc.stdout + "\n" + proc.stderr).strip()
            return proc.returncode, output
        except subprocess.TimeoutExpired:
            logger.warning("MetricsCollector: command timed out: %s", command)
            return -1, ""
        except Exception as exc:
            logger.warning("MetricsCollector: command failed: %s — %s", command, exc)
            return -1, ""

    def _run_tests(self) -> dict:
        """Run test command and parse results.

        Supports pytest-style output parsing:
          - "X passed, Y failed, Z errors"
          - "X passed"
          - coverage percentage from pytest-cov
        """
        exit_code, output = self._run_command(self._test_command)
        result: dict = {}

        if exit_code == -1:
            return result

        # pytest summary line: "5 passed, 2 failed, 1 error in 3.45s"
        passed_match = re.search(r"(\d+)\s+passed", output)
        failed_match = re.search(r"(\d+)\s+failed", output)
        error_match = re.search(r"(\d+)\s+error", output)

        passed = int(passed_match.group(1)) if passed_match else 0
        failed = int(failed_match.group(1)) if failed_match else 0
        errors = int(error_match.group(1)) if error_match else 0

        total = passed + failed + errors
        if total > 0:
            result["total"] = total
            result["passed"] = passed

        # Coverage: "TOTAL ... XX%" or "Coverage: XX%"
        cov_match = re.search(r"(\d+(?:\.\d+)?)\s*%", output)
        if cov_match:
            # Try to find a more specific coverage pattern
            total_cov = re.search(r"TOTAL\s+.*?(\d+(?:\.\d+)?)\s*%", output)
            if total_cov:
                result["coverage"] = float(total_cov.group(1))
            else:
                # Generic pattern — might catch other percentages, but good enough
                generic_cov = re.search(r"(?:coverage|cov)[^\d]*(\d+(?:\.\d+)?)\s*%", output, re.IGNORECASE)
                if generic_cov:
                    result["coverage"] = float(generic_cov.group(1))

        return result

    def _run_lint(self) -> dict:
        """Run lint command and count warnings/errors.

        Supports ruff/flake8/pylint-style output:
          - Count lines with E/W prefixes
          - Count "Found N error(s)" summary lines
        """
        exit_code, output = self._run_command(self._lint_command)
        result: dict = {}

        if exit_code == -1:
            return result

        # ruff/flake8 style: "Found 5 errors" or "X errors"
        errors_summary = re.search(r"[Ff]ound\s+(\d+)\s+error", output)
        warnings_summary = re.search(r"[Ff]ound\s+(\d+)\s+warning", output)

        if errors_summary:
            result["errors"] = int(errors_summary.group(1))
        else:
            # Count lines that look like error reports (file:line:col: EXXX)
            error_lines = re.findall(r"^.+:\d+:\d+:\s*[EF]\d+", output, re.MULTILINE)
            result["errors"] = len(error_lines)

        if warnings_summary:
            result["warnings"] = int(warnings_summary.group(1))
        else:
            warning_lines = re.findall(r"^.+:\d+:\d+:\s*[WC]\d+", output, re.MULTILINE)
            result["warnings"] = len(warning_lines)

        # If exit_code == 0 and we found nothing, explicitly set zeros
        if exit_code == 0 and "errors" not in result:
            result["errors"] = 0
            result["warnings"] = 0

        return result

    def _run_typecheck(self) -> dict:
        """Run typecheck and count errors.

        Supports mypy/pyright style: "Found N error(s)" or count error lines.
        """
        exit_code, output = self._run_command(self._typecheck_command)
        result: dict = {}

        if exit_code == -1:
            return result

        # mypy: "Found 3 errors in 2 files"
        found_match = re.search(r"[Ff]ound\s+(\d+)\s+error", output)
        if found_match:
            result["errors"] = int(found_match.group(1))
        elif exit_code == 0:
            result["errors"] = 0
        else:
            # Count lines with ": error:" pattern
            error_lines = re.findall(r":\s*error:", output)
            result["errors"] = len(error_lines)

        return result

    def _run_build(self) -> dict:
        """Run build command and measure time."""
        start = time.time()
        exit_code, _ = self._run_command(self._build_command)
        duration = time.time() - start

        result: dict = {}
        if exit_code != -1:
            result["duration"] = round(duration, 2)

        return result


# ────────────────────────────────────────────────────────────────────────────────
# Data-Driven Verifier
# ────────────────────────────────────────────────────────────────────────────────


DEFAULT_THRESHOLDS: dict = {
    "min_quality_delta": -0.1,      # 最多允许 quality 下降 0.1
    "allow_test_regression": False,
    "allow_lint_regression": True,   # lint 可以暂时增加
    "confidence_threshold": 0.7,     # 低于此置信度需要 LLM review
}


class DataDrivenVerifier:
    """Verifies feature quality using quantitative metrics comparison.

    Compares a current snapshot against a pre-change baseline and produces
    a :class:`VerificationVerdict` with a pass/fail decision, confidence
    level, and whether further LLM review is needed.
    """

    def __init__(self, collector: MetricsCollector, thresholds: Optional[dict] = None):
        self._collector = collector
        self._thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    @property
    def collector(self) -> MetricsCollector:
        """Expose collector for external baseline capture."""
        return self._collector

    def verify(self, baseline: MetricSnapshot) -> VerificationVerdict:
        """Collect current metrics and compare against baseline.

        Args:
            baseline: Metrics collected before the change

        Returns:
            VerificationVerdict with pass/fail decision
        """
        current = self._collector.collect_snapshot()
        delta = MetricsDelta(baseline=baseline, current=current)

        # Rule-based verdict
        if delta.test_regression and not self._thresholds["allow_test_regression"]:
            return VerificationVerdict(
                passed=False,
                confidence=0.9,
                reason=f"Test regression detected: {delta.summary}",
                requires_llm_review=False,
                metrics_delta=delta,
            )

        if delta.quality_delta < self._thresholds["min_quality_delta"]:
            return VerificationVerdict(
                passed=False,
                confidence=0.8,
                reason=f"Quality score dropped below threshold: {delta.summary}",
                requires_llm_review=True,
                metrics_delta=delta,
            )

        # Positive or neutral change
        confidence = min(1.0, 0.5 + abs(delta.quality_delta) * 2)
        return VerificationVerdict(
            passed=True,
            confidence=confidence,
            reason=f"Metrics OK: {delta.summary}",
            requires_llm_review=confidence < self._thresholds["confidence_threshold"],
            metrics_delta=delta,
        )


__all__ = [
    "DataDrivenVerifier",
    "MetricSnapshot",
    "MetricsCollector",
    "MetricsDelta",
    "VerificationVerdict",
]
