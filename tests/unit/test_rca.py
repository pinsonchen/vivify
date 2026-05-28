"""Tests for vivify.intelligence.rca.RootCauseAnalyzer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional
from unittest.mock import MagicMock

import pytest

from vivify.intelligence.models import RcaReport
from vivify.intelligence.rca import RootCauseAnalyzer
from vivify.models.issue import Issue, IssueLevel
from vivify.models.snapshot import ActionLog


# ── Helpers ─────────────────────────────────────────────────────────────────


def make_issue(
    *,
    category: str = "lint",
    title: str = "missing newline",
    level: IssueLevel = IssueLevel.LOW,
    source: str = "probe",
) -> Issue:
    return Issue.factory(
        category=category,
        level=level,
        title=title,
        source_probe=source,
    )


def make_log(
    *,
    title: str = "fix lint",
    action_type: str = "heal",
    improved: bool = True,
    category: str = "lint",
) -> ActionLog:
    return ActionLog(
        run_id="r1",
        round_num=1,
        action_type=action_type,
        status="success" if improved else "failed",
        category=category,
        title=title,
        improved=improved,
    )


@pytest.fixture
def storage() -> MagicMock:
    s = MagicMock()
    s.get_failure_count.return_value = 0
    s.get_rca_reports.return_value = []
    s.search_action_logs.return_value = []
    s.save_rca_report.return_value = 1
    return s


@pytest.fixture
def analyzer(storage: MagicMock) -> RootCauseAnalyzer:
    return RootCauseAnalyzer(storage=storage, rca_threshold=3)


# ── group_similar_issues ────────────────────────────────────────────────────


class TestGroupSimilarIssues:
    def test_empty_issues(self, analyzer: RootCauseAnalyzer):
        assert analyzer.group_similar_issues([]) == []

    def test_single_issue(self, analyzer: RootCauseAnalyzer):
        issue = make_issue(category="lint", title="missing newline at EOF")
        clusters = analyzer.group_similar_issues([issue])
        assert len(clusters) == 1
        assert clusters[0].representative is issue
        assert clusters[0].members == [issue]
        assert clusters[0].category == "lint"

    def test_same_category_similar_titles(self, analyzer: RootCauseAnalyzer):
        a = make_issue(category="lint", title="missing newline at EOF foo bar")
        b = make_issue(category="lint", title="missing newline at EOF baz qux")
        clusters = analyzer.group_similar_issues([a, b])
        # 同类别 + 高相似度 → 应聚成 1 簇
        assert len(clusters) == 1
        assert len(clusters[0].members) == 2

    def test_different_categories_separate(self, analyzer: RootCauseAnalyzer):
        a = make_issue(category="lint", title="missing newline")
        b = make_issue(category="security", title="missing newline")
        clusters = analyzer.group_similar_issues([a, b])
        cats = sorted(c.category for c in clusters)
        assert cats == ["lint", "security"]
        assert len(clusters) == 2

    def test_low_similarity_not_merged(self, analyzer: RootCauseAnalyzer):
        a = make_issue(category="lint", title="alpha beta gamma delta")
        b = make_issue(category="lint", title="zulu yankee xray whiskey")
        clusters = analyzer.group_similar_issues([a, b])
        # Jaccard ≈ 0 < 0.4 阈值，应该分开
        assert len(clusters) == 2


# ── analyze_recurrence ──────────────────────────────────────────────────────


class TestAnalyzeRecurrence:
    def test_below_threshold_returns_none(self, analyzer: RootCauseAnalyzer, storage):
        storage.get_failure_count.return_value = 2  # < 3
        issue = make_issue()
        assert analyzer.analyze_recurrence(issue) is None
        storage.save_rca_report.assert_not_called()

    def test_at_threshold_generates_report(self, analyzer: RootCauseAnalyzer, storage):
        storage.get_failure_count.return_value = 3
        storage.get_rca_reports.return_value = []
        issue = make_issue(category="lint", title="bad style")
        report = analyzer.analyze_recurrence(issue)
        assert report is not None
        assert isinstance(report, RcaReport)
        assert report.recurrence_count == 3
        assert report.issue_hash == issue.hash

    def test_existing_report_skips(self, analyzer: RootCauseAnalyzer, storage):
        storage.get_failure_count.return_value = 4
        # 已有更新计数的报告 → 跳过
        storage.get_rca_reports.return_value = [
            {"recurrence_count": 4, "id": 1}
        ]
        issue = make_issue()
        assert analyzer.analyze_recurrence(issue) is None
        storage.save_rca_report.assert_not_called()

    def test_report_saved_to_storage(self, analyzer: RootCauseAnalyzer, storage):
        storage.get_failure_count.return_value = 5
        storage.get_rca_reports.return_value = []
        issue = make_issue()
        report = analyzer.analyze_recurrence(issue)
        assert report is not None
        storage.save_rca_report.assert_called_once_with(report)


# ── format_rca_context ──────────────────────────────────────────────────────


class TestFormatRcaContext:
    def test_format_output(self, analyzer: RootCauseAnalyzer):
        report = RcaReport(
            issue_hash="abcd",
            recurrence_count=4,
            root_cause="Flaky tests",
            pattern="Persistent failure",
            suggested_strategy="Stabilize CI",
        )
        out = analyzer.format_rca_context(report)
        assert "Root Cause Analysis" in out
        assert "occurred 4 times" in out
        assert "Flaky tests" in out
        assert "Persistent failure" in out
        assert "Stabilize CI" in out


# ── Pattern detection / root cause / strategy ──────────────────────────────


class TestPatternDetection:
    def test_fix_then_regress_pattern(self, analyzer: RootCauseAnalyzer):
        history = [
            make_log(improved=True),
            make_log(improved=True),
            make_log(improved=False),
        ]
        issue = make_issue(category="lint", title="bad")
        pattern = analyzer._detect_pattern(issue, history)
        assert "Fix-then-regress" in pattern

    def test_persistent_failure_pattern(self, analyzer: RootCauseAnalyzer):
        history = [make_log(improved=False) for _ in range(3)]
        issue = make_issue(category="ci", title="bad")
        pattern = analyzer._detect_pattern(issue, history)
        assert "Persistent failure" in pattern
        assert "3" in pattern

    def test_root_cause_by_category(self, analyzer: RootCauseAnalyzer):
        # lint/format
        rc = analyzer._infer_root_cause(make_issue(category="lint"), [])
        assert "style" in rc.lower() or "lint" in rc.lower()
        # test/ci
        rc = analyzer._infer_root_cause(make_issue(category="test"), [])
        assert "test" in rc.lower()
        # dependency
        rc = analyzer._infer_root_cause(make_issue(category="dependency"), [])
        assert "depend" in rc.lower()
        # security
        rc = analyzer._infer_root_cause(make_issue(category="security"), [])
        assert "security" in rc.lower() or "credential" in rc.lower()
        # doc
        rc = analyzer._infer_root_cause(make_issue(category="doc"), [])
        assert "doc" in rc.lower()
        # generic
        rc = analyzer._infer_root_cause(make_issue(category="weird"), [])
        assert isinstance(rc, str) and rc

    def test_strategy_suggestion(self, analyzer: RootCauseAnalyzer):
        s_lint = analyzer._suggest_strategy(make_issue(category="lint"), [])
        assert "pre-commit" in s_lint or "CI gate" in s_lint
        s_test = analyzer._suggest_strategy(make_issue(category="test"), [])
        assert "test" in s_test.lower()
        s_dep = analyzer._suggest_strategy(make_issue(category="dependency"), [])
        assert "depend" in s_dep.lower() or "pin" in s_dep.lower()
        # 历史很多 → 架构建议
        history = [make_log() for _ in range(6)]
        s_arch = analyzer._suggest_strategy(make_issue(category="weird"), history)
        assert "architectural" in s_arch.lower() or "architecture" in s_arch.lower()
