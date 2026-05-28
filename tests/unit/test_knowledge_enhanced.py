"""Tests for vivify.knowledge.context_provider 增强功能.

Covers:
- get_targeted_context: entity extraction & graph matching
- get_historical_context: knowledge_entries injection
- get_conventions_for_system_prompt: compact convention formatting
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vivify.knowledge.context_provider import KnowledgeContextProvider
from vivify.knowledge.models import (
    CodeConvention,
    EdgeType,
    GraphEdge,
    GraphMetadata,
    GraphNode,
    KnowledgeGraph,
    NodeType,
)
from vivify.knowledge.storage import KnowledgeStorage
from vivify.models.snapshot import KnowledgeEntry


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def graph_with_entities() -> KnowledgeGraph:
    """Graph containing FUNCTION/CLASS/FILE/MODULE nodes."""
    module = GraphNode(
        id="module:vivify/kernel",
        type=NodeType.MODULE,
        name="kernel",
        path="vivify/kernel",
        responsibility="Core orchestrator",
    )
    func = GraphNode(
        id="func:vivify/kernel/loop.py:run_loop",
        type=NodeType.FUNCTION,
        name="run_loop",
        path="vivify/kernel/loop.py",
        summary="Main event loop entry point",
    )
    cls = GraphNode(
        id="class:vivify/agents/qodercli_agent.py:QoderCliAgent",
        type=NodeType.CLASS,
        name="QoderCliAgent",
        path="vivify/agents/qodercli_agent.py",
        summary="Agent that drives qodercli",
    )
    file_node = GraphNode(
        id="file:vivify/dashboard/app.py",
        type=NodeType.FILE,
        name="app.py",
        path="vivify/dashboard/app.py",
        summary="Dashboard FastAPI app",
    )
    edges = [
        GraphEdge(source=module.id, target=func.id, type=EdgeType.CONTAINS),
    ]
    return KnowledgeGraph(
        metadata=GraphMetadata(project_name="vivify"),
        nodes=[module, func, cls, file_node],
        edges=edges,
    )


@pytest.fixture
def populated_root(project_root: Path, graph_with_entities: KnowledgeGraph) -> Path:
    KnowledgeStorage(project_root).save_graph(graph_with_entities)
    return project_root


# ── get_targeted_context ────────────────────────────────────────────────────


class TestGetTargetedContext:
    def test_finds_function_by_name(self, populated_root: Path):
        provider = KnowledgeContextProvider(populated_root)
        out = provider.get_targeted_context(
            feature_title="refactor run_loop",
            feature_description="rework the run_loop entry point",
        )
        assert out
        assert "精准知识上下文" in out
        assert "run_loop" in out

    def test_finds_class_by_name(self, populated_root: Path):
        provider = KnowledgeContextProvider(populated_root)
        out = provider.get_targeted_context(
            feature_title="extend QoderCliAgent",
            feature_description="add streaming to QoderCliAgent",
        )
        assert out
        assert "QoderCliAgent" in out

    def test_fallback_when_no_match(self, populated_root: Path):
        provider = KnowledgeContextProvider(populated_root)
        # 描述里有看似 entity 的词，但图谱中没有对应节点
        out = provider.get_targeted_context(
            feature_title="add nonexistent_function",
            feature_description="implement TotallyMissingClass",
        )
        # 实现：未匹配返回空
        assert out == ""

    def test_empty_description(self, populated_root: Path):
        provider = KnowledgeContextProvider(populated_root)
        # 完全没有可解析的 entity → 空
        assert provider.get_targeted_context("", "") == ""
        # 仅普通词，无 snake/camel/file pattern
        assert provider.get_targeted_context("hello world", "do something") == ""


# ── get_historical_context ──────────────────────────────────────────────────


class TestGetHistoricalContext:
    def test_returns_formatted_history(self, project_root: Path):
        provider = KnowledgeContextProvider(project_root)
        storage = MagicMock()
        storage.search_knowledge.return_value = [
            KnowledgeEntry(
                category="lint",
                pattern="missing newline",
                solution_summary="ran formatter and committed",
                success=True,
            ),
            KnowledgeEntry(
                category="lint",
                pattern="trailing whitespace",
                solution_summary="cleaned up trailing spaces",
                success=True,
            ),
        ]
        out = provider.get_historical_context("lint", "missing newline at EOF", storage)
        assert "Previously Solved Similar Issues" in out
        assert "missing newline" in out
        assert "trailing whitespace" in out
        # 调用方式
        storage.search_knowledge.assert_called_once()

    def test_no_storage_returns_empty(self, project_root: Path):
        provider = KnowledgeContextProvider(project_root)
        assert provider.get_historical_context("lint", "x", None) == ""

    def test_filters_unsuccessful(self, project_root: Path):
        provider = KnowledgeContextProvider(project_root)
        storage = MagicMock()
        storage.search_knowledge.return_value = [
            KnowledgeEntry(
                category="ci", pattern="flaky", solution_summary="retry",
                success=False,
            ),
            KnowledgeEntry(
                category="ci", pattern="timeout", solution_summary="bumped",
                success=False,
            ),
        ]
        # 全部 success=False → 空
        assert provider.get_historical_context("ci", "any", storage) == ""

    def test_max_3_entries(self, project_root: Path):
        provider = KnowledgeContextProvider(project_root)
        storage = MagicMock()
        storage.search_knowledge.return_value = [
            KnowledgeEntry(
                category="lint", pattern=f"pat{i}",
                solution_summary=f"sol{i}", success=True,
            )
            for i in range(5)
        ]
        out = provider.get_historical_context("lint", "x", storage)
        # 最多 3 条 + 标题 = 4 行
        lines = out.split("\n")
        assert len(lines) <= 4
        assert "pat0" in out and "pat1" in out and "pat2" in out
        assert "pat3" not in out
        assert "pat4" not in out


# ── get_conventions_for_system_prompt ──────────────────────────────────────


class TestGetConventionsForSystemPrompt:
    def test_formats_conventions(self, project_root: Path):
        storage = KnowledgeStorage(project_root)
        storage.save_conventions(
            [
                CodeConvention(category="naming", rule="Use snake_case for functions"),
                CodeConvention(category="imports", rule="Group stdlib/third-party/local"),
            ]
        )
        provider = KnowledgeContextProvider(project_root)
        out = provider.get_conventions_for_system_prompt()
        assert "[Project Conventions]" in out
        assert "[naming]" in out
        assert "snake_case" in out
        assert "[imports]" in out

    def test_no_conventions_returns_empty(self, project_root: Path):
        provider = KnowledgeContextProvider(project_root)
        # 未保存任何 conventions
        assert provider.get_conventions_for_system_prompt() == ""

    def test_length_under_500(self, project_root: Path):
        storage = KnowledgeStorage(project_root)
        # 故意构造大量 conventions，验证截断
        storage.save_conventions(
            [
                CodeConvention(category="naming", rule="x" * 60)
                for _ in range(50)
            ]
        )
        provider = KnowledgeContextProvider(project_root)
        out = provider.get_conventions_for_system_prompt()
        assert out
        assert len(out) < 500
