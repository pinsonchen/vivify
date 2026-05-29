"""Unit tests for data-driven verification infrastructure (Task #119)."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from vivify.verifier.metrics_collector import (
    DataDrivenVerifier,
    MetricSnapshot,
    MetricsCollector,
    MetricsDelta,
    VerificationVerdict,
)


# ────────────────────────────────────────────────────────────────────────────────
# MetricSnapshot tests
# ────────────────────────────────────────────────────────────────────────────────


class TestMetricSnapshot:
    """Tests for MetricSnapshot.quality_score computation."""

    def test_quality_score_no_data_returns_neutral(self):
        """With no metrics at all, quality_score should be 0.5."""
        snap = MetricSnapshot()
        assert snap.quality_score == 0.5

    def test_quality_score_perfect_tests(self):
        """100% test pass rate → quality near 1.0."""
        snap = MetricSnapshot(test_count=10, test_pass_count=10)
        # Only test weight (3.0) contributes: 1.0 * 3.0 / 3.0 = 1.0
        assert snap.quality_score == 1.0

    def test_quality_score_all_tests_failed(self):
        """0% pass rate → quality 0.0."""
        snap = MetricSnapshot(test_count=10, test_pass_count=0)
        assert snap.quality_score == 0.0

    def test_quality_score_partial_tests(self):
        """50% pass rate → score reflects weighted average."""
        snap = MetricSnapshot(test_count=10, test_pass_count=5)
        # pass_rate = 0.5; score = 0.5 * 3.0 / 3.0 = 0.5
        assert snap.quality_score == pytest.approx(0.5)

    def test_quality_score_with_lint_errors(self):
        """Lint errors reduce quality score."""
        snap = MetricSnapshot(lint_error_count=5)
        # lint_score = max(0.0, 1.0 - 5*0.1) = 0.5
        # total = 0.5 * 2.0 / 2.0 = 0.5
        assert snap.quality_score == pytest.approx(0.5)

    def test_quality_score_zero_lint_errors(self):
        """Zero lint errors → lint score 1.0."""
        snap = MetricSnapshot(lint_error_count=0)
        assert snap.quality_score == 1.0

    def test_quality_score_many_lint_errors_floors_at_zero(self):
        """More than 10 lint errors → lint_score floors at 0.0."""
        snap = MetricSnapshot(lint_error_count=15)
        assert snap.quality_score == 0.0

    def test_quality_score_with_type_errors(self):
        """Type errors reduce quality score similarly to lint."""
        snap = MetricSnapshot(type_errors=3)
        # type_score = max(0.0, 1.0 - 3*0.1) = 0.7
        # total = 0.7 * 2.0 / 2.0 = 0.7
        assert snap.quality_score == pytest.approx(0.7)

    def test_quality_score_combined_metrics(self):
        """Multiple metrics combine with weights."""
        snap = MetricSnapshot(
            test_count=10, test_pass_count=8,
            lint_error_count=2,
            type_errors=1,
        )
        # pass_rate = 0.8, weight 3.0
        # lint_score = 1.0 - 0.2 = 0.8, weight 2.0
        # type_score = 1.0 - 0.1 = 0.9, weight 2.0
        expected = (0.8 * 3.0 + 0.8 * 2.0 + 0.9 * 2.0) / (3.0 + 2.0 + 2.0)
        assert snap.quality_score == pytest.approx(expected)

    def test_quality_score_zero_test_count_ignored(self):
        """test_count=0 means tests are not a factor."""
        snap = MetricSnapshot(test_count=0, test_pass_count=0, lint_error_count=0)
        # Only lint contributes: 1.0 * 2.0 / 2.0 = 1.0
        assert snap.quality_score == 1.0


# ────────────────────────────────────────────────────────────────────────────────
# MetricsDelta tests
# ────────────────────────────────────────────────────────────────────────────────


class TestMetricsDelta:
    """Tests for MetricsDelta comparison logic."""

    def test_quality_delta_improvement(self):
        baseline = MetricSnapshot(test_count=10, test_pass_count=7)
        current = MetricSnapshot(test_count=10, test_pass_count=9)
        delta = MetricsDelta(baseline=baseline, current=current)
        assert delta.quality_delta > 0

    def test_quality_delta_regression(self):
        baseline = MetricSnapshot(test_count=10, test_pass_count=9)
        current = MetricSnapshot(test_count=10, test_pass_count=5)
        delta = MetricsDelta(baseline=baseline, current=current)
        assert delta.quality_delta < 0

    def test_quality_delta_no_change(self):
        snap = MetricSnapshot(test_count=10, test_pass_count=8)
        delta = MetricsDelta(baseline=snap, current=snap)
        assert delta.quality_delta == 0.0

    def test_test_regression_detected(self):
        baseline = MetricSnapshot(test_count=10, test_pass_count=10)
        current = MetricSnapshot(test_count=10, test_pass_count=8)
        delta = MetricsDelta(baseline=baseline, current=current)
        assert delta.test_regression is True

    def test_test_regression_not_detected_when_improved(self):
        baseline = MetricSnapshot(test_count=10, test_pass_count=7)
        current = MetricSnapshot(test_count=10, test_pass_count=9)
        delta = MetricsDelta(baseline=baseline, current=current)
        assert delta.test_regression is False

    def test_test_regression_none_values(self):
        """When pass counts are None, no regression is reported."""
        baseline = MetricSnapshot()
        current = MetricSnapshot()
        delta = MetricsDelta(baseline=baseline, current=current)
        assert delta.test_regression is False

    def test_lint_regression_detected(self):
        baseline = MetricSnapshot(lint_error_count=2)
        current = MetricSnapshot(lint_error_count=5)
        delta = MetricsDelta(baseline=baseline, current=current)
        assert delta.lint_regression is True

    def test_lint_regression_not_detected_when_improved(self):
        baseline = MetricSnapshot(lint_error_count=5)
        current = MetricSnapshot(lint_error_count=2)
        delta = MetricsDelta(baseline=baseline, current=current)
        assert delta.lint_regression is False

    def test_lint_regression_none_values(self):
        baseline = MetricSnapshot()
        current = MetricSnapshot(lint_error_count=3)
        delta = MetricsDelta(baseline=baseline, current=current)
        assert delta.lint_regression is False

    def test_summary_contains_quality_info(self):
        baseline = MetricSnapshot(test_count=10, test_pass_count=10)
        current = MetricSnapshot(test_count=10, test_pass_count=8)
        delta = MetricsDelta(baseline=baseline, current=current)
        assert "Quality:" in delta.summary
        assert "Δ" in delta.summary

    def test_summary_includes_test_regression_warning(self):
        baseline = MetricSnapshot(test_count=10, test_pass_count=10)
        current = MetricSnapshot(test_count=10, test_pass_count=7)
        delta = MetricsDelta(baseline=baseline, current=current)
        assert "Test regression" in delta.summary

    def test_summary_includes_lint_regression_warning(self):
        baseline = MetricSnapshot(lint_error_count=0)
        current = MetricSnapshot(lint_error_count=5)
        delta = MetricsDelta(baseline=baseline, current=current)
        assert "Lint regression" in delta.summary


# ────────────────────────────────────────────────────────────────────────────────
# MetricsCollector tests
# ────────────────────────────────────────────────────────────────────────────────


class TestMetricsCollector:
    """Tests for MetricsCollector subprocess execution and parsing."""

    def _make_collector(self, **overrides):
        config = {
            "test_command": "pytest",
            "lint_command": "ruff check .",
            "typecheck_command": "mypy .",
            "build_command": "make build",
            "feedback_timeout_seconds": 60,
        }
        config.update(overrides)
        return MetricsCollector(workspace=Path("/tmp/test"), config=config)

    @patch("vivify.verifier.metrics_collector.subprocess.run")
    def test_collect_snapshot_with_pytest_output(self, mock_run):
        """Parse pytest-style output correctly."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="===== 8 passed, 2 failed in 3.45s =====",
            stderr="",
        )
        collector = self._make_collector(
            lint_command="", typecheck_command="", build_command=""
        )
        snap = collector.collect_snapshot()
        assert snap.test_count == 10
        assert snap.test_pass_count == 8
        assert snap.timestamp != ""

    @patch("vivify.verifier.metrics_collector.subprocess.run")
    def test_collect_snapshot_lint_errors(self, mock_run):
        """Parse ruff/flake8 error summary."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="Found 3 errors",
            stderr="",
        )
        collector = self._make_collector(
            test_command="", typecheck_command="", build_command=""
        )
        snap = collector.collect_snapshot()
        assert snap.lint_error_count == 3

    @patch("vivify.verifier.metrics_collector.subprocess.run")
    def test_collect_snapshot_lint_clean(self, mock_run):
        """Clean lint run → 0 errors."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="All checks passed!",
            stderr="",
        )
        collector = self._make_collector(
            test_command="", typecheck_command="", build_command=""
        )
        snap = collector.collect_snapshot()
        assert snap.lint_error_count == 0

    @patch("vivify.verifier.metrics_collector.subprocess.run")
    def test_collect_snapshot_typecheck(self, mock_run):
        """Parse mypy error count."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="Found 5 errors in 3 files (checked 20 source files)",
            stderr="",
        )
        collector = self._make_collector(
            test_command="", lint_command="", build_command=""
        )
        snap = collector.collect_snapshot()
        assert snap.type_errors == 5

    @patch("vivify.verifier.metrics_collector.subprocess.run")
    def test_collect_snapshot_build_time(self, mock_run):
        """Measure build duration."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Build successful",
            stderr="",
        )
        collector = self._make_collector(
            test_command="", lint_command="", typecheck_command=""
        )
        snap = collector.collect_snapshot()
        assert snap.build_time_seconds is not None
        assert snap.build_time_seconds >= 0

    @patch("vivify.verifier.metrics_collector.subprocess.run")
    def test_timeout_graceful(self, mock_run):
        """Timeout should be handled gracefully."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=60)
        collector = self._make_collector(
            lint_command="", typecheck_command="", build_command=""
        )
        snap = collector.collect_snapshot()
        # Should not crash, test metrics should be None
        assert snap.test_count is None
        assert snap.test_pass_count is None

    @patch("vivify.verifier.metrics_collector.subprocess.run")
    def test_exception_graceful(self, mock_run):
        """General exception should be handled gracefully."""
        mock_run.side_effect = OSError("No such file")
        collector = self._make_collector(
            lint_command="", typecheck_command="", build_command=""
        )
        snap = collector.collect_snapshot()
        assert snap.test_count is None

    def test_no_commands_returns_empty_snapshot(self):
        """If no commands configured, snapshot has all None values."""
        collector = self._make_collector(
            test_command="", lint_command="", typecheck_command="", build_command=""
        )
        snap = collector.collect_snapshot()
        assert snap.test_count is None
        assert snap.lint_error_count is None
        assert snap.type_errors is None
        assert snap.build_time_seconds is None

    @patch("vivify.verifier.metrics_collector.subprocess.run")
    def test_parse_line_based_lint_errors(self, mock_run):
        """Parse file:line:col: EXXX style lint output."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout=(
                "src/main.py:10:5: E101 indentation error\n"
                "src/main.py:15:1: E302 expected 2 blank lines\n"
                "src/utils.py:3:1: W291 trailing whitespace\n"
            ),
            stderr="",
        )
        collector = self._make_collector(
            test_command="", typecheck_command="", build_command=""
        )
        snap = collector.collect_snapshot()
        assert snap.lint_error_count == 2  # E101, E302
        assert snap.lint_warning_count == 1  # W291


# ────────────────────────────────────────────────────────────────────────────────
# DataDrivenVerifier tests
# ────────────────────────────────────────────────────────────────────────────────


class TestDataDrivenVerifier:
    """Tests for DataDrivenVerifier threshold-based verdict logic."""

    def _make_verifier(self, current_snapshot: MetricSnapshot, **thresholds):
        collector = MagicMock()
        collector.collect_snapshot.return_value = current_snapshot
        return DataDrivenVerifier(collector=collector, thresholds=thresholds or None)

    def test_passes_when_quality_improves(self):
        baseline = MetricSnapshot(test_count=10, test_pass_count=7)
        current = MetricSnapshot(test_count=10, test_pass_count=9)
        verifier = self._make_verifier(current)
        verdict = verifier.verify(baseline)
        assert verdict.passed is True
        assert verdict.confidence > 0.5

    def test_fails_on_test_regression(self):
        baseline = MetricSnapshot(test_count=10, test_pass_count=10)
        current = MetricSnapshot(test_count=10, test_pass_count=7)
        verifier = self._make_verifier(current)
        verdict = verifier.verify(baseline)
        assert verdict.passed is False
        assert "Test regression" in verdict.reason
        assert verdict.confidence == 0.9
        assert verdict.requires_llm_review is False

    def test_test_regression_allowed_when_configured(self):
        """When allow_test_regression=True, test regression doesn't auto-fail."""
        baseline = MetricSnapshot(test_count=10, test_pass_count=10)
        current = MetricSnapshot(test_count=10, test_pass_count=9)
        verifier = self._make_verifier(
            current, allow_test_regression=True,
            min_quality_delta=-0.5, confidence_threshold=0.7,
            allow_lint_regression=True,
        )
        verdict = verifier.verify(baseline)
        # Should pass because regression is allowed and delta isn't too bad
        assert verdict.passed is True

    def test_fails_on_quality_drop_below_threshold(self):
        # No test regression (same pass count), but quality drops via lint
        baseline = MetricSnapshot(test_count=10, test_pass_count=10, lint_error_count=0)
        current = MetricSnapshot(test_count=10, test_pass_count=10, lint_error_count=10)
        verifier = self._make_verifier(current)
        verdict = verifier.verify(baseline)
        assert verdict.passed is False
        assert verdict.requires_llm_review is True

    def test_passes_with_neutral_change(self):
        """Same quality → passes."""
        snap = MetricSnapshot(test_count=10, test_pass_count=8)
        verifier = self._make_verifier(snap)
        verdict = verifier.verify(snap)
        assert verdict.passed is True

    def test_low_confidence_requests_llm_review(self):
        """When quality delta is small, confidence is low → LLM review needed."""
        baseline = MetricSnapshot(test_count=10, test_pass_count=8)
        current = MetricSnapshot(test_count=10, test_pass_count=8)
        verifier = self._make_verifier(current)
        verdict = verifier.verify(baseline)
        # delta=0, confidence = 0.5 + 0*2 = 0.5 < 0.7 threshold
        assert verdict.requires_llm_review is True

    def test_high_confidence_skips_llm_review(self):
        """Large positive delta → high confidence → no LLM needed."""
        baseline = MetricSnapshot(test_count=10, test_pass_count=5)
        current = MetricSnapshot(test_count=10, test_pass_count=10)
        verifier = self._make_verifier(current)
        verdict = verifier.verify(baseline)
        # delta = 0.5, confidence = 0.5 + 0.5*2 = 1.5 → capped at 1.0
        assert verdict.requires_llm_review is False
        assert verdict.confidence == 1.0

    def test_metrics_delta_attached_to_verdict(self):
        baseline = MetricSnapshot(test_count=10, test_pass_count=8)
        current = MetricSnapshot(test_count=10, test_pass_count=9)
        verifier = self._make_verifier(current)
        verdict = verifier.verify(baseline)
        assert verdict.metrics_delta is not None
        assert verdict.metrics_delta.baseline is baseline
        assert verdict.metrics_delta.current is current

    def test_no_data_graceful(self):
        """With empty snapshots, verifier should still produce a verdict."""
        baseline = MetricSnapshot()
        current = MetricSnapshot()
        verifier = self._make_verifier(current)
        verdict = verifier.verify(baseline)
        # Both have quality 0.5 → delta=0 → passes
        assert verdict.passed is True
        assert verdict.confidence == 0.5

    def test_custom_thresholds(self):
        """Custom thresholds override defaults."""
        baseline = MetricSnapshot(test_count=10, test_pass_count=10)
        current = MetricSnapshot(test_count=10, test_pass_count=9)
        # Strict: even -0.01 delta fails
        verifier = self._make_verifier(
            current,
            min_quality_delta=0.0,
            allow_test_regression=False,
            allow_lint_regression=False,
            confidence_threshold=0.9,
        )
        verdict = verifier.verify(baseline)
        assert verdict.passed is False


# ────────────────────────────────────────────────────────────────────────────────
# VerificationVerdict tests
# ────────────────────────────────────────────────────────────────────────────────


class TestVerificationVerdict:
    """Tests for the VerificationVerdict dataclass."""

    def test_basic_construction(self):
        v = VerificationVerdict(
            passed=True,
            confidence=0.85,
            reason="All good",
            requires_llm_review=False,
        )
        assert v.passed is True
        assert v.confidence == 0.85
        assert v.reason == "All good"
        assert v.metrics_delta is None

    def test_with_metrics_delta(self):
        baseline = MetricSnapshot(test_count=5, test_pass_count=5)
        current = MetricSnapshot(test_count=5, test_pass_count=4)
        delta = MetricsDelta(baseline=baseline, current=current)
        v = VerificationVerdict(
            passed=False,
            confidence=0.9,
            reason="regression",
            requires_llm_review=False,
            metrics_delta=delta,
        )
        assert v.metrics_delta.test_regression is True
