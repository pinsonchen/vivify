"""Multi-dimensional trend analysis based on historical KPI data."""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone
from typing import List, Optional, TYPE_CHECKING

from vivify.intelligence.models import (
    Anomaly,
    Correlation,
    HealthSummary,
    KpiTrend,
    TrendReport,
)
from vivify.models.snapshot import ActionLog, KpiSnapshot

if TYPE_CHECKING:
    from vivify.interfaces.storage import StorageProvider

logger = logging.getLogger(__name__)


class TrendAnalyzer:
    """Multi-dimensional trend analysis for project health monitoring."""

    def __init__(self, storage: "StorageProvider", window_days: int = 7):
        self.storage = storage
        self.window_days = window_days

    def analyze_kpi_trends(self, window_days: int | None = None) -> TrendReport:
        """分析最近 N 天 KPI 趋势.

        输出：
        - 每个 KPI 的趋势方向（improving/stable/degrading）
        - 异常点检测（Z-score > 2）
        - 预测未来 3 天趋势（线性外推）
        """
        days = window_days or self.window_days
        since = datetime.now(timezone.utc) - timedelta(days=days)

        # 获取所有 KPI snapshots
        snapshots = self.storage.read_snapshots(since)
        if not snapshots:
            return TrendReport(period_start=since, period_end=datetime.now(timezone.utc))

        # 提取所有 KPI 名称
        kpi_names: set[str] = set()
        for snap in snapshots:
            kpi_names.update(snap.metrics.keys())

        # 对每个 KPI 分析趋势
        kpi_trends: dict[str, KpiTrend] = {}
        all_anomalies: List[Anomaly] = []
        predictions: dict[str, float] = {}

        for kpi_name in kpi_names:
            # 构建时间序列
            series: list[tuple[datetime, float]] = []
            for snap in snapshots:
                if kpi_name in snap.metrics:
                    try:
                        val = float(snap.metrics[kpi_name])
                        series.append((snap.captured_at, val))
                    except (ValueError, TypeError):
                        continue

            if len(series) < 2:
                continue

            # 线性回归
            trend = self._compute_trend(series)
            kpi_trends[kpi_name] = trend
            predictions[kpi_name] = trend.predicted_value

            # 异常检测
            anomalies = self._detect_anomalies_for_series(kpi_name, series)
            all_anomalies.extend(anomalies)

        return TrendReport(
            kpi_trends=kpi_trends,
            anomalies=all_anomalies,
            predictions=predictions,
            period_start=since,
            period_end=datetime.now(timezone.utc),
        )

    def detect_anomalies(self, snapshots: List[KpiSnapshot]) -> List[Anomaly]:
        """检测所有 KPI 的异常点."""
        all_anomalies: List[Anomaly] = []

        kpi_names: set[str] = set()
        for snap in snapshots:
            kpi_names.update(snap.metrics.keys())

        for kpi_name in kpi_names:
            series: list[tuple[datetime, float]] = []
            for snap in snapshots:
                if kpi_name in snap.metrics:
                    try:
                        val = float(snap.metrics[kpi_name])
                        series.append((snap.captured_at, val))
                    except (ValueError, TypeError):
                        continue

            anomalies = self._detect_anomalies_for_series(kpi_name, series)
            all_anomalies.extend(anomalies)

        return all_anomalies

    def correlate_changes_with_kpi(self, window_days: int | None = None) -> List[Correlation]:
        """关联代码变更与 KPI 变动.

        逻辑：
        1. 获取 window 内成功的 fix/develop action_logs
        2. 获取同期 KPI snapshots
        3. 对每个 action，检测其时间点前后 KPI 变化
        4. 显著变化（> 1 std）输出为 Correlation
        """
        days = window_days or self.window_days
        since = datetime.now(timezone.utc) - timedelta(days=days)

        # 获取成功的 actions
        actions = self.storage.read_action_logs_since(
            since, action_types=["heal", "direct_fix", "feature_dev"]
        )
        successful_actions = [a for a in actions if a.status == "success" and a.improved]

        if not successful_actions:
            return []

        # 获取 KPI snapshots
        snapshots = self.storage.read_snapshots(since)
        if len(snapshots) < 2:
            return []

        correlations: List[Correlation] = []

        for action in successful_actions:
            action_time = action.created_at

            # 找 action 前后的 snapshot
            before_snaps = [s for s in snapshots if s.captured_at < action_time]
            after_snaps = [s for s in snapshots if s.captured_at >= action_time]

            if not before_snaps or not after_snaps:
                continue

            before_snap = before_snaps[-1]  # 最近的 before
            after_snap = after_snaps[0]     # 最近的 after

            # 比较每个 KPI
            for kpi_name in before_snap.metrics:
                if kpi_name not in after_snap.metrics:
                    continue
                try:
                    before_val = float(before_snap.metrics[kpi_name])
                    after_val = float(after_snap.metrics[kpi_name])
                except (ValueError, TypeError):
                    continue

                delta = after_val - before_val
                if abs(delta) < 0.01:  # 忽略微小变化
                    continue

                # 计算是否显著（相对变化 > 5%）
                if before_val != 0 and abs(delta / before_val) > 0.05:
                    direction = "positive" if delta > 0 else "negative"
                    correlations.append(Correlation(
                        action_id=action.id,
                        action_title=action.title or "",
                        kpi_name=kpi_name,
                        delta=delta,
                        direction=direction,
                        timestamp=action_time,
                    ))

        return correlations

    def generate_health_summary(self) -> HealthSummary:
        """生成项目健康度总结."""
        report = self.analyze_kpi_trends()

        improving: List[str] = []
        degrading: List[str] = []
        risks: List[str] = []

        for kpi_name, trend in report.kpi_trends.items():
            if trend.direction == "improving":
                improving.append(kpi_name)
            elif trend.direction == "degrading":
                degrading.append(kpi_name)

        # 风险：预测值恶化的 KPI
        for kpi_name, trend in report.kpi_trends.items():
            if trend.direction == "degrading" and trend.confidence > 0.6:
                risks.append(f"{kpi_name} predicted to degrade further (slope={trend.slope:.4f})")

        # 风险：异常点
        for anomaly in report.anomalies[-3:]:
            risks.append(f"Anomaly in {anomaly.kpi_name}: z-score={anomaly.z_score:.2f}")

        # 评级
        total = len(report.kpi_trends)
        if total == 0:
            grade = "B"  # 无数据默认
        else:
            improving_ratio = len(improving) / total
            degrading_ratio = len(degrading) / total

            if degrading_ratio > 0.5:
                grade = "D"
            elif degrading_ratio > 0.3:
                grade = "C"
            elif improving_ratio > 0.5:
                grade = "A"
            else:
                grade = "B"

        return HealthSummary(
            grade=grade,
            improving=improving,
            degrading=degrading,
            risks=risks,
        )

    # ── internal helpers ──

    def _compute_trend(self, series: list[tuple[datetime, float]]) -> KpiTrend:
        """对单个 KPI 时间序列计算趋势."""
        if len(series) < 2:
            return KpiTrend()

        values = [float(v) for _, v in series]
        current_value = values[-1]

        # 线性回归：y = slope * x + intercept
        n = len(values)
        x = list(range(n))
        slope, intercept = self._linear_regression(x, values)

        # 预测未来 3 个 "steps"（若每天一个 snapshot，则 3 天后）
        predicted = slope * (n + 3) + intercept

        # 判断方向
        if abs(slope) < 0.001 * (max(abs(v) for v in values) or 1):
            direction = "stable"
        elif slope > 0:
            direction = "improving"
        else:
            direction = "degrading"

        # 置信度基于 R²
        r_squared = self._r_squared(x, values, slope, intercept)

        return KpiTrend(
            direction=direction,
            slope=slope,
            current_value=current_value,
            predicted_value=predicted,
            confidence=max(0.0, min(1.0, r_squared)),
        )

    def _detect_anomalies_for_series(
        self, kpi_name: str, series: list[tuple[datetime, float]]
    ) -> List[Anomaly]:
        """对单个 KPI 检测异常点（滑动窗口 Z-score）."""
        if len(series) < 5:
            return []

        values = [float(v) for _, v in series]
        timestamps = [t for t, _ in series]

        anomalies: List[Anomaly] = []
        window_size = min(5, len(values) - 1)

        for i in range(window_size, len(values)):
            window = values[i - window_size:i]
            try:
                mean = statistics.mean(window)
                stdev = statistics.stdev(window)
            except statistics.StatisticsError:
                continue

            if stdev == 0:
                continue

            z_score = abs(values[i] - mean) / stdev
            if z_score > 2.0:
                anomalies.append(Anomaly(
                    kpi_name=kpi_name,
                    value=values[i],
                    z_score=z_score,
                    detected_at=timestamps[i],
                    context=f"Value {values[i]:.2f} deviates {z_score:.1f} std from window mean {mean:.2f}",
                ))

        return anomalies

    @staticmethod
    def _linear_regression(x: list, y: list) -> tuple[float, float]:
        """简单线性回归，返回 (slope, intercept)."""
        n = len(x)
        if n < 2:
            return 0.0, (y[0] if y else 0.0)

        x_mean = sum(x) / n
        y_mean = sum(y) / n

        numerator = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
        denominator = sum((xi - x_mean) ** 2 for xi in x)

        if denominator == 0:
            return 0.0, y_mean

        slope = numerator / denominator
        intercept = y_mean - slope * x_mean
        return slope, intercept

    @staticmethod
    def _r_squared(x: list, y: list, slope: float, intercept: float) -> float:
        """计算 R² 决定系数."""
        n = len(y)
        if n < 2:
            return 0.0

        y_mean = sum(y) / n
        ss_tot = sum((yi - y_mean) ** 2 for yi in y)
        ss_res = sum((yi - (slope * xi + intercept)) ** 2 for xi, yi in zip(x, y))

        if ss_tot == 0:
            return 1.0  # 完美拟合（所有值相同）

        return max(0.0, 1.0 - ss_res / ss_tot)
