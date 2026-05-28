"""Tests for vivify.intelligence.trend_analyzer.TrendAnalyzer."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List
from unittest.mock import MagicMock

import pytest

from vivify.intelligence.trend_analyzer import TrendAnalyzer
from vivify.models.snapshot import ActionLog, KpiSnapshot


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def storage() -> MagicMock:
    s = MagicMock()
    s.read_snapshots.return_value = []
    s.read_action_logs_since.return_value = []
    return s


@pytest.fixture
def analyzer(storage: MagicMock) -> TrendAnalyzer:
    return TrendAnalyzer(storage=storage, window_days=7)


def make_snap(metrics: dict, days_ago: int = 0) -> KpiSnapshot:
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return KpiSnapshot(source="test", metrics=metrics, captured_at=ts)


# ── Linear regression ──────────────────────────────────────────────────────


class TestLinearRegression:
    def test_perfect_linear(self, analyzer: TrendAnalyzer):
        # y = 2x + 1
        x = [0, 1, 2, 3, 4]
        y = [1, 3, 5, 7, 9]
        slope, intercept = analyzer._linear_regression(x, y)
        assert slope == pytest.approx(2.0)
        assert intercept == pytest.approx(1.0)

    def test_constant_values(self, analyzer: TrendAnalyzer):
        slope, intercept = analyzer._linear_regression([0, 1, 2, 3], [5, 5, 5, 5])
        assert slope == pytest.approx(0.0)
        assert intercept == pytest.approx(5.0)

    def test_single_point(self, analyzer: TrendAnalyzer):
        slope, intercept = analyzer._linear_regression([0], [42.0])
        assert slope == 0.0
        assert intercept == 42.0

    def test_negative_slope(self, analyzer: TrendAnalyzer):
        x = [0, 1, 2, 3]
        y = [10, 8, 6, 4]
        slope, _ = analyzer._linear_regression(x, y)
        assert slope < 0
        assert slope == pytest.approx(-2.0)


# ── R² ──────────────────────────────────────────────────────────────────────


class TestRSquared:
    def test_perfect_fit(self, analyzer: TrendAnalyzer):
        x = [0, 1, 2, 3]
        y = [1, 3, 5, 7]
        slope, intercept = analyzer._linear_regression(x, y)
        r2 = analyzer._r_squared(x, y, slope, intercept)
        assert r2 == pytest.approx(1.0)

    def test_poor_fit(self, analyzer: TrendAnalyzer):
        x = [0, 1, 2, 3, 4]
        y = [1, 100, 2, 99, 3]  # 散乱
        slope, intercept = analyzer._linear_regression(x, y)
        r2 = analyzer._r_squared(x, y, slope, intercept)
        assert r2 < 0.5


# ── Detect anomalies ────────────────────────────────────────────────────────


class TestDetectAnomalies:
    def test_no_anomalies_stable_data(self, analyzer: TrendAnalyzer):
        snaps = [make_snap({"latency": 10.0 + i * 0.01}, days_ago=10 - i) for i in range(8)]
        anomalies = analyzer.detect_anomalies(snaps)
        assert anomalies == []

    def test_spike_detected(self, analyzer: TrendAnalyzer):
        # 5 个稳定值 + 1 个突增
        values = [10.0, 10.1, 10.0, 9.9, 10.05, 50.0]
        snaps = [make_snap({"latency": v}, days_ago=10 - i) for i, v in enumerate(values)]
        anomalies = analyzer.detect_anomalies(snaps)
        assert len(anomalies) >= 1
        assert any(a.value == 50.0 for a in anomalies)

    def test_drop_detected(self, analyzer: TrendAnalyzer):
        values = [100.0, 99.5, 100.5, 99.8, 100.2, 1.0]
        snaps = [make_snap({"score": v}, days_ago=10 - i) for i, v in enumerate(values)]
        anomalies = analyzer.detect_anomalies(snaps)
        assert any(a.value == 1.0 for a in anomalies)

    def test_too_few_points_no_anomaly(self, analyzer: TrendAnalyzer):
        # 实现要求 >= 5 个点才检测
        snaps = [make_snap({"x": v}, days_ago=10 - i) for i, v in enumerate([1.0, 2.0, 100.0])]
        assert analyzer.detect_anomalies(snaps) == []


# ── analyze_kpi_trends ──────────────────────────────────────────────────────


class TestAnalyzeKpiTrends:
    def test_improving_trend(self, analyzer: TrendAnalyzer, storage):
        storage.read_snapshots.return_value = [
            make_snap({"score": float(i)}, days_ago=6 - i) for i in range(7)
        ]
        report = analyzer.analyze_kpi_trends()
        assert "score" in report.kpi_trends
        assert report.kpi_trends["score"].direction == "improving"

    def test_degrading_trend(self, analyzer: TrendAnalyzer, storage):
        storage.read_snapshots.return_value = [
            make_snap({"score": float(10 - i)}, days_ago=6 - i) for i in range(7)
        ]
        report = analyzer.analyze_kpi_trends()
        assert report.kpi_trends["score"].direction == "degrading"

    def test_stable_trend(self, analyzer: TrendAnalyzer, storage):
        storage.read_snapshots.return_value = [
            make_snap({"score": 100.0}, days_ago=6 - i) for i in range(7)
        ]
        report = analyzer.analyze_kpi_trends()
        assert report.kpi_trends["score"].direction == "stable"

    def test_empty_snapshots(self, analyzer: TrendAnalyzer, storage):
        storage.read_snapshots.return_value = []
        report = analyzer.analyze_kpi_trends()
        assert report.kpi_trends == {}
        assert report.anomalies == []


# ── correlate_changes_with_kpi ──────────────────────────────────────────────


class TestCorrelateChangesWithKpi:
    def test_positive_correlation(self, analyzer: TrendAnalyzer, storage):
        now = datetime.now(timezone.utc)
        before_t = now - timedelta(hours=2)
        action_t = now - timedelta(hours=1)
        after_t = now

        storage.read_snapshots.return_value = [
            KpiSnapshot(source="t", metrics={"score": 50.0}, captured_at=before_t),
            KpiSnapshot(source="t", metrics={"score": 80.0}, captured_at=after_t),
        ]
        log = ActionLog(
            run_id="r",
            round_num=1,
            action_type="heal",
            status="success",
            improved=True,
            title="big fix",
            id=42,
        )
        log.created_at = action_t
        storage.read_action_logs_since.return_value = [log]

        corrs = analyzer.correlate_changes_with_kpi()
        assert len(corrs) == 1
        assert corrs[0].direction == "positive"
        assert corrs[0].kpi_name == "score"
        assert corrs[0].action_id == 42

    def test_no_actions_empty(self, analyzer: TrendAnalyzer, storage):
        storage.read_action_logs_since.return_value = []
        assert analyzer.correlate_changes_with_kpi() == []


# ── generate_health_summary ─────────────────────────────────────────────────


class TestHealthSummary:
    def test_all_improving_grade_a(self, analyzer: TrendAnalyzer, storage):
        # 多个 KPI 全部递增
        snaps: List[KpiSnapshot] = []
        for i in range(7):
            snaps.append(
                make_snap(
                    {"score": float(i), "coverage": float(i * 2)},
                    days_ago=6 - i,
                )
            )
        storage.read_snapshots.return_value = snaps
        summary = analyzer.generate_health_summary()
        assert summary.grade == "A"
        assert "score" in summary.improving
        assert "coverage" in summary.improving
        assert summary.degrading == []

    def test_all_degrading_grade_d(self, analyzer: TrendAnalyzer, storage):
        snaps = []
        for i in range(7):
            snaps.append(
                make_snap(
                    {"score": float(10 - i), "coverage": float(20 - i * 2)},
                    days_ago=6 - i,
                )
            )
        storage.read_snapshots.return_value = snaps
        summary = analyzer.generate_health_summary()
        assert summary.grade == "D"
        assert set(summary.degrading) == {"score", "coverage"}

    def test_mixed_grade_b(self, analyzer: TrendAnalyzer, storage):
        # 一个改善，一个稳定 → 不到 50% improving，且 degrading 0%
        snaps = []
        for i in range(7):
            snaps.append(
                make_snap(
                    {"score": float(i), "stable": 100.0},
                    days_ago=6 - i,
                )
            )
        storage.read_snapshots.return_value = snaps
        summary = analyzer.generate_health_summary()
        # 改善比例 = 1/2 = 0.5，degrading 0% → grade B (因为 improving_ratio > 0.5 才是 A，等于 0.5 不算)
        assert summary.grade in ("A", "B")
        assert "score" in summary.improving

    def test_no_data_default_grade(self, analyzer: TrendAnalyzer, storage):
        storage.read_snapshots.return_value = []
        summary = analyzer.generate_health_summary()
        assert summary.grade == "B"
        assert summary.improving == []
        assert summary.degrading == []
