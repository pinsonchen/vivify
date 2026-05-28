"""AI-powered Root Cause Analysis for recurring issues."""
from __future__ import annotations

import logging
import re
from typing import Optional, List, TYPE_CHECKING

from vivify.intelligence.models import RcaReport, IssueCluster
from vivify.models.issue import Issue
from vivify.models.snapshot import ActionLog

if TYPE_CHECKING:
    from vivify.interfaces.storage import StorageProvider

logger = logging.getLogger(__name__)


class RootCauseAnalyzer:
    """Analyzes recurring issues to identify root causes and patterns."""

    def __init__(self, storage: "StorageProvider", rca_threshold: int = 3):
        self.storage = storage
        self.rca_threshold = rca_threshold

    def analyze_recurrence(self, issue: Issue) -> Optional[RcaReport]:
        """判断 issue 是否为重复问题，达到阈值时生成根因报告.

        流程：
        1. 查询 failure_tracking 获取 recurrence_count
        2. 若 < threshold，返回 None
        3. 检查是否已有 RCA 报告（避免重复分析）
        4. 收集历史修复记录
        5. 基于模式分析生成 RCA 报告（规则引擎，非 AI 调用）
        """
        # 获取重复次数
        recurrence_count = self.storage.get_failure_count(issue.hash)
        if recurrence_count < self.rca_threshold:
            return None

        # 检查是否已有近期 RCA 报告
        existing = self.storage.get_rca_reports(issue.hash, limit=1)
        if existing and existing[0].get("recurrence_count", 0) >= recurrence_count:
            return None  # 已分析过且次数未增加

        # 收集历史修复记录
        fix_history = self.get_fix_history(issue)

        # 基于模式生成 RCA（规则引擎）
        report = self._generate_rca_report(issue, recurrence_count, fix_history)

        # 持久化
        if report:
            self.storage.save_rca_report(report)

        return report

    def group_similar_issues(self, issues: List[Issue]) -> List[IssueCluster]:
        """将当前轮次的多个 issue 按相似度聚类.

        算法：
        1. 按 category 分组
        2. 同 category 内按标题 Jaccard 相似度 > 0.4 归组
        3. 返回每组的代表 issue + 关联 issue 列表
        """
        if not issues:
            return []

        # 按 category 分组
        by_category: dict[str, list[Issue]] = {}
        for issue in issues:
            by_category.setdefault(issue.category, []).append(issue)

        clusters: List[IssueCluster] = []

        for category, cat_issues in by_category.items():
            if len(cat_issues) == 1:
                clusters.append(IssueCluster(
                    representative=cat_issues[0],
                    members=cat_issues,
                    category=category,
                    common_pattern=cat_issues[0].title,
                ))
                continue

            # Jaccard 聚类
            used = set()
            for i, issue_a in enumerate(cat_issues):
                if i in used:
                    continue
                cluster_members = [issue_a]
                used.add(i)

                tokens_a = self._tokenize(issue_a.title)
                for j, issue_b in enumerate(cat_issues):
                    if j in used:
                        continue
                    tokens_b = self._tokenize(issue_b.title)
                    similarity = self._jaccard(tokens_a, tokens_b)
                    if similarity > 0.4:
                        cluster_members.append(issue_b)
                        used.add(j)

                common = self._extract_common_pattern(cluster_members)
                clusters.append(IssueCluster(
                    representative=cluster_members[0],
                    members=cluster_members,
                    category=category,
                    common_pattern=common,
                ))

        return clusters

    def get_fix_history(self, issue: Issue) -> List[ActionLog]:
        """获取同类问题的历史修复记录."""
        logs = self.storage.search_action_logs(category=issue.category, limit=10)
        return [l for l in logs if l.action_type in ("heal", "direct_fix")]

    def format_rca_context(self, report: RcaReport) -> str:
        """将 RCA 报告格式化为可注入 prompt 的上下文."""
        lines = [
            "## Root Cause Analysis (Recurring Issue)",
            f"This issue has occurred {report.recurrence_count} times.",
            f"Identified pattern: {report.pattern}",
            f"Root cause: {report.root_cause}",
            f"Suggested fix strategy: {report.suggested_strategy}",
            "",
        ]
        return "\n".join(lines)

    # ── internal helpers ──

    def _generate_rca_report(
        self, issue: Issue, recurrence_count: int, fix_history: List[ActionLog]
    ) -> RcaReport:
        """基于模式分析生成 RCA 报告（规则引擎）."""
        # 分析修复历史中的模式
        pattern = self._detect_pattern(issue, fix_history)
        root_cause = self._infer_root_cause(issue, fix_history)
        strategy = self._suggest_strategy(issue, fix_history)

        # 收集相关 issue hashes
        related = [log.title or "" for log in fix_history[:5] if log.title]

        return RcaReport(
            issue_hash=issue.hash,
            recurrence_count=recurrence_count,
            root_cause=root_cause,
            pattern=pattern,
            suggested_strategy=strategy,
            related_issues=related[:5],
            confidence=min(0.9, 0.3 + recurrence_count * 0.1),
        )

    def _detect_pattern(self, issue: Issue, history: List[ActionLog]) -> str:
        """检测问题模式."""
        if not history:
            return f"Recurring {issue.category} issue: {issue.title}"

        # 检查是否修复后总是复发
        successful_fixes = [h for h in history if h.improved]
        if successful_fixes and len(history) > len(successful_fixes):
            return "Fix-then-regress pattern: fixes succeed but issue recurs"

        # 检查是否总是修复失败
        failed_fixes = [h for h in history if not h.improved]
        if len(failed_fixes) == len(history):
            return f"Persistent failure: all {len(history)} fix attempts failed"

        return f"Intermittent {issue.category} issue with {len(history)} prior attempts"

    def _infer_root_cause(self, issue: Issue, history: List[ActionLog]) -> str:
        """推断根因."""
        cat = issue.category.lower()

        # 基于 category 的启发式根因推断
        if "lint" in cat or "format" in cat:
            return "Code style violations likely introduced by automated changes without linting"
        elif "test" in cat or "ci" in cat:
            if any("flak" in (h.title or "").lower() for h in history):
                return "Flaky test infrastructure causing intermittent failures"
            return "Test infrastructure fragility or unhandled edge cases"
        elif "dependency" in cat or "dep" in cat:
            return "Dependency update cycle creating recurring conflicts"
        elif "security" in cat or "secret" in cat:
            return "Security configuration drift or credential rotation gaps"
        elif "doc" in cat:
            return "Documentation not auto-synced with code changes"

        # 通用推断
        successful = sum(1 for h in history if h.improved)
        if successful > 0:
            return f"Root cause not fully addressed by previous fixes ({successful} partial fixes)"
        return f"Underlying structural issue causing recurring {issue.category} failures"

    def _suggest_strategy(self, issue: Issue, history: List[ActionLog]) -> str:
        """建议修复策略."""
        cat = issue.category.lower()

        if "lint" in cat or "format" in cat:
            return "Add pre-commit hooks or CI gate to prevent style violations"
        elif "test" in cat:
            return "Stabilize test infrastructure; consider retry mechanism for flaky tests"
        elif "dependency" in cat:
            return "Pin dependency versions or add automated compatibility testing"
        elif "security" in cat:
            return "Implement automated secret rotation and security scanning in CI"
        elif "doc" in cat:
            return "Add doc generation to CI pipeline for auto-sync"

        # 基于历史
        if len(history) >= 5:
            return "Consider architectural fix rather than incremental patching"
        return "Investigate deeper root cause; current fix approach may be insufficient"

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """分词."""
        text = text.lower()
        # 分割 camelCase 和 snake_case
        text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
        tokens = re.split(r'[\s_\-./\\:;,!?()[\]{}]+', text)
        return {t for t in tokens if len(t) >= 2}

    @staticmethod
    def _jaccard(set_a: set, set_b: set) -> float:
        """Jaccard 相似度."""
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _extract_common_pattern(issues: List[Issue]) -> str:
        """提取聚类中的共性模式."""
        if not issues:
            return ""
        if len(issues) == 1:
            return issues[0].title
        # 取标题中的公共词
        all_tokens = [RootCauseAnalyzer._tokenize(i.title) for i in issues]
        common = set.intersection(*all_tokens) if all_tokens else set()
        if common:
            return f"Common: {' '.join(sorted(common)[:5])}"
        return issues[0].category
